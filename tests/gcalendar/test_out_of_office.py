"""
Unit tests for Google Calendar Out of Office MCP tools

Tests the manage_out_of_office tool with mocked API responses,
verifying the exact API payloads sent to Google Calendar.
"""

import pytest
from unittest.mock import Mock, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import (
    _create_ooo_event_impl,
    _list_ooo_events_impl,
    _update_ooo_event_impl,
    _delete_ooo_event_impl,
    _validate_auto_decline_mode,
    _ooo_time_entry,
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


# ---------------------------------------------------------------------------
# _validate_auto_decline_mode
# ---------------------------------------------------------------------------


class TestValidateAutoDeclineMode:
    def test_returns_default_when_none(self):
        result = _validate_auto_decline_mode(None, "test")
        assert result == "declineAllConflictingInvitations"

    def test_accepts_valid_modes(self):
        for mode in [
            "declineAllConflictingInvitations",
            "declineOnlyNewConflictingInvitations",
            "declineNone",
        ]:
            assert _validate_auto_decline_mode(mode, "test") == mode

    def test_rejects_invalid_mode(self):
        with pytest.raises(ValueError, match="Invalid auto_decline_mode"):
            _validate_auto_decline_mode("invalidMode", "test")


# ---------------------------------------------------------------------------
# _ooo_time_entry — date-only to dateTime conversion
# ---------------------------------------------------------------------------


class TestOooTimeEntry:
    def test_date_only_start_converts_to_midnight_when_timezone_provided(self):
        result = _ooo_time_entry(
            "2026-04-05", is_end=False, timezone="America/New_York"
        )
        assert result == {
            "dateTime": "2026-04-05T00:00:00",
            "timeZone": "America/New_York",
        }

    def test_date_only_end_converts_to_midnight_when_timezone_provided(self):
        result = _ooo_time_entry("2026-04-06", is_end=True, timezone="America/New_York")
        assert result == {
            "dateTime": "2026-04-06T00:00:00",
            "timeZone": "America/New_York",
        }

    def test_datetime_passed_through_unchanged(self):
        result = _ooo_time_entry("2026-04-05T09:00:00Z", is_end=False)
        assert result == {"dateTime": "2026-04-05T09:00:00Z"}

    def test_timezone_added_when_provided(self):
        result = _ooo_time_entry(
            "2026-04-05", is_end=False, timezone="America/New_York"
        )
        assert result == {
            "dateTime": "2026-04-05T00:00:00",
            "timeZone": "America/New_York",
        }

    def test_timezone_added_to_datetime_input(self):
        result = _ooo_time_entry(
            "2026-04-05T09:00:00", is_end=False, timezone="Europe/London"
        )
        assert result == {
            "dateTime": "2026-04-05T09:00:00",
            "timeZone": "Europe/London",
        }

    def test_rejects_date_only_without_timezone(self):
        with pytest.raises(ValueError, match="require either a timezone"):
            _ooo_time_entry("2026-04-05", is_end=False, timezone=None)

    def test_rejects_naive_datetime_without_timezone(self):
        with pytest.raises(ValueError, match="require either a timezone"):
            _ooo_time_entry("2026-04-05T09:00:00", is_end=False, timezone=None)


# ---------------------------------------------------------------------------
# _create_ooo_event_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_ooo_full_day_sends_correct_event_body():
    """Verify the exact event body sent to the API for a full-day OOO event."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "ooo123",
            "htmlLink": "https://calendar.google.com/event?eid=ooo123",
            "summary": "Out of Office",
            "start": {"date": "2026-04-05"},
            "end": {"date": "2026-04-12"},
        }
    )

    result = await _create_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-05",
        end_time="2026-04-12",
        timezone="America/New_York",
    )

    # Verify the API was called with the correct event body
    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    assert body["eventType"] == "outOfOffice"
    assert body["summary"] == "Out of Office"
    assert body["start"] == {
        "dateTime": "2026-04-05T00:00:00",
        "timeZone": "America/New_York",
    }
    assert body["end"] == {
        "dateTime": "2026-04-12T00:00:00",
        "timeZone": "America/New_York",
    }
    assert "visibility" not in body
    assert body["transparency"] == "opaque"
    assert (
        body["outOfOfficeProperties"]["autoDeclineMode"]
        == "declineAllConflictingInvitations"
    )
    assert body["outOfOfficeProperties"]["declineMessage"] == ""

    # Verify calendarId
    assert call_args[1]["calendarId"] == "primary"

    # Verify output contains expected info
    assert "Successfully created Out of Office event" in result
    assert "user@example.com" in result
    assert "ooo123" in result


@pytest.mark.asyncio
async def test_create_ooo_with_custom_params_sends_correct_body():
    """Verify custom summary, decline mode, and decline message are in the API payload."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "ooo456",
            "htmlLink": "https://calendar.google.com/event?eid=ooo456",
            "summary": "Vacation",
            "start": {"date": "2026-05-01"},
            "end": {"date": "2026-05-08"},
        }
    )

    result = await _create_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-05-01",
        end_time="2026-05-08",
        summary="Vacation",
        auto_decline_mode="declineOnlyNewConflictingInvitations",
        decline_message="On vacation, contact backup@example.com",
        timezone="America/New_York",
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    assert body["eventType"] == "outOfOffice"
    assert body["summary"] == "Vacation"
    assert body["start"] == {
        "dateTime": "2026-05-01T00:00:00",
        "timeZone": "America/New_York",
    }
    assert body["end"] == {
        "dateTime": "2026-05-08T00:00:00",
        "timeZone": "America/New_York",
    }
    assert (
        body["outOfOfficeProperties"]["autoDeclineMode"]
        == "declineOnlyNewConflictingInvitations"
    )
    assert (
        body["outOfOfficeProperties"]["declineMessage"]
        == "On vacation, contact backup@example.com"
    )

    assert "Vacation" in result
    assert "declineOnlyNewConflictingInvitations" in result


@pytest.mark.asyncio
async def test_create_ooo_supports_recurrence():
    """Verify recurring OOO series sends recurrence rules to the API."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "oooRecurring",
            "htmlLink": "https://calendar.google.com/event?eid=oooRecurring",
            "summary": "Weekly OOO",
            "start": {"dateTime": "2026-05-01T09:00:00-04:00"},
            "end": {"dateTime": "2026-05-01T17:00:00-04:00"},
        }
    )

    await _create_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-05-01T09:00:00-04:00",
        end_time="2026-05-01T17:00:00-04:00",
        summary="Weekly OOO",
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=4"],
    )

    body = mock_service.events().insert.call_args[1]["body"]
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=FR;COUNT=4"]


@pytest.mark.asyncio
async def test_create_ooo_partial_day_uses_datetime_not_date():
    """Verify partial-day OOO uses dateTime keys, not date keys."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "ooo789",
            "htmlLink": "https://calendar.google.com/event?eid=ooo789",
            "summary": "Out of Office",
            "start": {"dateTime": "2026-04-05T09:00:00Z"},
            "end": {"dateTime": "2026-04-05T17:00:00Z"},
        }
    )

    await _create_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-05T09:00:00Z",
        end_time="2026-04-05T17:00:00Z",
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    # Must use dateTime, not date, for partial-day events
    assert body["start"] == {"dateTime": "2026-04-05T09:00:00Z"}
    assert body["end"] == {"dateTime": "2026-04-05T17:00:00Z"}
    assert "date" not in body["start"]
    assert "date" not in body["end"]


@pytest.mark.asyncio
async def test_create_ooo_custom_calendar_id():
    """Verify custom calendar_id is passed to the API."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "ooo1", "htmlLink": "link", "start": {}, "end": {}}
    )

    await _create_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        start_time="2026-04-05",
        end_time="2026-04-12",
        calendar_id="custom_calendar_id",
        timezone="America/New_York",
    )

    call_args = mock_service.events().insert.call_args
    assert call_args[1]["calendarId"] == "custom_calendar_id"


@pytest.mark.asyncio
async def test_create_ooo_invalid_decline_mode_never_calls_api():
    """Invalid decline mode raises ValueError before any API call."""
    mock_service = _create_mock_service()

    with pytest.raises(ValueError, match="Invalid auto_decline_mode"):
        await _create_ooo_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            start_time="2026-04-05",
            end_time="2026-04-12",
            auto_decline_mode="badValue",
            timezone="America/New_York",
        )


@pytest.mark.asyncio
async def test_create_ooo_date_only_requires_timezone():
    """Date-only OOO input must be anchored with a timezone."""
    mock_service = _create_mock_service()

    with pytest.raises(ValueError, match="require either a timezone"):
        await _create_ooo_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            start_time="2026-04-05",
            end_time="2026-04-12",
        )


# ---------------------------------------------------------------------------
# _list_ooo_events_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_ooo_passes_event_type_filter():
    """Verify the API is called with eventTypes=['outOfOffice']."""
    mock_service = _create_mock_service()
    mock_service.events().list().execute = Mock(return_value={"items": []})

    await _list_ooo_events_impl(
        service=mock_service,
        user_google_email="user@example.com",
    )

    call_args = mock_service.events().list.call_args
    params = call_args[1]

    assert params["eventTypes"] == ["outOfOffice"]
    assert params["singleEvents"] is True
    assert params["orderBy"] == "startTime"
    assert params["calendarId"] == "primary"


@pytest.mark.asyncio
async def test_list_ooo_formats_results_with_ooo_properties():
    """Verify OOO-specific fields (decline mode, message) appear in output."""
    mock_service = _create_mock_service()
    mock_service.events().list().execute = Mock(
        return_value={
            "items": [
                {
                    "id": "ooo1",
                    "summary": "Out of Office",
                    "start": {"date": "2026-04-05"},
                    "end": {"date": "2026-04-12"},
                    "outOfOfficeProperties": {
                        "autoDeclineMode": "declineAllConflictingInvitations",
                        "declineMessage": "Away on vacation",
                    },
                },
                {
                    "id": "ooo2",
                    "summary": "Conference",
                    "start": {"date": "2026-05-01"},
                    "end": {"date": "2026-05-03"},
                    "outOfOfficeProperties": {
                        "autoDeclineMode": "declineNone",
                        "declineMessage": "",
                    },
                },
            ]
        }
    )

    result = await _list_ooo_events_impl(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "Found 2 out-of-office event(s)" in result
    assert "ooo1" in result
    assert "ooo2" in result
    assert "Away on vacation" in result
    assert "declineAllConflictingInvitations" in result
    assert "declineNone" in result
    # Empty decline message should NOT appear in output
    assert "Conference" in result


@pytest.mark.asyncio
async def test_list_ooo_empty():
    """List returns 'no events found' message when empty."""
    mock_service = _create_mock_service()
    mock_service.events().list().execute = Mock(return_value={"items": []})

    result = await _list_ooo_events_impl(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "No out-of-office events found" in result


@pytest.mark.asyncio
async def test_list_ooo_respects_time_max():
    """Verify time_max is passed to API when provided."""
    mock_service = _create_mock_service()
    mock_service.events().list().execute = Mock(return_value={"items": []})

    await _list_ooo_events_impl(
        service=mock_service,
        user_google_email="user@example.com",
        time_min="2026-01-01T00:00:00Z",
        time_max="2026-12-31T23:59:59Z",
    )

    call_args = mock_service.events().list.call_args
    params = call_args[1]

    assert "timeMax" in params


# ---------------------------------------------------------------------------
# _update_ooo_event_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_ooo_uses_patch_not_update():
    """Verify update uses events().patch() (not update()) since eventType is immutable."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "ooo123",
            "eventType": "outOfOffice",
            "outOfOfficeProperties": {
                "autoDeclineMode": "declineAllConflictingInvitations",
                "declineMessage": "Away",
            },
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "ooo123",
            "htmlLink": "https://calendar.google.com/event?eid=ooo123",
            "summary": "Out of Office",
            "start": {"date": "2026-04-05"},
            "end": {"date": "2026-04-14"},
        }
    )

    await _update_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="ooo123",
        end_time="2026-04-14",
        timezone="America/New_York",
    )

    # Verify patch was called with the right event_id
    patch_call_args = mock_service.events().patch.call_args
    assert patch_call_args[1]["eventId"] == "ooo123"
    assert patch_call_args[1]["calendarId"] == "primary"

    # Verify eventType is NOT in the patch body (it's immutable)
    patch_body = patch_call_args[1]["body"]
    assert "eventType" not in patch_body


@pytest.mark.asyncio
async def test_update_ooo_sends_only_changed_fields():
    """Verify patch body only contains the fields being updated."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "ooo123",
            "eventType": "outOfOffice",
            "summary": "Out of Office",
            "start": {"date": "2026-04-05"},
            "end": {"date": "2026-04-12"},
            "outOfOfficeProperties": {
                "autoDeclineMode": "declineAllConflictingInvitations",
                "declineMessage": "Away",
            },
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "ooo123",
            "htmlLink": "link",
            "summary": "Updated OOO",
            "start": {"date": "2026-04-05"},
            "end": {"date": "2026-04-12"},
        }
    )

    await _update_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="ooo123",
        summary="Updated OOO",
    )

    patch_body = mock_service.events().patch.call_args[1]["body"]

    # Only summary should be in the patch body
    assert patch_body["summary"] == "Updated OOO"
    assert "start" not in patch_body
    assert "end" not in patch_body
    assert "outOfOfficeProperties" not in patch_body


@pytest.mark.asyncio
async def test_update_ooo_preserves_existing_decline_mode_when_only_message_changes():
    """When only decline_message is updated, existing autoDeclineMode is preserved."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "ooo123",
            "eventType": "outOfOffice",
            "outOfOfficeProperties": {
                "autoDeclineMode": "declineNone",
                "declineMessage": "Old message",
            },
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "ooo123",
            "htmlLink": "link",
            "summary": "Out of Office",
            "start": {"date": "2026-04-05"},
            "end": {"date": "2026-04-12"},
        }
    )

    await _update_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="ooo123",
        decline_message="New message",
    )

    patch_body = mock_service.events().patch.call_args[1]["body"]
    ooo_props = patch_body["outOfOfficeProperties"]

    # Decline mode should be preserved from existing event
    assert ooo_props["autoDeclineMode"] == "declineNone"
    assert ooo_props["declineMessage"] == "New message"


@pytest.mark.asyncio
async def test_update_ooo_can_patch_recurrence():
    """Recurring OOO rules should be patchable independently."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "ooo123",
            "eventType": "outOfOffice",
            "outOfOfficeProperties": {
                "autoDeclineMode": "declineNone",
                "declineMessage": "Away",
            },
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "ooo123",
            "htmlLink": "link",
            "summary": "Out of Office",
            "start": {"dateTime": "2026-04-05T09:00:00Z"},
            "end": {"dateTime": "2026-04-05T17:00:00Z"},
        }
    )

    await _update_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="ooo123",
        recurrence=["RRULE:FREQ=WEEKLY;COUNT=6"],
    )

    patch_body = mock_service.events().patch.call_args[1]["body"]
    assert patch_body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=6"]
    assert "outOfOfficeProperties" not in patch_body


@pytest.mark.asyncio
async def test_update_non_ooo_event_raises_error():
    """Update raises ValueError when event is not an OOO event."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "regular123",
            "eventType": "default",
            "summary": "Team Meeting",
        }
    )

    with pytest.raises(ValueError, match="not an Out of Office event"):
        await _update_ooo_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="regular123",
            summary="New title",
        )


@pytest.mark.asyncio
async def test_update_ooo_no_changes_skips_api_call():
    """Update with no changes returns message without calling patch."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "ooo123",
            "eventType": "outOfOffice",
            "outOfOfficeProperties": {},
        }
    )

    # Record call count before our operation
    patch_calls_before = mock_service.events().patch.call_count

    result = await _update_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="ooo123",
    )

    assert "No changes specified" in result
    # Verify no new patch calls were made
    assert mock_service.events().patch.call_count == patch_calls_before


# ---------------------------------------------------------------------------
# _delete_ooo_event_impl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_ooo_calls_delete_with_correct_ids():
    """Verify delete calls the API with correct calendarId and eventId."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={"id": "ooo123", "eventType": "outOfOffice"}
    )
    mock_service.events().delete().execute = Mock(return_value=None)

    result = await _delete_ooo_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="ooo123",
        calendar_id="my_calendar",
    )

    delete_call_args = mock_service.events().delete.call_args
    assert delete_call_args[1]["calendarId"] == "my_calendar"
    assert delete_call_args[1]["eventId"] == "ooo123"
    assert "Successfully deleted" in result


@pytest.mark.asyncio
async def test_delete_ooo_event_not_found_raises():
    """Delete raises when event not found (404)."""
    from googleapiclient.errors import HttpError

    mock_service = _create_mock_service()
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_service.events().get().execute = Mock(
        side_effect=HttpError(resp=mock_resp, content=b"Not Found")
    )

    with pytest.raises(Exception, match="Event not found"):
        await _delete_ooo_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="nonexistent",
        )


@pytest.mark.asyncio
async def test_delete_non_ooo_event_raises_error():
    """Delete refuses to act on regular events."""
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={"id": "regular123", "eventType": "default"}
    )

    with pytest.raises(ValueError, match="not an Out of Office event"):
        await _delete_ooo_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="regular123",
        )
