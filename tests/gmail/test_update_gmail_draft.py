"""Tests for the update_gmail_draft tool.

Behavior notes (confirmed against the implementation in gmail/gmail_tools.py):
  * `attach_to_thread` defaults to False, and when False the update body's
    "message" dict does NOT get a "threadId" key even if thread_id is passed
    (only attach_to_thread=True adds it).
  * When thread_id is given and in_reply_to/references/to are not all explicit,
    update_gmail_draft fetches the thread via threads().get() to derive reply
    headers/recipient (same reply-context logic as draft_gmail_message), so
    those tests must mock a realistic thread response.
  * When to, in_reply_to and references are all explicit, no thread fetch
    happens and the headers pass straight through into the raw MIME message.
"""

import base64
import os
import sys
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gmail.gmail_tools import update_gmail_draft


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _parse_raw_message(raw_message: str):
    return BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw_message)
    )


def _mock_update_response(draft_id: str, message_id: str) -> dict:
    return {"id": draft_id, "message": {"id": message_id}}


def _thread_response(*message_ids):
    """Realistic threads().get() payload, mirroring test_draft_gmail_message.py."""
    return {
        "messages": [
            {
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": message_id},
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "Subject", "value": "Meeting tomorrow"},
                    ],
                }
            }
            for message_id in message_ids
        ]
    }


@pytest.mark.asyncio
async def test_update_gmail_draft_builds_raw_message_with_new_subject_and_body():
    mock_service = Mock()
    mock_service.users().drafts().update().execute.return_value = _mock_update_response(
        "draft1", "newmsg1"
    )

    await _unwrap(update_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft1",
        subject="New subject",
        body="New body content",
        to="recipient@example.com",
        include_signature=False,
    )

    update_kwargs = (
        mock_service.users.return_value.drafts.return_value.update.call_args.kwargs
    )
    assert update_kwargs["userId"] == "me"
    assert update_kwargs["id"] == "draft1"

    raw_message = update_kwargs["body"]["message"]["raw"]
    parsed = _parse_raw_message(raw_message)

    assert parsed["Subject"] == "New subject"
    assert parsed["To"] == "recipient@example.com"
    assert "New body content" in parsed.get_body(preferencelist=("plain",)).get_content()


@pytest.mark.asyncio
async def test_update_gmail_draft_reports_draft_and_message_id_in_result():
    mock_service = Mock()
    mock_service.users().drafts().update().execute.return_value = _mock_update_response(
        "draft42", "newmsg42"
    )

    result = await _unwrap(update_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft42",
        subject="Subject",
        body="Body",
        include_signature=False,
    )

    assert isinstance(result, str)
    assert "draft42" in result
    assert "newmsg42" in result


@pytest.mark.asyncio
async def test_update_gmail_draft_supports_html_body_format():
    mock_service = Mock()
    mock_service.users().drafts().update().execute.return_value = _mock_update_response(
        "draft1", "newmsg1"
    )

    await _unwrap(update_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft1",
        subject="HTML update",
        body="<p>Updated content</p>",
        body_format="html",
        to="recipient@example.com",
        include_signature=False,
    )

    update_kwargs = (
        mock_service.users.return_value.drafts.return_value.update.call_args.kwargs
    )
    raw_message = update_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "<p>Updated content</p>" in raw_text


@pytest.mark.asyncio
async def test_update_gmail_draft_attach_to_thread_includes_thread_id_in_body():
    mock_service = Mock()
    mock_service.users().drafts().update().execute.return_value = _mock_update_response(
        "draft1", "newmsg1"
    )
    # thread_id without explicit in_reply_to/references/to triggers a thread
    # fetch to derive the reply headers, so the mock must return real messages.
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
    )

    await _unwrap(update_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft1",
        subject="Reply",
        body="Reply body",
        thread_id="thread123",
        attach_to_thread=True,
        include_signature=False,
    )

    update_kwargs = (
        mock_service.users.return_value.drafts.return_value.update.call_args.kwargs
    )
    assert update_kwargs["body"]["message"]["threadId"] == "thread123"

    # Reply headers derived from the fetched thread should land in the raw MIME.
    raw_message = update_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")
    assert "In-Reply-To: <msg2@example.com>" in raw_text
    assert "References: <msg1@example.com> <msg2@example.com>" in raw_text


@pytest.mark.asyncio
async def test_update_gmail_draft_attach_to_thread_without_thread_id_raises_user_input_error():
    mock_service = Mock()

    with pytest.raises(UserInputError):
        await _unwrap(update_gmail_draft)(
            service=mock_service,
            user_google_email="user@example.com",
            draft_id="draft1",
            subject="Reply",
            body="Reply body",
            attach_to_thread=True,
            include_signature=False,
        )

    assert mock_service.users.return_value.drafts.return_value.update.call_count == 0


@pytest.mark.asyncio
async def test_update_gmail_draft_without_attach_to_thread_omits_thread_id():
    mock_service = Mock()
    mock_service.users().drafts().update().execute.return_value = _mock_update_response(
        "draft1", "newmsg1"
    )
    # thread_id without explicit in_reply_to/references/to triggers a thread
    # fetch to derive the reply headers, so the mock must return real messages.
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
    )

    await _unwrap(update_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft1",
        subject="Reply",
        body="Reply body",
        thread_id="thread123",
        include_signature=False,
    )

    update_kwargs = (
        mock_service.users.return_value.drafts.return_value.update.call_args.kwargs
    )
    assert "threadId" not in update_kwargs["body"]["message"]


@pytest.mark.asyncio
async def test_update_gmail_draft_includes_explicit_in_reply_to_and_references_headers():
    mock_service = Mock()
    mock_service.users().drafts().update().execute.return_value = _mock_update_response(
        "draft1", "newmsg1"
    )

    await _unwrap(update_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft1",
        subject="Re: Meeting tomorrow",
        body="Replying to an earlier message.",
        to="recipient@example.com",
        thread_id="thread123",
        in_reply_to="<msg2@example.com>",
        references="<msg1@example.com> <msg2@example.com>",
        include_signature=False,
    )

    update_kwargs = (
        mock_service.users.return_value.drafts.return_value.update.call_args.kwargs
    )
    raw_message = update_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "In-Reply-To: <msg2@example.com>" in raw_text
    assert "References: <msg1@example.com> <msg2@example.com>" in raw_text
