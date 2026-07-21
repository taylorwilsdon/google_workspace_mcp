"""Integration-level tests for the include_reminders opt-in on get_events and
list_calendars. These exercise the real tool functions (unwrapped past their
auth/error decorators) against a mock Calendar service to verify the reminder
data is surfaced only when requested and placed in the output correctly.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import get_events, list_calendars


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


_EVENT_WITH_CUSTOM_REMINDERS = {
    "id": "evt123",
    "summary": "Standup",
    "start": {"dateTime": "2026-04-06T09:00:00Z"},
    "end": {"dateTime": "2026-04-06T09:15:00Z"},
    "htmlLink": "https://calendar.google.com/event?eid=evt123",
    "reminders": {
        "useDefault": False,
        "overrides": [{"method": "popup", "minutes": 10}],
    },
}


# ---------------------------------------------------------------------------
# get_events — single event by ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_events_single_includes_reminders_when_requested():
    service = Mock()
    service.events().get().execute = Mock(return_value=_EVENT_WITH_CUSTOM_REMINDERS)

    result = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        event_id="evt123",
        detailed=True,
        include_reminders=True,
    )

    assert "Reminders: popup 10 min before" in result


@pytest.mark.asyncio
async def test_get_events_single_omits_reminders_by_default():
    service = Mock()
    service.events().get().execute = Mock(return_value=_EVENT_WITH_CUSTOM_REMINDERS)

    result = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        event_id="evt123",
        detailed=True,
    )

    assert "Reminders:" not in result


# ---------------------------------------------------------------------------
# get_events — multiple events (list path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_events_list_includes_reminders_when_requested():
    default_event = {
        "id": "evt456",
        "summary": "1:1",
        "start": {"dateTime": "2026-04-07T09:00:00Z"},
        "end": {"dateTime": "2026-04-07T09:30:00Z"},
        "htmlLink": "https://calendar.google.com/event?eid=evt456",
        "reminders": {"useDefault": True},
    }
    service = Mock()
    service.events().list().execute = Mock(
        return_value={"items": [_EVENT_WITH_CUSTOM_REMINDERS, default_event]}
    )

    result = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        detailed=True,
        include_reminders=True,
    )

    assert "Reminders: popup 10 min before" in result
    assert "Reminders: Using calendar default reminders" in result


@pytest.mark.asyncio
async def test_get_events_list_omits_reminders_by_default():
    service = Mock()
    service.events().list().execute = Mock(
        return_value={"items": [_EVENT_WITH_CUSTOM_REMINDERS]}
    )

    result = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        detailed=True,
    )

    assert "Reminders:" not in result


# ---------------------------------------------------------------------------
# list_calendars — default reminders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_calendars_includes_default_reminders_when_requested():
    service = Mock()
    service.calendarList().list().execute = Mock(
        return_value={
            "items": [
                {
                    "id": "primary",
                    "summary": "Personal",
                    "primary": True,
                    "defaultReminders": [{"method": "popup", "minutes": 30}],
                }
            ]
        }
    )

    result = await _unwrap(list_calendars)(
        service=service,
        user_google_email="user@example.com",
        include_reminders=True,
    )

    assert "Default Reminders: popup 30 min before" in result


@pytest.mark.asyncio
async def test_list_calendars_omits_default_reminders_by_default():
    service = Mock()
    service.calendarList().list().execute = Mock(
        return_value={
            "items": [
                {
                    "id": "primary",
                    "summary": "Personal",
                    "primary": True,
                    "defaultReminders": [{"method": "popup", "minutes": 30}],
                }
            ]
        }
    )

    result = await _unwrap(list_calendars)(
        service=service,
        user_google_email="user@example.com",
    )

    assert "Default Reminders:" not in result
