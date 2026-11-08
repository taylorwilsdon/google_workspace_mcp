"""Environment-backed configuration for FastMCP's OAuth proxy."""

import logging
import os
from typing import Optional


logger = logging.getLogger(__name__)

OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV = (
    "WORKSPACE_MCP_OAUTH_PROXY_TOKEN_EXPIRY_THRESHOLD_SECONDS"
)
OAUTH_ACCESS_TOKEN_EXPIRY_ENV = "WORKSPACE_MCP_OAUTH_PROXY_ACCESS_TOKEN_EXPIRY_SECONDS"
OAUTH_REFRESH_TOKEN_EXPIRY_ENV = (
    "WORKSPACE_MCP_OAUTH_PROXY_REFRESH_TOKEN_EXPIRY_SECONDS"
)
OAUTH_REQUIRE_CONSENT_ENV = "WORKSPACE_MCP_OAUTH_PROXY_REQUIRE_CONSENT"
OAUTH_GOOGLE_PROMPT_ENV = "WORKSPACE_MCP_OAUTH_PROXY_GOOGLE_PROMPT"
MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS = 5 * 60
MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS = 30 * 24 * 60 * 60
MAX_OAUTH_REFRESH_TOKEN_EXPIRY_SECONDS = 365 * 24 * 60 * 60

_CONSENT_MODES: dict[str, bool | str] = {"always": True, "remember": "remember"}
_GOOGLE_PROMPT_VALUES = ("none", "consent", "select_account")


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
    fallback_refresh_token_expiry_seconds = _parse_expiry_seconds_env(
        OAUTH_REFRESH_TOKEN_EXPIRY_ENV,
        minimum=1,
        maximum=MAX_OAUTH_REFRESH_TOKEN_EXPIRY_SECONDS,
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
    if fallback_refresh_token_expiry_seconds is not None:
        logger.info(
            "OAuth 2.1: issuing FastMCP refresh tokens with a %ds lifetime",
            fallback_refresh_token_expiry_seconds,
        )
        expiry_kwargs["fallback_refresh_token_expiry_seconds"] = (
            fallback_refresh_token_expiry_seconds
        )
    return expiry_kwargs


def get_oauth_proxy_consent_kwargs() -> dict[str, bool | str]:
    """Return GoogleProvider's consent-screen kwarg, empty when unset."""
    raw = os.getenv(OAUTH_REQUIRE_CONSENT_ENV, "").strip().lower()
    if not raw:
        return {}
    if raw not in _CONSENT_MODES:
        raise ValueError(
            f"{OAUTH_REQUIRE_CONSENT_ENV} must be one of "
            f"{', '.join(_CONSENT_MODES)}, got {raw!r}"
        )
    return {"require_authorization_consent": _CONSENT_MODES[raw]}


def get_oauth_proxy_google_prompt_kwargs() -> dict[str, dict[str, str]]:
    """Return the Google ``prompt`` to send upstream, empty when unset.

    Unset keeps FastMCP's default, ``consent``. ``select_account consent``
    lets the user pick the Google account instead of reusing the browser's
    signed-in one, and still returns a refresh token.
    """
    raw = os.getenv(OAUTH_GOOGLE_PROMPT_ENV, "").strip().lower()
    if not raw:
        return {}
    values = raw.split()
    unknown = [v for v in values if v not in _GOOGLE_PROMPT_VALUES]
    if unknown or ("none" in values and len(values) > 1):
        raise ValueError(
            f"{OAUTH_GOOGLE_PROMPT_ENV} must be 'none' or a space-separated "
            f"combination of 'consent' and 'select_account', got {raw!r}"
        )
    if "consent" not in values:
        logger.warning(
            "%s=%r omits 'consent': Google returns no refresh token on a repeat "
            "authorization, so sessions end when the access token expires",
            OAUTH_GOOGLE_PROMPT_ENV,
            raw,
        )
    prompt = " ".join(dict.fromkeys(values))
    logger.info("OAuth 2.1: sending prompt=%r to Google", prompt)
    return {"extra_authorize_params": {"prompt": prompt}}
