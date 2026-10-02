"""Resolve caller-visible Google account identity for legacy auth modes.

Credential lookup remains strict in ``auth.google_auth``. This module only
provides the server/decorator layer with an account identity that it can safely
advertise or inject before a tool call reaches credential lookup.
"""

from dataclasses import dataclass
import logging
import os
from typing import Optional

from auth.credential_store import get_credential_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LegacyAccountIdentity:
    """Account identity visible to legacy OAuth 2.0 tool calls."""

    single_user: bool
    configured_email: Optional[str]
    stored_users: tuple[str, ...]

    @property
    def sole_stored_email(self) -> Optional[str]:
        """Return the sole stored account when single-user mode makes it unambiguous."""
        if self.single_user and len(self.stored_users) == 1:
            return self.stored_users[0]
        return None

    @property
    def default_email(self) -> Optional[str]:
        """Return an explicit configured account, or the sole stored account."""
        if self.configured_email:
            return self.configured_email
        return self.sole_stored_email

    @property
    def default_is_sole_stored(self) -> bool:
        """True when the default was inferred rather than explicitly configured."""
        return bool(self.sole_stored_email and not self.configured_email)

    def canonical_stored_email(self, requested_email: str) -> Optional[str]:
        """Return the stored spelling for a case-insensitive account match."""
        requested = requested_email.strip().casefold()
        matches = [user for user in self.stored_users if user.casefold() == requested]
        return matches[0] if len(matches) == 1 else None


def _normalize_email(value: Optional[str]) -> Optional[str]:
    """Trim an optional email value and collapse blank strings to ``None``."""
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def get_legacy_account_identity() -> LegacyAccountIdentity:
    """Resolve configured and locally stored identity for legacy auth.

    Stored accounts are enumerated only in ``--single-user`` mode. The local
    credential-store backend supports this operation; unsupported or unhealthy
    stores fail soft here so tool discovery remains available and the existing
    authentication path can report the underlying problem when invoked.
    """
    single_user = os.getenv("MCP_SINGLE_USER_MODE") == "1"
    configured_email = _normalize_email(os.getenv("USER_GOOGLE_EMAIL"))
    stored_users: tuple[str, ...] = ()

    if single_user:
        try:
            stored_users = tuple(get_credential_store().list_users())
        except Exception as exc:
            logger.warning(
                "Unable to enumerate single-user credential accounts: %s", exc
            )

    if configured_email:
        requested = configured_email.casefold()
        matches = [user for user in stored_users if user.casefold() == requested]
        if len(matches) == 1:
            configured_email = matches[0]

    return LegacyAccountIdentity(
        single_user=single_user,
        configured_email=configured_email,
        stored_users=stored_users,
    )
