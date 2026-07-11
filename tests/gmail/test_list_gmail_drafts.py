"""Tests for the list_gmail_drafts tool (draft rascunho family, F1-owned implementation).

NOTE: gmail.gmail_tools.list_gmail_drafts does not exist yet in this worktree --
family F1 is implementing it in parallel on gmail/gmail_tools.py. These tests are
written against the agreed contract and will fail to import/collect until that
implementation lands and this worktree is merged with it. That failure mode is
expected at authoring time.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import list_gmail_drafts


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _draft_entry(draft_id: str, message_id: str, thread_id: str) -> dict:
    return {"id": draft_id, "message": {"id": message_id, "threadId": thread_id}}


def _metadata_message(message_id: str, thread_id: str, subject: str, snippet: str) -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
        "snippet": snippet,
        "payload": {"headers": [{"name": "Subject", "value": subject}]},
    }


def _message_get_side_effect(responses_by_message_id: dict):
    """Return a side_effect callable keyed by the `id` kwarg, mirroring the
    keyed-Mock idiom used in tests/gmail/test_body_format.py."""

    def message_get(**kwargs):
        request = Mock()
        request.execute.return_value = responses_by_message_id[kwargs["id"]]
        return request

    return message_get


@pytest.mark.asyncio
async def test_list_gmail_drafts_returns_two_drafts_with_details():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {
        "drafts": [
            _draft_entry("draft1", "msg1", "thread1"),
            _draft_entry("draft2", "msg2", "thread2"),
        ]
    }
    mock_service.users().messages().get.side_effect = _message_get_side_effect(
        {
            "msg1": _metadata_message(
                "msg1", "thread1", "Quarterly report", "Please find attached the report..."
            ),
            "msg2": _metadata_message(
                "msg2", "thread2", "Lunch tomorrow?", "Are you free at noon..."
            ),
        }
    )

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert isinstance(result, str)
    assert "draft1" in result
    assert "msg1" in result
    assert "thread1" in result
    assert "Quarterly report" in result
    assert "Please find attached the report" in result
    assert "draft2" in result
    assert "msg2" in result
    assert "thread2" in result
    assert "Lunch tomorrow?" in result
    assert "Are you free at noon" in result

    # Each draft's subject/snippet must come from a per-draft metadata fetch.
    get_calls = mock_service.users.return_value.messages.return_value.get.call_args_list
    assert len(get_calls) == 2
    for call in get_calls:
        assert call.kwargs["format"] == "metadata"
        assert call.kwargs["metadataHeaders"] == ["Subject"]


@pytest.mark.asyncio
async def test_list_gmail_drafts_returns_empty_page_message():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {"drafts": []}

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert isinstance(result, str)
    assert result.strip() != ""
    # No drafts means no per-draft subject/snippet lookups should happen.
    assert mock_service.users.return_value.messages.return_value.get.call_count == 0


@pytest.mark.asyncio
async def test_list_gmail_drafts_uses_default_page_size_without_optional_params():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {"drafts": []}

    await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    list_kwargs = mock_service.users.return_value.drafts.return_value.list.call_args.kwargs
    assert list_kwargs["userId"] == "me"
    assert list_kwargs["maxResults"] == 20
    assert "pageToken" not in list_kwargs
    assert "q" not in list_kwargs


@pytest.mark.asyncio
async def test_list_gmail_drafts_passes_page_token_and_query_to_list_request():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {"drafts": []}

    await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
        page_size=5,
        page_token="tok123",
        query="is:draft has:attachment",
    )

    list_kwargs = mock_service.users.return_value.drafts.return_value.list.call_args.kwargs
    assert list_kwargs["userId"] == "me"
    assert list_kwargs["maxResults"] == 5
    assert list_kwargs["pageToken"] == "tok123"
    assert list_kwargs["q"] == "is:draft has:attachment"


@pytest.mark.asyncio
async def test_list_gmail_drafts_caps_page_size_at_fifty():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {"drafts": []}

    await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
        page_size=100,
    )

    list_kwargs = mock_service.users.return_value.drafts.return_value.list.call_args.kwargs
    assert list_kwargs["maxResults"] == 50


@pytest.mark.asyncio
async def test_list_gmail_drafts_surfaces_next_page_token():
    mock_service = Mock()
    mock_service.users().drafts().list().execute.return_value = {
        "drafts": [_draft_entry("draft1", "msg1", "thread1")],
        "nextPageToken": "next-token-abc",
    }
    mock_service.users().messages().get.side_effect = _message_get_side_effect(
        {"msg1": _metadata_message("msg1", "thread1", "Hello", "snippet text")}
    )

    result = await _unwrap(list_gmail_drafts)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "next-token-abc" in result
