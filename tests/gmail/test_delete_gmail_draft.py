"""Tests for deleting Gmail drafts by either draft or contained message ID."""

from unittest.mock import AsyncMock, Mock, call

import pytest

from auth.scopes import GMAIL_COMPOSE_SCOPE
from core.server import server
from core.utils import GOOGLE_API_WRITE_RETRIES, UserInputError
from gmail.gmail_helpers import _delete_gmail_draft_by_identifier
import gmail.gmail_tools as gmail_tools


def _unwrap(tool):
    """Unwrap FastMCP and service decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _request(response):
    request = Mock()
    request.execute.return_value = response
    return request


def _build_service(*draft_pages):
    service = Mock()
    service.users().drafts().list.side_effect = [_request(page) for page in draft_pages]
    service.users().drafts().delete.return_value = _request({})
    return service


@pytest.mark.asyncio
async def test_delete_gmail_draft_accepts_draft_id():
    """Removing draft-ID resolution or the delete call must fail this test."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "draft-123",
                    "message": {"id": "message-123", "threadId": "thread-1"},
                }
            ]
        }
    )
    result = await _delete_gmail_draft_by_identifier(service, "draft-123")

    service.users().drafts().delete.assert_called_once_with(userId="me", id="draft-123")
    service.users().drafts().delete.return_value.execute.assert_called_once_with(
        num_retries=GOOGLE_API_WRITE_RETRIES
    )
    assert result == "Draft permanently deleted! Draft ID: draft-123"


@pytest.mark.asyncio
async def test_delete_gmail_draft_accepts_contained_message_id():
    """Removing contained-message-ID resolution must fail this test."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "draft-456",
                    "message": {"id": "message-456", "threadId": "thread-1"},
                }
            ]
        }
    )

    result = await _delete_gmail_draft_by_identifier(service, "message-456")

    service.users().drafts().delete.assert_called_once_with(userId="me", id="draft-456")
    assert result == "Draft permanently deleted! Draft ID: draft-456"


@pytest.mark.asyncio
async def test_delete_gmail_draft_follows_draft_pagination():
    """Removing page-token traversal must fail this test."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "draft-first-page",
                    "message": {"id": "message-first-page", "threadId": "thread-1"},
                }
            ],
            "nextPageToken": "page-2",
        },
        {
            "drafts": [
                {
                    "id": "draft-second-page",
                    "message": {"id": "message-second-page", "threadId": "thread-2"},
                }
            ]
        },
    )

    result = await _delete_gmail_draft_by_identifier(service, "message-second-page")

    assert service.users().drafts().list.call_args_list == [
        call(userId="me", maxResults=500),
        call(userId="me", maxResults=500, pageToken="page-2"),
    ]
    service.users().drafts().delete.assert_called_once_with(
        userId="me", id="draft-second-page"
    )
    assert result == "Draft permanently deleted! Draft ID: draft-second-page"


@pytest.mark.asyncio
async def test_delete_gmail_draft_refuses_ambiguous_identifier():
    """Removing ambiguity protection must fail this destructive-operation test."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "shared-identifier",
                    "message": {"id": "message-one", "threadId": "thread-1"},
                },
                {
                    "id": "draft-two",
                    "message": {
                        "id": "shared-identifier",
                        "threadId": "thread-2",
                    },
                },
            ]
        }
    )

    with pytest.raises(UserInputError, match="matches multiple Gmail drafts"):
        await _delete_gmail_draft_by_identifier(service, "shared-identifier")

    service.users().drafts().delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_gmail_draft_scans_later_pages_for_ambiguity():
    """A first-page match must not bypass a collision on a later page."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "shared-identifier",
                    "message": {"id": "message-one", "threadId": "thread-1"},
                }
            ],
            "nextPageToken": "page-2",
        },
        {
            "drafts": [
                {
                    "id": "draft-two",
                    "message": {
                        "id": "shared-identifier",
                        "threadId": "thread-2",
                    },
                }
            ]
        },
    )

    with pytest.raises(UserInputError, match="matches multiple Gmail drafts"):
        await _delete_gmail_draft_by_identifier(service, "shared-identifier")

    assert service.users().drafts().list.call_args_list == [
        call(userId="me", maxResults=500),
        call(userId="me", maxResults=500, pageToken="page-2"),
    ]
    service.users().drafts().delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_gmail_draft_selects_one_of_multiple_drafts_in_same_thread():
    """Choosing by thread instead of the contained Message ID must fail this test."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "draft-old",
                    "message": {"id": "message-old", "threadId": "shared-thread"},
                },
                {
                    "id": "draft-new",
                    "message": {"id": "message-new", "threadId": "shared-thread"},
                },
            ]
        }
    )

    result = await _delete_gmail_draft_by_identifier(service, "message-old")

    service.users().drafts().delete.assert_called_once_with(userId="me", id="draft-old")
    assert "draft-old" in result


@pytest.mark.asyncio
async def test_delete_gmail_draft_rejects_unknown_identifier_without_deleting():
    """Deleting an arbitrary draft when resolution misses must fail this test."""
    service = _build_service(
        {
            "drafts": [
                {
                    "id": "draft-existing",
                    "message": {
                        "id": "message-existing",
                        "threadId": "thread-1",
                    },
                }
            ]
        }
    )

    with pytest.raises(UserInputError, match="No Gmail draft found"):
        await _delete_gmail_draft_by_identifier(service, "not-a-draft")

    service.users().drafts().delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_gmail_draft_tool_delegates_response_formatting(monkeypatch):
    service = Mock()
    helper = AsyncMock(return_value="helper-formatted response")
    monkeypatch.setattr(gmail_tools, "_delete_gmail_draft_by_identifier", helper)

    result = await _unwrap(gmail_tools.delete_gmail_draft)(
        service=service,
        user_google_email="user@example.com",
        draft_identifier="draft-123",
    )

    helper.assert_awaited_once_with(service, "draft-123")
    assert result == "helper-formatted response"


@pytest.mark.asyncio
async def test_delete_gmail_draft_declares_scope_and_destructive_annotations():
    registered_tools = {tool.name: tool for tool in await server.list_tools()}
    annotations = registered_tools["delete_gmail_draft"].annotations

    assert gmail_tools.delete_gmail_draft._required_google_scopes == [
        GMAIL_COMPOSE_SCOPE
    ]
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is True
    assert annotations.idempotentHint is False
    assert annotations.openWorldHint is True
