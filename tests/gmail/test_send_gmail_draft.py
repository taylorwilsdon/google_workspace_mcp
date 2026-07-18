"""Tests for send_gmail_draft (draft_id + edit-proof thread_id)."""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gmail.gmail_tools import send_gmail_draft


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_send_gmail_draft_sends_by_id():
    service = Mock()
    send_request = Mock()
    send_request.execute.return_value = {"id": "msg-123", "threadId": "thr-456"}
    service.users().drafts().send.return_value = send_request

    result = await _unwrap(send_gmail_draft)(
        service=service, user_google_email="user@example.com", draft_id="draft-abc"
    )

    service.users().drafts().send.assert_called_with(
        userId="me", body={"id": "draft-abc"}
    )
    assert "draft-abc" in result
    assert "msg-123" in result
    assert "thr-456" in result


@pytest.mark.asyncio
async def test_send_gmail_draft_by_thread_resolves_unique_draft():
    service = Mock()
    service.users().drafts().list().execute.return_value = {
        "drafts": [
            {"id": "d1", "message": {"id": "m1", "threadId": "thrX"}},
            {"id": "d2", "message": {"id": "m2", "threadId": "other"}},
        ]
    }
    send_request = Mock()
    send_request.execute.return_value = {"id": "sent1", "threadId": "thrX"}
    service.users().drafts().send.return_value = send_request

    result = await _unwrap(send_gmail_draft)(
        service=service, user_google_email="user@example.com", thread_id="thrX"
    )

    service.users().drafts().send.assert_called_with(userId="me", body={"id": "d1"})
    assert "sent1" in result


@pytest.mark.asyncio
async def test_send_gmail_draft_thread_no_draft_errors():
    service = Mock()
    service.users().drafts().list().execute.return_value = {
        "drafts": [{"id": "d1", "message": {"threadId": "other"}}]
    }
    with pytest.raises(UserInputError, match="No draft found"):
        await _unwrap(send_gmail_draft)(
            service=service, user_google_email="user@example.com", thread_id="thrX"
        )


@pytest.mark.asyncio
async def test_send_gmail_draft_thread_ambiguous_errors():
    service = Mock()
    service.users().drafts().list().execute.return_value = {
        "drafts": [
            {"id": "d1", "message": {"threadId": "thrX"}},
            {"id": "d2", "message": {"threadId": "thrX"}},
        ]
    }
    with pytest.raises(UserInputError, match="ambiguous"):
        await _unwrap(send_gmail_draft)(
            service=service, user_google_email="user@example.com", thread_id="thrX"
        )


@pytest.mark.asyncio
async def test_send_gmail_draft_requires_exactly_one_handle():
    service = Mock()
    with pytest.raises(UserInputError, match="exactly one"):
        await _unwrap(send_gmail_draft)(
            service=service, user_google_email="user@example.com"
        )
    with pytest.raises(UserInputError, match="exactly one"):
        await _unwrap(send_gmail_draft)(
            service=service,
            user_google_email="user@example.com",
            draft_id="d1",
            thread_id="t1",
        )


@pytest.mark.asyncio
async def test_send_gmail_draft_thread_resolution_paginates():
    """The target draft on a later page is still found (no false 'not found')."""
    service = Mock()
    service.users().drafts().list().execute.side_effect = [
        {
            "drafts": [{"id": "d1", "message": {"threadId": "other"}}],
            "nextPageToken": "p2",
        },
        {"drafts": [{"id": "d2", "message": {"threadId": "thrX"}}]},
    ]
    send_request = Mock()
    send_request.execute.return_value = {"id": "sent2", "threadId": "thrX"}
    service.users().drafts().send.return_value = send_request

    result = await _unwrap(send_gmail_draft)(
        service=service, user_google_email="user@example.com", thread_id="thrX"
    )

    service.users().drafts().send.assert_called_with(userId="me", body={"id": "d2"})
    assert "sent2" in result
