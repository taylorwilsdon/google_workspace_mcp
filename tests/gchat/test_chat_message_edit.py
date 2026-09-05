"""Regression tests for message edits through the registered tool's decorators."""

import json
from unittest.mock import AsyncMock, Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

import auth.service_decorator as service_decorator
from core.server import server
from core.utils import UserInputError
from gchat.chat_tools import send_message


@pytest.fixture
def chat_service(monkeypatch):
    """Inject a fake API service while retaining auth and HTTP error wrappers."""
    service = Mock()
    monkeypatch.setattr(service_decorator, "_user_email_is_managed", lambda: False)
    monkeypatch.setattr(
        service_decorator,
        "_get_auth_context",
        AsyncMock(return_value=(None, None, None)),
    )
    monkeypatch.setattr(service_decorator, "_detect_oauth_version", lambda *args: False)
    monkeypatch.setattr(
        service_decorator,
        "_authenticate_service",
        AsyncMock(return_value=(service, "test@example.com")),
    )
    return service


async def _edit_message(message_name):
    # Access the callable without unwrapping authentication or HTTP error handling.
    public_fn = getattr(send_message, "fn", send_message)
    return await public_fn(
        user_google_email="test@example.com",
        space_id="spaces/S",
        message_text="corrected text",
        message_name=message_name,
    )


@pytest.mark.asyncio
async def test_send_message_advertises_destructive_updates():
    tool = await server.get_tool("send_message")
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_name",
    [
        "",
        "M",
        "spaces/S/messages/",
        "spaces/S/messages/M/extra",
        "spaces//messages/M",
        "rooms/S/messages/M",
        "spaces/S/threads/T",
        "spaces/OTHER/messages/M",
    ],
)
async def test_invalid_edit_remains_input_error(chat_service, message_name):
    with pytest.raises(UserInputError, match="message_name"):
        await _edit_message(message_name)

    chat_service.spaces.assert_not_called()
    chat_service.close.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_id", ["M", "BBBBBBBBBBB.BBBBBBBBBBB", "client-custom-name"]
)
async def test_valid_message_names_are_editable(chat_service, message_id):
    message_name = f"spaces/S/messages/{message_id}"
    messages = chat_service.spaces.return_value.messages.return_value
    messages.patch.return_value.execute.return_value = {
        "name": message_name,
        "lastUpdateTime": "2025-01-01T00:00:00Z",
    }

    result = await _edit_message(message_name)

    assert "Message updated" in result
    assert message_name in result
    messages.patch.assert_called_once_with(
        name=message_name, updateMask="text", body={"text": "corrected text"}
    )
    messages.create.assert_not_called()
    chat_service.close.assert_called_once_with()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "api_message"),
    [(403, "Cannot edit another user's message"), (404, "Message not found")],
)
async def test_edit_api_failure_is_surfaced_without_creating(
    chat_service, status, api_message
):
    messages = chat_service.spaces.return_value.messages.return_value
    error = HttpError(
        Response({"status": str(status)}),
        json.dumps({"error": {"code": status, "message": api_message}}).encode(),
    )
    messages.patch.return_value.execute.side_effect = error

    with pytest.raises(Exception, match="API error in send_message") as exc_info:
        await _edit_message("spaces/S/messages/M")

    assert api_message in str(exc_info.value)
    assert exc_info.value.__cause__ is error
    messages.patch.return_value.execute.assert_called_once_with()
    messages.create.assert_not_called()
    chat_service.close.assert_called_once_with()
