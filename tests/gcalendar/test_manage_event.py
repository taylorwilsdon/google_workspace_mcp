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
from auth.scopes import CALENDAR_EVENTS_SCOPE, CALENDAR_LIST_READONLY_SCOPE
from gcalendar.calendar_helpers import (
    _build_addon_conference_data,
    _resolve_conference_data,
)
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
    mock_service.events().patch().execute = Mock(return_value={})
    return mock_service


def test_manage_event_declares_calendar_list_read_scope():
    assert manage_event._required_google_scopes == [
        CALENDAR_EVENTS_SCOPE,
        CALENDAR_LIST_READONLY_SCOPE,
    ]


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
    with pytest.raises(UserInputError):
        _resolve_conference_data(None, "zoom", None, None, None, None)


def test_resolve_conference_data_rejects_raw_and_helper_combined():
    with pytest.raises(UserInputError):
        _resolve_conference_data(
            {"a": 1}, "zoom", "https://zoom.us/j/1", None, None, None
        )


def test_resolve_conference_data_rejects_conflict_with_google_meet():
    with pytest.raises(UserInputError):
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
    with pytest.raises(UserInputError, match="conference_provider and conference_uri"):
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
    with pytest.raises(UserInputError, match="Invalid send_updates 'invalid'"):
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
async def test_manage_event_delete_can_refuse_events_with_attendees():
    fn = _unwrap(manage_event)
    mock_service = _create_mock_service()
    mock_service.calendarList().get().execute = Mock(
        return_value={"accessRole": "owner"}
    )
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "evt123",
            "attendees": [
                {"email": "owner@example.com"},
                {"email": "guest@example.com"},
            ],
        }
    )

    with pytest.raises(UserInputError, match="1 attendee"):
        await fn(
            service=mock_service,
            user_google_email="owner@example.com",
            action="delete",
            event_id="evt123",
            only_if_no_attendees=True,
        )

    mock_service.events().delete.assert_not_called()


@pytest.mark.asyncio
async def test_manage_event_delete_proceeds_when_only_owner_is_attendee():
    fn = _unwrap(manage_event)
    mock_service = _create_mock_service()
    mock_service.calendarList().get().execute = Mock(
        return_value={"accessRole": "owner"}
    )
    mock_service.events().get().execute = Mock(
        return_value={"id": "evt123", "attendees": [{"email": "owner@example.com"}]}
    )

    await fn(
        service=mock_service,
        user_google_email="owner@example.com",
        action="delete",
        event_id="evt123",
        only_if_no_attendees=True,
    )

    mock_service.events().delete.assert_called_once()


@pytest.mark.asyncio
async def test_manage_event_delete_allows_owner_attendee_without_email():
    fn = _unwrap(manage_event)
    mock_service = _create_mock_service()
    mock_service.calendarList().get().execute = Mock(
        return_value={"accessRole": "owner"}
    )
    mock_service.events().get().execute = Mock(
        return_value={"id": "evt123", "attendees": [{"displayName": "Owner"}]}
    )

    await fn(
        service=mock_service,
        user_google_email="owner@example.com",
        action="delete",
        event_id="evt123",
        only_if_no_attendees=True,
    )

    mock_service.events().delete.assert_called_once()


@pytest.mark.asyncio
async def test_manage_event_delete_refuses_recurring_series_with_guard():
    fn = _unwrap(manage_event)
    mock_service = _create_mock_service()
    mock_service.calendarList().get().execute = Mock(
        return_value={"accessRole": "owner"}
    )
    mock_service.events().get().execute = Mock(
        return_value={
            "id": "series123",
            "attendees": [{"email": "owner@example.com"}],
            "recurrence": ["RRULE:FREQ=WEEKLY;COUNT=4"],
        }
    )

    with pytest.raises(UserInputError, match="recurring series"):
        await fn(
            service=mock_service,
            user_google_email="owner@example.com",
            action="delete",
            event_id="series123",
            only_if_no_attendees=True,
        )

    mock_service.events().delete.assert_not_called()


@pytest.mark.asyncio
async def test_manage_event_delete_refuses_guard_when_user_is_not_calendar_owner():
    fn = _unwrap(manage_event)
    mock_service = _create_mock_service()
    mock_service.calendarList().get().execute = Mock(
        return_value={"accessRole": "writer"}
    )
    mock_service.events().get().execute = Mock(
        return_value={"id": "evt123", "attendees": [{"email": "user@example.com"}]}
    )

    with pytest.raises(UserInputError, match="calendar owner"):
        await fn(
            service=mock_service,
            user_google_email="user@example.com",
            action="delete",
            event_id="evt123",
            calendar_id="shared@example.com",
            only_if_no_attendees=True,
        )

    mock_service.events().delete.assert_not_called()


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

    with pytest.raises(UserInputError, match="'email' key"):
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

    with pytest.raises(UserInputError, match="'email' key"):
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

    with pytest.raises(UserInputError, match="'email' key"):
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

    assert mock_service.mock_calls == []


@pytest.mark.asyncio
async def test_manage_event_validation_errors_are_user_input():
    fn = handle_http_errors("manage_event", service_type="calendar")(
        _unwrap(manage_event)
    )

    with pytest.raises(UserInputError, match="Invalid send_updates"):
        await fn(
            service=Mock(),
            user_google_email="user@example.com",
            action="create",
            send_updates="invalid",
        )


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


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize(
    "color, expected", [("Tomato", "11"), (" basil ", "10"), ("5", "5")]
)
async def test_manage_event_resolves_color_names_and_ids(action, color, expected):
    mock_service = _create_mock_service()

    await _unwrap(manage_event)(
        service=mock_service,
        user_google_email="user@example.com",
        action=action,
        event_id="evt123",
        summary="Meeting",
        start_time="2026-04-06T09:00:00Z",
        end_time="2026-04-06T09:30:00Z",
        color_id=color,
    )

    write = (
        mock_service.events().insert
        if action == "create"
        else mock_service.events().patch
    )
    assert write.call_args[1]["body"]["colorId"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("color", ["chartreuse", "0", "12"])
async def test_manage_event_rejects_unknown_color(color):
    mock_service = _create_mock_service()
    mock_service.reset_mock()

    with pytest.raises(UserInputError, match="tomato \\(11\\)"):
        await _unwrap(manage_event)(
            service=mock_service,
            user_google_email="user@example.com",
            action="create",
            summary="Meeting",
            start_time="2026-04-06T09:00:00Z",
            end_time="2026-04-06T09:30:00Z",
            color_id=color,
        )

    assert mock_service.mock_calls == []


_NY = "America/New_York"
_SERIES = {
    "id": "series1",
    "iCalUID": "series1@google.com",
    "etag": '"3181"',
    "htmlLink": "https://calendar.google.com/event?eid=series1",
    "summary": "Weekly sync",
    "attendees": [{"email": "teammate@example.com"}],
    "start": {"dateTime": "2026-01-05T12:00:00-05:00", "timeZone": _NY},
    "end": {"dateTime": "2026-01-05T13:00:00-05:00", "timeZone": _NY},
    "recurrence": [
        "RRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=52",
        "EXDATE;TZID=America/New_York:20260119T120000",
    ],
}
_INSTANCE = {
    "id": "series1_20260511T160000Z",
    "recurringEventId": "series1",
    "originalStartTime": {"dateTime": "2026-05-11T12:00:00-04:00", "timeZone": _NY},
    "start": {"dateTime": "2026-05-11T12:00:00-04:00", "timeZone": _NY},
    "end": {"dateTime": "2026-05-11T13:00:00-04:00", "timeZone": _NY},
}


def _series_service(instance, series, *later_gets):
    service = Mock()
    events = service.events.return_value
    events.get.return_value.execute.side_effect = [instance, series, *later_gets]
    events.insert.return_value.execute.return_value = {
        "id": "series2",
        "summary": series.get("summary"),
    }
    return service, events


@pytest.mark.asyncio
async def test_manage_event_this_and_following_splits_series():
    service, events = _series_service(_INSTANCE, _SERIES)

    result = await _unwrap(manage_event)(
        service=service,
        user_google_email="user@example.com",
        action="update",
        event_id=_INSTANCE["id"],
        start_time="2026-05-13T10:00:00-04:00",
        end_time="2026-05-13T11:00:00-04:00",
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=WE"],
        this_and_following=True,
    )

    events.patch.assert_called_once_with(
        calendarId="primary",
        eventId="series1",
        body={
            "recurrence": [
                "RRULE:FREQ=WEEKLY;BYDAY=MO;UNTIL=20260511T155959Z",
                "EXDATE;TZID=America/New_York:20260119T120000",
            ]
        },
        sendUpdates="all",
    )
    inserted = events.insert.call_args.kwargs["body"]
    assert inserted["summary"] == "Weekly sync"
    assert inserted["attendees"] == [{"email": "teammate@example.com"}]
    assert inserted["start"] == {
        "dateTime": "2026-05-13T10:00:00-04:00",
        "timeZone": _NY,
    }
    assert inserted["end"] == {
        "dateTime": "2026-05-13T11:00:00-04:00",
        "timeZone": _NY,
    }
    assert inserted["recurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=WE"]
    assert inserted["id"] != "series1"
    assert not {"iCalUID", "etag", "htmlLink"} & inserted.keys()
    assert "series ID series2" in result


@pytest.mark.asyncio
async def test_this_and_following_moved_instance_keeps_series_slot():
    moved = {
        **_INSTANCE,
        "start": {"dateTime": "2026-05-12T15:00:00-04:00", "timeZone": _NY},
        "end": {"dateTime": "2026-05-12T17:30:00-04:00", "timeZone": _NY},
    }
    service, events = _series_service(moved, _SERIES)

    await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id=moved["id"],
        summary="Renamed",
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=MO"],
        this_and_following=True,
    )

    inserted = events.insert.call_args.kwargs["body"]
    assert inserted["start"] == _INSTANCE["originalStartTime"]
    assert inserted["end"] == {
        "dateTime": "2026-05-11T13:00:00-04:00",
        "timeZone": _NY,
    }


@pytest.mark.asyncio
async def test_this_and_following_all_day_keeps_rules_and_instance_times():
    series = {
        "id": "weekly1",
        "summary": "Garden",
        "start": {"date": "2026-01-03"},
        "end": {"date": "2026-01-04"},
        "recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20261231"],
    }
    instance = {
        "id": "weekly1_20260509",
        "recurringEventId": "weekly1",
        "originalStartTime": {"date": "2026-05-09"},
        "start": {"date": "2026-05-09"},
        "end": {"date": "2026-05-10"},
    }
    service, events = _series_service(instance, series)

    await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id=instance["id"],
        summary="Vegetable garden",
        this_and_following=True,
    )

    assert events.patch.call_args.kwargs["body"] == {
        "recurrence": ["RRULE:FREQ=WEEKLY;UNTIL=20260508"]
    }
    inserted = events.insert.call_args.kwargs["body"]
    assert inserted["summary"] == "Vegetable garden"
    assert inserted["start"] == {"date": "2026-05-09"}
    assert inserted["end"] == {"date": "2026-05-10"}
    assert inserted["recurrence"] == ["RRULE:FREQ=WEEKLY;UNTIL=20261231"]


@pytest.mark.asyncio
async def test_this_and_following_restores_series_when_insert_fails():
    service, events = _series_service(_INSTANCE, _SERIES, RuntimeError("not found"))
    events.insert.return_value.execute.side_effect = RuntimeError("insert failed")

    with pytest.raises(RuntimeError, match="insert failed"):
        await _modify_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id=_INSTANCE["id"],
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=WE"],
            this_and_following=True,
        )

    restored = events.patch.call_args_list[-1].kwargs
    assert events.patch.call_count == 2
    assert restored["eventId"] == "series1"
    assert restored["body"] == {"recurrence": _SERIES["recurrence"]}


@pytest.mark.asyncio
async def test_this_and_following_reports_truncated_series_when_restore_fails():
    service, events = _series_service(_INSTANCE, _SERIES, RuntimeError("not found"))
    events.insert.return_value.execute.side_effect = RuntimeError("insert failed")
    events.patch.return_value.execute.side_effect = [{}, RuntimeError("rate limit")]

    with pytest.raises(Exception, match="now ends before instance") as excinfo:
        await _modify_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id=_INSTANCE["id"],
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=WE"],
            this_and_following=True,
        )

    assert "COUNT=52" in str(excinfo.value)


@pytest.mark.asyncio
async def test_this_and_following_keeps_insert_that_landed_despite_error():
    created = {"id": "series2", "summary": "Weekly sync"}
    service, events = _series_service(_INSTANCE, _SERIES, created)
    events.insert.return_value.execute.side_effect = TimeoutError("read timed out")

    result = await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id=_INSTANCE["id"],
        recurrence=["RRULE:FREQ=WEEKLY;BYDAY=WE"],
        this_and_following=True,
    )

    inserted_id = events.insert.call_args.kwargs["body"]["id"]
    assert events.get.call_args.kwargs["eventId"] == inserted_id
    assert events.patch.call_count == 1
    assert "series ID series2" in result


@pytest.mark.asyncio
async def test_this_and_following_rejects_series_with_rdate():
    series = {**_SERIES, "recurrence": ["RRULE:FREQ=WEEKLY", "RDATE:20260601T130000Z"]}
    service, events = _series_service(_INSTANCE, series)

    with pytest.raises(UserInputError, match="RDATE"):
        await _modify_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id=_INSTANCE["id"],
            recurrence=["RRULE:FREQ=WEEKLY;BYDAY=WE"],
            this_and_following=True,
        )

    events.patch.assert_not_called()


@pytest.mark.asyncio
async def test_this_and_following_rejects_non_update_actions():
    service = Mock()

    with pytest.raises(UserInputError, match="only to the update action"):
        await _unwrap(manage_event)(
            service=service,
            user_google_email="user@example.com",
            action="delete",
            event_id=_INSTANCE["id"],
            this_and_following=True,
        )

    assert service.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "instance, recurrence, error",
    [
        (_SERIES, ["RRULE:FREQ=WEEKLY"], "not an active instance"),
        (
            {**_INSTANCE, "status": "cancelled"},
            ["RRULE:FREQ=WEEKLY"],
            "not an active instance",
        ),
        (
            {**_INSTANCE, "originalStartTime": {"dateTime": "2026-01-05T17:00:00Z"}},
            ["RRULE:FREQ=WEEKLY"],
            "first instance",
        ),
        (_INSTANCE, None, "fixed COUNT"),
    ],
)
async def test_this_and_following_rejects_before_writing(instance, recurrence, error):
    service, events = _series_service(instance, _SERIES)

    with pytest.raises(UserInputError, match=error):
        await _modify_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id=instance["id"],
            summary="Renamed",
            recurrence=recurrence,
            this_and_following=True,
        )

    events.patch.assert_not_called()
    events.insert.assert_not_called()
