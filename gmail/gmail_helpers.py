"""Email helper utilities for Gmail tools."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from typing import Any, Optional

from fastmcp.exceptions import ToolError as ToolExecutionError
from googleapiclient.errors import HttpError

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
    "Message-ID",
    "In-Reply-To",
    "References",
    "Date",
    "List-Unsubscribe",
    "Precedence",
    "List-Id",
]


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
        h["name"]: h["value"] for h in messages[0].get("payload", {}).get("headers", [])
    }
    thread_subject = first_headers.get("Subject") or None

    sender_counter: Counter[str] = Counter()
    participants: set[str] = set()
    non_draft_participants: set[str] = set()
    excluded_drafts = 0

    last_non_draft = None  # (datetime, message_dict, headers_dict)

    for message in messages:
        label_ids = message.get("labelIds", []) or []
        is_draft = "DRAFT" in label_ids

        headers = {
            h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])
        }

        from_addr = headers.get("From", "")
        _name, from_email = parseaddr(from_addr)
        from_norm = _normalize_email(from_email) if from_email else ""

        # Collect participants from From/To/Cc using getaddresses (RFC-correct
        # parsing of quoted display names with embedded commas).
        header_values = [headers.get(hdr, "") for hdr in ("From", "To", "Cc")]
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
            headers.get("Date", ""), message.get("internalDate")
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
    last_sender_raw = last_headers.get("From", "")
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
