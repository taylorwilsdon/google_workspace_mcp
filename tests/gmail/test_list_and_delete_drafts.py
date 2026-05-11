"""Tests for list_gmail_drafts and delete_gmail_draft tools."""
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import (  # noqa: E402
    delete_gmail_draft,
    list_gmail_drafts,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _draft_metadata(draft_id, subject, to, from_addr="user@example.com", snippet=""):
    return {
        "id": draft_id,
        "message": {
            "id": f"msg_{draft_id}",
            "threadId": f"thread_{draft_id}",
            "snippet": snippet,
            "payload": {
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "From", "value": from_addr},
                    {"name": "To", "value": to},
                ]
            },
        },
    }


@pytest.mark.asyncio
async def test_list_gmail_drafts_returns_formatted_drafts():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {
        "drafts": [
            {"id": "r123", "message": {"id": "msg_r123", "threadId": "t1"}},
            {"id": "r456", "message": {"id": "msg_r456", "threadId": "t2"}},
        ]
    }
    mock_service.users().drafts().get().execute.side_effect = [
        _draft_metadata("r123", "First draft", "alice@example.com", snippet="Hi Alice"),
        _draft_metadata("r456", "Second draft", "bob@example.com", snippet="Hi Bob"),
    ]

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "Found 2 draft(s)" in result
    assert "Draft ID: r123" in result
    assert "First draft" in result
    assert "alice@example.com" in result
    assert "Draft ID: r456" in result
    assert "Second draft" in result
    assert "bob@example.com" in result


@pytest.mark.asyncio
async def test_list_gmail_drafts_empty_account():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {}

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert result == "No drafts found."


@pytest.mark.asyncio
async def test_list_gmail_drafts_passes_query_to_api():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {}

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
        query="subject:invoice",
    )

    assert "matching 'subject:invoice'" in result
    list_kwargs = (
        mock_service.users.return_value.drafts.return_value.list.call_args.kwargs
    )
    assert list_kwargs["q"] == "subject:invoice"
    assert list_kwargs["userId"] == "me"


@pytest.mark.asyncio
async def test_list_gmail_drafts_propagates_page_token():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {
        "drafts": [{"id": "r1", "message": {"id": "msg_r1", "threadId": "t1"}}],
        "nextPageToken": "token-next",
    }
    mock_service.users().drafts().get().execute.return_value = _draft_metadata(
        "r1", "Subject", "to@example.com"
    )

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
        page_token="token-current",
    )

    list_kwargs = (
        mock_service.users.return_value.drafts.return_value.list.call_args.kwargs
    )
    assert list_kwargs["pageToken"] == "token-current"
    assert "page_token='token-next'" in result


@pytest.mark.asyncio
async def test_delete_gmail_draft_calls_api_and_confirms():
    mock_service = Mock()
    mock_service.users().drafts().delete().execute.return_value = None

    result = await _unwrap(delete_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="r_abc123",
    )

    assert "Draft r_abc123 deleted successfully." == result
    delete_kwargs = (
        mock_service.users.return_value.drafts.return_value.delete.call_args.kwargs
    )
    assert delete_kwargs["id"] == "r_abc123"
    assert delete_kwargs["userId"] == "me"
