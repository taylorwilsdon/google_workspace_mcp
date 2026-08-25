"""HMAC-signed tokens for temporary attachment download URLs."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 3600

_generated_key: Optional[bytes] = None
_key_lock = threading.Lock()


def _signing_key() -> bytes:
    material = (
        os.getenv("WORKSPACE_ATTACHMENT_SIGNING_KEY", "").strip()
        or os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "").strip()
        or os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    )
    if material:
        return material.encode("utf-8")

    global _generated_key
    with _key_lock:
        if _generated_key is None:
            _generated_key = secrets.token_bytes(32)
            logger.warning(
                "No attachment signing key configured; using a random "
                "process-stable key. Attachment URLs become invalid after a "
                "process restart. Set WORKSPACE_ATTACHMENT_SIGNING_KEY (or "
                "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY / "
                "GOOGLE_OAUTH_CLIENT_SECRET) for stable tokens."
            )
        return _generated_key


def mint_attachment_token(
    file_id: str, ttl_seconds: int = DEFAULT_TTL_SECONDS
) -> str:
    """Return ``{exp}.{hex_hmac}`` bound to ``file_id``."""
    exp = int(time.time()) + max(1, int(ttl_seconds))
    msg = f"{file_id}.{exp}".encode("utf-8")
    sig = hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_attachment_token(file_id: str, token: Optional[str]) -> bool:
    """Validate an attachment download token."""
    if not token or "." not in token:
        return False
    try:
        exp_str, sig = token.split(".", 1)
        exp = int(exp_str)
    except (ValueError, TypeError):
        return False
    if exp < int(time.time()):
        return False
    expected = hmac.new(
        _signing_key(), f"{file_id}.{exp}".encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, sig)
