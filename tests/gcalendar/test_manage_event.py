"""
Unit tests for generic Google Calendar event helpers.

Focuses on recurrence support for create/update flows.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import (
    _create_event_impl,
    _modify_event_impl,
    manage_event,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service():
    mock_service = Mock()
    mock_service.events().insert().execute = Mock(return_value={})
    mock_service.events().get().execute = Mock(return_value={})
    mock_service.events().update().execute = Mock(return_value={})
    return mock_service


@pytest.mark.asyncio
async def test_create_event_passes_conference_data_verbatim():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    teams_payload = {
        "entryPoints": [
            {
                "entryPointType": "video",
                "uri": "https://teams.microsoft.com/l/meetup-join/abc",
                "label": "Microsoft Teams",
            }
        ]
    }

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Sync",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        conference_data=teams_payload,
    )

    call = mock_service.events().insert.call_args
    assert call[1]["body"]["conferenceData"] == teams_payload
    assert call[1]["conferenceDataVersion"] == 1


@pytest.mark.asyncio
async def test_create_event_default_omits_conference_data():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Sync",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
    )

    call = mock_service.events().insert.call_args
    assert "conferenceData" not in call[1]["body"]
    assert call[1]["conferenceDataVersion"] == 0


@pytest.mark.asyncio
async def test_create_event_conference_data_takes_precedence_over_meet():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    teams_payload = {
        "entryPoints": [
            {"entryPointType": "video", "uri": "https://teams.microsoft.com/l/x"}
        ]
    }

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Sync",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        add_google_meet=True,
        conference_data=teams_payload,
    )

    call = mock_service.events().insert.call_args
    assert call[1]["body"]["conferenceData"] == teams_payload
    assert "createRequest" not in call[1]["body"]["conferenceData"]
    assert call[1]["conferenceDataVersion"] == 1


@pytest.mark.asyncio
async def test_create_event_supports_recurrence():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "evt123",
            "htmlLink": "https://calendar.google.com/event?eid=evt123",
            "summary": "Standup",
        }
    )

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Standup",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:15:00Z",
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]

    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]


@pytest.mark.asyncio
async def test_modify_event_preserves_existing_recurrence_when_not_overridden():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Standup",
            "start": {"dateTime": "2026-04-06T09:00:00Z"},
            "end": {"dateTime": "2026-04-06T09:15:00Z"},
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
        }
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Team Standup"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        summary="Team Standup",
    )

    update_body = mock_service.events().update.call_args[1]["body"]
    assert update_body["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]


@pytest.mark.asyncio
async def test_modify_event_can_update_recurrence():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Standup",
            "start": {"dateTime": "2026-04-06T09:00:00Z"},
            "end": {"dateTime": "2026-04-06T09:15:00Z"},
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"],
        }
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Standup"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        recurrence=["RRULE:FREQ=WEEKLY;COUNT=6"],
    )

    update_body = mock_service.events().update.call_args[1]["body"]
    assert update_body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=6"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update", "delete", "rsvp"])
async def test_manage_event_rejects_invalid_send_updates(action):
    fn = _unwrap(manage_event)
    with pytest.raises(ValueError, match="Invalid send_updates 'invalid'"):
        await fn(
            service=Mock(),
            user_google_email="user@example.com",
            action=action,
            summary="x",
            start_time="2026-04-06T09:00:00Z",
            end_time="2026-04-06T09:15:00Z",
            event_id="evt123",
            response="accepted",
            send_updates="invalid",
        )


@pytest.mark.asyncio
async def test_update_event_passes_conference_data_verbatim():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Sync",
            "start": {"dateTime": "2026-04-06T09:00:00Z"},
            "end": {"dateTime": "2026-04-06T09:30:00Z"},
        }
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    teams_payload = {
        "entryPoints": [
            {
                "entryPointType": "video",
                "uri": "https://teams.microsoft.com/l/meetup-join/abc",
                "label": "Microsoft Teams",
            }
        ]
    }

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        conference_data=teams_payload,
    )

    call = mock_service.events().update.call_args
    assert call[1]["body"]["conferenceData"] == teams_payload
    assert call[1]["conferenceDataVersion"] == 1


@pytest.mark.asyncio
async def test_update_event_default_omits_conference_data():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Sync",
            "start": {"dateTime": "2026-04-06T09:00:00Z"},
            "end": {"dateTime": "2026-04-06T09:30:00Z"},
        }
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Renamed"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        summary="Renamed",
    )

    call = mock_service.events().update.call_args
    assert "conferenceData" not in call[1]["body"]
    assert call[1]["conferenceDataVersion"] == 0


@pytest.mark.asyncio
async def test_update_event_conference_data_takes_precedence_over_meet():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Sync",
            "start": {"dateTime": "2026-04-06T09:00:00Z"},
            "end": {"dateTime": "2026-04-06T09:30:00Z"},
        }
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    teams_payload = {
        "entryPoints": [
            {"entryPointType": "video", "uri": "https://teams.microsoft.com/l/x"}
        ]
    }

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        add_google_meet=True,
        conference_data=teams_payload,
    )

    call = mock_service.events().update.call_args
    assert call[1]["body"]["conferenceData"] == teams_payload
    assert "createRequest" not in call[1]["body"]["conferenceData"]
    assert call[1]["conferenceDataVersion"] == 1


@pytest.mark.asyncio
async def test_update_event_preserves_start_end_when_omitted():
    """description-only update should not lose existing start/end."""
    mock_service = _create_mock_service()
    existing_start = {"dateTime": "2026-04-06T09:00:00+09:00", "timeZone": "Asia/Seoul"}
    existing_end = {"dateTime": "2026-04-06T10:00:00+09:00", "timeZone": "Asia/Seoul"}
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Sync",
            "start": existing_start,
            "end": existing_end,
        }
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        description="updated description only",
    )

    update_body = mock_service.events().update.call_args[1]["body"]
    assert update_body["start"] == existing_start
    assert update_body["end"] == existing_end
    assert update_body["description"] == "updated description only"
