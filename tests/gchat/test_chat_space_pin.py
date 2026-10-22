"""Tests for send_message creating spaces and pinning messages."""

import json
import ssl
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError
from httplib2 import Response

import auth.service_decorator as service_decorator
from auth.scopes import CHAT_SPACES_SCOPE, CHAT_WRITE_SCOPE
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
    spaces = service.spaces.return_value
    spaces.setup.return_value.execute.return_value = {
        "name": "spaces/NEW",
        "displayName": "Project 8542",
    }
    spaces.messages.return_value.create.return_value.execute.return_value = {
        "name": "spaces/NEW/messages/M",
        "createTime": "2026-01-01T00:00:00Z",
    }
    spaces.messagePins.return_value.create.return_value.execute.return_value = {
        "name": "spaces/NEW/messagePins/M",
        "message": "spaces/NEW/messages/M",
    }
    return service


async def _send(**kwargs):
    public_fn = getattr(send_message, "fn", send_message)
    return await public_fn(
        user_google_email="test@example.com", message_text="hello", **kwargs
    )


def _scope_error():
    detail = {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    }
    body = {"error": {"code": 403, "message": "Forbidden", "details": [detail]}}
    return HttpError(Response({"status": "403"}), json.dumps(body).encode())


def test_plain_send_needs_only_messages_scope():
    assert send_message._required_google_scopes == [CHAT_WRITE_SCOPE]
    assert CHAT_SPACES_SCOPE not in send_message._required_google_scopes


@pytest.mark.asyncio
async def test_create_space_with_members_then_send(chat_service):
    result = await _send(
        create_space_name="Project 8542",
        member_emails=["a@example.com", " ", "b@example.com"],
    )

    spaces = chat_service.spaces.return_value
    spaces.setup.assert_called_once_with(
        body={
            "space": {"spaceType": "SPACE", "displayName": "Project 8542"},
            "memberships": [
                {"member": {"name": "users/a@example.com", "type": "HUMAN"}},
                {"member": {"name": "users/b@example.com", "type": "HUMAN"}},
            ],
        }
    )
    spaces.messages.return_value.create.assert_called_once_with(
        parent="spaces/NEW", body={"text": "hello"}
    )
    spaces.messagePins.return_value.create.assert_not_called()
    assert "Created space 'Project 8542' (ID: spaces/NEW)" in result
    assert "Message sent to space 'spaces/NEW'" in result


@pytest.mark.asyncio
async def test_create_space_without_members_omits_memberships(chat_service):
    await _send(create_space_name="Solo")

    chat_service.spaces.return_value.setup.assert_called_once_with(
        body={"space": {"spaceType": "SPACE", "displayName": "Solo"}}
    )


@pytest.mark.asyncio
async def test_pin_new_message(chat_service):
    result = await _send(space_id="spaces/NEW", pin=True)

    chat_service.spaces.return_value.messagePins.return_value.create.assert_called_once_with(
        parent="spaces/NEW", body={"message": "spaces/NEW/messages/M"}
    )
    assert "Pinned. Pin ID: spaces/NEW/messagePins/M" in result


@pytest.mark.asyncio
async def test_pin_edited_message(chat_service):
    messages = chat_service.spaces.return_value.messages.return_value
    messages.patch.return_value.execute.return_value = {"name": "spaces/S/messages/OLD"}

    result = await _send(
        space_id="spaces/S", message_name="spaces/S/messages/OLD", pin=True
    )

    messages.create.assert_not_called()
    chat_service.spaces.return_value.messagePins.return_value.create.assert_called_once_with(
        parent="spaces/S", body={"message": "spaces/S/messages/OLD"}
    )
    assert "Message updated" in result and "Pinned." in result


@pytest.mark.asyncio
async def test_pin_failure_still_reports_sent_message(chat_service):
    pins = chat_service.spaces.return_value.messagePins.return_value
    pins.create.return_value.execute.side_effect = HttpError(
        Response({"status": "403"}),
        json.dumps({"error": {"code": 403, "message": "Not allowed"}}).encode(),
    )

    result = await _send(space_id="spaces/NEW", pin=True)

    assert "Message sent to space 'spaces/NEW'" in result
    assert "Pin failed (message was still delivered)" in result


@pytest.mark.asyncio
async def test_pin_of_already_pinned_message_is_not_a_failure(chat_service):
    pins = chat_service.spaces.return_value.messagePins.return_value
    pins.create.return_value.execute.side_effect = HttpError(
        Response({"status": "409"}),
        json.dumps({"error": {"code": 409, "message": "Already exists"}}).encode(),
    )

    result = await _send(space_id="spaces/NEW", pin=True)

    assert "Already pinned." in result
    assert "Pin failed" not in result


@pytest.mark.asyncio
async def test_send_failure_after_create_names_the_new_space(chat_service):
    messages = chat_service.spaces.return_value.messages.return_value
    messages.create.return_value.execute.side_effect = HttpError(
        Response({"status": "400"}),
        json.dumps({"error": {"code": 400, "message": "Bad request"}}).encode(),
    )

    with pytest.raises(ToolError, match="Retry with space_id='spaces/NEW'"):
        await _send(create_space_name="Project 8542")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [ConnectionResetError("connection reset"), ssl.SSLError("bad record")]
)
async def test_interrupted_send_after_create_names_space_without_retrying(
    chat_service, error
):
    spaces = chat_service.spaces.return_value
    spaces.messages.return_value.create.return_value.execute.side_effect = error

    with pytest.raises(ToolError, match="may or may not have been delivered") as exc:
        await _send(create_space_name="Project 8542")

    assert "space_id='spaces/NEW'" in str(exc.value)
    spaces.setup.assert_called_once()


@pytest.mark.asyncio
async def test_create_space_without_spaces_scope_says_so(chat_service):
    spaces = chat_service.spaces.return_value
    spaces.setup.return_value.execute.side_effect = _scope_error()

    with pytest.raises(ToolError, match="chat.spaces scope"):
        await _send(create_space_name="Project 8542")

    spaces.messages.return_value.create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (_scope_error(), "pinning needs the chat.spaces scope"),
        (ConnectionResetError("connection reset"), "connection reset"),
    ],
)
async def test_pin_errors_still_report_the_delivered_message(
    chat_service, error, expected
):
    pins = chat_service.spaces.return_value.messagePins.return_value
    pins.create.return_value.execute.side_effect = error

    result = await _send(space_id="spaces/NEW", pin=True)

    assert "Message sent to space 'spaces/NEW'" in result
    assert "Pin failed (message was still delivered)" in result
    assert expected in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "space_id"),
        ({"space_id": "  "}, "space_id"),
        ({"space_id": "spaces/S", "create_space_name": "X"}, "create_space_name"),
        (
            {"create_space_name": "X", "message_name": "spaces/S/messages/M"},
            "create_space_name",
        ),
        ({"space_id": "spaces/S", "member_emails": ["a@example.com"]}, "member_emails"),
    ],
)
async def test_invalid_space_arguments(chat_service, kwargs, match):
    with pytest.raises(UserInputError, match=match):
        await _send(**kwargs)

    chat_service.spaces.assert_not_called()
