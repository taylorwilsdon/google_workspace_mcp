"""Short claim-check handles for signed download URLs.

A self-contained signed JWT URL is enormous — a Gmail ``attachmentId`` alone
runs ~300 characters, pushing the full URL past 600 characters (~250 LLM
tokens every time it passes through the model). This module implements the
claim-check alternative: the claims that would have been signed into the JWT
are stored server-side in the shared KV store, keyed by a random 128-bit
handle, and the URL carries only the handle (~22 chars): ``/dl/{handle}``.

The handle *is* the capability: 128 bits from a CSPRNG is as unguessable as
an HMAC signature, the store's TTL enforces the same expiry the JWT would
have carried, and — unlike a JWT — a handle can be revoked by deleting the
row. Records are Fernet-encrypted with the same derived key as the attachment
credential cache, whose store (and backend selection) they reuse wholesale.

Handles live in the same encrypted store as the attachment credential cache
(``core.attachment_cred_cache``) under their own collection: the shared
``WORKSPACE_MCP_OAUTH_PROXY_*`` backend when configured, else an in-process
store, which works single-container. Multi-replica deployments need the
shared backend — but they already do, for credential recovery. When no store
is usable at all, ``store_download_ref`` returns None and the caller falls
back to the self-contained JWT URL, which always works.
"""

import logging
import re
import secrets
import time
from typing import Optional

logger = logging.getLogger(__name__)

_COLLECTION = "signed_download_refs"

# 16 bytes → 22-char urlsafe handle; the whole security margin of the URL.
_HANDLE_BYTES = 16

# token_urlsafe output is [A-Za-z0-9_-]; bound the length so arbitrary path
# garbage never reaches the store as a key.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

# Module-level singleton, same pattern as attachment_cred_cache.
_store = None
_store_built = False


def _build_store():
    """Reuse the attachment credential cache's store (own collection) once.

    Same backend selection, encryption, and failure behavior as the credential
    cache — handles just occupy a different collection in the same store, so
    the two features cannot drift in configuration.
    """
    global _store, _store_built
    if _store_built:
        return _store
    _store_built = True

    try:
        from core.attachment_cred_cache import _build_store as _build_cache_store

        _store = _build_cache_store()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Download handle store unavailable: %s", exc)
        _store = None

    return _store


async def store_download_ref(claims: dict, ttl_seconds: float) -> Optional[str]:
    """Store download claims under a fresh random handle.

    Returns the handle, or None when no store is available or the write fails —
    the caller then falls back to the self-contained JWT URL.
    """
    store = _build_store()
    if store is None:
        return None
    handle = secrets.token_urlsafe(_HANDLE_BYTES)
    try:
        await store.put(handle, dict(claims), collection=_COLLECTION, ttl=ttl_seconds)
        return handle
    except Exception as exc:
        logger.warning("Failed to store download handle: %s", exc)
        return None


async def load_download_ref(handle: str) -> Optional[dict]:
    """Return the claims for a handle, or None (unknown, expired, or malformed)."""
    if not handle or not _HANDLE_RE.fullmatch(handle):
        return None
    store = _build_store()
    if store is None:
        return None
    try:
        record = await store.get(handle, collection=_COLLECTION)
    except Exception as exc:
        logger.warning("Failed to load download handle: %s", exc)
        return None
    if not record:
        return None
    # The store's TTL is the primary expiry; the exp claim (same value a signed
    # JWT would carry) is a backstop against a backend whose TTL semantics slip.
    # Fail closed: a record with a missing or malformed exp is rejected too.
    exp = record.get("exp")
    if not isinstance(exp, (int, float)) or exp < time.time():
        return None
    return record
