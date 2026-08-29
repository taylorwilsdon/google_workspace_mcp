"""Allowlist enforcement on the *effective* attendee list of a Calendar write.

An update notifies everyone left on the event, so attendees preserved from the
existing event count, not just the ones passed in. The account's own entry and
room resources are not third parties. An RSVP comment is content delivered to
the organizer.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.recipient_allowlist import ALLOWLIST_ENV, RecipientNotAllowedError
from gcalendar.calendar_tools import _modify_event_impl, _rsvp_event_impl

SELF = {"email": "user@example.com", "self": True}


def _service(existing_event):
    """Build a mock Calendar service whose events().get returns existing_event."""
    service = Mock()
    service.events().get().execute = Mock(return_value=existing_event)
    service.events().patch().execute = Mock(
        return_value={"id": "evt1", "htmlLink": "https://example.test/evt1"}
    )
    return service


async def _modify(service, **fields):
    """Call _modify_event_impl for event 'evt1' with the given field updates."""
    return await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt1",
        **fields,
    )


@pytest.mark.asyncio
async def test_update_refuses_when_preserved_attendee_is_unlisted(monkeypatch):
    """Preserved unlisted attendees are refused before the patch is sent."""
    monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
    service = _service({"attendees": [SELF, {"email": "stranger@example.com"}]})
    with pytest.raises(RecipientNotAllowedError, match="stranger@example.com"):
        await _modify(service, summary="New title")
    service.events().patch().execute.assert_not_called()


@pytest.mark.asyncio
async def test_update_allows_listed_attendees_self_and_resources(monkeypatch):
    """Listed attendees, the account's own entry and room resources pass."""
    monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
    service = _service(
        {
            "attendees": [
                SELF,
                {"email": "mum@example.com"},
                {"email": "room@resource.calendar.google.com", "resource": True},
            ]
        }
    )
    await _modify(service, summary="New title")
    service.events().patch().execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_unchanged_when_allowlist_inactive(monkeypatch):
    """With the allowlist off, preserved attendees are not checked."""
    monkeypatch.delenv(ALLOWLIST_ENV, raising=False)
    service = _service({"attendees": [SELF, {"email": "stranger@example.com"}]})
    await _modify(service, summary="New title")
    service.events().patch().execute.assert_called_once()


@pytest.mark.asyncio
async def test_rsvp_comment_to_unlisted_organizer_refused(monkeypatch):
    """A bare RSVP passes; one with a comment to an unlisted organizer is refused."""
    monkeypatch.setenv(ALLOWLIST_ENV, "mum@example.com")
    event = {"attendees": [SELF], "organizer": {"email": "stranger@example.com"}}
    # A bare response carries no content and is allowed.
    await _rsvp_event_impl(_service(event), "user@example.com", "evt1", "accepted")
    with pytest.raises(RecipientNotAllowedError, match="stranger@example.com"):
        await _rsvp_event_impl(
            _service(event), "user@example.com", "evt1", "accepted", comment="See you"
        )
