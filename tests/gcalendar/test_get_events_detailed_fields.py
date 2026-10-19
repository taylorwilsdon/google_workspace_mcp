"""
Unit tests for get_events detailed output field parity.

The single-event branch (event_id + detailed) and the ranged branch
(time_min/time_max + detailed) used to format their output separately, so they
drifted: colorId was emitted only for single-event lookups, and
recurringEventId, eventType and status by neither. A ranged query could not be
used to audit event colours or resolve a recurring series parent.

Both branches now render through _format_event_detail_lines. Every test below
runs against both, so re-inlining either one — or adding a field to only one —
fails here.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_helpers import _format_event_time
from gcalendar.calendar_tools import get_events


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _mock_service(items):
    mock_service = Mock()
    mock_service.events().list().execute = Mock(return_value={"items": items})
    mock_service.events().get().execute = Mock(return_value=items[0])
    return mock_service


async def _ranged_detail(item, *, single_events=True):
    """Detailed output via the time_min/time_max branch.

    Also asserts the forwarded request matches what was asked for, so tests
    using this helper check the actual events.list() call, not just the
    formatted text it produced.
    """
    service = _mock_service([item])
    result = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        time_min="2026-04-06T00:00:00Z",
        time_max="2026-04-07T00:00:00Z",
        detailed=True,
        single_events=single_events,
    )

    params = service.events().list.call_args.kwargs
    assert params["singleEvents"] is single_events
    if single_events:
        assert params["orderBy"] == "startTime"
    else:
        assert "orderBy" not in params

    return result


async def _single_detail(item):
    """Detailed output via the event_id branch."""
    return await _unwrap(get_events)(
        service=_mock_service([item]),
        user_google_email="user@example.com",
        event_id=item["id"],
        detailed=True,
    )


async def _ranged_basic(item):
    """Basic output via the time_min/time_max branch."""
    return await _unwrap(get_events)(
        service=_mock_service([item]),
        user_google_email="user@example.com",
        time_min="2026-04-06T00:00:00Z",
        time_max="2026-04-07T00:00:00Z",
        detailed=False,
    )


async def _single_basic(item):
    """Basic output via the event_id branch."""
    return await _unwrap(get_events)(
        service=_mock_service([item]),
        user_google_email="user@example.com",
        event_id=item["id"],
        detailed=False,
    )


# Every test runs through both formatters. Their disagreement was the bug.
both_branches = pytest.mark.parametrize(
    "detail", [_ranged_detail, _single_detail], ids=["ranged", "single"]
)
all_get_paths = pytest.mark.parametrize(
    "read",
    [_ranged_basic, _single_basic, _ranged_detail, _single_detail],
    ids=["ranged-basic", "single-basic", "ranged-detailed", "single-detailed"],
)

RECURRING_INSTANCE = {
    "id": "evt123_20260406T090000Z",
    "summary": "Standup",
    "start": {"dateTime": "2026-04-06T09:00:00Z"},
    "end": {"dateTime": "2026-04-06T09:15:00Z"},
    "htmlLink": "https://calendar.google.com/event?eid=evt123",
    "colorId": "8",
    "recurringEventId": "evt123",
    "status": "confirmed",
}

RECURRING_SERIES = {
    "id": "evt123",
    "summary": "Standup",
    "start": {
        "dateTime": "2026-04-06T09:00:00Z",
        "timeZone": "Europe/London",
    },
    "end": {
        "dateTime": "2026-04-06T09:15:00Z",
        "timeZone": "Europe/London",
    },
    "htmlLink": "https://calendar.google.com/event?eid=evt123",
    "recurrence": [
        "RRULE:FREQ=WEEKLY;INTERVAL=4;BYDAY=MO",
        "EXDATE:20260504T090000Z",
    ],
    "status": "confirmed",
}

# Every optional field set to a value that renders, so parity can be asserted
# on all four at once. RECURRING_INSTANCE deliberately can't do that: its
# eventType is absent and its status is the suppressed default.
ALL_FIELDS_INSTANCE = {
    "id": "ooo1",
    "summary": "Out of office",
    "start": {"date": "2026-04-06"},
    "end": {"date": "2026-04-07"},
    "htmlLink": "https://calendar.google.com/event?eid=ooo1",
    "colorId": "5",
    "recurringEventId": "ooo",
    "eventType": "outOfOffice",
    "status": "tentative",
}

ORDINARY_MEETING = {
    "id": "evt1",
    "summary": "One-off",
    "start": {"dateTime": "2026-04-06T09:00:00Z"},
    "end": {"dateTime": "2026-04-06T09:15:00Z"},
    "htmlLink": "https://calendar.google.com/event?eid=evt1",
    "eventType": "default",
    "status": "confirmed",
}

CANCELLED_RECURRING_EXCEPTION = {
    "id": "evt123_20260420T090000Z",
    "status": "cancelled",
    "recurringEventId": "evt123",
    "originalStartTime": {
        "dateTime": "2026-04-20T11:00:00+02:00",
        "timeZone": "Europe/Paris",
    },
}


@pytest.mark.asyncio
@both_branches
@pytest.mark.parametrize(
    "item,line",
    [
        (RECURRING_INSTANCE, "Color ID: 8"),
        (RECURRING_INSTANCE, "Recurring Event ID: evt123"),
        (ALL_FIELDS_INSTANCE, "Color ID: 5"),
        (ALL_FIELDS_INSTANCE, "Recurring Event ID: ooo"),
        (ALL_FIELDS_INSTANCE, "Event Type: outOfOffice"),
        (ALL_FIELDS_INSTANCE, "Status: tentative"),
    ],
)
async def test_detailed_output_emits_event_metadata(detail, item, line):
    """All four fields must reach the output, from either branch.

    colorId lets a date range be audited for colour without a per-event lookup;
    recurringEventId is what you need to edit a series rather than one instance.
    """
    assert line in await detail(item)


@pytest.mark.asyncio
@both_branches
async def test_non_default_event_type_is_surfaced(detail):
    """workingLocation/outOfOffice events are indistinguishable without eventType."""
    result = await detail(
        {
            "id": "wl1",
            "summary": "Home",
            "start": {"date": "2026-04-06"},
            "end": {"date": "2026-04-07"},
            "htmlLink": "https://calendar.google.com/event?eid=wl1",
            "eventType": "workingLocation",
        }
    )

    assert "Event Type: workingLocation" in result


@pytest.mark.asyncio
@both_branches
@pytest.mark.parametrize("status", ["cancelled", "tentative"])
async def test_non_confirmed_status_is_surfaced(detail, status):
    """Only 'confirmed' is suppressed — other statuses must remain visible.

    Guards the omission test below: a regression that dropped every status,
    rather than just the default one, would otherwise go unnoticed.
    """
    result = await detail({**RECURRING_INSTANCE, "status": status})

    assert f"Status: {status}" in result


@pytest.mark.asyncio
@both_branches
async def test_default_event_type_and_confirmed_status_are_omitted(detail):
    """Only non-default values are emitted, to keep output compact."""
    result = await detail(ORDINARY_MEETING)

    assert "Event Type:" not in result
    assert "Status:" not in result
    assert "Recurring Event ID:" not in result


@pytest.mark.asyncio
@both_branches
async def test_missing_color_id_renders_as_none(detail):
    """colorId always renders, defaulting to the string 'None' on both branches."""
    result = await detail({k: v for k, v in ORDINARY_MEETING.items() if k != "colorId"})

    assert "Color ID: None" in result


@pytest.mark.asyncio
async def test_basic_ranged_output_is_unchanged():
    """detailed=False output must stay compact — no new fields leak into it."""
    result = await _unwrap(get_events)(
        service=_mock_service([RECURRING_INSTANCE]),
        user_google_email="user@example.com",
        time_min="2026-04-06T00:00:00Z",
        time_max="2026-04-07T00:00:00Z",
        detailed=False,
    )

    assert "Color ID" not in result
    assert "Recurring Event ID" not in result


@pytest.mark.asyncio
@both_branches
async def test_detailed_recurring_master_emits_lossless_recurrence(detail):
    """Detailed output preserves every recurrence line from a series master."""
    result = await detail(RECURRING_SERIES)

    assert (
        'Recurrence: ["RRULE:FREQ=WEEKLY;INTERVAL=4;BYDAY=MO", '
        '"EXDATE:20260504T090000Z"]' in result
    )


@pytest.mark.asyncio
async def test_cancelled_recurring_exception_uses_original_start_time():
    """Sparse Google tombstones render as exclusions instead of crashing."""
    result = await _single_detail(CANCELLED_RECURRING_EXCEPTION)

    assert "Starts: 2026-04-20T11:00:00+02:00" in result
    assert "Ends: Unavailable" in result
    assert (
        'Original Start Time: {"dateTime": "2026-04-20T11:00:00+02:00", '
        '"timeZone": "Europe/Paris"}' in result
    )
    assert "Recurring Event ID: evt123" in result
    assert "Status: cancelled" in result


@pytest.mark.asyncio
async def test_ranged_cancelled_exception_uses_original_start_time():
    """Unexpanded ranges render sparse cancelled exceptions."""
    result = await _ranged_detail(CANCELLED_RECURRING_EXCEPTION, single_events=False)

    assert "Starts: 2026-04-20T11:00:00+02:00" in result
    assert "Ends: Unavailable" in result
    assert (
        'Original Start Time: {"dateTime": "2026-04-20T11:00:00+02:00", '
        '"timeZone": "Europe/Paris"}' in result
    )
    assert "Recurring Event ID: evt123" in result
    assert "Status: cancelled" in result


@pytest.mark.asyncio
async def test_all_day_cancelled_exception_uses_original_date():
    """All-day exclusions use originalStartTime.date as their start boundary."""
    result = await _ranged_detail(
        {
            **CANCELLED_RECURRING_EXCEPTION,
            "originalStartTime": {"date": "2026-04-20"},
        },
        single_events=False,
    )

    assert "Starts: 2026-04-20" in result
    assert "Ends: Unavailable" in result
    assert 'Original Start Time: {"date": "2026-04-20"}' in result


@pytest.mark.asyncio
async def test_get_events_can_return_unexpanded_recurring_masters():
    """Unexpanded requests reach Google without start-time ordering."""
    service = _mock_service([RECURRING_SERIES])

    await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        time_min="2026-04-01T00:00:00Z",
        time_max="2026-05-01T00:00:00Z",
        detailed=True,
        single_events=False,
    )

    params = service.events().list.call_args.kwargs
    assert params["singleEvents"] is False
    assert "orderBy" not in params


@pytest.mark.asyncio
async def test_unexpanded_query_without_time_min_keeps_older_masters_discoverable():
    """Do not filter masters by their first occurrence when no range was requested."""
    older_series = {
        **RECURRING_SERIES,
        "start": {"dateTime": "2020-01-06T09:00:00Z"},
        "end": {"dateTime": "2020-01-06T09:15:00Z"},
        "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
    }
    service = _mock_service([older_series])

    await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        detailed=True,
        single_events=False,
    )

    params = service.events().list.call_args.kwargs
    assert "timeMin" not in params


@pytest.mark.asyncio
async def test_get_events_expands_recurring_series_by_default():
    """The default request remains expanded and chronologically ordered."""
    service = _mock_service([RECURRING_INSTANCE])

    await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        time_min="2026-04-01T00:00:00Z",
        time_max="2026-05-01T00:00:00Z",
    )

    params = service.events().list.call_args.kwargs
    assert params["singleEvents"] is True
    assert params["orderBy"] == "startTime"


@pytest.mark.asyncio
@all_get_paths
@pytest.mark.parametrize(
    "item,start_evidence,end_evidence",
    [
        (
            {
                **ORDINARY_MEETING,
                "start": {"dateTime": "2026-08-20T17:15:00+02:00"},
                "end": {"dateTime": "2026-08-20T18:00:00+02:00"},
            },
            "2026-08-20T17:15:00+02:00 [weekday: Thursday; ISO weekday: 4]",
            "2026-08-20T18:00:00+02:00 [weekday: Thursday; ISO weekday: 4]",
        ),
        (
            {
                **ORDINARY_MEETING,
                "start": {"dateTime": "2026-08-20T00:15:00+14:00"},
                "end": {"dateTime": "2026-08-19T23:45:00-10:00"},
            },
            "2026-08-20T00:15:00+14:00 [weekday: Thursday; ISO weekday: 4]",
            "2026-08-19T23:45:00-10:00 [weekday: Wednesday; ISO weekday: 3]",
        ),
        (
            {
                **ORDINARY_MEETING,
                "start": {"dateTime": "2026-03-29T01:30:00+01:00"},
                "end": {"dateTime": "2026-03-29T03:30:00+02:00"},
            },
            "2026-03-29T01:30:00+01:00 [weekday: Sunday; ISO weekday: 7]",
            "2026-03-29T03:30:00+02:00 [weekday: Sunday; ISO weekday: 7]",
        ),
        (
            {
                **ORDINARY_MEETING,
                "start": {"dateTime": "2026-08-20T23:30:00Z"},
                "end": {"dateTime": "2026-08-21T00:30:00Z"},
            },
            "2026-08-20T23:30:00Z [weekday: Thursday; ISO weekday: 4]",
            "2026-08-21T00:30:00Z [weekday: Friday; ISO weekday: 5]",
        ),
        (
            {
                **ORDINARY_MEETING,
                "start": {"dateTime": "2026-08-20T17:15:00"},
                "end": {"dateTime": "2026-08-20T18:00:00"},
            },
            "2026-08-20T17:15:00",
            "2026-08-20T18:00:00",
        ),
        (
            {
                **ALL_FIELDS_INSTANCE,
                "start": {"date": "2026-08-20"},
                "end": {"date": "2026-08-22"},
            },
            "2026-08-20 [weekday: Thursday; ISO weekday: 4]",
            "2026-08-22 [weekday: Saturday; ISO weekday: 6; exclusive all-day end]",
        ),
    ],
    ids=["timed", "offset-crossing", "dst-offsets", "z", "offset-less", "all-day"],
)
async def test_get_events_always_retains_raw_times_with_deterministic_weekdays(
    read, item, start_evidence, end_evidence
):
    result = await read(item)

    assert f"Starts: {start_evidence}" in result
    assert f"Ends: {end_evidence}" in result


@pytest.mark.parametrize(
    "boundary",
    [
        {"dateTime": "20260820T171500+0200"},
        {"dateTime": "2026-08-20T17:15:00+02"},
        {"dateTime": "2026-08-20T17:15:00+02:00:30"},
        {"dateTime": "2026-08-20T17:15:00,5+02:00"},
        {"dateTime": "2026-08-20T24:00:00+02:00"},
        {"dateTime": "2026-08-20T17:15:00+02:60"},
        {"dateTime": "2026-08-20"},
        {"date": "2026-08-20T17:15:00+02:00"},
        {"date": "20260820"},
        {"date": "2026-W34-4"},
    ],
    ids=[
        "basic-datetime",
        "short-offset",
        "offset-seconds",
        "comma-fraction",
        "out-of-range-hour",
        "out-of-range-offset-minute",
        "date-in-datetime-field",
        "datetime-in-date-field",
        "compact-date",
        "week-date",
    ],
)
def test_format_event_time_preserves_non_google_formats(boundary):
    """Python's permissive ISO parser must not define Google's wire format."""
    value = next(iter(boundary.values()))

    assert _format_event_time({"start": boundary}, "start") == value
