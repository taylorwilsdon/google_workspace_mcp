"""Simple thread-safe sliding-window rate limiter for ASGI HTTP routes."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


class RateLimitMiddleware:
    """Per-client IP sliding-window rate limits with path-class budgets."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._hits: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._oauth_limit = _int_env("WORKSPACE_RATE_LIMIT_OAUTH_PER_MINUTE", 20)
        self._attachment_limit = _int_env(
            "WORKSPACE_RATE_LIMIT_ATTACHMENT_PER_MINUTE", 60
        )
        self._health_limit = _int_env("WORKSPACE_RATE_LIMIT_HEALTH_PER_MINUTE", 120)
        self._default_limit = _int_env("WORKSPACE_RATE_LIMIT_DEFAULT_PER_MINUTE", 180)
        self._window = 60.0
        self._enabled = os.getenv("WORKSPACE_RATE_LIMIT_ENABLED", "true").lower() in {
            "1",
            "true",
            "yes",
        }

    def _client_ip(self, scope: Scope) -> str:
        headers = dict(scope.get("headers") or [])
        forwarded = headers.get(b"x-forwarded-for")
        if forwarded:
            return forwarded.decode("latin-1").split(",")[0].strip() or "unknown"
        client = scope.get("client")
        if client and client[0]:
            return str(client[0])
        return "unknown"

    def _bucket_and_limit(self, path: str) -> Tuple[str, int]:
        if path.startswith("/oauth") or path in {"/callback", "/auth/callback"}:
            return "oauth", self._oauth_limit
        if path.startswith("/attachments/"):
            return "attachment", self._attachment_limit
        if path in {"/", "/health", "/health/details"}:
            return "health", self._health_limit
        return "default", self._default_limit

    def _allow(self, key: Tuple[str, str], limit: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            cutoff = now - self._window
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        bucket, limit = self._bucket_and_limit(path)
        ip = self._client_ip(scope)
        if not self._allow((ip, bucket), limit):
            logger.warning(
                "Rate limit exceeded ip=%s bucket=%s path=%s", ip, bucket, path
            )
            response = JSONResponse(
                {"error": "Rate limit exceeded. Please retry later."},
                status_code=429,
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
