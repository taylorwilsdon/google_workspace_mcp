"""Tests for manage_event's move action."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call

import pytest
from googleapiclient.errors import HttpError

from core.utils import UserInputError
from gcalendar.calendar_tools import manage_event


async def _move(service: Mock, **kwargs: Any) -> str:
    tool = manage_event.fn if hasattr(manage_event, "fn") else manage_event
    while hasattr(tool, "__wrapped__"):
        tool = tool.__wrapped__
    return cast(str, await tool(service, "user@example.com", action="move", **kwargs))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args,notification",
    [
        ({}, "none"),
        ({"send_updates": "all"}, "all"),
        ({"send_updates": "externalOnly"}, "externalOnly"),
    ],
)
async def test_move_calls_native_move_and_summarizes_result(
    args: dict[str, str], notification: str
) -> None:
    service = Mock()
    service.events.return_value.move.return_value.execute.return_value = {
        "id": "original",
        "summary": "Planning",
        "organizer": {"email": "team@example.com"},
        "htmlLink": "https://calendar.google.com/event?eid=original",
    }
    result = await _move(
        service,
        calendar_id="personal@example.com",
        event_id="original",
        destination_calendar_id="team@example.com",
        **args,
    )
    assert result == (
        "Moved event 'Planning' (ID: original) from calendar 'personal@example.com' "
        "to 'team@example.com'. Link: https://calendar.google.com/event?eid=original"
    )
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
async def test_invalid_move_makes_no_google_call(
    kwargs: dict[str, Any], message: str
) -> None:
    service = Mock()
    with pytest.raises(UserInputError, match=message):
        await _move(service, **kwargs)
    assert service.mock_calls == []


@pytest.mark.asyncio
async def test_move_propagates_http_error() -> None:
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
