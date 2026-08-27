"""Email helper utilities for Gmail tools."""

from __future__ import annotations

import asyncio
import html
import logging
import re
import ssl
from collections import Counter
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional

from fastmcp.exceptions import ToolError as ToolExecutionError
from googleapiclient.errors import HttpError

from core.utils import GOOGLE_API_WRITE_RETRIES, UserInputError

logger = logging.getLogger(__name__)

RAW_BODY_TRUNCATE_LIMIT = 20000
GMAIL_QUOTA_ERROR_MARKERS = (
    "dailyLimitExceeded",
    "quotaExceeded",
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "usageLimits",
    "quota",
    "rate limit",
)

GMAIL_METADATA_HEADERS = [
    "Subject",
    "From",
    "To",
    "Cc",
    "Reply-To",
    "Message-ID",
    "In-Reply-To",
    "References",
    "Date",
    "List-Unsubscribe",
    "Precedence",
    "List-Id",
]


async def _delete_gmail_draft_by_identifier(service: Any, draft_identifier: str) -> str:
    """Resolve and permanently delete exactly one Gmail draft.

    ``draft_identifier`` may be either a Draft resource ID or the contained
    Gmail Message ID. All draft pages are scanned so identifiers from older
    search results remain usable. Resolution refuses ambiguous identifiers
    rather than risking deletion of the wrong draft.
    """
    matching_draft_ids = set()
    page_token = None

    while True:
        request_params = {"userId": "me", "maxResults": 500}
        if page_token:
            request_params["pageToken"] = page_token
        response = await asyncio.to_thread(
            service.users().drafts().list(**request_params).execute
        )
        matching_draft_ids.update(
            draft.get("id")
            for draft in response.get("drafts") or []
            if isinstance(draft, dict)
            and draft.get("id")
            and (
                draft.get("id") == draft_identifier
                or (draft.get("message") or {}).get("id") == draft_identifier
            )
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if not matching_draft_ids:
        raise UserInputError(
            f"No Gmail draft found for identifier '{draft_identifier}'."
        )
    if len(matching_draft_ids) > 1:
        raise UserInputError(
            f"Identifier '{draft_identifier}' matches multiple Gmail drafts; "
            "no draft was deleted."
        )

    draft_id = matching_draft_ids.pop()
    await asyncio.to_thread(
        service.users().drafts().delete(userId="me", id=draft_id).execute,
        num_retries=GOOGLE_API_WRITE_RETRIES,
    )
    return f"Draft permanently deleted! Draft ID: {draft_id}"


def _normalize_email(address: str) -> str:
    """Lowercase an email address and strip plus-addressing so that
    e.g. 'Alex <alex+foo@scopestack.io>' normalizes to 'alex@scopestack.io'.

    This is the key primitive for 'is this message from Alex?' checks - plus
    addresses are Alex, not a third party.
    """
    _name, addr = parseaddr(address or "")
    addr = addr.lower().strip()
    if not addr or "@" not in addr:
        return addr
    local, _, domain = addr.partition("@")
    local = local.split("+", 1)[0]
    return f"{local}@{domain}"


def _http_error_status(error: HttpError) -> Optional[int]:
    status = getattr(getattr(error, "resp", None), "status", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _is_quota_or_rate_limit_error(error: HttpError) -> bool:
    details = str(error).lower()
    content = getattr(error, "content", None)
    if isinstance(content, bytes):
        details = f"{details} {content.decode('utf-8', errors='ignore').lower()}"
    elif content:
        details = f"{details} {str(content).lower()}"
    return any(marker.lower() in details for marker in GMAIL_QUOTA_ERROR_MARKERS)


def _is_benign_signature_http_error(error: HttpError) -> bool:
    status = _http_error_status(error)
    return status == 401 or (status == 403 and not _is_quota_or_rate_limit_error(error))


def _signature_fetch_tool_error(error: Exception) -> ToolExecutionError:
    return ToolExecutionError(f"Failed to fetch Gmail send-as signatures: {error}")


def _is_retryable_error(error: Any) -> bool:
    """Whether an error is transient and safe to retry.

    Covers SSL errors, HTTP 429 (rate limit), 5xx, and quota/rate-limit markers
    in the error body. Reads are idempotent so retrying these is safe.
    """
    if isinstance(error, ssl.SSLError):
        return True
    if isinstance(error, HttpError):
        status = _http_error_status(error)
        if status is not None and (status == 429 or status >= 500):
            return True
        return _is_quota_or_rate_limit_error(error)
    return False


async def _fetch_with_retry(
    build_request: Callable[[], Any],
    item_id: str,
    item_label: str,
    log_prefix: str,
    max_retries: int = 3,
) -> tuple[str, Optional[dict], Optional[Exception]]:
    """Execute a Gmail read request, retrying transient failures.

    `build_request` is re-invoked per attempt so each retry gets a fresh
    request object. Backs off exponentially (1s, 2s, 4s) on retryable errors;
    anything else is returned to the caller immediately. Returns
    `(item_id, response, error)` with exactly one of response/error set.
    """
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            response = await asyncio.to_thread(build_request().execute)
            return item_id, response, None
        except Exception as error:
            last_error = error
            if not _is_retryable_error(error) or attempt == max_retries:
                break
            delay = 2**attempt
            logger.warning(
                f"[{log_prefix}] Retryable error for {item_label} {item_id} on "
                f"attempt {attempt + 1}: {error}. Retrying in {delay}s..."
            )
            await asyncio.sleep(delay)

    logger.error(f"[{log_prefix}] Failed to fetch {item_label} {item_id}: {last_error}")
    return item_id, None, last_error


def _retryable_result_ids(
    results: Mapping[str, Mapping[str, Any]], item_ids: Iterable[str]
) -> list[str]:
    """IDs whose batch sub-request failed with a transient, retryable error.

    The batch API reports per-sub-request errors inside an otherwise successful
    response, so these are invisible to any client-side retry and have to be
    re-fetched individually.
    """
    return [
        item_id
        for item_id in item_ids
        if _is_retryable_error(results.get(item_id, {}).get("error"))
    ]


def _parse_date_header(
    date_str: str, internal_date_ms: str | int | None
) -> tuple[Optional[str], Optional[datetime]]:
    """Parse Gmail internalDate or a Date header to a UTC-aware datetime.

    Prefer Gmail's internalDate because it reflects Gmail's message ordering;
    fall back to the Date header when internalDate is unavailable or malformed.
    Always returns UTC-aware datetimes so naive/aware comparisons don't raise
    TypeError.

    Returns (iso_string, datetime) or (None, None) if both sources fail.
    """
    if internal_date_ms is not None:
        try:
            ms = int(internal_date_ms)
            dt = datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
            return dt.isoformat(), dt
        except (TypeError, ValueError) as e:
            logger.debug(
                "Could not convert internalDate %r to timestamp; falling back to "
                "Date header: %s",
                internal_date_ms,
                e,
            )

    if date_str:
        try:
            dt = parsedate_to_datetime(date_str)
            # Normalize to UTC (parsedate_to_datetime may return naive or offset-aware).
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat(), dt
        except (TypeError, ValueError) as e:
            logger.debug(
                "Could not parse Date header %r: %s",
                date_str,
                e,
            )

    return None, None


def _parse_message_id_chain(header_value: Optional[str]) -> list[str]:
    """Extract RFC Message-IDs from a reply header value."""
    if not header_value:
        return []

    message_ids = re.findall(r"<[^>]+>", header_value)
    return message_ids or header_value.split()


def _derive_reply_headers(
    thread_message_ids: list[str],
    in_reply_to: Optional[str],
    references: Optional[str],
    target: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Fill reply headers, defaulting to the latest automatically eligible message.

    Automatic targets are non-draft, non-trash messages with an RFC Message-ID.
    """
    derived_in_reply_to = in_reply_to
    derived_references = references

    if not thread_message_ids and not target:
        return derived_in_reply_to, derived_references

    target_message_id = target.get("message_id") if target else None
    if not derived_in_reply_to:
        # References describe ancestry; only In-Reply-To explicitly selects an
        # older reply parent. Without one, rebuild both headers from the latest
        # eligible message in the thread.
        derived_in_reply_to = target_message_id or thread_message_ids[-1]
        derived_references = None

    if not derived_references:
        if target_message_id and derived_in_reply_to == target_message_id:
            reference_chain = _parse_message_id_chain(target.get("references"))
            if not reference_chain:
                parent_ids = _parse_message_id_chain(target.get("in_reply_to"))
                if len(parent_ids) == 1:
                    reference_chain = parent_ids
            if target_message_id not in reference_chain:
                reference_chain.append(target_message_id)
            derived_references = " ".join(reference_chain)
        elif derived_in_reply_to and derived_in_reply_to in thread_message_ids:
            reply_index = thread_message_ids.index(derived_in_reply_to)
            derived_references = " ".join(thread_message_ids[: reply_index + 1])
        elif derived_in_reply_to:
            derived_references = derived_in_reply_to
        else:
            derived_references = " ".join(thread_message_ids)

    return derived_in_reply_to, derived_references


def _derive_reply_all_recipients(
    target: Mapping[str, Any],
    exclude: set[str],
    to: Optional[str],
    cc: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Fill missing reply-all recipients from the message being answered.

    To = whoever sent it (Reply-To when set, else From). Cc = every other
    participant, minus `exclude` and minus anyone already in To. Caller-supplied
    values win, mirroring _derive_reply_headers.

    `exclude` applies to To as well as Cc, so replying to a message the account
    sent itself derives no recipient rather than addressing the account; the
    caller passes `to` explicitly for that case.

    Only the authenticated address and the selected Send As alias can be excluded
    reliably: a caller sending under several aliases still has to post-filter,
    which is why this is opt-in rather than applied to every threaded send.
    """
    excluded = {addr.lower() for addr in exclude if addr}
    sender = target.get("reply_to") or target.get("from") or ""

    derived_to = to
    if not derived_to:
        derived_to = ", ".join(
            addr
            for _name, addr in getaddresses([sender])
            if addr and addr.lower() not in excluded
        )

    derived_cc = cc
    if not derived_cc:
        seen = {addr.lower() for _n, addr in getaddresses([derived_to or ""]) if addr}
        seen |= excluded
        others = []
        # The sender leads the Cc candidates so an explicit `to` that redirects the
        # reply still keeps the person being replied to on it. When To was derived
        # from the sender they are already in `seen`, so this is a no-op there.
        for _name, addr in getaddresses(
            [sender, target.get("to", ""), target.get("cc", "")]
        ):
            if addr and addr.lower() not in seen:
                seen.add(addr.lower())
                others.append(addr)
        derived_cc = ", ".join(others) or None

    return derived_to or None, derived_cc


def _analyze_thread_ownership_impl(
    thread_response: dict,
    user_google_email: str,
) -> dict[str, Any]:
    """Pure analysis of a Gmail thread API response. Takes the response from
    users().threads().get(format='full') and returns structured ownership
    metadata. Kept separate from the @server.tool wrapper so tests can call
    it with fabricated thread data.
    """
    messages = thread_response.get("messages", []) or []
    thread_id = thread_response.get("id", "")

    if not messages:
        return {
            "thread_id": thread_id,
            "thread_subject": None,
            "last_sender": None,
            "last_timestamp": None,
            "ball_in_court_of": None,
            "message_count_by_sender": {},
            "participants": [],
            "excluded_drafts": 0,
            "message_count": 0,
        }

    normalized_user = _normalize_email(user_google_email)

    # Thread subject: first message's Subject header
    first_headers = {
        h["name"].lower(): h["value"]
        for h in messages[0].get("payload", {}).get("headers", [])
    }
    thread_subject = first_headers.get("subject") or None

    sender_counter: Counter[str] = Counter()
    participants: set[str] = set()
    non_draft_participants: set[str] = set()
    excluded_drafts = 0

    last_non_draft = None  # (datetime, message_dict, headers_dict)

    for message in messages:
        label_ids = message.get("labelIds", []) or []
        is_draft = "DRAFT" in label_ids

        raw_headers = message.get("payload", {}).get("headers", [])
        headers = {h["name"].lower(): h["value"] for h in raw_headers}

        from_addr = headers.get("from", "")
        _name, from_email = parseaddr(from_addr)
        from_norm = _normalize_email(from_email) if from_email else ""

        # Collect participants from From/To/Cc using getaddresses (RFC-correct
        # parsing of quoted display names with embedded commas). Read the raw
        # list so repeated fields are combined rather than silently overwritten.
        header_values = [
            h["value"]
            for h in raw_headers
            if h["name"].lower() in {"from", "to", "cc"} and h["value"]
        ]
        message_participants = set()
        for _n, addr in getaddresses([v for v in header_values if v]):
            norm = _normalize_email(addr) if addr else ""
            if norm and "@" in norm:
                participants.add(norm)
                message_participants.add(norm)

        if is_draft:
            excluded_drafts += 1
            continue

        non_draft_participants.update(message_participants)

        if from_norm and "@" in from_norm:
            sender_counter[from_norm] += 1

        _iso, dt = _parse_date_header(
            headers.get("date", ""), message.get("internalDate")
        )
        if dt is not None:
            if last_non_draft is None or dt >= last_non_draft[0]:
                last_non_draft = (dt, message, headers)

    if last_non_draft is None:
        # All messages were drafts - no sent state to reason about
        return {
            "thread_id": thread_id,
            "thread_subject": thread_subject,
            "last_sender": None,
            "last_timestamp": None,
            "ball_in_court_of": None,
            "message_count_by_sender": dict(sender_counter),
            "participants": sorted(participants),
            "excluded_drafts": excluded_drafts,
            "message_count": len(messages),
        }

    last_dt, _last_message, last_headers = last_non_draft
    last_sender_raw = last_headers.get("from", "")
    _n, last_sender_email = parseaddr(last_sender_raw)
    last_sender_norm = _normalize_email(last_sender_email) if last_sender_email else ""

    # Ball-in-court: "user" = user owes reply, "them" = other party owes reply,
    # None = unresolvable. Use non-draft participants, so outbound-only threads
    # still see the recipient while draft-only recipients are ignored.
    external_participants = (
        non_draft_participants - {normalized_user}
        if normalized_user
        else non_draft_participants
    )
    if not normalized_user or "@" not in normalized_user or "@" not in last_sender_norm:
        ball_in_court_of = None
    elif not external_participants:
        ball_in_court_of = None
    elif last_sender_norm == normalized_user:
        ball_in_court_of = "them"
    else:
        ball_in_court_of = "user"

    return {
        "thread_id": thread_id,
        "thread_subject": thread_subject,
        "last_sender": last_sender_raw or None,
        "last_timestamp": last_dt.isoformat(),
        "ball_in_court_of": ball_in_court_of,
        "message_count_by_sender": dict(sender_counter),
        "participants": sorted(participants),
        "excluded_drafts": excluded_drafts,
        "message_count": len(messages),
    }


def _build_forward_content(
    headers: dict[str, str],
    bodies: dict[str, str],
    forward_message: Optional[str],
    forward_message_format: Literal["plain", "html"],
    subject_override: Optional[str],
) -> tuple[str, str, Literal["plain", "html"]]:
    """Build the (subject, body, body_format) for a forwarded message.

    Pure formatting kept out of the @server.tool wrapper so it is independently
    testable: quotes the original headers/body and prepends an optional note.
    An explicit subject_override wins over the derived 'Fwd: <subject>'.
    """
    original_subject = headers.get("Subject", "(no subject)")
    original_from = headers.get("From", "(unknown sender)")
    original_date = headers.get("Date", "(unknown date)")
    original_to = headers.get("To", "")
    original_text = bodies.get("text", "")
    original_html = bodies.get("html", "")
    has_html = bool(original_html.strip())

    forward_header_text = (
        "---------- Forwarded message ---------\n"
        f"From: {original_from}\n"
        f"Date: {original_date}\n"
        f"Subject: {original_subject}\n"
        f"To: {original_to}"
    )
    # Header values are escaped; they render as text but may carry markup.
    forward_header_html = (
        '<div style="color: #777;">'
        "---------- Forwarded message ---------<br/>"
        f"From: {html.escape(original_from)}<br/>"
        f"Date: {html.escape(original_date)}<br/>"
        f"Subject: {html.escape(original_subject)}<br/>"
        f"To: {html.escape(original_to)}"
        "</div>"
    )

    # Render as HTML when the original is HTML or the note is explicitly HTML.
    if has_html or (forward_message and forward_message_format == "html"):
        note_html = ""
        if forward_message:
            if forward_message_format == "html":
                note_html = f"<div>{forward_message}</div><br/>"
            else:
                escaped = html.escape(forward_message).replace("\n", "<br/>")
                note_html = f"<div>{escaped}</div><br/>"
        original_body_html = (
            original_html
            if has_html
            else html.escape(original_text).replace("\n", "<br/>")
        )
        forward_body = (
            f"{note_html}"
            '<div style="border-left: 1px solid #ccc; padding-left: 10px; margin-left: 10px;">'
            f"{forward_header_html}<br/>{original_body_html}</div>"
        )
        body_format: Literal["plain", "html"] = "html"
    else:
        note = f"{forward_message}\n\n" if forward_message else ""
        forward_body = f"{note}{forward_header_text}\n\n{original_text}"
        body_format = "plain"

    # Derive the subject, avoiding a double prefix for existing "Fwd:"/"FW:".
    subject = subject_override or original_subject
    if not subject_override and not subject.lower().lstrip().startswith(
        ("fwd:", "fw:")
    ):
        subject = f"Fwd: {original_subject}"

    return subject, forward_body, body_format


class _HTMLBlockTextExtractor(HTMLParser):
    """Extract text from HTML, preserving block-level line breaks.

    _HTMLTextExtractor (in gmail_tools.py) is tuned for *reading* arbitrary
    message bodies, where flowing single-line text is preferred -- it inserts
    no separator at all between block elements (</div><div> etc.), and its
    get_text() collapses every whitespace run, including newlines, to a
    single space. That is the wrong shape whenever the output has to keep the
    author's structure: Gmail signatures are laid out line-by-line (name /
    title / phone / ...) using <br> or one <div>/<p> per line, and the
    text/plain alternative of an outgoing HTML message has to preserve
    paragraph breaks because it is what non-HTML clients actually display.
    Run through the general extractor, adjacent lines end up concatenated
    with zero whitespace between them (e.g. "-Erik" immediately followed by
    "Erik Holzhauer" with no break at all), which no amount of whitespace
    collapsing afterward can repair since there was never a separating
    character to begin with. This extractor treats block boundaries as
    real newlines and only collapses horizontal whitespace within a line.
    """

    # Elements whose boundaries are visible line breaks once the markup is
    # gone. Table cells are deliberately absent: a line per cell would wreck
    # table-based layouts, and the separator they do need is its own issue.
    _BLOCK_TAGS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "dd",
            "div",
            "dl",
            "dt",
            "fieldset",
            "figcaption",
            "figure",
            "footer",
            "form",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "tbody",
            "tfoot",
            "thead",
            "tr",
            "ul",
        }
    )

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def _append_line_break(self, *, force: bool = False) -> None:
        """Append a structural break, optionally preserving an empty line."""
        if not self._text:
            return
        if force or self._text[-1] != "\n":
            self._text.append("\n")

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
            return
        if self._skip:
            return
        if tag == "br":
            # Unlike a block boundary, consecutive <br> tags intentionally
            # represent empty lines.
            self._append_line_break(force=True)
        elif tag in self._BLOCK_TAGS:
            # A nested block starts a new visual line even when its parent
            # already contains text (for example, Name<div>Title</div>).
            self._append_line_break()

    def handle_endtag(self, tag):
        # Closing a block ends its visual line. _append_line_break avoids a
        # duplicate when the block already ended with a nested block or <br>.
        if tag in ("script", "style"):
            self._skip = False
        elif tag in self._BLOCK_TAGS and not self._skip:
            self._append_line_break()

    def handle_data(self, data):
        if not self._skip:
            # HTML collapses source whitespace. Convert it here so formatting
            # newlines in pretty-printed markup cannot become visible breaks;
            # only the structural delimiters above emit "\n".
            normalized = re.sub(r"\s+", " ", data)
            if normalized == " " and (not self._text or self._text[-1] == "\n"):
                return
            self._text.append(normalized)

    def get_text(self) -> str:
        raw = "".join(self._text)
        # Collapse horizontal whitespace within each line, but keep the
        # newlines that mark real line/paragraph breaks. Adjacent block
        # boundaries (e.g. </div><div>) produce back-to-back newlines,
        # which the re.sub below caps at one blank line.
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text_preserving_breaks(html_content: str) -> str:
    """Convert HTML to plain text, keeping block boundaries as line breaks.

    Falls back to the original markup if parsing fails, so a malformed body is
    still delivered as *something* rather than an empty part.
    """
    try:
        parser = _HTMLBlockTextExtractor()
        parser.feed(html_content)
        # feed() withholds any tail that could still turn out to be an
        # incomplete construct -- a trailing "&" or an unterminated entity.
        # close() flushes it; without this a body ending in "Tom &amp" comes
        # back empty rather than merely losing the entity.
        parser.close()
        return parser.get_text()
    except Exception:
        return html_content


def _signature_html_to_text(signature_html: str) -> str:
    """Convert Gmail signature HTML to plain text, preserving line breaks."""
    return html_to_text_preserving_breaks(signature_html)


async def _get_send_as_entries(service) -> List[Dict[str, Any]]:
    """Fetch the account's Gmail send-as settings."""
    try:
        response = await asyncio.to_thread(
            service.users().settings().sendAs().list(userId="me").execute
        )
    except HttpError as e:
        if _is_benign_signature_http_error(e):
            logger.info(
                "Skipping Gmail send-as lookup: missing auth/scope for settings endpoint."
            )
            return []
        logger.error(f"Failed to fetch Gmail send-as settings: {e}", exc_info=True)
        raise _signature_fetch_tool_error(e) from e
    except Exception as e:
        logger.error(f"Failed to fetch Gmail send-as settings: {e}", exc_info=True)
        raise _signature_fetch_tool_error(e) from e

    return response.get("sendAs", [])


def _find_send_as_entry(
    send_as_entries: List[Dict[str, Any]], from_email: str
) -> Optional[Dict[str, Any]]:
    """Find a send-as entry by email address, case-insensitively."""
    from_email_normalized = from_email.strip().lower()
    return next(
        (
            entry
            for entry in send_as_entries
            if entry.get("sendAsEmail", "").strip().lower() == from_email_normalized
        ),
        None,
    )


async def _get_send_as_signature_html(service, from_email: Optional[str] = None) -> str:
    """
    Fetch signature HTML from Gmail send-as settings.

    Returns empty string when the account has no signature configured or when
    auth/scope errors mean the settings endpoint is unavailable.
    """
    send_as_entries = await _get_send_as_entries(service)
    if not send_as_entries:
        return ""

    if from_email:
        entry = _find_send_as_entry(send_as_entries, from_email)
        if entry:
            return entry.get("signature", "") or ""

    for entry in send_as_entries:
        if entry.get("isPrimary"):
            return entry.get("signature", "") or ""

    return send_as_entries[0].get("signature", "") or ""


async def _get_send_as_identity_and_signature(
    service,
    from_email: Optional[str],
    fallback_email: str,
) -> tuple[str, str]:
    """Resolve the requested or default Gmail send-as identity and signature."""
    send_as_entries = await _get_send_as_entries(service)
    selected_entry = None

    if from_email:
        selected_entry = _find_send_as_entry(send_as_entries, from_email)
    else:
        selected_entry = next(
            (entry for entry in send_as_entries if entry.get("isDefault")), None
        )
        if selected_entry is None:
            selected_entry = next(
                (entry for entry in send_as_entries if entry.get("isPrimary")), None
            )
        if selected_entry is None and send_as_entries:
            selected_entry = send_as_entries[0]

    sender_email = from_email or fallback_email
    signature_html = ""
    if selected_entry:
        if not from_email:
            sender_email = selected_entry.get("sendAsEmail") or fallback_email
        signature_html = selected_entry.get("signature", "") or ""

    return sender_email, signature_html


async def _get_send_as_signature_html_for_tool(
    service, from_email: Optional[str] = None
) -> str:
    """Fetch signature HTML and convert non-benign failures to tool errors."""
    return await _get_send_as_signature_html(service, from_email=from_email)
