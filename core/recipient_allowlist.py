"""Outbound recipient allowlist — code-enforced guardrail for agent deployments.

When ``WORKSPACE_ALLOWED_RECIPIENTS`` is set (comma-separated email addresses),
every operation that can put content in front of a third party — sending or
forwarding Gmail, creating auto-forward filters, inviting Calendar attendees,
granting Drive access or enabling public link sharing — is checked against it
in code, so no prompt phrasing can bypass
the policy. This exists for unattended/agent deployments where the model, not
a human, drives the tools.

Semantics:
- Unset → inactive. No checks run; nothing changes for existing deployments.
- Set but empty → **fail closed**: every recipient-bearing operation is refused.
- ``WORKSPACE_ALLOWED_RECIPIENTS=*`` → explicitly unrestricted.
- Otherwise → each recipient's bare address (case-insensitive; ``Name <a@b>``
  forms accepted) must appear in the comma-separated list.

Public link sharing ("anyone with the link") is governed separately by
``WORKSPACE_ALLOW_PUBLIC_SHARING`` (default: blocked whenever the recipient
allowlist is active) because a public link is, by definition, an unlisted
recipient.
"""

import logging
import os
from email.utils import getaddresses
from typing import Iterable, List, Optional, Set, Union

logger = logging.getLogger(__name__)

ALLOWLIST_ENV = "WORKSPACE_ALLOWED_RECIPIENTS"
PUBLIC_SHARING_ENV = "WORKSPACE_ALLOW_PUBLIC_SHARING"
_WILDCARD = "*"


class RecipientNotAllowedError(ValueError):
    """Raised when an outbound operation targets a non-allowlisted recipient."""


def allowlist_active() -> bool:
    """True when the deployment has opted into recipient enforcement.

    The check module is always imported, but enforcement only engages when the
    env var is present (even empty — empty means "block all").
    """
    return ALLOWLIST_ENV in os.environ


def _allowed() -> Optional[Set[str]]:
    """Return the normalized allow set, ``None`` for wildcard/inactive."""
    if not allowlist_active():
        return None
    raw = os.environ.get(ALLOWLIST_ENV, "")
    if raw.strip() == _WILDCARD:
        return None
    return {addr.strip().lower() for addr in raw.split(",") if addr.strip()}


def _extract_addresses(values: Iterable[Union[str, dict, None]]) -> List[str]:
    """Flatten recipient inputs to bare lowercase addresses.

    Accepts strings (possibly comma-separated, possibly ``Name <addr>`` form)
    and calendar-style dicts with an ``email`` key. ``None`` entries are
    skipped.
    """
    flat: List[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            email = str(value.get("email", "")).strip()
            if email:
                flat.append(email.lower())
            continue
        for _name, addr in getaddresses([str(value)]):
            addr = addr.strip().lower()
            if addr:
                flat.append(addr)
    return flat


def enforce_recipients(
    values: Iterable[Union[str, dict, None]],
    operation: str,
) -> None:
    """Refuse *operation* unless every recipient in *values* is allowlisted.

    When the env var is absent the deployment has not opted in and this is a
    no-op. When it IS set — even to an empty string — enforcement is
    fail-closed: only listed addresses pass, and an empty list blocks all.

    Raises:
        RecipientNotAllowedError: naming the offending addresses.
    """
    allowed = _allowed()
    if allowed is None:
        if allowlist_active():
            # Wildcard: explicitly unrestricted.
            return
        return  # feature not enabled for this deployment

    recipients = _extract_addresses(values)
    if not recipients:
        return  # nothing outbound (e.g. event without attendees)

    rejected = sorted({r for r in recipients if r not in allowed})
    if rejected:
        logger.warning(
            "Blocked %s: recipient(s) not in %s: %s",
            operation,
            ALLOWLIST_ENV,
            ", ".join(rejected),
        )
        raise RecipientNotAllowedError(
            f"{operation} refused: recipient(s) not in the configured allowlist "
            f"({ALLOWLIST_ENV}): {', '.join(rejected)}. "
            f"Ask the operator to add them if this is intended."
        )


def enforce_public_sharing(operation: str) -> None:
    """Refuse public/anyone-with-the-link sharing unless explicitly allowed.

    Engages only when the recipient allowlist is active; a public link is an
    unlisted recipient. Override with ``WORKSPACE_ALLOW_PUBLIC_SHARING=true``.
    """
    if not allowlist_active():
        return
    if os.environ.get(PUBLIC_SHARING_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    logger.warning("Blocked %s: public link sharing is disabled by policy", operation)
    raise RecipientNotAllowedError(
        f"{operation} refused: public ('anyone with the link') sharing is "
        f"disabled while {ALLOWLIST_ENV} is active. Set "
        f"{PUBLIC_SHARING_ENV}=true to permit it."
    )


def enforce_drive_access(
    action: str,
    share_type: Optional[str] = None,
    share_with: Optional[str] = None,
    recipients: Optional[Iterable[dict]] = None,
    new_owner_email: Optional[str] = None,
) -> None:
    """Apply the outbound policy to a ``manage_drive_access`` call.

    Grants to user/group addresses and ownership transfers are recipient-bearing
    and go through :func:`enforce_recipients`; ``domain``/``anyone`` grants (and
    batch entries without an email) expose the file to unlisted recipients and
    fall under :func:`enforce_public_sharing`. ``update`` and ``revoke`` act on
    existing grants only and are never gated. No-op unless the allowlist is
    active.
    """
    operation = "manage_drive_access"
    if action == "grant":
        if share_type in ("user", "group"):
            enforce_recipients([share_with], operation)
        else:
            enforce_public_sharing(operation)
    elif action == "grant_batch":
        for recipient in recipients or []:
            if recipient.get("domain") or not recipient.get("email"):
                enforce_public_sharing(operation)
            else:
                enforce_recipients([recipient], operation)
    elif action == "transfer_owner":
        enforce_recipients([new_owner_email], operation)
