import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

from core.server import server
from core.utils import GOOGLE_API_WRITE_RETRIES, UserInputError
from gmail.gmail_tools import draft_gmail_message


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_delete_uses_draft_id_without_creating_or_modifying_messages():
    service = Mock()
    result = await _unwrap(draft_gmail_message)(
        service=service,
        user_google_email="user@example.com",
        action="delete",
        draft_id="r123456789",
    )

    service.users().drafts().delete.assert_called_once_with(
        userId="me", id="r123456789"
    )
    service.users().drafts().delete().execute.assert_called_once_with(
        num_retries=GOOGLE_API_WRITE_RETRIES
    )
    service.users().drafts().create.assert_not_called()
    service.users().messages.assert_not_called()
    service.users().settings.assert_not_called()
    assert result == "Draft permanently deleted. Draft ID: r123456789"


@pytest.mark.asyncio
@pytest.mark.parametrize("draft_id", [None, "", "   "])
async def test_delete_requires_a_nonempty_draft_id(draft_id):
    service = Mock()
    with pytest.raises(UserInputError, match="draft_id is required"):
        await _unwrap(draft_gmail_message)(
            service=service,
            user_google_email="user@example.com",
            action="delete",
            draft_id=draft_id,
        )
    service.users.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_delete_propagates_api_errors_without_claiming_success(status):
    service = Mock()
    error = HttpError(
        SimpleNamespace(status=status, reason="Request failed"),
        json.dumps({"error": {"message": "Request failed"}}).encode(),
    )
    service.users().drafts().delete().execute.side_effect = error
    with pytest.raises(HttpError) as raised:
        await _unwrap(draft_gmail_message)(
            service=service,
            user_google_email="user@example.com",
            action="delete",
            draft_id="r123456789",
        )
    assert raised.value is error
    service.users().drafts().create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({}, "subject and body are required"),
        ({"subject": "Hello"}, "subject and body are required"),
        ({"body": "Hello"}, "subject and body are required"),
        ({"draft_id": "r123456789"}, "draft_id is only supported"),
        ({"action": "invalid"}, "action must be"),
    ],
)
async def test_create_rejects_missing_content_or_ambiguous_draft_id(kwargs, message):
    service = Mock()
    with pytest.raises(UserInputError, match=message):
        await _unwrap(draft_gmail_message)(
            service=service, user_google_email="user@example.com", **kwargs
        )
    service.users.assert_not_called()


@pytest.mark.asyncio
async def test_deletion_is_discoverable_and_marked_destructive():
    tools = {tool.name: tool for tool in await server.list_tools()}
    tool = tools["draft_gmail_message"]
    schema = tool.parameters
    assert schema["properties"]["action"]["enum"] == ["create", "delete"]
    assert schema["properties"]["action"]["default"] == "create"
    assert "subject" not in schema.get("required", [])
    assert "body" not in schema.get("required", [])
    assert "draft_id" in schema["properties"]
    assert tool.annotations.destructiveHint is True
    assert tool.annotations.readOnlyHint is False
    assert "delete_gmail_draft" not in tools
