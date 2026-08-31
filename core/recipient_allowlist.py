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
  forms accepted) must match an entry in the comma-separated list. An entry is
  either an exact address (``mum@example.com``) or a **domain wildcard**
  (``*@example.com``, or the equivalent shorthand ``@example.com``) allowing
  every address at that domain. The two forms mix freely, so a deployment can
  trust its own workspace domain while still naming individual outsiders:
  ``WORKSPACE_ALLOWED_RECIPIENTS=*@mycompany.com,accountant@example.org``.
  Domain matching is exact — ``*@example.com`` does **not** cover
  ``user@mail.example.com``; list the subdomain separately if you want it.

Public link sharing ("anyone with the link") is governed separately by
``WORKSPACE_ALLOW_PUBLIC_SHARING`` (default: blocked whenever the recipient
allowlist is active) because a public link is, by definition, an unlisted
recipient.
"""

import logging
import os
from email.utils import getaddresses
from typing import Iterable, List, NamedTuple, Optional, Set, Union

logger = logging.getLogger(__name__)

ALLOWLIST_ENV = "WORKSPACE_ALLOWED_RECIPIENTS"
PUBLIC_SHARING_ENV = "WORKSPACE_ALLOW_PUBLIC_SHARING"
_WILDCARD = "*"
_DOMAIN_WILDCARD_PREFIX = "*@"


class RecipientNotAllowedError(ValueError):
    """Raised when an outbound operation targets a non-allowlisted recipient."""


def allowlist_active() -> bool:
    """True when the deployment has opted into recipient enforcement.

    The check module is always imported, but enforcement only engages when the
    env var is present (even empty — empty means "block all").
    """
    return ALLOWLIST_ENV in os.environ


class _AllowSpec(NamedTuple):
    """Parsed allowlist: exact addresses plus whole-domain wildcards."""

    exact: Set[str]
    domains: Set[str]


def _allowed() -> Optional[_AllowSpec]:
    """Return the parsed allow spec, ``None`` for wildcard/inactive.

    Entries are either exact addresses or domain wildcards (``*@example.com``,
    shorthand ``@example.com``). An entry with no ``@`` at all cannot match any
    address, so it is kept in ``exact`` and simply never matches — a typo fails
    closed rather than silently widening the policy.
    """
    if not allowlist_active():
        return None
    raw = os.environ.get(ALLOWLIST_ENV, "")
    if raw.strip() == _WILDCARD:
        return None
    exact: Set[str] = set()
    domains: Set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip().lower()
        if not entry:
            continue
        if entry.startswith(_DOMAIN_WILDCARD_PREFIX):
            domain = entry[len(_DOMAIN_WILDCARD_PREFIX) :]
        elif entry.startswith("@"):
            domain = entry[1:]
        else:
            exact.add(entry)
            continue
        if domain:
            domains.add(domain)
    return _AllowSpec(exact=exact, domains=domains)


def _matches(address: str, spec: _AllowSpec) -> bool:
    """True when *address* is listed exactly or covered by a domain wildcard."""
    if address in spec.exact:
        return True
    _, _, domain = address.rpartition("@")
    return bool(domain) and domain in spec.domains


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
    A recipient matches either an exact entry or a ``*@domain`` wildcard.

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

    rejected = sorted({r for r in recipients if not _matches(r, allowed)})
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


RESOURCE_CALENDAR_SUFFIX = "@resource.calendar.google.com"


def enforce_event_attendees(
    attendees: Optional[Iterable[Union[str, dict, None]]],
    operation: str,
    *,
    self_email: Optional[str] = None,
) -> None:
    """Apply :func:`enforce_recipients` to a Calendar attendee list.

    Two kinds of entry are not third parties and are exempt — judged by
    *address only*, never by flags on the record: the authenticated account
    itself (``self_email``, taken by the caller from the verified request
    context) and Google room/resource calendars (addresses ending in
    ``@resource.calendar.google.com``). ``self``/``resource`` keys on attendee
    dicts are deliberately ignored: on ``modify_event`` the attendee objects
    come from the caller, so honouring them would let
    ``{"email": "x@…", "self": true}`` bypass the policy.

    Call it on the *effective* attendee list of an event write — including
    attendees preserved from the existing event — because an update notifies
    everyone left on it.
    """
    own = (self_email or "").strip().lower()
    third_parties = [
        addr
        for addr in _extract_addresses(attendees or [])
        if addr != own and not addr.endswith(RESOURCE_CALENDAR_SUFFIX)
    ]
    enforce_recipients(third_parties, operation)


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
    and go through :func:`enforce_recipients`; ``domain``/``anyone`` grants
    (single or batch, judged by each entry's ``share_type``) expose the file to
    unlisted recipients and fall under :func:`enforce_public_sharing`. ``update`` and ``revoke`` act on
    existing grants only and are never gated. No-op unless the allowlist is
    active.

    Note a deliberate asymmetry: a Drive ``domain`` grant stays under the
    public-sharing gate even when that domain is allowlisted for email.
    Allowing mail to ``*@example.com`` says "these people may be written to";
    exposing a file to an entire domain is a different act, so it keeps
    requiring ``WORKSPACE_ALLOW_PUBLIC_SHARING``.
    """
    operation = "manage_drive_access"
    if action == "grant":
        if share_type in ("user", "group"):
            enforce_recipients([share_with], operation)
        else:
            enforce_public_sharing(operation)
    elif action == "grant_batch":
        for recipient in recipients or []:
            # Route by the entry's own share_type (default "user"), exactly as
            # the tool does: a listed email does not make an "anyone"/"domain"
            # grant private.
            if recipient.get("share_type", "user") in ("user", "group"):
                enforce_recipients([recipient], operation)
            else:
                enforce_public_sharing(operation)
    elif action == "transfer_owner":
        enforce_recipients([new_owner_email], operation)
