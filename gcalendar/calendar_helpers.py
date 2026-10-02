"""
Google Calendar Helper Functions

This module provides utility functions for formatting Google Calendar
event data for display.
"""

import datetime
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

_RFC3339_DATETIME = re.compile(
    r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d"
    r"(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)"
)
_GOOGLE_ALL_DAY_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class EventBoundary:
    """One resolved start/end boundary of a Google Calendar event.

    Google returns each boundary as ``{"dateTime": ..., "timeZone": ...}`` (or
    ``{"date": ...}`` for all-day events), and those two fields do NOT agree by
    default. ``events.list`` / ``events.get`` normalize every ``dateTime`` in the
    response into ONE zone -- the ``timeZone`` request parameter, defaulting to the
    calendar's own -- while leaving each boundary's IANA ``timeZone`` untouched. An
    arrival authored as 17:50 Europe/Amsterdam therefore comes back as
    ``2026-08-21T18:50:00+03:00`` on an Asia/Jerusalem calendar: the correct instant,
    rendered in a zone the author never chose.

    This type resolves the pair into a single tz-aware moment expressed in the
    boundary's OWN zone, so cross-timezone events (flights, above all) report the
    wall-clock and offset that were actually authored. The instant is unchanged;
    only its presentation is corrected.
    """

    raw: str
    """The dateTime/date string exactly as Google returned it."""

    local_date: datetime.date
    """Calendar date at this boundary, in its own zone when that zone was resolved."""

    moment: Optional[datetime.datetime] = None
    """Timezone-aware instant, converted when :attr:`timezone_resolved` is true."""

    timezone: Optional[str] = None
    """IANA zone name exactly as Google supplied it, even if locally unresolvable."""

    timezone_resolved: bool = False
    """Whether :attr:`moment` was converted into :attr:`timezone`."""

    is_all_day: bool = False
    is_exclusive_end: bool = False

    @property
    def iso_weekday(self) -> int:
        return self.local_date.isoweekday()

    def isoformat(self) -> str:
        """RFC3339 stamp in this boundary's own zone.

        Falls back to :attr:`raw` verbatim when no IANA zone was resolved. Without a
        resolved zone there is no reliable wall-clock conversion to make, so the value
        Google returned is the best timestamp -- and echoing it unchanged preserves its
        exact spelling (``Z`` rather than ``+00:00``, offset-less values untouched).
        """
        if self.moment is None or not self.timezone_resolved:
            return self.raw
        return self.moment.isoformat()

    def render(self) -> str:
        """Human-readable boundary: local stamp plus zone and weekday evidence."""
        evidence: List[str] = []
        if self.timezone:
            evidence.append(self.timezone)
        evidence.append(f"weekday: {_WEEKDAYS[self.iso_weekday - 1]}")
        evidence.append(f"ISO weekday: {self.iso_weekday}")
        if self.is_exclusive_end:
            evidence.append("exclusive all-day end")
        return f"{self.isoformat()} [{'; '.join(evidence)}]"


def _resolve_zone(boundary: Dict[str, Any]) -> Optional[ZoneInfo]:
    """Return the boundary's IANA zone, or None when absent or unrecognized."""
    tz_name = boundary.get("timeZone")
    if not isinstance(tz_name, str) or not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        logger.debug(
            "Unrecognized event timeZone %r; keeping returned offset.", tz_name
        )
        return None


def parse_event_boundary(item: Dict[str, Any], field: str) -> Optional[EventBoundary]:
    """Parse one raw Google event boundary into an :class:`EventBoundary`.

    Returns None when the payload is malformed or shaped unexpectedly, so callers
    can fall back to echoing the raw value rather than guessing at a time.
    """
    boundary = item.get(field)
    if not isinstance(boundary, dict):
        return None
    value = boundary.get("dateTime", boundary.get("date"))
    if not isinstance(value, str):
        return None

    if "dateTime" in boundary:
        if _RFC3339_DATETIME.fullmatch(value) is None:
            return None
        try:
            normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
            moment = datetime.datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if moment.tzinfo is None:
            return None
        zone = _resolve_zone(boundary)
        if zone is not None:
            moment = moment.astimezone(zone)
        tz_name = boundary.get("timeZone")
        timezone = tz_name if isinstance(tz_name, str) and tz_name else None
        return EventBoundary(
            raw=value,
            local_date=moment.date(),
            moment=moment,
            timezone=timezone,
            timezone_resolved=zone is not None,
        )

    if _GOOGLE_ALL_DAY_DATE.fullmatch(value) is None:
        return None
    try:
        local_date = datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return EventBoundary(
        raw=value,
        local_date=local_date,
        is_all_day=True,
        is_exclusive_end=(field == "end"),
    )


def _format_event_time(item: Dict[str, Any], field: str) -> str:
    """Format one event boundary, including sparse cancelled exceptions.

    Google may return cancelled instances of an unexpanded recurring series as
    tombstones with no ``start`` or ``end``. Their ``originalStartTime`` is the
    occurrence they exclude, so use it as the start boundary instead of
    failing or presenting an unexplained ``None``.
    """
    boundary_field = field
    if (
        not isinstance(item.get(field), dict)
        and field == "start"
        and item.get("status") == "cancelled"
    ):
        boundary_field = "originalStartTime"

    parsed = parse_event_boundary(item, boundary_field)
    if parsed is None:
        boundary = item.get(boundary_field)
        if isinstance(boundary, dict):
            value = boundary.get("dateTime", boundary.get("date"))
            return value if isinstance(value, str) else "Unavailable"
        return "Unavailable"
    return parsed.render()


def _get_meeting_link(item: Dict[str, Any]) -> str:
    """Extract video meeting link from event conference data or hangoutLink."""
    conference_data = item.get("conferenceData")
    if conference_data and "entryPoints" in conference_data:
        for entry_point in conference_data["entryPoints"]:
            if entry_point.get("entryPointType") == "video":
                uri = entry_point.get("uri", "")
                if uri:
                    return uri
    hangout_link = item.get("hangoutLink", "")
    if hangout_link:
        return hangout_link
    return ""


def _format_attendee_details(
    attendees: List[Dict[str, Any]], indent: str = "  "
) -> str:
    """
      Format attendee details including response status, organizer, and optional flags.

      Example output format:
      "  user@example.com: accepted
    manager@example.com: declined (organizer)
    optional-person@example.com: tentative (optional)"

      Args:
          attendees: List of attendee dictionaries from Google Calendar API
          indent: Indentation to use for newline-separated attendees (default: "  ")

      Returns:
          Formatted string with attendee details, or "None" if no attendees
    """
    if not attendees:
        return "None"

    attendee_details_list = []
    for a in attendees:
        email = a.get("email", "unknown")
        response_status = a.get("responseStatus", "unknown")
        optional = a.get("optional", False)
        organizer = a.get("organizer", False)

        detail_parts = [f"{email}: {response_status}"]
        if organizer:
            detail_parts.append("(organizer)")
        if optional:
            detail_parts.append("(optional)")

        attendee_details_list.append(" ".join(detail_parts))

    return f"\n{indent}".join(attendee_details_list)


def _format_attachment_details(
    attachments: List[Dict[str, Any]], indent: str = "  "
) -> str:
    """
    Format attachment details including file information.


    Args:
        attachments: List of attachment dictionaries from Google Calendar API
        indent: Indentation to use for newline-separated attachments (default: "  ")

    Returns:
        Formatted string with attachment details, or "None" if no attachments
    """
    if not attachments:
        return "None"

    attachment_details_list = []
    for att in attachments:
        title = att.get("title", "Untitled")
        file_url = att.get("fileUrl", "No URL")
        file_id = att.get("fileId", "No ID")
        mime_type = att.get("mimeType", "Unknown")

        attachment_info = (
            f"{title}\n"
            f"{indent}File URL: {file_url}\n"
            f"{indent}File ID: {file_id}\n"
            f"{indent}MIME Type: {mime_type}"
        )
        attachment_details_list.append(attachment_info)

    return f"\n{indent}".join(attachment_details_list)


def _format_person(person: Optional[Dict[str, Any]]) -> Optional[str]:
    """Format a Google Calendar person dict (creator or organizer) for display."""
    if not person:
        return None
    name = (person.get("displayName") or "").strip()
    email = (person.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    if name:
        return name
    if email:
        return f"<{email}>"
    return None


def _format_event_detail_lines(
    item: Dict[str, Any],
    prefix: str,
    indent: str,
    include_attachments: bool = False,
) -> str:
    """
    Format the shared body of a detailed event, one field per line.

    Both detailed output paths in `get_events` — the single-event lookup and the
    ranged listing — emit the same fields and differ only in line prefix and
    continuation indent. Formatting them here is what keeps the two in step.

    Args:
        item: Event resource from the Google Calendar API
        prefix: Prefix for each field line (e.g. "- " or "  ")
        indent: Continuation indent for multi-line values (attendees, attachments)
        include_attachments: Whether to append attachment details

    Returns:
        Newline-terminated block of detail lines
    """
    lines = [
        f"{prefix}Description: {item.get('description', 'No Description')}",
        f"{prefix}Location: {item.get('location', 'No Location')}",
        f"{prefix}Color ID: {item.get('colorId', 'None')}",
    ]

    recurring_event_id = item.get("recurringEventId")
    if recurring_event_id:
        lines.append(f"{prefix}Recurring Event ID: {recurring_event_id}")

    original_start_time = item.get("originalStartTime")
    is_exception = item.get("status") != "confirmed" or not item.get("start")
    if original_start_time and is_exception:
        lines.append(f"{prefix}Original Start Time: {json.dumps(original_start_time)}")

    recurrence = item.get("recurrence")
    if recurrence:
        # Keep the individual RFC5545 lines lossless and machine-readable. A
        # recurring master may carry RRULE plus RDATE/EXDATE entries whose
        # commas and semicolons make a hand-joined string ambiguous.
        lines.append(f"{prefix}Recurrence: {json.dumps(recurrence)}")

    # eventType and status are omitted at their API defaults, so an ordinary
    # one-off meeting stays as compact as it was before these fields existed.
    event_type = item.get("eventType")
    if event_type and event_type != "default":
        lines.append(f"{prefix}Event Type: {event_type}")

    status = item.get("status")
    if status and status != "confirmed":
        lines.append(f"{prefix}Status: {status}")

    creator_str = _format_person(item.get("creator"))
    if creator_str:
        lines.append(f"{prefix}Creator: {creator_str}")

    organizer_str = _format_person(item.get("organizer"))
    if organizer_str:
        lines.append(f"{prefix}Organizer: {organizer_str}")

    meeting_link = _get_meeting_link(item)
    if meeting_link:
        lines.append(f"{prefix}Meeting Link: {meeting_link}")

    attendees = item.get("attendees", [])
    attendee_emails = (
        ", ".join([a.get("email", "") for a in attendees]) if attendees else "None"
    )
    lines.append(f"{prefix}Attendees: {attendee_emails}")
    lines.append(
        f"{prefix}Attendee Details: {_format_attendee_details(attendees, indent=indent)}"
    )

    if include_attachments:
        attachment_details = _format_attachment_details(
            item.get("attachments", []), indent=indent
        )
        lines.append(f"{prefix}Attachments: {attachment_details}")

    return "".join(f"{line}\n" for line in lines)
