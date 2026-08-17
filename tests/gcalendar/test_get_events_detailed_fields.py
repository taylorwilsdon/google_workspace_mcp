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


async def _ranged_detail(item):
    """Detailed output via the time_min/time_max branch."""
    return await _unwrap(get_events)(
        service=_mock_service([item]),
        user_google_email="user@example.com",
        time_min="2026-04-06T00:00:00Z",
        time_max="2026-04-07T00:00:00Z",
        detailed=True,
    )


async def _single_detail(item):
    """Detailed output via the event_id branch."""
    return await _unwrap(get_events)(
        service=_mock_service([item]),
        user_google_email="user@example.com",
        event_id=item["id"],
        detailed=True,
    )


# Every test runs through both formatters. Their disagreement was the bug.
both_branches = pytest.mark.parametrize(
    "detail", [_ranged_detail, _single_detail], ids=["ranged", "single"]
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
