"""Signed capability URLs for streaming attachment downloads (Design A).

Neither the Gmail nor the Drive API exposes a native public/signed download URL —
the bytes only come back from an authenticated API call carrying the user's OAuth
token. That leaves two unappealing default behaviours for a remote MCP server:

  * stateless mode returns the attachment as base64 *through the model* — slow and
    enormously token-hungry; and
  * non-stateless mode writes the file to local disk and serves it from an
    unauthenticated ``/attachments/{file_id}`` route — incompatible with a
    stateless, multi-replica hosted deployment, and a per-user authz gap.

Design A threads the needle: the tool hands the client a short-lived **signed URL**
whose token encodes the attachment reference, its owner, and an expiry. The
``/attachments/signed/{token}`` route verifies the signature, recovers the owner's
credentials, fetches the bytes from Google on demand, and streams them straight to
the client. Nothing is base64-encoded into the model and nothing is written to
disk; the signature *is* the per-user authorization.

The signing key defaults to the OAuth client secret so the feature works with no
extra configuration in the single-profile PoC, but a dedicated
``WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY`` should be set for any real deployment.
"""

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import jwt

logger = logging.getLogger(__name__)

_ALG = "HS256"
# Lifetime of a signed URL. The cached credential record (attachment_cred_cache)
# is given the same TTL so creds never outlive the link that needs them.
ATTACHMENT_URL_TTL_SECONDS = 900  # 15 minutes
_DEFAULT_TTL_SECONDS = ATTACHMENT_URL_TTL_SECONDS

# Keep a signed URL strictly inside the credential snapshot's remaining life: the
# URL must expire at least this margin before the credential does.
_TTL_EXPIRY_MARGIN_SECONDS = 30

# Reserved JWT claim keys that a source-specific ``ref`` must never override.
_RESERVED_CLAIMS = frozenset({"src", "sub", "iat", "exp", "fn", "mt"})


def signed_attachment_urls_enabled() -> bool:
    """True when the tool should return signed streaming URLs instead of base64/disk."""
    return os.getenv("WORKSPACE_MCP_SIGNED_ATTACHMENT_URLS", "false").lower() == "true"


def short_signed_urls_enabled() -> bool:
    """True (default) when signed URLs should use short claim-check handles.

    Short URLs store the claims server-side (``core.download_handles``) and put
    only a 22-char random handle in the URL — ~10x fewer characters/LLM tokens
    than the self-contained JWT form. Set WORKSPACE_MCP_SHORT_SIGNED_URLS=false
    to force the JWT form (e.g. multi-replica deployments that deliberately run
    without a shared storage backend).
    """
    return os.getenv("WORKSPACE_MCP_SHORT_SIGNED_URLS", "true").lower() == "true"


def clamp_ttl_to_expiry(
    expiry: Optional[datetime],
    default_ttl: int = ATTACHMENT_URL_TTL_SECONDS,
    *,
    now: Optional[datetime] = None,
) -> int:
    """Clamp a signed-URL TTL so the link never outlives its credential snapshot.

    In OAuth 2.1 proxy mode the recovered credential is a bare access token with no
    refresh_token, so once it expires the signed-download route cannot renew it.
    The URL must therefore expire no later than the token. Returns:

      - ``default_ttl`` when ``expiry`` is None (unknown remaining life);
      - otherwise ``min(default_ttl, seconds_left - margin)``, which may be **<= 0**
        when the credential is already at/near expiry.

    The result is intentionally **not** floored to a minimum: flooring could yield a
    URL that outlives the credential. A non-positive return signals the caller to
    skip minting and fall back to the normal download path.

    Args:
        expiry: The credential's expiry as a naive UTC datetime
            (``google.oauth2.credentials.Credentials.expiry``), or None.
        default_ttl: The desired TTL when not constrained by the token.
        now: Reference time (naive/aware UTC); defaults to ``datetime.now(UTC)``.
    """
    if expiry is None:
        return default_ttl
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    seconds_left = (expiry.replace(tzinfo=timezone.utc) - ref).total_seconds()
    return int(min(default_ttl, seconds_left - _TTL_EXPIRY_MARGIN_SECONDS))


def _signing_key() -> str:
    """Resolve the HMAC signing key.

    Prefer a dedicated key; fall back to the OAuth client secret so the PoC works
    out of the box (the secret is already a high-entropy shared secret unique to
    the profile, and never leaves the server).
    """
    key = os.getenv("WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY") or os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRET"
    )
    if not key:
        raise RuntimeError(
            "No signing key available for attachment URLs. Set "
            "WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY (or GOOGLE_OAUTH_CLIENT_SECRET)."
        )
    return key


def mint_download_token(
    *,
    source: str,
    user_email: str,
    ref: dict,
    filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Sign a capability token for a single downloadable resource and owner.

    Source-agnostic: ``ref`` carries the source-specific locator (Gmail:
    ``{"mid", "aid"}``; Drive: ``{"fid", "emt"?}``) and is merged into the token
    claims. The matching fetcher in ``core.signed_download`` knows how to turn
    those claims back into bytes.

    Args:
        source: Origin of the resource (``"gmail"`` | ``"drive"``).
        user_email: Owner whose credentials the route must use to fetch the bytes.
        ref: Source-specific locator claims (must not collide with reserved keys
            ``src``/``sub``/``iat``/``exp``/``fn``/``mt``).
        filename: Resolved filename, signed in so the route can name the download
            without re-resolving. Use for stable-id sources (e.g. Drive); omit for
            Gmail, whose attachment id is ephemeral (the route resolves by size).
        mime_type: Resolved MIME type, signed in for the same reason.
        ttl_seconds: Lifetime of the URL.

    Raises:
        ValueError: if ``ref`` contains a key that collides with a reserved claim,
            which would otherwise let the locator override ``src``/``sub``/``exp``.
    """
    payload = _build_claims(
        source=source,
        user_email=user_email,
        ref=ref,
        filename=filename,
        mime_type=mime_type,
        ttl_seconds=ttl_seconds,
    )
    return jwt.encode(payload, _signing_key(), algorithm=_ALG)


def _build_claims(
    *,
    source: str,
    user_email: str,
    ref: dict,
    filename: Optional[str],
    mime_type: Optional[str],
    ttl_seconds: int,
) -> dict:
    """Assemble the claims for one downloadable resource (shared by JWT + handle forms)."""
    collisions = _RESERVED_CLAIMS & ref.keys()
    if collisions:
        raise ValueError(
            f"ref must not contain reserved claim keys: {sorted(collisions)}"
        )
    now = int(time.time())
    payload = {
        "src": source,
        "sub": user_email,
        "iat": now,
        "exp": now + ttl_seconds,
        **ref,
    }
    if filename:
        payload["fn"] = filename
    if mime_type:
        payload["mt"] = mime_type
    return payload


def mint_attachment_token(
    *,
    source: str,
    message_id: str,
    attachment_id: str,
    user_email: str,
    filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Gmail convenience wrapper over :func:`mint_download_token`."""
    return mint_download_token(
        source=source,
        user_email=user_email,
        ref={"mid": message_id, "aid": attachment_id},
        filename=filename,
        mime_type=mime_type,
        ttl_seconds=ttl_seconds,
    )


def verify_attachment_token(token: str) -> Optional[dict]:
    """Verify and decode a capability token. Returns the claims, or None if invalid.

    Signature mismatch, expiry, and malformed tokens all return None — the route
    treats any None as a 403.
    """
    try:
        return jwt.decode(
            token,
            _signing_key(),
            algorithms=[_ALG],
            # Server-minted tokens always carry these; requiring them means a
            # validly-signed token without an expiry can never pass.
            options={"require": ["exp", "sub", "iat"]},
        )
    except Exception:
        return None


def format_ttl(ttl_seconds: float) -> str:
    """Human label for a link lifetime (e.g. ``"45 seconds"``, ``"~14 minutes"``).

    The effective TTL is clamped to the credential's remaining life, so near the
    token's expiry a link may live far less than the default 15 minutes — the
    tool response must state the real lifetime, not the default.
    """
    if ttl_seconds < 120:
        return f"{int(ttl_seconds)} seconds"
    return f"~{int(ttl_seconds // 60)} minutes"


def _external_base_url() -> str:
    """Resolve the externally reachable base URL (reverse-proxy aware).

    Mirrors ``attachment_storage.get_attachment_url`` so every download route
    resolves the same base.
    """
    from core.config import WORKSPACE_MCP_PORT, WORKSPACE_MCP_BASE_URI

    external_url = os.getenv("WORKSPACE_EXTERNAL_URL")
    if external_url:
        return external_url.rstrip("/")
    return f"{WORKSPACE_MCP_BASE_URI}:{WORKSPACE_MCP_PORT}"


def get_signed_attachment_url(token: str) -> str:
    """Build the absolute ``/attachments/signed/{token}`` URL for a minted token."""
    return f"{_external_base_url()}/attachments/signed/{token}"


async def build_download_url(
    *,
    source: str,
    user_email: str,
    ref: dict,
    filename: Optional[str] = None,
    mime_type: Optional[str] = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> str:
    """Best URL for a downloadable resource: short claim-check form, else JWT.

    The short form (``/dl/{handle}``, ~60 chars total) stores the claims in the
    shared KV store via ``core.download_handles``; it needs a store write, so
    when that is unavailable or fails — or short URLs are disabled — this falls
    back to the self-contained signed-JWT form (``/attachments/signed/{token}``,
    up to ~700 chars for Gmail), which always works.
    """
    claims = _build_claims(
        source=source,
        user_email=user_email,
        ref=ref,
        filename=filename,
        mime_type=mime_type,
        ttl_seconds=ttl_seconds,
    )
    if short_signed_urls_enabled():
        from core.download_handles import store_download_ref

        handle = await store_download_ref(claims, ttl_seconds)
        if handle:
            return f"{_external_base_url()}/dl/{handle}"
        logger.debug("Short signed URL unavailable (no handle store); using JWT form.")
    token = jwt.encode(claims, _signing_key(), algorithm=_ALG)
    return f"{_external_base_url()}/attachments/signed/{token}"
