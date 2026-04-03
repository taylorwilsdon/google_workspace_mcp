"""Unit tests for the RSVP action in manage_event."""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import _rsvp_event_impl


def _make_event(attendees, organizer_self=False):
    return {
        "id": "evt123",
        "summary": "Team Sync",
        "attendees": attendees,
        "organizer": {"self": organizer_self, "email": "org@example.com"},
    }


def _create_mock_service(event):
    mock_service = Mock()
    mock_service.events().get().execute = Mock(return_value=event)
    mock_service.events().patch().execute = Mock(return_value=event)
    return mock_service


# -- Happy path tests --


@pytest.mark.asyncio
async def test_rsvp_accepted():
    attendees = [
        {"email": "user@example.com", "self": True, "responseStatus": "needsAction"},
        {"email": "other@example.com", "responseStatus": "accepted"},
    ]
    service = _create_mock_service(_make_event(attendees))

    result = await _rsvp_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt123",
        response="accepted",
    )

    assert "accepted" in result
    patch_kwargs = service.events().patch.call_args[1]
    assert patch_kwargs["body"]["attendees"][0]["responseStatus"] == "accepted"
    assert patch_kwargs["sendUpdates"] == "all"


@pytest.mark.asyncio
async def test_rsvp_declined_with_comment():
    attendees = [
        {"email": "user@example.com", "self": True, "responseStatus": "needsAction"},
    ]
    service = _create_mock_service(_make_event(attendees))

    await _rsvp_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt123",
        response="declined",
        comment="Out of office",
    )

    patch_body = service.events().patch.call_args[1]["body"]
    assert patch_body["attendees"][0]["responseStatus"] == "declined"
    assert patch_body["attendees"][0]["comment"] == "Out of office"


@pytest.mark.asyncio
async def test_rsvp_custom_send_updates():
    attendees = [
        {"email": "user@example.com", "self": True, "responseStatus": "needsAction"},
    ]
    service = _create_mock_service(_make_event(attendees))

    await _rsvp_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt123",
        response="tentative",
        send_updates="none",
    )

    assert service.events().patch.call_args[1]["sendUpdates"] == "none"


# -- Validation tests --


@pytest.mark.asyncio
async def test_rsvp_invalid_response():
    service = Mock()
    with pytest.raises(ValueError, match="Invalid response 'maybe'"):
        await _rsvp_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id="evt123",
            response="maybe",
        )


@pytest.mark.asyncio
async def test_rsvp_invalid_send_updates():
    service = Mock()
    with pytest.raises(ValueError, match="Invalid send_updates 'invalid'"):
        await _rsvp_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id="evt123",
            response="accepted",
            send_updates="invalid",
        )


# -- Guard tests --


@pytest.mark.asyncio
async def test_rsvp_no_attendees():
    event = {"id": "evt123", "summary": "Solo", "organizer": {"self": False}}
    service = _create_mock_service(event)

    with pytest.raises(Exception, match="no attendee list"):
        await _rsvp_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id="evt123",
            response="accepted",
        )


@pytest.mark.asyncio
async def test_rsvp_organizer_blocked():
    attendees = [
        {"email": "org@example.com", "self": True, "responseStatus": "accepted"},
    ]
    service = _create_mock_service(_make_event(attendees, organizer_self=True))

    with pytest.raises(Exception, match="organizer"):
        await _rsvp_event_impl(
            service=service,
            user_google_email="org@example.com",
            event_id="evt123",
            response="declined",
        )


@pytest.mark.asyncio
async def test_rsvp_user_not_in_attendees():
    attendees = [
        {"email": "other@example.com", "responseStatus": "accepted"},
    ]
    service = _create_mock_service(_make_event(attendees))

    with pytest.raises(Exception, match="not found in the event's attendee list"):
        await _rsvp_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id="evt123",
            response="accepted",
        )
