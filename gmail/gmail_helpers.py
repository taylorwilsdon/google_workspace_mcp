"""Email helper utilities for Gmail tools."""

from __future__ import annotations

import asyncio
import logging
import re
import ssl
from collections import Counter
from datetime import datetime, timezone
from email.header import Header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional

import httplib2
from fastmcp.exceptions import ToolError as ToolExecutionError
from googleapiclient.errors import HttpError

from core.utils import UserInputError
from gmail.gmail_web_mime import (
    TextDirection,
    assemble_alternative,
    assemble_web_message,
    base_text_direction,
    build_forwarded_container_html,
    build_forwarded_plain,
    build_quote_container_html,
    build_quote_plain,
    encode_raw,
    format_address_list,
    format_attribution_html,
    format_attribution_plain,
    format_display_address,
    format_gmail_date,
    gmail_boundary,
    new_message_html,
    plain_body_to_html,
)

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

EMAIL_REACTION_MIME_TYPE = "text/vnd.google.email-reaction+json"
# Reactions require MIME types, which format=metadata omits. Exclude bodies at
# the root and first two part levels, then retain complete deeper parts (and
# their bodies) so recursive inspection can reach every MIME type.
THREAD_REPLY_CONTEXT_FIELDS = (
    "messages(labelIds,payload(headers,mimeType,parts(mimeType,parts(mimeType,parts))))"
)

# Gmail accepts label colors only from a fixed palette, and rejects anything else
# with an opaque 400. Both backgroundColor and textColor draw from this same set.
# https://developers.google.com/gmail/api/reference/rest/v1/users.labels#Label
# Synchronized with Gmail v1 discovery revision 20260824. When Gmail changes the
# LabelColor schema, update this set and its exact fingerprint test together.
GMAIL_LABEL_COLORS = frozenset(
    {
        "#000000",
        "#007286",
        "#04502e",
        "#076239",
        "#083018",
        "#094228",
        "#0b4f30",
        "#0b804b",
        "#0d3472",
        "#0d3b44",
        "#149e60",
        "#16a765",
        "#16a766",
        "#1a764d",
        "#1c4587",
        "#1e53b8",
        "#202124",
        "#285bac",
        "#2a9c68",
        "#2da2bb",
        "#3c78d8",
        "#3d188e",
        "#3dc789",
        "#41236d",
        "#42d692",
        "#434343",
        "#43d692",
        "#44b984",
        "#464646",
        "#4986e7",
        "#4a86e8",
        "#521d28",
        "#54240e",
        "#594c05",
        "#633e04",
        "#653e9b",
        "#662e37",
        "#666666",
        "#684e07",
        "#68dfa9",
        "#6d9eeb",
        "#711a36",
        "#757575",
        "#7858c3",
        "#7a2e0b",
        "#7a4706",
        "#822111",
        "#83334c",
        "#89d3b2",
        "#8a1c0a",
        "#8e63ce",
        "#98d7e4",
        "#994a64",
        "#999999",
        "#a0eac9",
        "#a2dcc1",
        "#a46a21",
        "#a479e2",
        "#a4c2f4",
        "#aa8831",
        "#ac2b16",
        "#b3efd3",
        "#b65775",
        "#b694e8",
        "#b6cff5",
        "#b99aff",
        "#b9e4d0",
        "#c2185b",
        "#c2c2c2",
        "#c6f3de",
        "#c9daf8",
        "#cc3a21",
        "#cca6ac",
        "#cccccc",
        "#cf8933",
        "#d0bcf1",
        "#d5ae49",
        "#d93025",
        "#e07798",
        "#e3d7ff",
        "#e4d7f5",
        "#e66550",
        "#e7e7e7",
        "#eaa041",
        "#ebdbde",
        "#efa093",
        "#efefef",
        "#f2b2a8",
        "#f2c960",
        "#f3f3f3",
        "#f691b2",
        "#f691b3",
        "#f6c5be",
        "#f7a7c0",
        "#fad165",
        "#fb4c2f",
        "#fbc8d9",
        "#fbd3e0",
        "#fbe983",
        "#fcda83",
        "#fcdee8",
        "#fce8b3",
        "#fdedc1",
        "#fef1d1",
        "#ff7537",
        "#ffad46",
        "#ffad47",
        "#ffbc6b",
        "#ffc8af",
        "#ffd6a2",
        "#ffdeb5",
        "#ffe6c7",
        "#ffffff",
    }
)


def _validate_label_color(field: str, value: str) -> str:
    """Normalize one label color and check it against Gmail's fixed palette.

    Gmail answers an unsupported color with a bare 400, so the check happens here
    to tell the caller which value was wrong and what is allowed.
    """
    normalized = value.strip().lower()
    if normalized not in GMAIL_LABEL_COLORS:
        raise ToolExecutionError(
            f"{field} '{value}' is not a Gmail label color. Gmail accepts only its "
            f"own palette of {len(GMAIL_LABEL_COLORS)} colors, listed at "
            "https://developers.google.com/gmail/api/reference/rest/v1/users.labels#Label"
        )
    return normalized


def build_label_color(
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
) -> Optional[Dict[str, str]]:
    """Build the `color` object for a Gmail label, or None when no color is given.

    Gmail requires both halves whenever `color` is set, so a half-specified color
    is rejected here rather than sent and refused.
    """
    if background_color is None and text_color is None:
        return None
    if background_color is None or text_color is None:
        raise ToolExecutionError(
            "background_color and text_color must be set together. Gmail requires "
            "both when a label color is set."
        )
    return {
        "backgroundColor": _validate_label_color("background_color", background_color),
        "textColor": _validate_label_color("text_color", text_color),
    }


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


async def send_message_once(service, send_body: Dict[str, Any]) -> Dict[str, Any]:
    """Send a message without retries, flagging failures that may have delivered it."""
    try:
        return await asyncio.to_thread(
            service.users().messages().send(userId="me", body=send_body).execute,
            num_retries=0,
        )
    except (
        HttpError,
        httplib2.HttpLib2ErrorWithResponse,
        ssl.SSLError,
        TimeoutError,
        ConnectionError,
    ) as error:
        if isinstance(error, HttpError) and (_http_error_status(error) or 0) < 500:
            raise
        raise ToolExecutionError(
            f"Gmail send failed with an ambiguous error ({error}), so the message "
            "may already have been delivered. Check the Sent folder before sending again."
        ) from error


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


def _is_email_reaction(payload: Mapping[str, Any]) -> bool:
    """Return True if a message payload is a Gmail emoji reaction."""
    return payload.get("mimeType") == EMAIL_REACTION_MIME_TYPE or any(
        _is_email_reaction(part) for part in payload.get("parts") or []
    )


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


_HTML_BLOCK_TAGS = frozenset(
    {
        "address",
        "aside",
        "fieldset",
        "figure",
        "footer",
        "header",
        "main",
        "nav",
        "p",
        "div",
        "br",
        "hr",
        "ul",
        "ol",
        "li",
        "dl",
        "dt",
        "dd",
        "table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "td",
        "th",
        "blockquote",
        "pre",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "html",
        "head",
        "title",
        "meta",
        "link",
        "body",
        "center",
        "section",
        "article",
    }
)
_HTML_VOID_TAGS = frozenset(
    {"area", "br", "col", "embed", "hr", "img", "input", "link", "meta", "wbr"}
)
_HTML_TAG_RE = re.compile(
    r"<\s*(/?)\s*([a-zA-Z][a-zA-Z0-9]*)(?:\"[^\"]*\"|'[^']*'|[^'\">])*>"
)
# Elements whose contents are not rendered as flowing text; a <br> inside them
# would corrupt CSS/JS or show up literally.
_HTML_RAW_TEXT_RE = re.compile(r"<\s*(?:pre|style|script|textarea)\b", re.IGNORECASE)
_NEWLINE_RUN_RE = re.compile(r"((?:\r?\n)+)")
_ESCAPED_LT = r"&(?:lt|#0*60|#x0*3c);"
_ESCAPED_GT = r"&(?:gt|#0*62|#x0*3e);"
# Non-space attribute start avoids quadratic backtracking against \s+.
_ESCAPED_HTML_OPENING_TAG_RE = re.compile(
    rf"^\s*{_ESCAPED_LT}\s*[A-Za-z][A-Za-z0-9-]*(?:\s+[^<>\s][^<>]*|\s*(?:/\s*)?)(?:>|{_ESCAPED_GT})",
    re.IGNORECASE,
)
_RAW_HTML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9-]*(?:\s|/|>)")


def _reject_entity_escaped_html_body(
    body: Optional[str], body_format: Literal["plain", "html"]
) -> None:
    """Reject an html body that opens with an escaped tag and contains no real tag."""
    if not body or body_format != "html":
        return
    if not _ESCAPED_HTML_OPENING_TAG_RE.match(body) or _RAW_HTML_TAG_RE.search(body):
        return
    raise UserInputError(
        "body_format='html' but the body's markup is HTML-entity-escaped "
        "(e.g. '&lt;div&gt;'), so the recipient would see the tags as literal "
        "text. Resend with real markup such as '<div>'. To show escaped text "
        "on purpose, wrap the body in a real tag such as '<p>...</p>'."
    )


def html_newlines_to_br(html_body: str) -> str:
    """Turn bare newlines inside an HTML body into ``<br>`` tags.

    LLM callers routinely pass ``body_format="html"`` with paragraphs separated
    by ``\\n`` and no block markup at all. Browsers collapse that whitespace, so
    the recipient gets one run-on paragraph. Only newlines that separate
    *content* (text or inline tags such as ``<b>``/``<a>``) become ``<br>``;
    newlines that merely sit next to a block-level tag (``<p>``, ``<li>``,
    ``<div>``...), just inside an inline element, before indentation, or at
    either end of the body are formatting whitespace and are left alone, so a
    well-formed HTML body -- including an appended Gmail signature -- comes
    back byte-identical. Bodies containing ``<pre>``, ``<style>``, ``<script>``
    or ``<textarea>`` are never touched.

    Apply this only to caller-authored HTML, never to a composed body that
    embeds third-party markup such as a quoted or forwarded message.
    """
    if not html_body or "\n" not in html_body:
        return html_body
    if _HTML_RAW_TEXT_RE.search(html_body):
        return html_body

    # Each token is (is_tag, raw, edge_after, edge_before): whether whitespace
    # right after / right before the tag is formatting rather than content.
    tokens: List[tuple] = []
    pos = 0
    for match in _HTML_TAG_RE.finditer(html_body):
        if match.start() > pos:
            tokens.append((False, html_body[pos : match.start()], False, False))
        closing, name = bool(match.group(1)), match.group(2).lower()
        block = name in _HTML_BLOCK_TAGS
        opens_inline = not closing and name not in _HTML_VOID_TAGS
        tokens.append((True, match.group(0), block or opens_inline, block or closing))
        pos = match.end()
    if pos < len(html_body):
        tokens.append((False, html_body[pos:], False, False))

    out: List[str] = []
    for index, (is_tag, text, _, _) in enumerate(tokens):
        if is_tag:
            out.append(text)
            continue
        prev_is_edge = index == 0 or tokens[index - 1][2]
        next_is_edge = index + 1 == len(tokens) or tokens[index + 1][3]
        pieces = _NEWLINE_RUN_RE.split(text)
        content = [i for i, piece in enumerate(pieces) if piece.strip()]
        first_content = content[0] if content else len(pieces)
        last_content = content[-1] if content else -1
        rebuilt: List[str] = []
        for piece_index, piece in enumerate(pieces):
            # re.split with one capture group puts newline runs at odd indices.
            if piece_index % 2 == 0:
                rebuilt.append(piece)
                continue
            indented = bool(pieces[piece_index + 1])
            if (piece_index < first_content and prev_is_edge) or (
                piece_index > last_content and (next_is_edge or indented)
            ):
                # Whitespace at a block or element edge, or indentation before
                # the next tag: formatting, not content.
                rebuilt.append(piece)
                continue
            rebuilt.append("<br>" * min(piece.count("\n"), 2) + "\n")
        out.append("".join(rebuilt))
    return "".join(out)


# A body that already opens with a ``<div ... dir=...>`` container; group
# ``dir`` spans the attribute value (quoted or bare). Attributes before ``dir``
# are consumed whole (name plus quoted or bare value), so ``dir=`` text inside
# another attribute's quoted value is never mistaken for the attribute itself.
_HTML_ATTR_VALUE = r"(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+)"
_LEADING_DIR_DIV_RE = re.compile(
    r"\s*<div"
    rf"(?:\s+(?!dir\s*=)[^\s\"'>/=]+(?:\s*=\s*{_HTML_ATTR_VALUE})?)*"
    rf"\s+dir\s*=\s*(?P<dir>{_HTML_ATTR_VALUE})",
    re.IGNORECASE,
)


def _force_leading_dir(body: str, direction: str) -> str:
    """Point a body's own leading ``<div dir=...>`` container at ``direction``."""
    wrapper = _LEADING_DIR_DIV_RE.match(body)
    if wrapper is None:
        return body
    return f'{body[: wrapper.start("dir")]}"{direction}"{body[wrapper.end("dir") :]}'


def _resolve_direction(direction: TextDirection, text: str) -> str:
    """Resolve ``"auto"`` to the base direction of ``text``."""
    return base_text_direction(text) if direction == "auto" else direction


def _derive_web_bodies(
    body: str, body_format: Literal["plain", "html"], direction: TextDirection
) -> tuple[str, str]:
    """Derive the (text/plain, text/html) parts of a Gmail-web compose.

    Plain text becomes Gmail's typed ``<div>`` structure inside the direction
    container; HTML is wrapped in that container unless it already starts with
    one, and its plain part keeps block breaks.
    """
    if body_format == "plain":
        html_part = plain_body_to_html(body)
        return body, new_message_html(html_part, _resolve_direction(direction, body))
    plain_part = html_to_text_preserving_breaks(body).strip()
    if _LEADING_DIR_DIV_RE.match(body) is None:
        resolved = _resolve_direction(direction, plain_part)
        return plain_part, new_message_html(body, resolved)
    # Keep the caller's own container; an explicit direction wins over its dir.
    if direction == "auto":
        return plain_part, body
    return plain_part, _force_leading_dir(body, direction)


def _parse_message_date(value: str) -> Optional[datetime]:
    """Parse a Date header in the sender's own offset, or return None.

    Gmail shows the sender's wall-clock time, not UTC.
    """
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _fill_missing_body(plain: str, html_body: str) -> tuple[str, str]:
    """Derive whichever of a message's plain or HTML body is missing."""
    if not plain and html_body:
        plain = html_to_text_preserving_breaks(html_body).strip()
    if not html_body and plain:
        html_body = plain_body_to_html(plain)
    return plain, html_body


def _build_web_reply_bodies(
    new_plain: str, new_html: str, target: Mapping[str, Any]
) -> tuple[str, str]:
    """Append a Gmail-web ``gmail_quote`` reply trail to both body parts.

    ``target`` is the parent message from the thread reply context. When it
    lacks a usable sender address or date, the bodies are returned unchanged so
    the reply still sends, just without a quote.
    """
    parent_name, parent_email = parseaddr(target.get("from") or "")
    parent_dt = _parse_message_date(target.get("date") or "")
    if "@" not in parent_email or parent_dt is None:
        return new_plain, new_html
    parent_name = parent_name.strip() or parent_email
    parent_text, parent_html = _fill_missing_body(
        target.get("text_body") or "", target.get("html_body") or ""
    )

    attr_plain = format_attribution_plain(parent_name, parent_email, parent_dt)
    attr_html = format_attribution_html(parent_name, parent_email, parent_dt)
    reply_plain = f"{new_plain}\n\n{attr_plain}\n\n{build_quote_plain(parent_text)}"
    reply_html = f"{new_html}<br>{build_quote_container_html(attr_html, parent_html)}"
    return reply_plain, reply_html


def _build_forward_content(
    headers: Mapping[str, str],
    bodies: Mapping[str, str],
    forward_message: Optional[str],
    forward_message_format: Literal["plain", "html"],
    subject_override: Optional[str],
    direction: TextDirection = "auto",
) -> tuple[str, str, str]:
    """Build the (subject, text/plain, text/html) of a Gmail-web forward.

    An explicit ``subject_override`` wins over the derived 'Fwd: <subject>'.
    The optional note leads the body and sets its base direction.
    """
    orig_subject = headers.get("Subject", "(no subject)")
    orig_from = headers.get("From", "(unknown sender)")
    orig_date = headers.get("Date", "(unknown date)")

    if subject_override:
        subject = subject_override
    elif orig_subject.lower().lstrip().startswith(("fwd:", "fw:")):
        subject = orig_subject
    else:
        subject = f"Fwd: {orig_subject}"

    from_name, from_email = parseaddr(orig_from)
    if "@" not in from_email:
        # No usable address: show the raw From as text, never as a dead mailto link.
        from_name, from_email = orig_from, ""
    orig_dt = _parse_message_date(orig_date)
    header = {
        "from_name": from_name.strip(),
        "from_email": from_email,
        "date": format_gmail_date(orig_dt) if orig_dt else orig_date,
        "subject": orig_subject,
        "to": headers.get("To", ""),
    }
    orig_plain, orig_html = _fill_missing_body(
        bodies.get("text", ""), bodies.get("html", "")
    )
    forward_plain = build_forwarded_plain(**header, orig_plain=orig_plain)
    container = build_forwarded_container_html(**header, orig_html=orig_html)

    if not forward_message:
        # Nothing user-authored to orient, so "auto" stays ltr.
        resolved = "ltr" if direction == "auto" else direction
        return subject, forward_plain, new_message_html(f"<br>{container}", resolved)

    if forward_message_format == "html":
        note_html = html_newlines_to_br(forward_message)
        note_plain = html_to_text_preserving_breaks(note_html).strip()
        if direction != "auto":
            # As on a regular send, an explicit direction wins over the note's dir.
            note_html = _force_leading_dir(note_html, direction)
    else:
        note_plain, note_html = forward_message, plain_body_to_html(forward_message)

    if note_plain:
        forward_plain = f"{note_plain}\n\n{forward_plain}"
    # The note sets the base direction; the original keeps its own dir markup.
    forward_html = new_message_html(
        f"{note_html}<br><br>{container}", _resolve_direction(direction, note_plain)
    )
    return subject, forward_plain, forward_html


_REPLY_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)*re\s*:", re.IGNORECASE)


def _reply_subject(subject: str, in_reply_to: Optional[str]) -> str:
    """Prefix ``Re:`` onto a reply's subject unless one follows any ``[tag]`` prefixes."""
    if in_reply_to and not _REPLY_PREFIX_RE.match(subject):
        return f"Re: {subject}"
    return subject


def _inherit_reply_subject(thread_id: str, target: Optional[Mapping[str, Any]]) -> str:
    """Return the replied-to message's subject, raising when it has none."""
    subject = (target or {}).get("subject") or ""
    if not subject.strip():
        raise UserInputError(
            f"Could not inherit a subject from thread '{thread_id}'. Pass 'subject' explicitly."
        )
    return subject


def _checked_header(field: str, value: str) -> str:
    """Reject CR/LF in a caller-supplied header value (RFC 5322 injection)."""
    if "\r" in value or "\n" in value:
        raise ValueError(f"Invalid {field} header value: line breaks not allowed.")
    return value


def _prepare_gmail_message_web(
    subject: str,
    plain_body: str,
    html_body: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    inline_parts: Optional[List[Dict[str, Any]]] = None,
    attachment_parts: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Assemble a Gmail-web faithful raw message.

    ``plain_body`` and ``html_body`` are the finished text/plain and text/html
    parts. Headers are authored in Gmail's order; To/Cc/Bcc are validated and
    their non-ASCII display names RFC 2047 encoded. Message-ID is left for
    Gmail to assign. Returns the base64url raw message.

    ``inline_parts`` (``{filename, mime_type, data, content_id}``) and
    ``attachment_parts`` (``{filename, mime_type, data}``) pick the smallest
    structure Gmail web uses: ``multipart/alternative`` alone, wrapped in
    ``multipart/related`` for inline images, in ``multipart/mixed`` for
    regular attachments, or both.
    """
    subject = _reply_subject(_checked_header("Subject", subject), in_reply_to)
    if not subject.isascii():
        # Encode after the check: a long encoded subject folds with a legal CRLF.
        subject = Header(subject, "utf-8", header_name="Subject").encode(
            maxlinelen=76, linesep="\r\n"
        )

    headers: List[tuple[str, str]] = [("MIME-Version", "1.0")]
    if references:
        # Fold the References chain with CRLF + TAB per RFC 5322 continuation.
        folded = "\r\n\t".join(_checked_header("References", references).split())
        headers.append(("References", folded))
    if in_reply_to:
        headers.append(("In-Reply-To", _checked_header("In-Reply-To", in_reply_to)))
    if bcc:
        headers.append(("Bcc", format_address_list(_checked_header("Bcc", bcc))))
    headers.append(("Subject", subject))
    if from_email:
        headers.append(("From", format_display_address(from_name, from_email)))
    if to:
        headers.append(("To", format_address_list(_checked_header("To", to))))
    if cc:
        headers.append(("Cc", format_address_list(_checked_header("Cc", cc))))

    if not inline_parts and not attachment_parts:
        message = assemble_alternative(
            headers=headers,
            plain_text=plain_body,
            html_text=html_body,
            boundary=gmail_boundary(),
        )
        return encode_raw(message)

    message = assemble_web_message(
        headers=headers,
        plain_text=plain_body,
        html_text=html_body,
        inline_parts=inline_parts or None,
        attachment_parts=attachment_parts or None,
        boundary_alt=gmail_boundary(),
        boundary_related=gmail_boundary() if inline_parts else None,
        boundary_mixed=gmail_boundary(),
    )
    return encode_raw(message)


def _build_web_compose_raw(
    *,
    subject: str,
    body: str,
    body_format: Literal["plain", "html"],
    to: Optional[str],
    cc: Optional[str],
    bcc: Optional[str],
    from_email: str,
    from_name: Optional[str],
    in_reply_to: Optional[str],
    references: Optional[str],
    direction: TextDirection,
    reply_target: Optional[Mapping[str, Any]],
    inline_parts: List[Dict[str, Any]],
    attachment_parts: List[Dict[str, Any]],
) -> str:
    """Build a Gmail-web raw message for the send/draft paths.

    Both body parts are derived from ``body``; when ``reply_target`` (the
    parent from the thread reply context) is given, Gmail's reply trail is
    appended to them. The classified attachment parts are carried as Gmail
    web nests them (see ``_prepare_gmail_message_web``).
    """
    plain_part, html_part = _derive_web_bodies(body, body_format, direction)
    if reply_target:
        plain_part, html_part = _build_web_reply_bodies(
            plain_part, html_part, reply_target
        )
    return _prepare_gmail_message_web(
        subject=subject,
        plain_body=plain_part,
        html_body=html_part,
        to=to,
        cc=cc,
        bcc=bcc,
        in_reply_to=in_reply_to,
        references=references,
        from_email=from_email,
        from_name=from_name,
        inline_parts=inline_parts,
        attachment_parts=attachment_parts,
    )


def _signature_html_to_text(signature_html: str) -> str:
    """Convert Gmail signature HTML to plain text, preserving line breaks."""
    return html_to_text_preserving_breaks(signature_html)


def _wrap_signature_html(signature_html: str) -> str:
    """Wrap signature HTML in the marker Gmail clients use to detect a signed draft."""
    return (
        '<div data-smartmail="gmail_signature">'
        f'<div dir="ltr">{signature_html}</div></div>'
    )


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


def _send_as_entry_is_usable(entry: Dict[str, Any]) -> bool:
    """True unless Gmail reports this send-as entry as pending verification."""
    return entry.get("verificationStatus") != "pending"


async def _get_send_as_identity_and_signature(
    service,
    from_email: Optional[str],
    fallback_email: str,
    require_match: bool = True,
) -> tuple[str, str, str]:
    """Resolve (sender_email, display_name, signature_html) for a send-as identity.

    ``display_name`` is the name Gmail web renders in the From line, or "" when
    unset. With require_match, raises when from_email is not a usable send-as
    address, since Gmail would silently send from the default address instead;
    without it, an unlisted from_email takes the primary entry's signature.
    Validation is skipped when the send-as list is unreadable.
    """
    send_as_entries = await _get_send_as_entries(service)
    selected_entry = None

    if from_email:
        if not send_as_entries:
            return from_email, "", ""
        selected_entry = _find_send_as_entry(send_as_entries, from_email)
        if not require_match:
            selected_entry = selected_entry or next(
                (entry for entry in send_as_entries if entry.get("isPrimary")),
                send_as_entries[0],
            )
        elif selected_entry is None or not _send_as_entry_is_usable(selected_entry):
            accepted = [
                entry.get("sendAsEmail", "")
                for entry in send_as_entries
                if _send_as_entry_is_usable(entry)
            ]
            raise ToolExecutionError(
                f"'{from_email}' is not an accepted 'Send mail as' address for this "
                f"mailbox, and Gmail would silently send from the default address "
                f"instead. Accepted: {', '.join(accepted) or '(none)'}. Add the "
                f"address under Gmail Settings > Accounts > Send mail as, or omit "
                f"from_email to use the default sender."
            )
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
    display_name = ""
    signature_html = ""
    if selected_entry:
        if not from_email:
            sender_email = selected_entry.get("sendAsEmail") or fallback_email
        display_name = (selected_entry.get("displayName") or "").strip()
        signature_html = selected_entry.get("signature", "") or ""

    return sender_email, display_name, signature_html


async def modify_gmail_thread_labels(
    service,
    thread_ids: Iterable[str],
    add_label_ids: Optional[List[str]] = None,
    remove_label_ids: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Apply label changes to whole threads, retrying transient errors per thread.

    Returns ``{thread_id: "applied" | "not_found" | "failed: <reason>"}``.
    """
    body: Dict[str, Any] = {}
    if add_label_ids:
        body["addLabelIds"] = list(add_label_ids)
    if remove_label_ids:
        body["removeLabelIds"] = list(remove_label_ids)
    outcomes: Dict[str, str] = {}
    for thread_id in thread_ids:
        _, _, error = await _fetch_with_retry(
            lambda tid=thread_id: (
                service.users().threads().modify(userId="me", id=tid, body=body)
            ),
            thread_id,
            "thread",
            "modify_gmail_thread_labels",
        )
        if error is None:
            outcomes[thread_id] = "applied"
        elif isinstance(error, HttpError) and _http_error_status(error) == 404:
            outcomes[thread_id] = "not_found"
        elif isinstance(error, HttpError):
            outcomes[thread_id] = (
                f"failed: HTTP {_http_error_status(error)} {error.reason}"
            )
        else:
            outcomes[thread_id] = f"failed: {type(error).__name__}"
    return outcomes


def _format_id_list(ids: List[str], limit: int = 10) -> str:
    """Join IDs for a result message, truncating very long lists."""
    shown = ", ".join(ids[:limit])
    if len(ids) > limit:
        shown += f", … (+{len(ids) - limit} more)"
    return shown


def format_thread_label_outcomes(
    thread_ids: List[str], outcomes: Dict[str, str], action_summary: str
) -> str:
    """Summarise per-thread results of a thread-level label change."""
    applied = [t for t in thread_ids if outcomes.get(t) == "applied"]
    missing = [t for t in thread_ids if outcomes.get(t) == "not_found"]
    failed = [
        f"{t} ({outcomes[t]})"
        for t in thread_ids
        if outcomes.get(t, "").startswith("failed")
    ]
    lines = [
        f"Label changes for {len(thread_ids)} thread ID(s): {action_summary}",
        f"Applied: {len(applied)}/{len(thread_ids)}",
    ]
    if missing:
        lines.append(
            f"No such thread ({len(missing)}): {_format_id_list(missing)}\n"
            "  One possible cause is passing a MESSAGE id where a THREAD id is required."
        )
    if failed:
        lines.append(f"Failed ({len(failed)}): {_format_id_list(failed)}")
    return "\n".join(lines)


# users.messages.batchModify accepts at most 1000 IDs per call.
GMAIL_BATCH_MODIFY_LIMIT = 1000
FILTER_APPLY_DEFAULT_MAX_MESSAGES = 5000


def _quote_search_term(value: Any) -> str:
    """Group a multi-word value so Gmail search treats it as one operand."""
    text = str(value).strip()
    if " " in text:
        return f"({text})"
    return text


def filter_criteria_to_query(criteria: Mapping[str, Any]) -> str:
    """Translate Gmail filter criteria into the equivalent search query.

    Covers from, to, subject, query, negatedQuery, hasAttachment and size.
    ``excludeChats`` has no search operator and is ignored (callers report it).
    Raises ValueError for ``size`` without an explicit ``sizeComparison``.
    """
    parts: List[str] = []
    if criteria.get("from"):
        parts.append(f"from:{_quote_search_term(criteria['from'])}")
    if criteria.get("to"):
        parts.append(f"to:{_quote_search_term(criteria['to'])}")
    if criteria.get("subject"):
        parts.append(f"subject:{_quote_search_term(criteria['subject'])}")
    if criteria.get("query"):
        parts.append(f"({criteria['query']})")
    if criteria.get("negatedQuery"):
        parts.append(f"-({criteria['negatedQuery']})")
    if criteria.get("hasAttachment"):
        parts.append("has:attachment")
    if criteria.get("size"):
        op = criteria.get("sizeComparison")
        if op not in ("larger", "smaller"):
            raise ValueError(
                "size criteria need sizeComparison 'larger' or 'smaller' to be "
                f"translated into a search query (got {op!r})"
            )
        parts.append(f"{op}:{int(criteria['size'])}")
    return " ".join(parts)


async def update_gmail_filter(
    service,
    filter_id: str,
    criteria: Optional[Mapping[str, Any]] = None,
    filter_action: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Replace a filter, keeping the parts the caller did not pass.

    Gmail has no filter update endpoint, so the new filter is created FIRST and
    the old one deleted afterwards: a failure never leaves the mailbox without
    the rule. Returns the new filter; the filter ID changes.
    """
    filters = service.users().settings().filters()
    old = await asyncio.to_thread(filters.get(userId="me", id=filter_id).execute)
    body = {
        "criteria": dict(criteria) if criteria else old.get("criteria", {}),
        "action": dict(filter_action) if filter_action else old.get("action", {}),
    }
    created = await asyncio.to_thread(filters.create(userId="me", body=body).execute)
    try:
        await asyncio.to_thread(filters.delete(userId="me", id=filter_id).execute)
    except Exception as error:
        raise ToolExecutionError(
            f"Created the new filter {created.get('id', '(unknown)')} but could "
            f"not delete the old filter {filter_id}: both are active now. "
            f"Delete {filter_id} manually. Cause: {error}"
        ) from error
    created.setdefault("criteria", body["criteria"])
    created.setdefault("action", body["action"])
    return created


async def apply_gmail_filter_to_existing(
    service,
    criteria: Mapping[str, Any],
    filter_action: Mapping[str, Any],
    dry_run: bool = False,
    max_messages: int = FILTER_APPLY_DEFAULT_MAX_MESSAGES,
) -> Dict[str, Any]:
    """Apply a filter's label actions to messages already in the mailbox.

    Gmail filters only act on new mail. Only matching messages change, not the
    rest of their threads, mirroring how the filter treats incoming mail.
    Forwarding is never applied retroactively.
    Returns a summary dict (query, matched, truncated, applied, notes).
    """
    query = filter_criteria_to_query(criteria)
    if not query:
        raise ValueError("Filter criteria produce an empty search query; refusing.")
    add = list(filter_action.get("addLabelIds") or [])
    remove = list(filter_action.get("removeLabelIds") or [])
    notes: List[str] = []
    if filter_action.get("forward"):
        notes.append("Forwarding is not applied to existing mail.")
    if criteria.get("excludeChats"):
        notes.append("excludeChats has no search equivalent and was ignored.")

    ids: List[str] = []
    page_token: Optional[str] = None
    while len(ids) < max_messages:
        params: Dict[str, Any] = {"userId": "me", "q": query, "maxResults": 500}
        if page_token:
            params["pageToken"] = page_token
        response = await asyncio.to_thread(
            service.users().messages().list(**params).execute
        )
        ids.extend(m["id"] for m in response.get("messages") or [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    truncated = len(ids) > max_messages or bool(page_token)
    ids = ids[:max_messages]

    applied = 0
    if not dry_run and ids and (add or remove):
        body: Dict[str, Any] = {}
        if add:
            body["addLabelIds"] = add
        if remove:
            body["removeLabelIds"] = remove
        for start in range(0, len(ids), GMAIL_BATCH_MODIFY_LIMIT):
            chunk = ids[start : start + GMAIL_BATCH_MODIFY_LIMIT]
            await asyncio.to_thread(
                service.users()
                .messages()
                .batchModify(userId="me", body={"ids": chunk, **body})
                .execute
            )
            applied += len(chunk)
    if not add and not remove:
        notes.append("The filter has no label actions; nothing to apply.")
    return {
        "query": query,
        "matched": len(ids),
        "truncated": truncated,
        "applied": applied,
        "add": add,
        "remove": remove,
        "notes": notes,
    }


def format_filter_apply_result(result: Mapping[str, Any], dry_run: bool) -> str:
    """Human-readable summary for apply_gmail_filter_to_existing."""
    matched = f"{result['matched']}{'+' if result['truncated'] else ''}"
    lines = [
        "DRY RUN: nothing changed."
        if dry_run
        else f"Applied to {result['applied']} messages.",
        f"Query: {result['query']}",
        f"Matching messages: {matched}",
    ]
    if result["add"]:
        lines.append(f"Add labels: {', '.join(result['add'])}")
    if result["remove"]:
        lines.append(f"Remove labels: {', '.join(result['remove'])}")
    lines.extend(result["notes"])
    if result["truncated"] and not dry_run:
        lines.append(
            "Stopped at max_messages; raise it to process the remaining matches."
        )
    return "\n".join(lines)
