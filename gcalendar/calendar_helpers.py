"""
Google Calendar Helper Functions

This module provides utility functions for formatting Google Calendar
event data for display.
"""

import asyncio
import datetime
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from googleapiclient.errors import HttpError

from auth.multi_account import (
    AccountService,
    format_account_errors,
    gather_per_account,
)

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


# ---------------------------------------------------------------------------
# Several calendars (and accounts) at once: get_events and query_freebusy with
# user_google_email="all" or calendar_id="all".
# ---------------------------------------------------------------------------

ALL_CALENDARS = "all"
FREEBUSY_MAX_CALENDARS = 50
_UTC = datetime.timezone.utc


def is_all_calendars(value: Any) -> bool:
    """True for calendar_id="all" (any case)."""
    return isinstance(value, str) and value.strip().lower() == ALL_CALENDARS


@dataclass
class CalendarEventEntry:
    """One event as found on one account's calendar."""

    item: Dict[str, Any]
    account: str
    calendar_id: str
    calendar_name: str
    calendar_time_zone: Optional[str] = None


@dataclass
class MergedEvent:
    """One event, with every account/calendar it was found on."""

    item: Dict[str, Any]
    sources: List[CalendarEventEntry]


@dataclass
class AccountEvents:
    """What one account's calendars returned."""

    account: str
    entries: List[CalendarEventEntry]
    truncated: List[str]
    calendar_errors: List[str]
    calendars_read: int = 0


async def list_calendars_to_read(
    service, selection: Optional[str]
) -> List[Dict[str, str]]:
    """Resolve a calendar selection to the calendars to read.

    None means the calendars the account owns or fully manages (accessRole
    "owner"): its primary plus any it created. Calendars it can only read,
    such as a holiday calendar or most shared ones, are left out, since their
    events are not the user's time. "all" means every calendar in the
    account's list. Anything else is one calendar ID.
    """
    if selection is not None and not is_all_calendars(selection):
        return [{"id": selection, "name": selection}]
    calendars: List[Dict[str, str]] = []
    params: Dict[str, Any] = {}
    while True:
        response = await asyncio.to_thread(
            service.calendarList().list(**params).execute
        )
        for cal in response.get("items", []):
            if selection is None and cal.get("accessRole") != "owner":
                continue
            name = "primary" if cal.get("primary") else cal.get("summary") or cal["id"]
            calendars.append({"id": cal["id"], "name": name})
        if not response.get("nextPageToken"):
            return calendars
        params = {"pageToken": response["nextPageToken"]}


def _source_label(account: str, cal_id: str, name: str) -> str:
    if name in (cal_id, "primary"):
        return f"{account} / {name}"
    return f'{account} / "{name}" ({cal_id})'


async def fetch_account_events(
    service,
    account: str,
    selection: Optional[str],
    request_params: Dict[str, Any],
) -> AccountEvents:
    """Read one page of events from each selected calendar of one account.

    Calendars are read one at a time: they share the account's HTTP
    connection. A calendar the API refuses is reported in ``calendar_errors``
    and the others are still returned.
    """
    result = AccountEvents(
        account=account, entries=[], truncated=[], calendar_errors=[]
    )
    for cal in await list_calendars_to_read(service, selection):
        label = _source_label(account, cal["id"], cal["name"])
        try:
            response = await asyncio.to_thread(
                service.events().list(calendarId=cal["id"], **request_params).execute
            )
        except HttpError as e:
            logger.warning(f"[get_events] {label} failed: {e}")
            result.calendar_errors.append(f"{label}: {e}")
            continue
        for item in response.get("items", []):
            result.entries.append(
                CalendarEventEntry(
                    item=item,
                    account=account,
                    calendar_id=cal["id"],
                    calendar_name=cal["name"],
                    calendar_time_zone=response.get("timeZone"),
                )
            )
        result.calendars_read += 1
        if response.get("nextPageToken"):
            result.truncated.append(label)
    return result


def event_sort_key(entry: CalendarEventEntry):
    """Chronological key across calendars in different zones.

    Timed events sort by their instant. All-day events sort at the start of
    their date in the calendar's own zone, ahead of timed events at that same
    instant.
    """
    start = parse_event_boundary(entry.item, "start")
    if start is None:
        return (datetime.datetime.max.replace(tzinfo=_UTC), 2)
    if start.moment is not None:
        return (start.moment.astimezone(_UTC), 1)
    zone = _resolve_zone({"timeZone": entry.calendar_time_zone}) or _UTC
    midnight = datetime.datetime.combine(start.local_date, datetime.time.min, zone)
    return (midnight.astimezone(_UTC), 0)


def _instant_key(item: Dict[str, Any]) -> str:
    start = parse_event_boundary(item, "start")
    if start is None:
        return json.dumps(item.get("start"), sort_keys=True)
    if start.moment is not None:
        return start.moment.astimezone(_UTC).isoformat()
    return start.raw


def merge_calendar_events(entries: List[CalendarEventEntry]) -> List[MergedEvent]:
    """Merge events from several calendars, earliest first.

    An event on more than one calendar (an invitation sent to two of the
    user's accounts, or a calendar shared between them) is listed once, with
    every place it was found. Occurrences of a recurring series stay separate:
    they share an iCalUID but not a start.
    """
    merged: Dict[tuple, MergedEvent] = {}
    order: Dict[tuple, Any] = {}
    for entry in entries:
        key = (
            entry.item.get("iCalUID") or entry.item.get("id"),
            _instant_key(entry.item),
        )
        if key in merged:
            merged[key].sources.append(entry)
        else:
            merged[key] = MergedEvent(item=entry.item, sources=[entry])
            order[key] = event_sort_key(entry)
    return [merged[key] for key in sorted(merged, key=lambda k: order[k])]


def _attendance_note(event: MergedEvent) -> str:
    """Flag an event that takes none of the user's time on any copy of it."""
    items = [source.item for source in event.sources]
    declined = all(
        any(
            a.get("self") and a.get("responseStatus") == "declined"
            for a in item.get("attendees", [])
        )
        for item in items
    )
    free = all(item.get("transparency") == "transparent" for item in items)
    notes = [
        note for note, on in (("declined", declined), ("shows as free", free)) if on
    ]
    return f" ({', '.join(notes)})" if notes else ""


def format_merged_events(
    events: List[MergedEvent],
    detailed: bool = False,
    include_attachments: bool = False,
) -> List[str]:
    """One block per event, tagged with the account and calendar it is on.

    The first source is the one to pass back (as user_google_email and
    calendar_id) to act on the event.
    """
    lines = []
    for event in events:
        item = event.item
        first, others = event.sources[0], event.sources[1:]
        line = (
            f'- "{item.get("summary", "No Title")}"{_attendance_note(event)} '
            f"(Starts: {_format_event_time(item, 'start')}, "
            f"Ends: {_format_event_time(item, 'end')})\n"
            f"  Account / calendar: "
            f"{_source_label(first.account, first.calendar_id, first.calendar_name)}"
        )
        if others:
            line += "\n  Also on: " + "; ".join(
                _source_label(s.account, s.calendar_id, s.calendar_name) for s in others
            )
        if detailed:
            line += "\n" + _format_event_detail_lines(
                item,
                prefix="  ",
                indent="    ",
                include_attachments=include_attachments,
            ).rstrip("\n")
        else:
            meeting_link = _get_meeting_link(item)
            if meeting_link:
                line += f"\n  Meeting: {meeting_link}"
        line += f"\n  ID: {item.get('id', 'No ID')} | Link: {item.get('htmlLink', 'No Link')}"
        lines.append(line)
    return lines


def _describe_selection(selection: Optional[str]) -> str:
    if selection is None:
        return "the own calendars"
    if is_all_calendars(selection):
        return "every calendar"
    return f"calendar '{selection}'"


async def get_events_across(
    accounts: List[AccountService],
    selection: Optional[str],
    request_params: Dict[str, Any],
    max_results: int,
    detailed: bool,
    include_attachments: bool,
    single_account: bool = False,
) -> str:
    """get_events over several calendars and/or accounts, merged by start.

    ``request_params`` are events.list parameters without ``calendarId``. With
    ``single_account`` an API failure propagates to the tool's error handling
    instead of being reported as an excluded account.
    """

    async def _fetch(account: AccountService) -> AccountEvents:
        return await fetch_account_events(
            account.service, account.email, selection, request_params
        )

    if single_account:
        results: List[Any] = [await _fetch(accounts[0])]
    else:
        results = await gather_per_account(accounts, _fetch)
    read = [r for r in results if isinstance(r, AccountEvents)]
    merged = merge_calendar_events([e for r in read for e in r.entries])
    shown = merged[:max_results]

    where = f"{_describe_selection(selection)} of " + (
        ", ".join(r.account for r in read) or "no account"
    )
    calendar_errors = [e for r in read for e in r.calendar_errors]
    incomplete = bool(calendar_errors) or len(read) < len(results)
    read_any = any(r.entries or r.calendars_read for r in read)
    if shown:
        lines = [f"Retrieved {len(shown)} events from {where}, sorted by start time:"]
        lines.extend(format_merged_events(shown, detailed, include_attachments))
    elif not read_any:
        lines = ["Could not read any calendar, so this says nothing about free time."]
    elif incomplete:
        lines = [
            f"No events found in {where} that could be read (some calendars "
            "could not, see below)."
        ]
    else:
        lines = [f"No events found in {where} for the specified time range."]

    if len(merged) > max_results or any(r.truncated for r in read):
        lines.append(
            "\nMore events exist in this range than max_results allows; "
            "raise max_results or narrow time_min/time_max."
        )
    if calendar_errors:
        lines.append("\nCalendars not included:")
        lines.extend(f"- {e}" for e in calendar_errors)
    lines.extend(format_account_errors(results))
    return "\n".join(lines)


def merge_busy_periods(
    periods: List[Dict[str, str]],
) -> List[Tuple[datetime.datetime, datetime.datetime]]:
    """Union of free/busy periods as sorted, non-overlapping UTC (start, end) pairs."""
    spans = []
    for period in periods:
        try:
            start = datetime.datetime.fromisoformat(
                re.sub(r"[zZ]$", "+00:00", period.get("start", ""))
            )
            end = datetime.datetime.fromisoformat(
                re.sub(r"[zZ]$", "+00:00", period.get("end", ""))
            )
        except (TypeError, ValueError):
            continue
        if start.tzinfo and end.tzinfo and end > start:
            spans.append((start.astimezone(_UTC), end.astimezone(_UTC)))
    spans.sort()
    merged: List[List[datetime.datetime]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _utc(moment: datetime.datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


async def _account_freebusy(
    service,
    selection: Optional[str],
    calendar_ids: Optional[List[str]],
    body: Dict[str, Any],
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """(calendar ID, name, freebusy entry) for one account's calendars."""
    if calendar_ids:
        calendars = [{"id": cal_id, "name": cal_id} for cal_id in calendar_ids]
    else:
        calendars = await list_calendars_to_read(service, selection)
    found = []
    # freebusy.query takes at most 50 calendars per request.
    for start in range(0, len(calendars), FREEBUSY_MAX_CALENDARS):
        chunk = calendars[start : start + FREEBUSY_MAX_CALENDARS]
        response = await asyncio.to_thread(
            service.freebusy()
            .query(body={**body, "items": [{"id": c["id"]} for c in chunk]})
            .execute
        )
        result = response.get("calendars", {})
        for cal in chunk:
            missing = {"errors": [{"domain": "calendar", "reason": "notReturned"}]}
            found.append((cal["id"], cal["name"], result.get(cal["id"], missing)))
    return found


async def query_freebusy_across(
    accounts: List[AccountService],
    selection: Optional[str],
    calendar_ids: Optional[List[str]],
    body: Dict[str, Any],
    single_account: bool = False,
) -> str:
    """query_freebusy over several calendars and/or accounts.

    ``body`` is the freebusy.query body without ``items``. The combined busy
    list only claims "free" when every calendar asked about was read.
    """

    async def _fetch(account: AccountService):
        found = await _account_freebusy(account.service, selection, calendar_ids, body)
        return account.email, found

    if single_account:
        results: List[Any] = [await _fetch(accounts[0])]
    else:
        results = await gather_per_account(accounts, _fetch)
    read = [r for r in results if not isinstance(r, AccountService)]

    busy: List[Dict[str, str]] = []
    per_calendar: List[str] = []
    incomplete = len(read) < len(results)
    for email, found in read:
        for cal_id, name, entry in found:
            label = _source_label(email, cal_id, name)
            errors = entry.get("errors", [])
            if errors:
                incomplete = True
                reasons = ", ".join(
                    f"{e.get('domain', 'unknown')}: {e.get('reason', 'unknown')}"
                    for e in errors
                )
                per_calendar.append(f"- {label}: not read ({reasons})")
                continue
            periods = entry.get("busy", [])
            busy.extend(periods)
            if periods:
                spans = ", ".join(
                    f"{p.get('start')} to {p.get('end')}" for p in periods
                )
                per_calendar.append(f"- {label}: busy {spans}")
            else:
                per_calendar.append(f"- {label}: free")

    combined = merge_busy_periods(busy)
    accounts_read = ", ".join(email for email, _ in read) or "no account"
    what = (
        f"calendars {', '.join(calendar_ids)}"
        if calendar_ids
        else _describe_selection(selection)
    )
    lines = [
        f"Free/Busy for {what} of {accounts_read}:",
        f"Time range: {body.get('timeMin')} to {body.get('timeMax')}",
        "",
    ]
    if not per_calendar:
        lines.append(
            "Could not read any calendar, so this says nothing about free time."
        )
    else:
        heading = (
            "Busy on the calendars that could be read (some could not, see below)"
            if incomplete
            else "Busy on any of these calendars"
        )
        lines.append(f"{heading}, combined (UTC):")
        if combined:
            lines.extend(f"- {_utc(s)} to {_utc(e)}" for s, e in combined)
        elif incomplete:
            lines.append("- none found")
        else:
            lines.append("- none: free on every calendar")
        lines.extend(["", "Per calendar:"])
        lines.extend(per_calendar)
    lines.extend(format_account_errors(results))
    return "\n".join(lines)
