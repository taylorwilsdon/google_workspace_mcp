"""Tests for the update_gmail_draft tool (draft rascunho family, F1-owned implementation).

NOTE: gmail.gmail_tools.update_gmail_draft does not exist yet in this worktree --
family F1 is implementing it in parallel on gmail/gmail_tools.py. These tests are
written against the agreed contract and will fail to import/collect until that
implementation lands and this worktree is merged with it. That failure mode is
expected at authoring time.

Assumptions made about behavior not pinned down by the contract (flagged so the
orchestrator/reviewer can confirm against the real implementation after merge):
  * `attach_to_thread` defaults to False, and when False the update body's
    "message" dict does NOT get a "threadId" key even if thread_id is passed
    (only attach_to_thread=True adds it, per the contract).
  * in_reply_to/references are asserted as direct passthrough into the raw MIME
    headers (mirroring the explicit-header assertions in
    test_draft_gmail_message_uses_explicit_in_reply_to_when_filling_references);
    we do NOT assert anything about automatic derivation from a thread fetch
    (e.g. a threads().get() call), since the contract does not specify that
    update_gmail_draft performs one.
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
