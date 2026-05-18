"""
Regression tests for issue #797: detailed get_events output omitted creator
and organizer fields, so events on shared calendars with no attendees lost
all attribution.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import _format_person, get_events


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# --- _format_person unit tests -------------------------------------------------


def test_format_person_both_fields():
    assert (
        _format_person({"displayName": "Ada Lovelace", "email": "ada@example.com"})
        == "Ada Lovelace <ada@example.com>"
    )


def test_format_person_email_only():
    assert _format_person({"email": "ada@example.com"}) == "<ada@example.com>"


def test_format_person_display_name_only():
    assert _format_person({"displayName": "Ada Lovelace"}) == "Ada Lovelace"


@pytest.mark.parametrize(
    "person",
    [None, {}, {"displayName": "", "email": ""}, {"displayName": "   "}],
)
def test_format_person_empty_returns_none(person):
    assert _format_person(person) is None


# --- get_events detailed-output integration tests ------------------------------


def _make_service(event):
    service = Mock()
    service.events().get().execute = Mock(return_value=event)
    service.events().list().execute = Mock(return_value={"items": [event]})
    return service


@pytest.mark.asyncio
async def test_detailed_single_event_includes_creator_and_organizer():
    event = {
        "id": "evt-1",
        "summary": "Roadmap review",
        "start": {"dateTime": "2026-05-18T10:00:00Z"},
        "end": {"dateTime": "2026-05-18T11:00:00Z"},
        "htmlLink": "https://calendar.google.com/event?eid=evt-1",
        "creator": {"displayName": "Ada Lovelace", "email": "ada@example.com"},
        "organizer": {"displayName": "Shared Calendar", "email": "team@example.com"},
    }
    service = _make_service(event)

    out = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        event_id="evt-1",
        detailed=True,
    )

    assert "- Creator: Ada Lovelace <ada@example.com>" in out
    assert "- Organizer: Shared Calendar <team@example.com>" in out


@pytest.mark.asyncio
async def test_detailed_single_event_omits_creator_organizer_when_absent():
    event = {
        "id": "evt-2",
        "summary": "Lone event",
        "start": {"dateTime": "2026-05-18T10:00:00Z"},
        "end": {"dateTime": "2026-05-18T11:00:00Z"},
        "htmlLink": "https://calendar.google.com/event?eid=evt-2",
    }
    service = _make_service(event)

    out = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        event_id="evt-2",
        detailed=True,
    )

    assert "Creator:" not in out
    assert "Organizer:" not in out


@pytest.mark.asyncio
async def test_detailed_multi_event_includes_creator_and_organizer():
    event = {
        "id": "evt-3",
        "summary": "Range event",
        "start": {"dateTime": "2026-05-18T10:00:00Z"},
        "end": {"dateTime": "2026-05-18T11:00:00Z"},
        "htmlLink": "https://calendar.google.com/event?eid=evt-3",
        "creator": {"email": "creator@example.com"},
        "organizer": {"displayName": "Org Owner"},
    }
    service = _make_service(event)

    out = await _unwrap(get_events)(
        service=service,
        user_google_email="user@example.com",
        time_min="2026-05-18T00:00:00Z",
        time_max="2026-05-19T00:00:00Z",
        detailed=True,
    )

    # Multi-event branch uses 2-space indent under the bullet line.
    assert "  Creator: <creator@example.com>" in out
    assert "  Organizer: Org Owner" in out
