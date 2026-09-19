"""Environment-backed configuration for FastMCP's OAuth proxy."""

import logging
import os
from typing import Optional


logger = logging.getLogger(__name__)

OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV = (
    "WORKSPACE_MCP_OAUTH_PROXY_TOKEN_EXPIRY_THRESHOLD_SECONDS"
)
OAUTH_ACCESS_TOKEN_EXPIRY_ENV = "WORKSPACE_MCP_OAUTH_PROXY_ACCESS_TOKEN_EXPIRY_SECONDS"
OAUTH_REQUIRE_CONSENT_ENV = "WORKSPACE_MCP_OAUTH_PROXY_REQUIRE_CONSENT"
MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS = 5 * 60
MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS = 30 * 24 * 60 * 60

_CONSENT_MODES: dict[str, bool | str] = {
    "always": True,
    "true": True,
    "1": True,
    "yes": True,
    "on": True,
    "remember": "remember",
    "external": "external",
    "never": False,
    "false": False,
    "0": False,
    "no": False,
    "off": False,
}


def _parse_expiry_seconds_env(
    name: str, *, minimum: int, maximum: int
) -> Optional[int]:
    """Read a bounded integer of seconds from the environment."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        seconds = int(raw)
    except ValueError:
        logger.warning("Ignoring %s: %r is not an integer", name, raw)
        return None
    if not minimum <= seconds <= maximum:
        logger.warning(
            "Ignoring %s: %d is outside the supported range %d-%d seconds",
            name,
            seconds,
            minimum,
            maximum,
        )
        return None
    return seconds


def get_oauth_proxy_expiry_kwargs() -> dict[str, int]:
    """Return configured token-expiry keyword arguments for GoogleProvider.

    Invalid or unset values are omitted so FastMCP retains ownership of its
    defaults and future compatibility.
    """
    token_expiry_threshold_seconds = _parse_expiry_seconds_env(
        OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV,
        minimum=0,
        maximum=MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS,
    )
    fastmcp_access_token_expiry_seconds = _parse_expiry_seconds_env(
        OAUTH_ACCESS_TOKEN_EXPIRY_ENV,
        minimum=1,
        maximum=MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS,
    )

    expiry_kwargs: dict[str, int] = {}
    if token_expiry_threshold_seconds is not None:
        logger.info(
            "OAuth 2.1: refreshing upstream tokens %ds before expiry",
            token_expiry_threshold_seconds,
        )
        expiry_kwargs["token_expiry_threshold_seconds"] = token_expiry_threshold_seconds
    if fastmcp_access_token_expiry_seconds is not None:
        logger.info(
            "OAuth 2.1: issuing FastMCP access tokens with a %ds lifetime",
            fastmcp_access_token_expiry_seconds,
        )
        expiry_kwargs["fastmcp_access_token_expiry_seconds"] = (
            fastmcp_access_token_expiry_seconds
        )
    return expiry_kwargs


def get_oauth_proxy_consent_kwargs() -> dict[str, bool | str]:
    """Return the configured consent-screen keyword argument for GoogleProvider.

    Unset or unrecognised values are omitted so FastMCP retains ownership of its
    default, which prompts on every authorization.
    """
    raw = os.getenv(OAUTH_REQUIRE_CONSENT_ENV, "").strip().lower()
    if not raw:
        return {}
    if raw not in _CONSENT_MODES:
        logger.warning(
            "Ignoring %s: %r is not one of %s",
            OAUTH_REQUIRE_CONSENT_ENV,
            raw,
            ", ".join(sorted(_CONSENT_MODES)),
        )
        return {}

    mode = _CONSENT_MODES[raw]
    if mode is False:
        logger.warning(
            "OAuth 2.1: consent screen disabled via %s; this drops the "
            "authorization-server-in-the-middle mitigation and is intended for "
            "local development only",
            OAUTH_REQUIRE_CONSENT_ENV,
        )
    else:
        logger.info("OAuth 2.1: consent screen mode set to %r", mode)
    return {"require_authorization_consent": mode}
