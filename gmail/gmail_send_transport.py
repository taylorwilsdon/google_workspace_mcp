"""SMTP submission transport for Gmail using XOAUTH2 authentication."""

import asyncio
import base64
import logging
import smtplib
import ssl
from email.utils import getaddresses, make_msgid, parseaddr

from auth.credential_store import get_credential_store
from auth.scopes import (
    GMAIL_READONLY_SCOPE,
    MAIL_GOOGLE_COM_SCOPE,
    has_required_scopes,
)
from core.config import get_send_transport
from core.utils import GOOGLE_API_WRITE_RETRIES

logger = logging.getLogger(__name__)

_SMTP_MISSING_SCOPE_NOTE = (
    " Note: SMTP transport requested but the 'https://mail.google.com/' scope is"
    " missing; sent via the Gmail API instead. Re-authenticate to enable SMTP."
)


async def send_via_smtp(
    sender: str,
    envelope_recipients: list[str],
    raw_bytes: bytes,
    user_email: str,
    access_token: str,
) -> str:
    """Submit *raw_bytes* (a complete RFC 822 message) via smtp.gmail.com:587.

    Authentication uses the XOAUTH2 mechanism.  SMTP/auth errors propagate to
    the caller unchanged — no swallowing, no fallback.

    Returns the decoded AUTH response string from the server.
    """

    def _send() -> str:
        auth_string = f"user={user_email}\x01auth=Bearer {access_token}\x01\x01"
        b64 = base64.b64encode(auth_string.encode("ascii")).decode("ascii")

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            # Explicit verifying context: starttls() without one uses a
            # non-verifying stdlib context on modern Python, which would expose
            # the XOAUTH2 bearer token to a MITM.
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            code, response = smtp.docmd("AUTH", "XOAUTH2 " + b64)
            if code == 334:
                # Gmail returned a SASL challenge (for XOAUTH2 this carries a
                # base64 error payload). Send the required empty continuation to
                # finish the exchange and read the final status, so the context
                # manager's QUIT doesn't raise and mask the real auth failure.
                code, response = smtp.docmd("")
            if code != 235:
                raise smtplib.SMTPAuthenticationError(code, response)
            smtp.sendmail(sender, envelope_recipients, raw_bytes)
            return response.decode() if isinstance(response, bytes) else str(response)

    return await asyncio.to_thread(_send)


def resolve_effective_transport(
    user_google_email: str,
) -> tuple[str, object | None, str]:
    """Resolve which transport will be used for sending.

    Returns ``(effective, creds, fallback_note)`` where *effective* is
    ``"api"`` or ``"smtp"``.

    Decision logic:
    - If the configured transport is not ``"smtp"``: ``("api", None, "")``.
    - If SMTP is requested and the credential holds ``MAIL_GOOGLE_COM_SCOPE``:
      ``("smtp", creds, "")``.
    - Otherwise (SMTP requested but scope absent): ``("api", creds, <note>)``.
    """
    requested = get_send_transport()
    if requested != "smtp":
        return ("api", None, "")

    creds = get_credential_store().get_credential(user_google_email)
    if creds and has_required_scopes(creds.scopes, [MAIL_GOOGLE_COM_SCOPE]):
        return ("smtp", creds, "")

    return ("api", creds, _SMTP_MISSING_SCOPE_NOTE)


async def dispatch_transmit(
    service,
    *,
    effective: str,
    creds,
    fallback_note: str,
    raw_message_b64: str,
    thread_id_final: str | None,
    sender: str,
    to: list[str] | None,
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    user_google_email: str,
    action_label: str,
    attachment_info: str,
    trailing_note: str,
) -> str:
    """Transmit a pre-encoded RFC 822 message via the resolved transport.

    *raw_message_b64* is a URL-safe base64-encoded byte string of the full
    MIME message.  Returns a human-readable result string.
    """
    if effective == "smtp":
        result = await _dispatch_smtp(
            service=service,
            creds=creds,
            raw_message_b64=raw_message_b64,
            sender=sender,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            user_google_email=user_google_email,
            action_label=action_label,
            attachment_info=attachment_info,
            trailing_note=trailing_note,
        )
        return result

    # API path
    send_body: dict = {"raw": raw_message_b64}
    if thread_id_final:
        send_body["threadId"] = thread_id_final

    sent = await asyncio.to_thread(
        service.users().messages().send(userId="me", body=send_body).execute,
        num_retries=GOOGLE_API_WRITE_RETRIES,
    )
    mid = sent.get("id")
    return f"{action_label}{attachment_info}! Message ID: {mid}{fallback_note}{trailing_note}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_addresses(header_values: list[str | None]) -> list[str]:
    """Parse RFC 2822 address lists, return deduplicated bare email addresses."""
    combined = ", ".join(v for v in header_values if v)
    pairs = getaddresses([combined])
    seen: set[str] = set()
    result: list[str] = []
    for _, addr in pairs:
        addr = addr.strip()
        if addr and addr not in seen:
            seen.add(addr)
            result.append(addr)
    return result


def _inject_message_id(raw_bytes: bytes, sender: str) -> tuple[bytes, str]:
    """Inject a unique ``Message-ID`` header into *raw_bytes* if not already present.

    Performs string surgery on the header block (no re-parse/re-serialize) so
    that no other bytes are altered.  Inserts immediately after the
    ``MIME-Version: 1.0`` line when present; otherwise after the first header
    line.

    Returns ``(modified_bytes, msgid)`` where *msgid* is the authored id string
    (e.g. ``<...@example.com>``).  If a ``Message-ID`` / ``Message-Id`` header
    already exists the original bytes are returned unchanged and the existing id
    is extracted and returned.
    """
    try:
        text = raw_bytes.decode("ascii", errors="surrogateescape")
    except Exception:
        # Non-ASCII raw — fall back to a generated id, return bytes unchanged
        domain = sender.split("@", 1)[-1] if "@" in sender else "gmail.com"
        return raw_bytes, make_msgid(domain=domain)

    # Split the header block from the body — only headers may carry a
    # Message-ID. Scanning the body too could falsely match a quoted/forwarded
    # "Message-ID:" line and short-circuit injection (feeding body text to the
    # rfc822msgid lookup).
    header_text, sep, body_text = text.partition("\r\n\r\n")
    header_lines = header_text.split("\r\n")

    # Check for an existing Message-ID (case-insensitive) in the headers only.
    for line in header_lines:
        if line.lower().startswith("message-id:"):
            existing = line.split(":", 1)[1].strip()
            return raw_bytes, existing

    # Generate a unique id using the sender's domain.
    domain = sender.split("@", 1)[-1] if "@" in sender else "gmail.com"
    msgid = make_msgid(domain=domain)

    # Insert after "MIME-Version:" if present, else after the first header line.
    insert_after = 0
    for i, line in enumerate(header_lines):
        if line.lower().startswith("mime-version:"):
            insert_after = i
            break

    header_lines.insert(insert_after + 1, f"Message-ID: {msgid}")
    modified = "\r\n".join(header_lines) + (sep + body_text if sep else "")
    try:
        modified_bytes = modified.encode("ascii", errors="surrogateescape")
    except Exception:
        return raw_bytes, msgid

    return modified_bytes, msgid


async def _dispatch_smtp(
    *,
    service,
    creds,
    raw_message_b64: str,
    sender: str,
    to: list[str] | None,
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    user_google_email: str,
    action_label: str,
    attachment_info: str,
    trailing_note: str,
) -> str:
    from google.auth.transport.requests import Request  # local import to avoid cycles

    # Ensure credentials are fresh.
    if creds.expired:
        await asyncio.to_thread(creds.refresh, Request())

    raw_bytes = base64.urlsafe_b64decode(raw_message_b64)

    # Normalize a possible "Display Name <addr>" sender to the bare address so it
    # is a valid SMTP envelope sender and yields a clean Message-ID domain split.
    sender = parseaddr(sender)[1] or sender

    # Inject a unique Message-ID so we can look up the message after send.
    raw_bytes, msgid = _inject_message_id(raw_bytes, sender)

    envelope_recipients = _bare_addresses(
        [
            *(to or []),
            *(cc or []),
            *(bcc or []),
        ]
    )

    resp = await send_via_smtp(
        sender,
        envelope_recipients,
        raw_bytes,
        user_google_email,
        creds.token,
    )

    # Best-effort message-id lookup.
    mid_suffix = await _lookup_message_id(
        service=service,
        creds=creds,
        msgid=msgid,
        action_label=action_label,
        resp=resp,
    )
    return f"{action_label}{attachment_info} via SMTP! (queued: {resp}) {mid_suffix}{trailing_note}"


async def _lookup_message_id(
    *,
    service,
    creds,
    msgid: str,
    action_label: str,
    resp: str,
) -> str:
    """Return the message-id fragment to append; never raises."""
    try:
        # Defensive only: via resolve_effective_transport an SMTP send always
        # holds MAIL_GOOGLE_COM_SCOPE, which implies GMAIL_READONLY_SCOPE in the
        # scope hierarchy, so this branch is unreachable on the normal path. It
        # guards direct dispatch_transmit callers with narrow-scope creds.
        if not has_required_scopes(creds.scopes, [GMAIL_READONLY_SCOPE]):
            missing = [GMAIL_READONLY_SCOPE]
            missing_str = ", ".join(missing)
            return (
                f"(could not find message id via api (best effort) due to missing"
                f" scope(s): {missing_str}; to enable, re-authenticate with these"
                f" scope(s))"
            )

        # Scope present — look up by the authored Message-ID. Gmail's
        # rfc822msgid: search expects the bare id; make_msgid() and header
        # values carry angle brackets, which would make the lookup miss.
        bare_msgid = msgid.strip().strip("<>")
        q = f"rfc822msgid:{bare_msgid}"
        res = await asyncio.to_thread(
            service.users().messages().list(userId="me", q=q, maxResults=1).execute
        )
        messages = res.get("messages", [])
        if messages:
            mid = messages[0].get("id")
            return f"Message ID: {mid}"
        return "(could not find message id via api — best effort)"
    except Exception:
        logger.debug("Best-effort message-id lookup failed", exc_info=True)
        return "(could not find message id via api — best effort)"
