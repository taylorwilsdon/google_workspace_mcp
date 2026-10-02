"""
Unit tests for generic Google Calendar event helpers.

Focuses on recurrence support for create/update flows.
"""

import inspect
import json
import os
import sys
from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError, handle_http_errors
from gcalendar.calendar_tools import (
    _build_addon_conference_data,
    _create_event_impl,
    _modify_event_impl,
    _resolve_conference_data,
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
    mock_service.events().patch().execute = Mock(return_value={})
    return mock_service


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
    mock_service.events().patch().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Team Standup"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        summary="Team Standup",
    )

    update_body = mock_service.events().patch.call_args[1]["body"]
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
    mock_service.events().patch().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Standup"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        recurrence=["RRULE:FREQ=WEEKLY;COUNT=6"],
    )

    update_body = mock_service.events().patch.call_args[1]["body"]
    assert update_body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=6"]


@pytest.mark.asyncio
async def test_modify_event_removes_google_meet_with_null_conference_data():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Standup",
            "conferenceData": {"conferenceId": "abc-defg-hij"},
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Standup"}
    )

    await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        add_google_meet=False,
    )

    patch_call = mock_service.events().patch.call_args
    update_body = patch_call[1]["body"]
    assert update_body["conferenceData"] is None
    assert patch_call[1]["conferenceDataVersion"] == 1


# ---------------------------------------------------------------------------
# Third-party conferencing (Zoom / Webex / Teams add-on) support
# ---------------------------------------------------------------------------


def test_build_addon_conference_data_maps_known_provider():
    data = _build_addon_conference_data(
        " Zoom ", " https://zoom.us/j/123 ", passcode="abc", conference_id="123"
    )
    assert data["conferenceSolution"]["key"]["type"] == "addOn"
    assert data["conferenceSolution"]["name"] == "Zoom Meeting"
    assert data["conferenceId"] == "123"
    assert data["entryPoints"][0] == {
        "entryPointType": "video",
        "uri": "https://zoom.us/j/123",
        "label": "Zoom Meeting",
        "passcode": "abc",
    }


def test_build_addon_conference_data_passes_through_unknown_provider():
    data = _build_addon_conference_data("BlueJeans", "https://bluejeans.com/123")
    assert data["conferenceSolution"]["name"] == "BlueJeans"
    assert "passcode" not in data["entryPoints"][0]
    assert "conferenceId" not in data


def test_resolve_conference_data_builds_from_helper_params():
    resolved = _resolve_conference_data(
        conference_data=None,
        conference_provider="zoom",
        conference_uri="https://zoom.us/j/123",
        conference_passcode=None,
        conference_id=None,
        add_google_meet=None,
    )
    assert resolved["conferenceSolution"]["key"]["type"] == "addOn"
    assert resolved["entryPoints"][0]["uri"] == "https://zoom.us/j/123"


def test_resolve_conference_data_passthrough_returned_as_is():
    raw = {"conferenceId": "x", "entryPoints": []}
    assert _resolve_conference_data(raw, None, None, None, None, None) is raw


def test_resolve_conference_data_returns_none_when_nothing_requested():
    assert _resolve_conference_data(None, None, None, None, None, None) is None


def test_resolve_conference_data_rejects_helper_without_uri():
    with pytest.raises(ValueError):
        _resolve_conference_data(None, "zoom", None, None, None, None)


def test_resolve_conference_data_rejects_raw_and_helper_combined():
    with pytest.raises(ValueError):
        _resolve_conference_data(
            {"a": 1}, "zoom", "https://zoom.us/j/1", None, None, None
        )


def test_resolve_conference_data_rejects_conflict_with_google_meet():
    with pytest.raises(ValueError):
        _resolve_conference_data(
            None, "zoom", "https://zoom.us/j/1", None, None, add_google_meet=True
        )


@pytest.mark.asyncio
async def test_create_event_attaches_conference_data():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "evt123",
            "htmlLink": "link",
            "summary": "Sync",
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://zoom.us/j/123"}
                ]
            },
        }
    )

    conference = _build_addon_conference_data("zoom", "https://zoom.us/j/123")
    result = await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Sync",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:15:00Z",
        conference_data=conference,
    )

    call_kwargs = mock_service.events().insert.call_args[1]
    assert call_kwargs["conferenceDataVersion"] == 1
    assert call_kwargs["body"]["conferenceData"] == conference
    assert "https://zoom.us/j/123" in result


@pytest.mark.asyncio
async def test_modify_event_attaches_conference_data():
    mock_service = _create_mock_service()
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "summary": "Sync",
            "start": {"dateTime": "2026-04-06T09:00:00Z"},
            "end": {"dateTime": "2026-04-06T09:15:00Z"},
        }
    )
    mock_service.events().patch().execute = Mock(
        return_value={
            "id": "evt123",
            "htmlLink": "link",
            "summary": "Sync",
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "video", "uri": "https://zoom.us/j/999"}
                ]
            },
        }
    )

    conference = _build_addon_conference_data("zoom", "https://zoom.us/j/999")
    result = await _modify_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        event_id="evt123",
        conference_data=conference,
    )

    patch_kwargs = mock_service.events().patch.call_args[1]
    assert patch_kwargs["conferenceDataVersion"] == 1
    assert patch_kwargs["body"]["conferenceData"] == conference
    assert "https://zoom.us/j/999" in result


@pytest.mark.asyncio
async def test_manage_event_rejects_conference_helper_without_uri():
    fn = _unwrap(manage_event)
    with pytest.raises(ValueError, match="conference_provider and conference_uri"):
        await fn(
            service=Mock(),
            user_google_email="user@example.com",
            action="create",
            summary="x",
            start_time="2026-04-06T09:00:00Z",
            end_time="2026-04-06T09:15:00Z",
            conference_provider="zoom",
        )


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
async def test_create_event_supports_attendee_objects():
    """Issue #1145: attendee objects with metadata like responseStatus must be accepted on create."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "evt123",
            "htmlLink": "https://calendar.google.com/event?eid=evt123",
            "summary": "Meeting",
        }
    )

    attendee_objects = [
        {"email": "organizer@example.com", "responseStatus": "accepted"},
        {"email": "guest@example.com", "optional": True},
    ]

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Meeting",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        attendees=attendee_objects,
    )

    call_args = mock_service.events().insert.call_args
    body = call_args[1]["body"]
    assert body["attendees"] == attendee_objects


@pytest.mark.asyncio
async def test_create_event_supports_mixed_attendees():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={
            "id": "evt123",
            "htmlLink": "https://calendar.google.com/event?eid=evt123",
            "summary": "Meeting",
        }
    )

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Meeting",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        attendees=[
            "plain@example.com",
            {"email": "object@example.com", "responseStatus": "accepted"},
        ],
    )

    body = mock_service.events().insert.call_args[1]["body"]
    assert body["attendees"] == [
        {"email": "plain@example.com"},
        {"email": "object@example.com", "responseStatus": "accepted"},
    ]


@pytest.mark.asyncio
async def test_create_event_preserves_plain_string_attendees():
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Meeting"}
    )

    await _create_event_impl(
        service=mock_service,
        user_google_email="user@example.com",
        summary="Meeting",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        attendees=["alice@example.com", "bob@example.com"],
    )

    body = mock_service.events().insert.call_args[1]["body"]
    assert body["attendees"] == [
        {"email": "alice@example.com"},
        {"email": "bob@example.com"},
    ]


@pytest.mark.asyncio
async def test_manage_event_create_action_routes_attendee_objects():
    """End-to-end manage_event(action='create') must preserve attendee objects."""
    mock_service = _create_mock_service()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "evt123", "htmlLink": "link", "summary": "Sync"}
    )

    fn = _unwrap(manage_event)
    await fn(
        service=mock_service,
        user_google_email="user@example.com",
        action="create",
        summary="Sync",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        attendees=[{"email": "lead@example.com", "responseStatus": "accepted"}],
    )

    body = mock_service.events().insert.call_args[1]["body"]
    assert body["attendees"] == [
        {"email": "lead@example.com", "responseStatus": "accepted"}
    ]


@pytest.mark.asyncio
async def test_create_event_rejects_attendee_without_email():
    mock_service = _create_mock_service()

    with pytest.raises(ValueError, match="'email' key"):
        await _create_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            summary="Meeting",
            start_time="2026-04-06T09:00:00Z",
            end_time="2026-04-06T09:30:00Z",
            attendees=[{"displayName": "No Email"}],
        )


@pytest.mark.asyncio
async def test_modify_event_rejects_attendee_without_email():
    mock_service = _create_mock_service()

    with pytest.raises(ValueError, match="'email' key"):
        await _modify_event_impl(
            service=mock_service,
            user_google_email="user@example.com",
            event_id="evt123",
            attendees=["kept@example.com", {"displayName": "No Email"}],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize("invalid_attendee", [{"displayName": "No Email"}, 123, None])
async def test_manage_event_attendee_errors_are_user_input(action, invalid_attendee):
    mock_service = _create_mock_service()
    mock_service.reset_mock()
    # Exercise the tool's error handler while bypassing authentication.
    fn = handle_http_errors("manage_event", service_type="calendar")(
        _unwrap(manage_event)
    )

    with pytest.raises(UserInputError, match="'email' key") as exc_info:
        await fn(
            service=mock_service,
            user_google_email="user@example.com",
            action=action,
            event_id="evt123",
            summary="Meeting",
            start_time="2026-04-06T09:00:00Z",
            end_time="2026-04-06T09:30:00Z",
            attendees=["kept@example.com", invalid_attendee],
        )

    assert isinstance(exc_info.value, ValueError)
    assert mock_service.mock_calls == []


@pytest.mark.asyncio
async def test_manage_event_unrelated_value_error_keeps_existing_handling():
    fn = handle_http_errors("manage_event", service_type="calendar")(
        _unwrap(manage_event)
    )

    with pytest.raises(Exception, match="An unexpected error occurred") as exc_info:
        await fn(
            service=Mock(),
            user_google_email="user@example.com",
            action="create",
            send_updates="invalid",
        )

    assert isinstance(exc_info.value.__cause__, ValueError)
    assert not isinstance(exc_info.value.__cause__, UserInputError)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            ["plain@example.com", {"email": "object@example.com"}],
            ["plain@example.com", {"email": "object@example.com"}],
        ),
        (
            json.dumps([{"email": "lead@example.com", "responseStatus": "accepted"}]),
            [{"email": "lead@example.com", "responseStatus": "accepted"}],
        ),
    ],
)
def test_manage_event_attendees_schema_accepts_mixed_and_json_string(raw, expected):
    annotation = (
        inspect.signature(_unwrap(manage_event)).parameters["attendees"].annotation
    )

    assert TypeAdapter(annotation).validate_python(raw) == expected
