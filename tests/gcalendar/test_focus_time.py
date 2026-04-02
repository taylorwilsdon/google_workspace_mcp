"""
Unit tests for Google Calendar Focus Time MCP tools.

Tests the manage_focus_time helpers with mocked API responses,
verifying the exact API payloads sent to Google Calendar.
"""

import os
import sys
from unittest.mock import Mock, MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import (
    _create_focus_time_event_impl,
    _delete_focus_time_event_impl,
    _focus_time_time_entry,
    _list_focus_time_events_impl,
    _update_focus_time_event_impl,
    _validate_chat_status,
)


def _create_mock_service():
    """Create a mock Calendar API service with explicit execute mocks."""
    mock_service = Mock()
    mock_service.events().insert().execute = Mock(return_value={})
    mock_service.events().list().execute = Mock(return_value={"items": []})
    mock_service.events().get().execute = Mock(return_value={})
    mock_service.events().patch().execute = Mock(return_value={})
    mock_service.events().delete().execute = Mock(return_value=None)
    return mock_service


class TestValidateChatStatus:
    def test_returns_none_when_none(self):
        assert _validate_chat_status(None, "test") is None

    def test_accepts_valid_values(self):
        assert _validate_chat_status("available", "test") == "available"
        assert _validate_chat_status("doNotDisturb", "test") == "doNotDisturb"

    def test_rejects_invalid_value(self):
        with pytest.raises(ValueError, match="Invalid chat_status"):
            _validate_chat_status("busy", "test")


class TestFocusTimeTimeEntry:
    def test_date_only_start_converts_to_midnight_when_timezone_provided(self):
        result = _focus_time_time_entry(
            "2026-04-05", is_end=False, timezone="America/New_York"
        )
        assert result == {
            "dateTime": "2026-04-05T00:00:00",
            "timeZone": "America/New_York",
        }

    def test_rejects_naive_datetime_without_timezone(self):
        with pytest.raises(ValueError, match="require either a timezone"):
            _focus_time_time_entry("2026-04-05T09:00:00", is_end=False, timezone=None)


@pytest.mark.asyncio
async def test_create_focus_time_defaults_to_do_not_disturb_and_supports_recurrence():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "focus123",
            "htmlLink": "https://calendar.google.com/event?eid=focus123",
            "summary": "Deep Work",
            "start": {"dateTime": "2026-04-05T09:00:00-04:00"},
            "end": {"dateTime": "2026-04-05T11:00:00-04:00"},
        }
    )

    result = await _create_focus_time_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-05T09:00:00-04:00",
        end_time="2026-04-05T11:00:00-04:00",
        summary="Deep Work",
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;COUNT=8"],
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    assert body["eventType"] == "focusTime"
    assert body["summary"] == "Deep Work"
    assert body["focusTimeProperties"]["autoDeclineMode"] == (
        "declineAllConflictingInvitations"
    )
    assert body["focusTimeProperties"]["chatStatus"] == "doNotDisturb"
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;COUNT=8"]
    assert call_args[1]["calendarId"] == "primary"
    assert "doNotDisturb" in result


@pytest.mark.asyncio
async def test_create_focus_time_accepts_custom_calendar_and_chat_status():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "focus234",
            "htmlLink": "https://calendar.google.com/event?eid=focus234",
            "summary": "Heads Down",
            "start": {"dateTime": "2026-04-05T09:00:00Z"},
            "end": {"dateTime": "2026-04-05T10:00:00Z"},
        }
    )

    await _create_focus_time_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-05T09:00:00Z",
        end_time="2026-04-05T10:00:00Z",
        calendar_id="user@example.com",
        chat_status="available",
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    assert call_args[1]["calendarId"] == "user@example.com"
    assert body["focusTimeProperties"]["chatStatus"] == "available"


@pytest.mark.asyncio
async def test_list_focus_time_passes_event_type_filter_and_expands_recurring_instances():
    mock_service = _create_mock_service()
    mock_service.events().list().execute = Mock(
        return_value={
            "items": [
                {
                    "id": "focus-instance-1",
                    "recurringEventId": "focus-series-1",
                    "summary": "Deep Work",
                    "start": {"dateTime": "2026-04-06T09:00:00-04:00"},
                    "end": {"dateTime": "2026-04-06T11:00:00-04:00"},
                    "focusTimeProperties": {
                        "autoDeclineMode": "declineOnlyNewConflictingInvitations",
                        "declineMessage": "In focus time",
                        "chatStatus": "doNotDisturb",
                    },
                }
            ]
        }
    )

    result = await _list_focus_time_events_impl(
        service=mock_service,
        user_google_email="user@example.com",
        calendar_id="user@example.com",
    )

    call_args = mock_service.events().list.call_args
    params = call_args[1]

    assert params["calendarId"] == "user@example.com"
    assert params["eventTypes"] == ["focusTime"]
    assert params["singleEvents"] is True
    assert params["orderBy"] == "startTime"
    assert "focus-instance-1" in result
    assert "In focus time" in result
    assert "doNotDisturb" in result


@pytest.mark.asyncio
async def test_update_focus_time_can_patch_recurrence_without_touching_focus_properties():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "focus123",
            "eventType": "focusTime",
            "summary": "Deep Work",
            "focusTimeProperties": {
                "autoDeclineMode": "declineAllConflictingInvitations",
                "declineMessage": "Heads down",
                "chatStatus": "doNotDisturb",
            },
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "focus123",
            "htmlLink": "https://calendar.google.com/event?eid=focus123",
            "summary": "Deep Work",
            "start": {"dateTime": "2026-04-05T09:00:00Z"},
            "end": {"dateTime": "2026-04-05T11:00:00Z"},
        }
    )

    await _update_focus_time_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="focus123",
        recurrence=["RRULE:FREQ=WEEKLY;COUNT=6"],
        calendar_id="user@example.com",
    )

    patch_call_args = mock_service.events().patch.call_args
    patch_body = patch_call_args[1]["body"]

    assert patch_call_args[1]["calendarId"] == "user@example.com"
    assert patch_call_args[1]["eventId"] == "focus123"
    assert patch_body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=6"]
    assert "focusTimeProperties" not in patch_body


@pytest.mark.asyncio
async def test_update_focus_time_preserves_existing_fields_when_only_chat_status_changes():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "focus123",
            "eventType": "focusTime",
            "focusTimeProperties": {
                "autoDeclineMode": "declineNone",
                "declineMessage": "Heads down",
                "chatStatus": "doNotDisturb",
            },
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "focus123",
            "htmlLink": "link",
            "summary": "Focus Time",
            "start": {"dateTime": "2026-04-05T09:00:00Z"},
            "end": {"dateTime": "2026-04-05T11:00:00Z"},
        }
    )

    await _update_focus_time_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="focus123",
        chat_status="available",
    )

    patch_body = mock_service.events().patch.call_args[1]["body"]
    props = patch_body["focusTimeProperties"]

    assert props["autoDeclineMode"] == "declineNone"
    assert props["declineMessage"] == "Heads down"
    assert props["chatStatus"] == "available"


@pytest.mark.asyncio
async def test_delete_focus_time_calls_delete_with_correct_ids():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={"id": "focus123", "eventType": "focusTime"}
    )

    result = await _delete_focus_time_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="focus123",
        calendar_id="user@example.com",
    )

    delete_call_args = mock_service.events().delete.call_args
    assert delete_call_args[1]["calendarId"] == "user@example.com"
    assert delete_call_args[1]["eventId"] == "focus123"
    assert "Successfully deleted" in result


@pytest.mark.asyncio
async def test_delete_focus_time_event_not_found_raises():
    from googleapiclient.errors import HttpError

    mock_service = _create_mock_service()
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_service.events().get().execute = Mock(
        side_effect=HttpError(resp=mock_resp, content=b"Not Found")
    )

    with pytest.raises(Exception, match="Event not found"):
        await _delete_focus_time_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="missing",
        )
