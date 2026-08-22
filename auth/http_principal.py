"""Principal resolution for plain HTTP routes (no MCP request context).

``AuthInfoMiddleware`` resolves the principal for tool and prompt calls, but custom
routes such as ``/attachments/{file_id}`` are ordinary HTTP endpoints with no MCP
session or FastMCP context to read from. They still need to know who is calling:
findings 24, 30 and 39 are all the same bug -- the attachment route served any stored
file to anyone who knew (or guessed) its id.

The rules mirror ``auth.principal``: identity comes from the transport, and there is
no fallback to something the caller asserts.
"""

import asyncio
import logging
from typing import Mapping, Optional

from auth.gateway_identity import GatewayIdentityError, extract_email_from_assertion
from auth.oauth_config import (
    get_oauth_config,
    is_oauth21_enabled,
    is_trust_gateway_identity,
)
from auth.principal import get_configured_user_email

logger = logging.getLogger(__name__)


def _bearer_token(headers: Mapping[str, str]) -> Optional[str]:
    auth_header = headers.get("authorization") or headers.get("Authorization") or ""
    if auth_header[:7].lower() != "bearer ":
        return None
    token = auth_header[7:].strip()
    return token or None


async def resolve_http_principal(headers: Mapping[str, str]) -> Optional[str]:
    """Return the verified principal for an HTTP request, or ``None``.

    Resolution order matches the deployment's single authoritative identity source:

    1. **Trusted gateway** -- verify the signed assertion in the configured header.
    2. **OAuth 2.1** -- verify the ``Authorization: Bearer`` token with the active
       auth provider and take the email from its claims.
    3. **Neither** -- the deployment is single-user (see ``auth.principal``), so the
       principal is ``USER_GOOGLE_EMAIL``. ``None`` when that is unset, which callers
       must treat as unauthenticated.

    ``None`` is always "could not verify", never "allow".
    """
    if is_trust_gateway_identity():
        header_name = get_oauth_config().gateway_identity_header
        assertion = headers.get(header_name)
        if not assertion:
            logger.debug("No trusted-gateway assertion header %r present", header_name)
            return None
        try:
            # PyJWKClient can do blocking network I/O on cold start / key rotation.
            return await asyncio.to_thread(extract_email_from_assertion, assertion)
        except GatewayIdentityError:
            logger.warning("Trusted-gateway assertion rejected on an HTTP route")
            return None
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Trusted-gateway assertion verification failed: %s", exc)
            return None

    if is_oauth21_enabled():
        token = _bearer_token(headers)
        if not token:
            return None
        from auth.oauth21_session_store import get_auth_provider

        provider = get_auth_provider()
        if provider is None:
            logger.warning("OAuth 2.1 is enabled but no auth provider is configured")
            return None
        try:
            verified = await provider.verify_token(token)
        except Exception as exc:
            logger.warning("Bearer token verification failed on an HTTP route: %s", exc)
            return None
        if not verified:
            return None
        email = getattr(verified, "email", None)
        if not email:
            claims = getattr(verified, "claims", None) or {}
            email = claims.get("email")
        return email or None

    # Legacy / stdio: single-user by construction.
    return get_configured_user_email()
