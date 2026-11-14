"""Synthetic tests for the existing Calendar tool's native move action."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from googleapiclient.errors import HttpError

from auth.permissions import set_permissions
from auth.scopes import CALENDAR_EVENTS_SCOPE, set_read_only
from core import tool_registry
from core.server import server
from core.utils import UserInputError
from gcalendar.calendar_tools import manage_event


async def _move(service, **kwargs):
    tool = manage_event.fn if hasattr(manage_event, "fn") else manage_event
    while hasattr(tool, "__wrapped__"):
        tool = tool.__wrapped__
    return await tool(service, "user@example.com", action="move", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args,notification",
    [
        ({}, "none"),
        ({"send_updates": "all"}, "all"),
        ({"send_updates": "externalOnly"}, "externalOnly"),
    ],
)
async def test_move_returns_native_resource_and_preserves_notification_policy(
    args, notification
):
    service = Mock()
    event = {
        "id": "original",
        "organizer": {"email": "team@example.com"},
        "attendees": [{"email": "guest@example.com", "responseStatus": "accepted"}],
    }
    service.events.return_value.move.return_value.execute.return_value = event
    result = await _move(
        service,
        calendar_id="personal@example.com",
        event_id="original",
        destination_calendar_id="team@example.com",
        **args,
    )
    assert json.loads(result) == event
    expected = (
        call.events()
        .move(
            calendarId="personal@example.com",
            eventId="original",
            destination="team@example.com",
            sendUpdates=notification,
        )
        .execute()
    )
    assert service.mock_calls == expected.call_list()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,message",
    [
        ({"event_id": "original"}, "destination_calendar_id"),
        ({"destination_calendar_id": "team@example.com"}, "event_id"),
        (
            {
                "event_id": "original",
                "destination_calendar_id": "team@example.com",
                "send_updates": "invalid",
            },
            "Invalid send_updates",
        ),
        (
            {
                "event_id": "original",
                "destination_calendar_id": "team@example.com",
                "this_and_following": True,
            },
            "applies only to the update action",
        ),
    ],
)
async def test_invalid_move_makes_no_google_call(kwargs, message):
    service = Mock()
    with pytest.raises(UserInputError, match=message):
        await _move(service, **kwargs)
    assert service.mock_calls == []


@pytest.mark.asyncio
async def test_denied_move_never_recreates_event():
    service = Mock()
    error = HttpError(SimpleNamespace(status=403, reason="Move rejected"), b"{}")
    service.events.return_value.move.return_value.execute.side_effect = error
    with pytest.raises(HttpError) as caught:
        await _move(
            service, event_id="original", destination_calendar_id="team@example.com"
        )
    assert caught.value is error
    expected = (
        call.events()
        .move(
            calendarId="primary",
            eventId="original",
            destination="team@example.com",
            sendUpdates="none",
        )
        .execute()
    )
    assert service.mock_calls == expected.call_list()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "permissions,read_only,removed",
    [
        ({"calendar": "readonly"}, False, True),
        ({"calendar": "full"}, False, False),
        (None, True, True),
    ],
)
async def test_move_preserves_scope_and_write_policy(
    monkeypatch, permissions, read_only, removed
):
    tool = await server.get_tool("manage_event")
    assert tool.fn._required_google_scopes == [CALENDAR_EVENTS_SCOPE]
    assert tool.annotations.read_only_hint is False
    provider = SimpleNamespace(_components={"tool:manage_event@1": tool}, removed=[])
    provider.remove_tool = provider.removed.append
    monkeypatch.setattr(tool_registry, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(tool_registry, "get_enabled_tools", lambda: None)
    monkeypatch.setattr(tool_registry, "get_disabled_tools", set)
    set_permissions(permissions)
    set_read_only(read_only)
    try:
        tool_registry.filter_server_tools(SimpleNamespace(local_provider=provider))
    finally:
        set_permissions(None)
        set_read_only(False)
    assert provider.removed == (["manage_event"] if removed else [])
