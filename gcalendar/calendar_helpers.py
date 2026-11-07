"""
Google Calendar Helper Functions

This module provides utility functions for validating Google Calendar tool
inputs, building event request payloads, and formatting event data for display.
"""

import asyncio
import contextlib
import datetime
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytz
from googleapiclient.discovery import build

from core.utils import UserInputError

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
      Format attendee details including response status, organizer and optional
      flags, and the attendee's RSVP note (the API's "comment" field).

      Example output format:
      "  user@example.com: accepted
    manager@example.com: declined (organizer)
    optional-person@example.com: tentative (optional)
    guest@example.com: declined (note: "Can't make it")"

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
        note = " ".join((a.get("comment") or "").split())
        if note:
            detail_parts.append(f"(note: {json.dumps(note, ensure_ascii=False)})")

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

    reminders = item.get("reminders") or {}
    if not reminders.get("useDefault", True):
        lines.append(f"{prefix}Reminders: {json.dumps(reminders.get('overrides', []))}")

    visibility = item.get("visibility")
    if visibility and visibility != "default":
        lines.append(f"{prefix}Visibility: {visibility}")

    transparency = item.get("transparency")
    if transparency and transparency != "opaque":
        lines.append(f"{prefix}Transparency: {transparency}")

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


def _parse_reminders_json(
    reminders_input: Optional[Union[str, List[Dict[str, Any]]]], function_name: str
) -> List[Dict[str, Any]]:
    """
    Parse reminders from JSON string or list object and validate them.

    Args:
        reminders_input: JSON string containing reminder objects or list of reminder objects
        function_name: Name of calling function for logging

    Returns:
        List of validated reminder objects
    """
    if not reminders_input:
        return []

    # Handle both string (JSON) and list inputs
    if isinstance(reminders_input, str):
        try:
            reminders = json.loads(reminders_input)
            if not isinstance(reminders, list):
                logger.warning(
                    f"[{function_name}] Reminders must be a JSON array, got {type(reminders).__name__}"
                )
                return []
        except json.JSONDecodeError as e:
            logger.warning(f"[{function_name}] Invalid JSON for reminders: {e}")
            return []
    elif isinstance(reminders_input, list):
        reminders = reminders_input
    else:
        logger.warning(
            f"[{function_name}] Reminders must be a JSON string or list, got {type(reminders_input).__name__}"
        )
        return []

    # Validate reminders
    if len(reminders) > 5:
        logger.warning(
            f"[{function_name}] More than 5 reminders provided, truncating to first 5"
        )
        reminders = reminders[:5]

    validated_reminders = []
    for reminder in reminders:
        if (
            not isinstance(reminder, dict)
            or "method" not in reminder
            or "minutes" not in reminder
        ):
            logger.warning(
                f"[{function_name}] Invalid reminder format: {reminder}, skipping"
            )
            continue

        method = reminder["method"].lower()
        if method not in ["popup", "email"]:
            logger.warning(
                f"[{function_name}] Invalid reminder method '{method}', must be 'popup' or 'email', skipping"
            )
            continue

        minutes = reminder["minutes"]
        if not isinstance(minutes, int) or minutes < 0 or minutes > 40320:
            logger.warning(
                f"[{function_name}] Invalid reminder minutes '{minutes}', must be integer 0-40320, skipping"
            )
            continue

        validated_reminders.append({"method": method, "minutes": minutes})

    return validated_reminders


def _apply_transparency_if_valid(
    event_body: Dict[str, Any],
    transparency: Optional[str],
    function_name: str,
) -> None:
    """
    Apply transparency to the event body if the provided value is valid.

    Args:
        event_body: Event payload being constructed.
        transparency: Provided transparency value.
        function_name: Name of the calling function for logging context.
    """
    if transparency is None:
        return

    valid_transparency_values = ["opaque", "transparent"]
    if transparency in valid_transparency_values:
        event_body["transparency"] = transparency
        logger.info(f"[{function_name}] Set transparency to '{transparency}'")
    else:
        logger.warning(
            f"[{function_name}] Invalid transparency value '{transparency}', must be 'opaque' or 'transparent', skipping"
        )


def _apply_visibility_if_valid(
    event_body: Dict[str, Any],
    visibility: Optional[str],
    function_name: str,
) -> None:
    """
    Apply visibility to the event body if the provided value is valid.

    Args:
        event_body: Event payload being constructed.
        visibility: Provided visibility value.
        function_name: Name of the calling function for logging context.
    """
    if visibility is None:
        return

    valid_visibility_values = ["default", "public", "private", "confidential"]
    if visibility in valid_visibility_values:
        event_body["visibility"] = visibility
        logger.info(f"[{function_name}] Set visibility to '{visibility}'")
    else:
        logger.warning(
            f"[{function_name}] Invalid visibility value '{visibility}', must be 'default', 'public', 'private', or 'confidential', skipping"
        )


_EVENT_COLOR_IDS = {
    "lavender": "1",
    "sage": "2",
    "grape": "3",
    "flamingo": "4",
    "banana": "5",
    "tangerine": "6",
    "peacock": "7",
    "graphite": "8",
    "blueberry": "9",
    "basil": "10",
    "tomato": "11",
}


def _resolve_color_id(color: Optional[str]) -> Optional[str]:
    """Resolve an event color name (e.g. "tomato") or ID ("1"-"11") to its colorId."""
    if color is None:
        return None
    normalized = color.strip().lower()
    color_id = _EVENT_COLOR_IDS.get(normalized, normalized)
    if color_id not in _EVENT_COLOR_IDS.values():
        options = ", ".join(f"{name} ({cid})" for name, cid in _EVENT_COLOR_IDS.items())
        raise UserInputError(f"Invalid color_id '{color}'. Must be one of: {options}")
    return color_id


_VALID_AUTO_DECLINE_MODES = {
    "declineAllConflictingInvitations",
    "declineOnlyNewConflictingInvitations",
    "declineNone",
}

_VALID_FOCUS_TIME_CHAT_STATUSES = {
    "available",
    "doNotDisturb",
}


def _validate_auto_decline_mode(mode: Optional[str], function_name: str) -> str:
    """Validate and return auto decline mode, defaulting to declineAllConflictingInvitations.

    Args:
        mode: The auto decline mode to validate.
        function_name: Name of the calling function for error context.

    Returns:
        A valid auto decline mode string.
    """
    if mode is None:
        return "declineAllConflictingInvitations"
    if mode not in _VALID_AUTO_DECLINE_MODES:
        raise UserInputError(
            f"[{function_name}] Invalid auto_decline_mode '{mode}'. "
            f"Must be one of: {', '.join(sorted(_VALID_AUTO_DECLINE_MODES))}"
        )
    return mode


def _preserve_existing_fields(
    event_body: Dict[str, Any],
    existing_event: Dict[str, Any],
    field_mappings: Dict[str, Any],
) -> None:
    """
    Helper function to preserve existing event fields when not explicitly provided.

    Args:
        event_body: The event body being built for the API call
        existing_event: The existing event data from the API
        field_mappings: Dict mapping field names to their new values (None means preserve existing)
    """
    for field_name, new_value in field_mappings.items():
        if new_value is None and field_name in existing_event:
            event_body[field_name] = existing_event[field_name]
            logger.info(f"[modify_event] Preserving existing {field_name}")
        elif new_value is not None:
            event_body[field_name] = new_value


def _correct_time_format_for_api(
    time_str: Optional[str], param_name: str, timezone: Optional[str] = None
) -> Optional[str]:
    """Normalize a time string into RFC3339 format suitable for the Google Calendar API."""
    if not time_str:
        return None

    # Defensive normalization: some LLM-driven MCP clients double-encode JSON
    # string arguments, passing values like '"2026-05-15T00:00:00Z"'
    time_str = time_str.strip().strip('"').strip("'").strip()
    if not time_str or time_str.lower() in ("null", "none"):
        return None

    logger.info(
        f"_correct_time_format_for_api: Processing {param_name} with value '{time_str}', timezone: '{timezone}'"
    )

    # Handle date-only format (YYYY-MM-DD)
    if len(time_str) == 10 and time_str.count("-") == 2:
        try:
            # Validate it's a proper date
            datetime.datetime.strptime(time_str, "%Y-%m-%d")
            # For date-only, convert using the provided timezone, or UTC if not provided
            if timezone:
                try:
                    tz = pytz.timezone(timezone)
                    # Parse the date and create a datetime at midnight in the specified timezone
                    date_obj = datetime.datetime.strptime(time_str, "%Y-%m-%d")
                    dt = tz.localize(date_obj)
                    # Convert to UTC and format as RFC3339
                    formatted = (
                        dt.astimezone(datetime.timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                except pytz.exceptions.UnknownTimeZoneError:
                    logger.warning(
                        f"Could not apply timezone '{timezone}', falling back to UTC for {param_name}"
                    )
                    formatted = f"{time_str}T00:00:00Z"
            else:
                formatted = f"{time_str}T00:00:00Z"
            logger.info(
                f"Formatting date-only {param_name} '{time_str}' to RFC3339: '{formatted}'"
            )
            return formatted
        except ValueError:
            logger.warning(
                f"{param_name} '{time_str}' looks like a date but is not valid YYYY-MM-DD. Using as is."
            )
            return time_str

    # Specifically address YYYY-MM-DDTHH:MM:SS by appending 'Z'
    if (
        len(time_str) == 19
        and time_str[10] == "T"
        and time_str.count(":") == 2
        and not (
            time_str.endswith("Z") or ("+" in time_str[10:]) or ("-" in time_str[10:])
        )
    ):
        try:
            # Validate the format before appending 'Z'
            datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
            logger.info(
                f"Formatting {param_name} '{time_str}' by appending 'Z' for UTC."
            )
            return time_str + "Z"
        except ValueError:
            logger.warning(
                f"{param_name} '{time_str}' looks like it needs 'Z' but is not valid YYYY-MM-DDTHH:MM:SS. Using as is."
            )
            return time_str

    # If it already has timezone info or doesn't match our patterns, return as is
    logger.info(f"{param_name} '{time_str}' doesn't need formatting, using as is.")
    return time_str


def _build_time_boundary(time_value: str, timezone: Optional[str]) -> Dict[str, str]:
    """Build one Google ``start``/``end`` boundary from a time string and its zone.

    Each boundary carries its OWN ``timeZone``, which is what makes a cross-timezone
    event expressible: a flight departing 13:45 Asia/Jerusalem and landing 17:50
    Europe/Amsterdam is one event whose two ends are authored in different zones.
    Forcing a single zone on both ends silently rewrites one of them -- the arrival
    above becomes 17:50 Israel time, an hour off, with no error raised.
    """
    if "T" not in time_value:
        return {"date": time_value}
    try:
        parsed = datetime.datetime.fromisoformat(re.sub(r"[zZ]$", "+00:00", time_value))
    except ValueError as exc:
        raise UserInputError(
            f"Invalid RFC3339 timestamp {time_value!r}. Use a value such as "
            "'2026-09-17T11:35:00' or '2026-09-17T11:35:00-07:00'."
        ) from exc
    # `is None` rather than falsy: an explicitly empty zone is an invalid value, not
    # an omitted one, and must reach validation below instead of being treated as
    # "no zone given".
    if timezone is None:
        return {"dateTime": time_value}
    # Reject an unresolvable zone here rather than stripping the caller's offset and
    # forwarding a name Google will refuse. Falling back to the offset instead would
    # be worse than erroring: it silently discards the zone the caller asked for and
    # books the event somewhere else.
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UserInputError(
            f"Unrecognized IANA timezone {timezone!r}. Use a zone name such as "
            "'America/New_York' or 'Europe/Amsterdam'."
        ) from exc
    if parsed.tzinfo is not None:
        time_value = parsed.astimezone(zone).isoformat()
    return {"dateTime": time_value, "timeZone": timezone}


def _saved_event_times(event: Dict[str, Any]) -> str:
    start = parse_event_boundary(event, "start")
    end = parse_event_boundary(event, "end")
    lines = [
        f"Saved start: {_format_event_time(event, 'start')}",
        f"Saved end: {_format_event_time(event, 'end')}",
    ]
    if start and end:
        if start.moment and end.moment:
            # Subtraction in a shared ZoneInfo uses wall time across DST changes.
            utc = datetime.timezone.utc
            seconds = round(
                (
                    end.moment.astimezone(utc) - start.moment.astimezone(utc)
                ).total_seconds()
            )
            lines.append(
                f"Elapsed duration: {seconds / 60:.15g} minutes ({seconds} seconds)"
            )
        elif start.is_all_day and end.is_all_day:
            days = (end.local_date - start.local_date).days
            unit = "day" if days == 1 else "days"
            lines.append(f"All-day span: {days} {unit} (end date exclusive)")
    return "\n" + "\n".join(lines)


# Friendly provider name -> conferenceSolution display name for the addOn block.
_CONFERENCE_SOLUTION_NAMES = {
    "zoom": "Zoom Meeting",
    "webex": "Webex",
    "teams": "Microsoft Teams",
    "microsoft teams": "Microsoft Teams",
}


def _build_addon_conference_data(
    provider: str,
    uri: str,
    passcode: Optional[str] = None,
    conference_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a Google Calendar ``conferenceData`` block for a third-party add-on.

    Used for providers (Zoom, Webex, Teams, ...) attached via the
    ``conferenceSolution.key.type = "addOn"`` mechanism rather than the native
    ``hangoutsMeet`` create request.
    """
    provider = provider.strip()
    uri = uri.strip()
    name = _CONFERENCE_SOLUTION_NAMES.get(provider.lower(), provider)
    entry_point: Dict[str, Any] = {
        "entryPointType": "video",
        "uri": uri,
        "label": name,
    }
    if passcode:
        entry_point["passcode"] = passcode
    conference_data: Dict[str, Any] = {
        "conferenceSolution": {"key": {"type": "addOn"}, "name": name},
        "entryPoints": [entry_point],
    }
    if conference_id:
        conference_data["conferenceId"] = conference_id
    return conference_data


def _resolve_conference_data(
    conference_data: Optional[Dict[str, Any]],
    conference_provider: Optional[str],
    conference_uri: Optional[str],
    conference_passcode: Optional[str],
    conference_id: Optional[str],
    add_google_meet: Optional[bool],
) -> Optional[Dict[str, Any]]:
    """Resolve the conferencing inputs into a single ``conferenceData`` dict.

    Accepts either a raw ``conference_data`` pass-through payload or the
    higher-level ``conference_provider``/``conference_uri`` helper params, and
    validates that they are not combined with each other or with
    ``add_google_meet``. Returns the resolved payload, or ``None`` if no
    third-party conference was requested.
    """
    helper_used = any(
        [conference_provider, conference_uri, conference_passcode, conference_id]
    )
    if conference_data is not None and helper_used:
        raise UserInputError(
            "Provide either conference_data (raw payload) or the "
            "conference_provider/conference_uri helper params, not both."
        )

    resolved = conference_data
    if helper_used:
        provider = (conference_provider or "").strip()
        uri = (conference_uri or "").strip()
        if not (provider and uri):
            raise UserInputError(
                "conference_provider and conference_uri are both required to "
                "attach a third-party conference."
            )
        resolved = _build_addon_conference_data(
            provider, uri, conference_passcode, conference_id
        )

    if resolved is not None and add_google_meet:
        raise UserInputError(
            "Cannot attach a third-party conference and add_google_meet on the "
            "same event; choose one."
        )
    return resolved


async def _build_attachment_entries(
    service,
    attachments: Union[str, List[str]],
    log_prefix: str,
) -> List[Dict[str, str]]:
    """
    Resolve Drive file IDs or URLs into Calendar attachment objects.

    Falls back to a generic title and MIME type when Drive metadata cannot be
    read, which happens when the calendar credentials carry no Drive scope.
    """
    if isinstance(attachments, str):
        attachments = [a.strip() for a in attachments.split(",") if a.strip()]

    entries: List[Dict[str, str]] = []
    drive_service = None
    try:
        try:
            drive_service = service._http and build("drive", "v3", http=service._http)
        except Exception as e:
            logger.warning(f"Could not build Drive service for MIME type lookup: {e}")

        for att in attachments:
            if att.startswith("https://"):
                # Match /d/<id>, /file/d/<id>, ?id=<id>
                match = re.search(r"(?:/d/|/file/d/|id=)([\w-]+)", att)
                file_id = match.group(1) if match else None
                logger.info(
                    f"[{log_prefix}] Extracted file_id '{file_id}' from attachment URL"
                )
            else:
                file_id = att
                logger.info(
                    f"[{log_prefix}] Using direct file_id '{file_id}' for attachment"
                )
            if not file_id:
                continue

            mime_type = "application/vnd.google-apps.drive-sdk"
            title = "Drive Attachment"
            # Try to get the actual MIME type and filename from Drive
            if drive_service:
                try:
                    file_metadata = await asyncio.to_thread(
                        lambda: (
                            drive_service.files()
                            .get(
                                fileId=file_id,
                                fields="mimeType,name",
                                supportsAllDrives=True,
                            )
                            .execute()
                        )
                    )
                    mime_type = file_metadata.get("mimeType", mime_type)
                    filename = file_metadata.get("name")
                    if filename:
                        title = filename
                        logger.info(
                            f"[{log_prefix}] Using filename '{filename}' as attachment title"
                        )
                    else:
                        logger.info(
                            f"[{log_prefix}] No filename found, using generic title"
                        )
                except Exception as e:
                    logger.warning(f"Could not fetch metadata for file {file_id}: {e}")

            entries.append(
                {
                    "fileUrl": f"https://drive.google.com/open?id={file_id}",
                    "title": title,
                    "mimeType": mime_type,
                }
            )
    finally:
        if drive_service:
            drive_service.close()
    return entries


_SERIES_SERVER_FIELDS = frozenset(
    {
        "created",
        "creator",
        "etag",
        "hangoutLink",
        "htmlLink",
        "iCalUID",
        "id",
        "organizer",
        "sequence",
        "updated",
    }
)


def _truncate_recurrence(recurrence: List[str], split_at: EventBoundary) -> List[str]:
    """Return ``recurrence`` with every RRULE ending just before ``split_at``."""
    if split_at.is_all_day:
        until = (split_at.local_date - datetime.timedelta(days=1)).strftime("%Y%m%d")
    else:
        last_kept = split_at.moment - datetime.timedelta(seconds=1)
        until = last_kept.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    truncated = []
    for line in recurrence:
        if line.upper().startswith("RRULE:"):
            parts = [
                part
                for part in line[len("RRULE:") :].split(";")
                if not part.upper().startswith(("COUNT=", "UNTIL="))
            ]
            line = f"RRULE:{';'.join(parts + [f'UNTIL={until}'])}"
        truncated.append(line)
    return truncated


async def _split_recurring_event(
    service,
    calendar_id: str,
    instance_id: str,
    changes: Dict[str, Any],
    send_updates: str,
) -> Dict[str, Any]:
    """Apply ``changes`` to a recurring instance and all later ones; return the new series.

    Ends the original series before the instance, then inserts a copy starting at it,
    restoring the original recurrence if the insert fails.
    """
    events = service.events()
    instance = await asyncio.to_thread(
        lambda: events.get(calendarId=calendar_id, eventId=instance_id).execute()
    )
    series_id = instance.get("recurringEventId")
    split_at = parse_event_boundary(instance, "originalStartTime")
    if not series_id or split_at is None or instance.get("status") == "cancelled":
        raise UserInputError(
            f"Event '{instance_id}' is not an active instance of a recurring series. "
            "Pass a recurring instance ID from get_events."
        )
    series = await asyncio.to_thread(
        lambda: events.get(calendarId=calendar_id, eventId=series_id).execute()
    )
    first = parse_event_boundary(series, "start")
    last = parse_event_boundary(series, "end")
    if first is None or last is None:
        raise Exception(f"Series '{series_id}' has no readable start or end.")
    if (first.moment or first.local_date) == (split_at.moment or split_at.local_date):
        raise UserInputError(
            f"Event '{instance_id}' is the first instance of its series. Update the "
            f"series '{series_id}' directly instead."
        )
    recurrence = series.get("recurrence", [])
    upper_rules = [r.upper() for r in recurrence]
    if any(r.startswith("RDATE") for r in upper_rules):
        raise UserInputError(
            "This series has RDATE entries, which cannot be split. Update the series "
            "directly instead."
        )
    if "recurrence" not in changes and any("COUNT=" in r for r in upper_rules):
        raise UserInputError(
            "This series ends after a fixed COUNT. Pass recurrence for the new series."
        )

    if split_at.is_all_day:
        span = last.local_date - first.local_date
        end = {"date": (split_at.local_date + span).isoformat()}
    else:
        span = last.moment - first.moment
        end = {**series["end"], "dateTime": (split_at.moment + span).isoformat()}
    new_series = {k: v for k, v in series.items() if k not in _SERIES_SERVER_FIELDS}
    new_series.update(
        {
            "id": uuid.uuid4().hex,
            "start": instance["originalStartTime"],
            "end": end,
            **changes,
        }
    )
    for key in ("start", "end"):
        zone = series[key].get("timeZone")
        if zone and "dateTime" in new_series[key]:
            new_series[key] = {"timeZone": zone, **new_series[key]}

    def set_recurrence(rules: List[str]) -> None:
        events.patch(
            calendarId=calendar_id,
            eventId=series_id,
            body={"recurrence": rules},
            sendUpdates=send_updates,
        ).execute()

    await asyncio.to_thread(set_recurrence, _truncate_recurrence(recurrence, split_at))
    try:
        return await asyncio.to_thread(
            lambda: events.insert(
                calendarId=calendar_id,
                body=new_series,
                supportsAttachments=True,
                conferenceDataVersion=1,
                sendUpdates=send_updates,
            ).execute()
        )
    except Exception:
        with contextlib.suppress(Exception):
            return await asyncio.to_thread(
                lambda: events.get(
                    calendarId=calendar_id, eventId=new_series["id"]
                ).execute()
            )
        try:
            await asyncio.to_thread(set_recurrence, recurrence)
        except Exception as restore_error:
            raise Exception(
                f"Could not create the new series or restore series '{series_id}', "
                f"which now ends before instance '{instance_id}'. Restore its "
                f"recurrence to {recurrence}."
            ) from restore_error
        raise


def _normalize_attendees(
    attendees: Optional[List[Union[str, Dict[str, Any]]]],
) -> Optional[List[Dict[str, Any]]]:
    """
    Normalize attendees input to list of attendee objects.

    Accepts either:
    - List of email strings: ["user@example.com", "other@example.com"]
    - List of attendee objects: [{"email": "user@example.com", "responseStatus": "accepted"}]
    - Mixed list of both formats

    Returns list of attendee dicts with at minimum 'email' key.

    Raises:
        UserInputError: If an attendee is neither an email string nor a
            dict with an 'email' key, so it is never silently left off the event.
    """
    if attendees is None:
        return None

    normalized = []
    for att in attendees:
        if isinstance(att, str):
            normalized.append({"email": att})
        elif isinstance(att, dict) and "email" in att:
            normalized.append(att)
        else:
            raise UserInputError(
                "Each attendee must be an email string or an object with an "
                f"'email' key; got {type(att).__name__}"
            )
    return normalized if normalized else None


def _status_event_time_entry(
    time_str: str, event_label: str, timezone: Optional[str] = None
) -> Dict[str, str]:
    """Build a start/end dict for an Out of Office or Focus Time event.

    Google Calendar API requires dateTime (not date) for these event types, so a
    date-only string (YYYY-MM-DD) converts to midnight on that day. Callers pass
    an exclusive end date, so the same conversion serves both boundaries.
    """
    if "T" not in time_str:
        time_str = f"{time_str}T00:00:00"
        logger.info(
            f"[status_event_time_entry] Converted date-only to dateTime: {time_str}"
        )

    has_explicit_offset = time_str.endswith("Z") or bool(
        re.search(r"[+-]\d{2}:\d{2}$", time_str)
    )
    if not has_explicit_offset and not timezone:
        raise UserInputError(
            f"{event_label} events require either a timezone parameter or a "
            "start/end timestamp with an explicit UTC offset."
        )

    entry: Dict[str, str] = {"dateTime": time_str}
    if timezone:
        entry["timeZone"] = timezone
    return entry


def _validate_chat_status(
    chat_status: Optional[str], function_name: str
) -> Optional[str]:
    """Validate chat status for Focus Time events."""
    if chat_status is None:
        return None
    if chat_status not in _VALID_FOCUS_TIME_CHAT_STATUSES:
        raise UserInputError(
            f"[{function_name}] Invalid chat_status '{chat_status}'. "
            f"Must be one of: {', '.join(sorted(_VALID_FOCUS_TIME_CHAT_STATUSES))}"
        )
    return chat_status
