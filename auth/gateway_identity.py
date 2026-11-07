"""
Trusted-gateway identity verification.

When an MCP-aware reverse proxy fronts this server, it authenticates the user and
attaches a SIGNED identity assertion (a JWT) to every upstream request. This module
verifies that assertion against the proxy's JWKS and returns the verified claims
(notably the user's email), so the asserted identity can be used as the per-request
principal — without this server terminating MCP OAuth itself.

Provider-agnostic: works with any proxy that injects a JWKS-verifiable JWT identity
header — e.g. Pomerium (x-pomerium-jwt-assertion, ES256), oauth2-proxy, Cloudflare
Access (cf-access-jwt-assertion, RS256), Istio/Envoy, Traefik ForwardAuth. The header
name, signing algorithm(s), JWKS URL, and optional issuer/audience are all configurable
(see auth.oauth_config); defaults target Pomerium.

Security: the assertion is verified cryptographically (signature + expiry, and optional
issuer/audience). An unverified or malformed assertion yields None — callers must treat
that as "no identity" (fail closed), never as a trusted user.
"""

import logging
from typing import Any, Optional

import jwt
from jwt import PyJWKClient
from pydantic.networks import validate_email

from auth.oauth_config import get_oauth_config
from auth.request_identity import get_request_identity

logger = logging.getLogger(__name__)


class GatewayIdentityError(Exception):
    """Raised when a request lacks a valid trusted-gateway identity."""


def normalize_principal_email(value: Any) -> Optional[str]:
    """Return the canonical form used for gateway principals and credential keys."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        _, canonical_email = validate_email(value)
    except ValueError:
        return None
    return canonical_email.lower()


def require_gateway_principal(authenticated_user: Any, authenticated_via: Any) -> str:
    """Return a canonical principal only when it came from gateway verification."""
    email = normalize_principal_email(authenticated_user)
    if authenticated_via != "gateway_assertion" or not email:
        raise GatewayIdentityError(
            "Trusted-gateway mode requires a request-scoped verified gateway principal"
        )
    return email


async def get_verified_gateway_principal(context=None) -> str:
    """Resolve the authoritative gateway principal from the active FastMCP request."""
    identity = await get_request_identity(context)
    if identity is None:
        raise GatewayIdentityError(
            "Trusted-gateway principal is unavailable outside an MCP request"
        )

    return require_gateway_principal(identity.email, identity.via)


# PyJWKClient caches fetched keys (and refreshes on unknown kid). Cache one client per
# JWKS URL for the process lifetime.
_jwks_clients: dict[str, PyJWKClient] = {}


def _get_jwks_client(jwks_url: str) -> PyJWKClient:
    client = _jwks_clients.get(jwks_url)
    if client is None:
        # PyJWKClient keeps fetched signing keys in-memory and re-fetches when it sees an
        # unknown kid (key rotation), so this is safe to hold for the process lifetime.
        client = PyJWKClient(jwks_url, cache_keys=True)
        _jwks_clients[jwks_url] = client
    return client


def verify_gateway_assertion(token: str) -> Optional[dict]:
    """
    Verify a trusted-gateway identity-assertion JWT and return its claims.

    Args:
        token: the raw JWT from the assertion header.

    Returns:
        The verified claims dict (includes "email"/"sub") on success, else None.
    """
    if not token:
        return None

    config = get_oauth_config()
    jwks_url = config.gateway_identity_jwks_url
    if not jwks_url:
        logger.error(
            "verify_gateway_assertion called but GATEWAY_IDENTITY_JWKS_URL is unset"
        )
        return None
    audience = config.gateway_identity_audience
    if not audience:
        logger.error(
            "verify_gateway_assertion called but GATEWAY_IDENTITY_AUDIENCE is unset"
        )
        return None

    try:
        signing_key = _get_jwks_client(jwks_url).get_signing_key_from_jwt(token)

        decode_kwargs: dict = {
            # Pin to the configured algorithm(s) so a malicious token can't downgrade to
            # "alg: none" or trigger an HMAC/asymmetric confusion attack.
            "algorithms": config.gateway_identity_algorithms,
            # Require expiry and audience; issuer is additionally enforced when configured.
            "options": {
                "require": ["exp"],
                "verify_aud": True,
            },
            "audience": audience,
        }
        if config.gateway_identity_issuer:
            decode_kwargs["issuer"] = config.gateway_identity_issuer

        claims = jwt.decode(token, signing_key.key, **decode_kwargs)
        return claims

    except jwt.PyJWTError as e:
        # Invalid signature / expired / wrong aud-iss / unknown kid, etc.
        logger.warning(
            "SECURITY: rejected gateway identity assertion (%s: %s)",
            type(e).__name__,
            e,
        )
        return None
    except Exception as e:  # noqa: BLE001 - JWKS fetch / network / unexpected
        logger.error(
            "Error verifying gateway identity assertion (%s: %s)",
            type(e).__name__,
            e,
        )
        return None


def extract_email_from_assertion(token: str) -> Optional[str]:
    """Verify the assertion and return the lowercased email claim, or None."""
    claims = verify_gateway_assertion(token)
    if not claims:
        return None
    email = normalize_principal_email(claims.get("email"))
    if not email:
        logger.warning(
            "SECURITY: verified gateway assertion has no usable 'email' claim (sub=%s)",
            claims.get("sub"),
        )
        return None
    return email
