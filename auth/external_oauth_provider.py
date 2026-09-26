"""
External OAuth Provider for Google Workspace MCP

Extends FastMCP's GoogleProvider to support external OAuth flows where
access tokens (ya29.*) are issued by external systems and need validation.

This provider acts as a Resource Server only - it validates tokens issued by
Google's Authorization Server but does not issue tokens itself.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import functools
import hashlib
import logging
import os
import time
from typing import Optional

from starlette.routing import Route
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth import AccessToken
from google.oauth2.credentials import Credentials

from auth.oauth_types import WorkspaceAccessToken

logger = logging.getLogger(__name__)

# Google's OAuth 2.0 Authorization Server
GOOGLE_ISSUER_URL = "https://accounts.google.com"

# Configurable session time in seconds (default: 1 hour, max: 24 hours)
_DEFAULT_SESSION_TIME = 3600
_MAX_SESSION_TIME = 86400

# Token validation is unauthenticated work and may block for the full Google API
# socket timeout. Keep it out of asyncio's process-wide default executor so a burst
# of invalid tokens cannot starve authenticated Google Workspace operations.
_DEFAULT_TOKEN_VALIDATION_WORKERS = 4

# Validated identities are remembered per token hash for this many seconds.
# Off by default: a cached token skips the userinfo check until it ages out.
_TOKEN_VALIDATION_CACHE_TTL_ENV = "WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL"
_TOKEN_VALIDATION_CACHE_MAX_ENTRIES = 10_000
# Caps how long a revoked or expired token can keep passing the local check.
_MAX_TOKEN_VALIDATION_CACHE_TTL = 300


@functools.lru_cache(maxsize=1)
def get_session_time() -> int:
    """Parse SESSION_TIME from environment with fallback, min/max clamp.

    Result is cached; changes require a server restart.
    """
    raw = os.getenv("SESSION_TIME", "")
    if not raw:
        return _DEFAULT_SESSION_TIME
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Invalid SESSION_TIME=%r, falling back to %d", raw, _DEFAULT_SESSION_TIME
        )
        return _DEFAULT_SESSION_TIME
    clamped = max(1, min(value, _MAX_SESSION_TIME))
    if clamped != value:
        logger.warning(
            "SESSION_TIME=%d clamped to %d (allowed range: 1–%d)",
            value,
            clamped,
            _MAX_SESSION_TIME,
        )
    return clamped


def get_token_validation_cache_ttl() -> int:
    """Parse WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL; unset or 0 disables it.

    Invalid values raise instead of falling back, so a misconfigured deployment
    fails at startup. Values above the maximum are clamped with a warning.
    """
    raw = os.getenv(_TOKEN_VALIDATION_CACHE_TTL_ENV, "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        raise ValueError(
            f"{_TOKEN_VALIDATION_CACHE_TTL_ENV} must be a non-negative integer "
            f"(seconds), got {raw!r}"
        )
    if value > _MAX_TOKEN_VALIDATION_CACHE_TTL:
        logger.warning(
            "%s=%d clamped to %d",
            _TOKEN_VALIDATION_CACHE_TTL_ENV,
            value,
            _MAX_TOKEN_VALIDATION_CACHE_TTL,
        )
        return _MAX_TOKEN_VALIDATION_CACHE_TTL
    return value


class ExternalOAuthProvider(GoogleProvider):
    """
    Extended GoogleProvider that supports validating external Google OAuth access tokens.

    This provider handles ya29.* access tokens by calling Google's userinfo API,
    while maintaining compatibility with standard JWT ID tokens.

    Unlike the standard GoogleProvider, this acts as a Resource Server only:
    - Does NOT create /authorize, /token, /register endpoints
    - Only advertises Google's authorization server in metadata
    - Only validates tokens, does not issue them
    """

    def __init__(
        self,
        client_id: str,
        client_secret: Optional[str] = None,
        resource_server_url: Optional[str] = None,
        token_validation_workers: int = _DEFAULT_TOKEN_VALIDATION_WORKERS,
        token_validation_cache_ttl: int = 0,
        **kwargs,
    ):
        """Initialize and store client credentials for token validation."""
        if token_validation_workers < 1:
            raise ValueError("token_validation_workers must be at least 1")
        if token_validation_cache_ttl < 0:
            raise ValueError("token_validation_cache_ttl must not be negative")

        self._resource_server_url = resource_server_url
        if resource_server_url and "resource_base_url" not in kwargs:
            kwargs["resource_base_url"] = resource_server_url
        super().__init__(client_id=client_id, client_secret=client_secret, **kwargs)
        # Store credentials as they're not exposed by parent class
        self._client_id = client_id
        self._client_secret = client_secret
        # Store as string - Pydantic validates it when passed to models
        self.resource_server_url = self._resource_server_url
        self._token_validation_executor: Optional[ThreadPoolExecutor] = (
            ThreadPoolExecutor(
                max_workers=token_validation_workers,
                thread_name_prefix="external-token-validation",
            )
        )
        # ThreadPoolExecutor has an unbounded internal queue. Admit no more work
        # than can run immediately so overload fails closed instead of accumulating.
        self._token_validation_slots = asyncio.Semaphore(token_validation_workers)
        # Only touched from the event loop, so no lock is needed. Values are
        # (monotonic expiry, email, sub); the token itself is never stored.
        self._token_validation_cache_ttl = token_validation_cache_ttl
        self._validated_identities: dict[str, tuple[float, str, Optional[str]]] = {}

    def _cached_identity(self, cache_key: str) -> Optional[tuple[str, Optional[str]]]:
        entry = self._validated_identities.get(cache_key)
        if entry is None:
            return None
        expires_at, email, sub = entry
        if expires_at <= time.monotonic():
            del self._validated_identities[cache_key]
            return None
        return email, sub

    def _remember_identity(
        self, cache_key: str, email: str, sub: Optional[str]
    ) -> None:
        if not self._token_validation_cache_ttl:
            return
        now = time.monotonic()
        cache = self._validated_identities
        if len(cache) >= _TOKEN_VALIDATION_CACHE_MAX_ENTRIES:
            for key in [
                k for k, (expires_at, _, _) in cache.items() if expires_at <= now
            ]:
                del cache[key]
        if len(cache) >= _TOKEN_VALIDATION_CACHE_MAX_ENTRIES:
            del cache[next(iter(cache))]
        cache[cache_key] = (now + self._token_validation_cache_ttl, email, sub)

    def _build_access_token(
        self, token: str, email: str, sub: Optional[str]
    ) -> WorkspaceAccessToken:
        scope_list = list(getattr(self, "required_scopes", []) or [])
        return WorkspaceAccessToken(
            token=token,
            scopes=scope_list,
            expires_at=int(time.time()) + get_session_time(),
            claims={"email": email, "sub": sub},
            client_id=self._client_id,
            email=email,
            sub=sub,
        )

    def close(self) -> None:
        """Stop accepting token-validation work and release executor resources."""
        self._validated_identities.clear()
        executor = self._token_validation_executor
        if executor is None:
            return
        self._token_validation_executor = None
        executor.shutdown(wait=False, cancel_futures=True)

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """
        Verify a token - supports both JWT ID tokens and ya29.* access tokens.

        For ya29.* access tokens (issued externally), validates by calling
        Google's userinfo API. For JWT tokens, delegates to parent class.

        Args:
            token: Token string to verify (JWT or ya29.* access token)

        Returns:
            AccessToken object if valid, None otherwise
        """
        # For ya29.* access tokens, validate using Google's userinfo API
        if token.startswith("ya29."):
            logger.debug("Validating external Google OAuth access token")

            cache_key = hashlib.sha256(token.encode()).hexdigest()
            cached = self._cached_identity(cache_key)
            if cached is not None:
                return self._build_access_token(token, *cached)

            try:
                from auth.google_auth import get_user_info

                # Create minimal Credentials object for userinfo API call
                credentials = Credentials(
                    token=token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=self._client_id,
                    client_secret=self._client_secret,
                )

                # Validate token by calling userinfo API. This is deliberately
                # isolated from asyncio's default executor, which handles the
                # authenticated Google Workspace API calls throughout the server.
                if self._token_validation_slots.locked():
                    logger.warning(
                        "External token validation capacity exhausted; rejecting token"
                    )
                    return None

                await self._token_validation_slots.acquire()
                executor = self._token_validation_executor
                if executor is None:
                    self._token_validation_slots.release()
                    logger.warning(
                        "External token validation requested after provider shutdown"
                    )
                    return None

                try:
                    validation_future = asyncio.get_running_loop().run_in_executor(
                        executor,
                        functools.partial(
                            get_user_info, credentials, skip_valid_check=True
                        ),
                    )
                except Exception:
                    self._token_validation_slots.release()
                    raise

                # Shield the worker future so request cancellation does not mark it
                # complete and release the slot while its HTTPS call is still running.
                validation_future.add_done_callback(
                    lambda _: self._token_validation_slots.release()
                )
                user_info = await asyncio.shield(validation_future)

                if user_info and user_info.get("email"):
                    logger.info(
                        f"Validated external access token for: {user_info['email']}"
                    )
                    self._remember_identity(
                        cache_key, user_info["email"], user_info.get("id")
                    )
                    return self._build_access_token(
                        token, user_info["email"], user_info.get("id")
                    )
                else:
                    logger.error("Could not get user info from access token")
                    return None

            except Exception as e:
                logger.error(f"Error validating external access token: {e}")
                return None

        # For JWT tokens, use parent class implementation
        return await super().verify_token(token)

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        """
        Get OAuth routes for external provider mode.

        Returns only protected resource metadata routes that point to Google
        as the authorization server. Does not create authorization server routes
        (/authorize, /token, etc.) since tokens are issued by Google directly.

        Args:
            mcp_path: Path where FastMCP mounts the protected MCP endpoint.

        Returns:
            List of routes - only protected resource metadata
        """
        from mcp.server.auth.routes import create_protected_resource_routes

        if not self.resource_server_url:
            logger.warning(
                "ExternalOAuthProvider: resource_server_url not set, no routes created"
            )
            return []

        self.set_mcp_path(mcp_path)
        resource_url = self._get_resource_url(mcp_path)
        if not resource_url:
            logger.warning(
                "ExternalOAuthProvider: protected resource URL could not be resolved"
            )
            return []

        # Create protected resource routes that point to Google as the authorization server
        # Pass strings directly - Pydantic validates them during model construction
        protected_routes = create_protected_resource_routes(
            resource_url=resource_url,
            authorization_servers=[GOOGLE_ISSUER_URL],
            scopes_supported=self.required_scopes,
            resource_name="Google Workspace MCP",
            resource_documentation=None,
        )

        logger.info(
            f"ExternalOAuthProvider: Created protected resource routes pointing to {GOOGLE_ISSUER_URL}"
        )
        return protected_routes
