"""Tests for send_message creating spaces and pinning messages."""

import json
import ssl
from unittest.mock import ANY, AsyncMock, Mock

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
    spaces.members.return_value.get.return_value.execute.return_value = {
        "state": "JOINED"
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
            "requestId": ANY,
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
async def test_create_space_leaves_out_the_caller(chat_service):
    await _send(
        create_space_name="Project 8542",
        member_emails=["Test@Example.com", "a@example.com"],
    )

    spaces = chat_service.spaces.return_value
    assert spaces.setup.call_args.kwargs["body"]["memberships"] == [
        {"member": {"name": "users/a@example.com", "type": "HUMAN"}}
    ]
    spaces.members.return_value.get.assert_called_once_with(
        name="spaces/NEW/members/a@example.com"
    )


@pytest.mark.asyncio
async def test_create_space_cleans_member_emails(chat_service):
    await _send(
        create_space_name="Project 8542",
        member_emails=["users/a@example.com", "A@Example.com", "b@example.com"],
    )

    spaces = chat_service.spaces.return_value
    assert spaces.setup.call_args.kwargs["body"]["memberships"] == [
        {"member": {"name": "users/a@example.com", "type": "HUMAN"}},
        {"member": {"name": "users/b@example.com", "type": "HUMAN"}},
    ]
    assert spaces.members.return_value.get.call_count == 2


@pytest.mark.asyncio
async def test_create_space_with_only_the_caller_sends_no_memberships(chat_service):
    await _send(create_space_name="Solo", member_emails=["test@example.com", " "])

    spaces = chat_service.spaces.return_value
    assert "memberships" not in spaces.setup.call_args.kwargs["body"]
    spaces.members.return_value.get.assert_not_called()


@pytest.mark.asyncio
async def test_create_space_lists_invited_and_missing_members(chat_service):
    states = {"a@example.com": "INVITED", "b@example.com": "NOT_A_MEMBER"}
    members = chat_service.spaces.return_value.members.return_value
    members.get.side_effect = lambda name: Mock(
        execute=Mock(return_value={"state": states[name.rsplit("/", 1)[1]]})
    )

    result = await _send(
        create_space_name="Project 8542",
        member_emails=["a@example.com", "b@example.com"],
    )

    assert "Invited (pending acceptance): a@example.com." in result
    assert "Member additions not confirmed for: b@example.com." in result


@pytest.mark.asyncio
async def test_create_space_and_pin_pins_in_the_new_space(chat_service):
    result = await _send(create_space_name="Project 8542", pin=True)

    chat_service.spaces.return_value.messagePins.return_value.create.assert_called_once_with(
        parent="spaces/NEW", body={"message": "spaces/NEW/messages/M"}
    )
    assert "Created space" in result and "Pinned." in result


@pytest.mark.asyncio
async def test_setup_interrupted_twice_says_the_space_may_exist(chat_service):
    setup = chat_service.spaces.return_value.setup
    setup.return_value.execute.side_effect = ConnectionResetError("connection reset")

    with pytest.raises(ToolError, match="may already exist"):
        await _send(create_space_name="Project 8542")

    assert setup.call_count == 2
    chat_service.spaces.return_value.messages.return_value.create.assert_not_called()


@pytest.mark.asyncio
async def test_create_space_confirms_each_member(chat_service):
    result = await _send(
        create_space_name="Project 8542", member_emails=["a@example.com"]
    )

    chat_service.spaces.return_value.members.return_value.get.assert_called_once_with(
        name="spaces/NEW/members/a@example.com"
    )
    assert "not confirmed" not in result


@pytest.mark.asyncio
async def test_create_space_reports_members_not_confirmed(chat_service):
    def get(name):
        request = Mock()
        request.execute.return_value = {"state": "JOINED"}
        if name.endswith("b@example.com"):
            request.execute.side_effect = HttpError(
                Response({"status": "404"}),
                json.dumps({"error": {"code": 404, "message": "Not found"}}).encode(),
            )
        elif name.endswith("c@example.com"):
            request.execute.side_effect = ConnectionResetError("connection reset")
        return request

    chat_service.spaces.return_value.members.return_value.get.side_effect = get

    result = await _send(
        create_space_name="Project 8542",
        member_emails=["a@example.com", "b@example.com", "c@example.com"],
    )

    assert "Member additions not confirmed for: b@example.com, c@example.com." in result
    assert "Message sent to space 'spaces/NEW'" in result


@pytest.mark.asyncio
async def test_create_space_without_memberships_scope_says_members_unconfirmed(
    chat_service,
):
    members = chat_service.spaces.return_value.members.return_value
    members.get.return_value.execute.side_effect = _scope_error()

    result = await _send(
        create_space_name="Project 8542",
        member_emails=["a@example.com", "b@example.com"],
    )

    assert "chat.memberships.readonly scope" in result
    members.get.assert_called_once()
    assert "Message sent to space 'spaces/NEW'" in result


@pytest.mark.asyncio
async def test_create_space_without_members_omits_memberships(chat_service):
    await _send(create_space_name="Solo")

    chat_service.spaces.return_value.setup.assert_called_once_with(
        body={
            "space": {"spaceType": "SPACE", "displayName": "Solo"},
            "requestId": ANY,
        }
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
async def test_interrupted_setup_retries_once_with_the_same_request_id(chat_service):
    setup = chat_service.spaces.return_value.setup
    setup.return_value.execute.side_effect = [
        ConnectionResetError("connection reset"),
        {"name": "spaces/NEW", "displayName": "Project 8542"},
    ]

    result = await _send(create_space_name="Project 8542")

    first, second = setup.call_args_list
    assert first.kwargs["body"]["requestId"]
    assert first.kwargs["body"] == second.kwargs["body"]
    assert "Created space 'Project 8542' (ID: spaces/NEW)" in result


@pytest.mark.asyncio
async def test_setup_http_error_is_not_retried(chat_service):
    setup = chat_service.spaces.return_value.setup
    setup.return_value.execute.side_effect = HttpError(
        Response({"status": "409"}),
        json.dumps({"error": {"code": 409, "message": "ALREADY_EXISTS"}}).encode(),
    )

    with pytest.raises(Exception, match="ALREADY_EXISTS"):
        await _send(create_space_name="Project 8542")

    setup.assert_called_once()
    chat_service.spaces.return_value.messages.return_value.create.assert_not_called()


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
        (
            {"create_space_name": "X", "thread_name": "spaces/S/threads/T"},
            "thread_name",
        ),
        ({"create_space_name": "X" * 129}, "128 characters"),
        ({"create_space_name": "X", "member_emails": ["Alice"]}, "Alice"),
        (
            {
                "create_space_name": "X",
                "member_emails": [f"u{i}@example.com" for i in range(50)],
            },
            "at most 49",
        ),
    ],
)
async def test_invalid_space_arguments(chat_service, kwargs, match):
    with pytest.raises(UserInputError, match=match):
        await _send(**kwargs)

    chat_service.spaces.assert_not_called()
