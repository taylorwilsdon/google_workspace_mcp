"""Principal resolution for the active request.

The *principal* is the Google account a request is allowed to act as. This module
is the single place that decides it, and it deliberately never consults tool
arguments: a caller-supplied ``user_google_email`` is only ever *checked against*
a principal that was established by the transport, never used to establish one.

Two rules hold everywhere:

* **Transport decides.** OAuth 2.1 access tokens and trusted-gateway assertions are
  verified identity. stdio has no per-request identity at all, so its principal
  comes from server configuration (``USER_GOOGLE_EMAIL``) or from the sole stored
  session -- both server-side state.
* **Fail closed.** When no principal can be established, callers raise. There is no
  path that falls back to "whatever the caller asked for", because that is exactly
  the impersonation primitive the audit flagged (findings 8, 21, 22, 35-37, 43).
"""

import logging
import os
from typing import Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


class PrincipalMismatchError(Exception):
    """A caller asked to act as an account other than the request's principal."""


class _SessionStore(Protocol):
    """The subset of the OAuth 2.1 session store this module reads."""

    def has_session(self, user_email: str) -> bool: ...

    def get_single_user_email(self) -> Optional[str]: ...


def get_configured_user_email() -> Optional[str]:
    """Return the server-configured single-user email, if any.

    Reads the live environment first so tests and runtime reconfiguration are
    honoured, then falls back to the value captured at import time.
    """
    env_value = os.getenv("USER_GOOGLE_EMAIL")
    if env_value:
        return env_value
    from core.config import USER_GOOGLE_EMAIL as _CAPTURED_USER_EMAIL

    return _CAPTURED_USER_EMAIL


def resolve_stdio_principal(
    store: _SessionStore,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the stdio principal from server-side state only.

    stdio carries no per-request credentials, so there is nothing to verify per
    call. The principal is therefore pinned to configuration:

    * ``USER_GOOGLE_EMAIL`` set -- that account and no other. If it has no stored
      session yet, no principal is resolved (the tool then drives the auth flow)
      rather than silently falling back to some other stored account.
    * ``USER_GOOGLE_EMAIL`` unset -- the sole stored session, when exactly one
      exists. Two or more stored accounts is ambiguous, so nothing is resolved.

    Returns ``(email, via)`` or ``(None, None)``.
    """
    configured = get_configured_user_email()
    if configured:
        if store.has_session(configured):
            return configured, "stdio_configured_user"
        logger.debug(
            "stdio principal %s is configured but has no stored session yet",
            configured,
        )
        return None, None

    single_user = store.get_single_user_email()
    if single_user:
        return single_user, "stdio_single_session"
    return None, None


def normalize_email(value: Optional[str]) -> str:
    """Canonicalise an address for comparison.

    Google treats the domain as case-insensitive and normalises the local part for
    Workspace accounts, so a case-only difference is the same account. Comparisons in
    the auth path go through here so they cannot disagree with each other about whether
    two spellings are one account.
    """
    return (value or "").strip().lower()


def emails_match(left: Optional[str], right: Optional[str]) -> bool:
    """True when both values name the same account. Empty never matches."""
    normalized_left = normalize_email(left)
    return bool(normalized_left) and normalized_left == normalize_email(right)


def assert_matches_principal(
    requested_email: Optional[str],
    principal_email: str,
    *,
    context: str,
) -> None:
    """Raise unless ``requested_email`` is absent or names the same account.

    Comparison is case-insensitive; see :func:`normalize_email`.
    """
    if not requested_email:
        return
    if emails_match(requested_email, principal_email):
        return
    logger.error(
        "Rejected cross-account request in %s: caller asked for %s but principal is %s",
        context,
        requested_email,
        principal_email,
    )
    raise PrincipalMismatchError(
        f"Requested account {requested_email} does not match the authenticated "
        f"account {principal_email}. You may only act as your own account."
    )
