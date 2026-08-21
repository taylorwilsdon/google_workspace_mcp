import base64
from email import policy
from email.parser import BytesParser
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from contextlib import asynccontextmanager

from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import gmail.gmail_tools as gmail_tools
from core.utils import UserInputError
from gmail.gmail_tools import (
    draft_gmail_message,
    send_gmail_message,
    _resolve_url_attachments,
    _try_read_local_attachment,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _thread_response(*message_ids):
    return {
        "messages": [
            {
                "payload": {
                    "headers": [{"name": "Message-ID", "value": message_id}],
                }
            }
            for message_id in message_ids
        ]
    }


def _encode_part(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode()


def _thread_message(
    message_id: str,
    *,
    subject: str = "Meeting tomorrow",
    from_value: str = "sender@example.com",
    reply_to: str | None = None,
    to_value: str = "user@example.com",
    cc_value: str | None = None,
    date: str = "Fri, 28 Mar 2026 10:00:00 -0400",
    text: str | None = None,
    html: str | None = None,
):
    headers = [
        {"name": "Message-ID", "value": message_id},
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_value},
        {"name": "To", "value": to_value},
        {"name": "Date", "value": date},
    ]
    if reply_to:
        headers.append({"name": "Reply-To", "value": reply_to})
    if cc_value:
        headers.append({"name": "Cc", "value": cc_value})

    payload = {"headers": headers}
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _encode_part(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _encode_part(html)}})
    if parts:
        payload["mimeType"] = "multipart/alternative"
        payload["parts"] = parts

    return {"payload": payload}


class _FakeStreamResponse:
    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    async def aiter_bytes(self, chunk_size=0):
        for chunk in self._chunks:
            yield chunk


def _mock_stream_response(response):
    @asynccontextmanager
    async def _stream(_url, **_kwargs):
        yield response

    return _stream


def _parse_raw_message(raw_message: str):
    return BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw_message)
    )


@pytest.mark.asyncio
async def test_draft_gmail_message_reports_actual_attachment_count(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
    attachment_path = tmp_path / "sample.txt"
    attachment_path.write_text("hello attachment", encoding="utf-8")

    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft123"}

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Attachment test",
        body="Please see attached.",
        attachments=[{"path": str(attachment_path)}],
        include_signature=False,
    )

    assert "Draft created with 1 attachment(s)! Draft ID: draft123" in result

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw_message)

    assert b"Content-Disposition: attachment;" in raw_bytes
    assert b"sample.txt" in raw_bytes


@pytest.mark.asyncio
async def test_draft_gmail_message_raises_when_no_attachments_are_added(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
    missing_path = tmp_path / "missing.txt"

    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft123"}

    with pytest.raises(UserInputError, match="No valid attachments were added"):
        await _unwrap(draft_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="Attachment test",
            body="Please see attached.",
            attachments=[{"path": str(missing_path)}],
            include_signature=False,
        )


@pytest.mark.asyncio
async def test_draft_gmail_message_surfaces_guidance_for_paths_outside_allowed_dirs(
    tmp_path, monkeypatch
):
    allowed_dir = tmp_path / "allowed"
    allowed_dir.mkdir()
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    blocked_path = blocked_dir / "sample.txt"
    blocked_path.write_text("hello attachment", encoding="utf-8")
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(allowed_dir))

    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft123"}

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(draft_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="Attachment test",
            body="Please see attached.",
            attachments=[{"path": str(blocked_path)}],
            include_signature=False,
        )

    message = str(exc_info.value)
    assert "No valid attachments were added" in message
    assert "permitted directories" in message
    assert "external mounts such as /run/media may be blocked" in message
    assert str(blocked_path) in message


@pytest.mark.asyncio
async def test_draft_gmail_message_appends_gmail_signature_html():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_sig"}
    mock_service.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {
                "sendAsEmail": "user@example.com",
                "isPrimary": True,
                "signature": "<div>Best,<br>Alice</div>",
            }
        ]
    }

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Signature test",
        body="<p>Hello</p>",
        body_format="html",
        include_signature=True,
    )

    assert "Draft created! Draft ID: draft_sig" in result

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "<p>Hello</p>" in raw_text
    assert "Best,<br>Alice" in raw_text


@pytest.mark.asyncio
async def test_send_gmail_message_appends_gmail_signature_html():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "msg_sig"}
    mock_service.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {
                "sendAsEmail": "user@example.com",
                "isPrimary": True,
                "signature": "<div>Best,<br>Alice</div>",
            }
        ]
    }

    result = await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Signature test",
        body="<p>Hello</p>",
        body_format="html",
        include_signature=True,
    )

    assert "Email sent! Message ID: msg_sig" in result

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    raw_message = send_kwargs["body"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "<p>Hello</p>" in raw_text
    assert "Best,<br>Alice" in raw_text


@pytest.mark.asyncio
async def test_send_gmail_message_appends_send_as_alias_signature_html():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "msg_alias"}
    list_mock = mock_service.users().settings().sendAs().list
    list_mock.return_value.execute.return_value = {
        "sendAs": [
            {
                "sendAsEmail": "user@example.com",
                "isPrimary": True,
                "signature": "<div>Primary,<br>Alice</div>",
            },
            {
                "sendAsEmail": "alias@example.com",
                "isPrimary": False,
                "signature": "<div>Alias,<br>Team</div>",
            },
        ]
    }

    result = await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Alias signature test",
        body="<p>Hello</p>",
        body_format="html",
        include_signature=True,
        from_email="alias@example.com",
    )

    assert "Email sent! Message ID: msg_alias" in result
    list_mock.assert_called_once_with(userId="me")

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    raw_message = send_kwargs["body"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "<p>Hello</p>" in raw_text
    assert "Alias,<br>Team" in raw_text
    assert "Primary,<br>Alice" not in raw_text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 403])
async def test_send_gmail_message_surfaces_signature_rate_limit_error(status):
    mock_service = Mock()
    http_error = HttpError(
        resp=SimpleNamespace(status=status, reason="Too Many Requests"),
        content=b'{"error":{"reason":"rateLimitExceeded"}}',
    )
    mock_service.users().settings().sendAs().list().execute.side_effect = http_error

    with pytest.raises(ToolError, match="Failed to fetch Gmail send-as signatures"):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="Signature test",
            body="<p>Hello</p>",
            body_format="html",
            include_signature=True,
        )

    assert mock_service.users.return_value.messages.return_value.send.call_count == 0


@pytest.mark.asyncio
async def test_send_gmail_message_skips_signature_when_disabled():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "msg_nosig"}
    list_mock = mock_service.users().settings().sendAs().list

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="No signature",
        body="<p>Hello</p>",
        body_format="html",
        include_signature=False,
    )

    # When include_signature is False we must NOT call the settings endpoint
    # at all (avoids requiring the gmail.settings.basic scope).
    assert list_mock.call_count == 0

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    raw_message = send_kwargs["body"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "<p>Hello</p>" in raw_text
    assert "Best," not in raw_text


@pytest.mark.asyncio
async def test_draft_gmail_message_builds_threaded_html_reply_as_multipart_alternative():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
    )

    await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="<p>Thanks for the update.</p>",
        body_format="html",
        thread_id="thread123",
        include_signature=False,
    )

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    parsed = _parse_raw_message(raw_message)

    assert parsed["Subject"] == "Re: Meeting tomorrow"
    assert parsed["To"] == "recipient@example.com"
    assert parsed["In-Reply-To"] == "<msg2@example.com>"
    assert parsed["References"] == "<msg1@example.com> <msg2@example.com>"
    assert parsed.get_content_type() == "multipart/alternative"
    assert parsed.get_body(preferencelist=("plain",)).get_content().strip() == (
        "Thanks for the update."
    )
    assert parsed.get_body(preferencelist=("html",)).get_content().strip() == (
        "<p>Thanks for the update.</p>"
    )


@pytest.mark.asyncio
async def test_draft_gmail_message_builds_html_attachments_with_mixed_top_level():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {
        "id": "draft_attachments"
    }

    await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Attachment test",
        body="<p>Please see attached.</p>",
        body_format="html",
        attachments=[
            {
                "filename": "a.pdf",
                "content": "cGRmMQ==",
                "mime_type": "application/pdf",
            },
            {
                "filename": "b.pdf",
                "content": "cGRmMg==",
                "mime_type": "application/pdf",
            },
        ],
        include_signature=False,
    )

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    parsed = _parse_raw_message(raw_message)
    attachments = list(parsed.iter_attachments())

    assert parsed.get_content_type() == "multipart/mixed"
    assert parsed.get_body(preferencelist=("html",)).get_content().strip() == (
        "<p>Please see attached.</p>"
    )
    assert parsed.get_body(preferencelist=("plain",)).get_content().strip() == (
        "Please see attached."
    )
    assert [attachment.get_filename() for attachment in attachments] == [
        "a.pdf",
        "b.pdf",
    ]


@pytest.mark.asyncio
async def test_draft_gmail_message_autofills_reply_recipient_from_thread_target():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="Alice Example <alice@example.com>",
                reply_to="reply@example.com",
            )
        ]
    }

    await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        subject="Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        include_signature=False,
    )

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    parsed = _parse_raw_message(create_kwargs["body"]["message"]["raw"])

    assert parsed["To"] == "reply@example.com"


@pytest.mark.asyncio
async def test_draft_gmail_message_fetches_thread_once_when_quoting_reply():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="Alice Example <alice@example.com>",
                text="Original plain text",
                html="<p>Original html</p>",
            )
        ]
    }
    mock_service.users.return_value.threads.return_value.get.reset_mock()

    await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="<p>Thanks for the update.</p>",
        body_format="html",
        thread_id="thread123",
        quote_original=True,
        include_signature=False,
    )

    assert mock_service.users.return_value.threads.return_value.get.call_count == 1
    thread_get_kwargs = (
        mock_service.users.return_value.threads.return_value.get.call_args.kwargs
    )
    assert thread_get_kwargs["format"] == "full"


@pytest.mark.asyncio
async def test_draft_gmail_message_autofills_reply_headers_from_thread():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
        "<msg3@example.com>",
    )

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        include_signature=False,
    )

    # Verify threads().get() was called with correct parameters
    thread_get_kwargs = (
        mock_service.users.return_value.threads.return_value.get.call_args.kwargs
    )
    assert thread_get_kwargs["userId"] == "me"
    assert thread_get_kwargs["id"] == "thread123"
    assert thread_get_kwargs["format"] == "metadata"
    assert "Message-ID" in thread_get_kwargs["metadataHeaders"]

    assert "Draft created! Draft ID: draft_reply" in result

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "In-Reply-To: <msg3@example.com>" in raw_text
    assert (
        "References: <msg1@example.com> <msg2@example.com> <msg3@example.com>"
        in raw_text
    )
    assert create_kwargs["body"]["message"]["threadId"] == "thread123"


@pytest.mark.asyncio
async def test_draft_gmail_message_uses_explicit_in_reply_to_when_filling_references():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
        "<msg3@example.com>",
    )

    await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="Replying to an earlier message.",
        thread_id="thread123",
        in_reply_to="<msg2@example.com>",
        include_signature=False,
    )

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "In-Reply-To: <msg2@example.com>" in raw_text
    assert "References: <msg1@example.com> <msg2@example.com>" in raw_text
    assert "<msg3@example.com>" not in raw_text


@pytest.mark.asyncio
async def test_draft_gmail_message_uses_explicit_references_when_filling_in_reply_to():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
        "<msg3@example.com>",
    )

    await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="Replying to an earlier message.",
        thread_id="thread123",
        references="<msg1@example.com> <msg2@example.com>",
        include_signature=False,
    )

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "In-Reply-To: <msg2@example.com>" in raw_text
    assert "References: <msg1@example.com> <msg2@example.com>" in raw_text
    assert "<msg3@example.com>" not in raw_text


@pytest.mark.asyncio
async def test_draft_gmail_message_gracefully_degrades_when_thread_fetch_fails():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.side_effect = RuntimeError("boom")

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        include_signature=False,
    )

    assert "Draft created! Draft ID: draft_reply" in result

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "In-Reply-To:" not in raw_text
    assert "References:" not in raw_text
    assert "threadId" not in create_kwargs["body"]["message"]


@pytest.mark.asyncio
async def test_draft_gmail_message_gracefully_degrades_when_thread_has_no_messages():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    mock_service.users().threads().get().execute.return_value = {"messages": []}

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        include_signature=False,
    )

    assert "Draft created! Draft ID: draft_reply" in result

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_message = create_kwargs["body"]["message"]["raw"]
    raw_text = base64.urlsafe_b64decode(raw_message).decode("utf-8", errors="ignore")

    assert "In-Reply-To:" not in raw_text
    assert "References:" not in raw_text
    assert "threadId" not in create_kwargs["body"]["message"]


@pytest.mark.asyncio
async def test_draft_gmail_message_gracefully_degrades_when_all_messages_trashed():
    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_reply"}
    trashed_messages = [
        _thread_message("<msg1@example.com>"),
        _thread_message("<msg2@example.com>"),
    ]
    for message in trashed_messages:
        message["labelIds"] = ["TRASH"]
    mock_service.users().threads().get().execute.return_value = {
        "messages": trashed_messages
    }

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        include_signature=False,
    )

    assert "Draft created! Draft ID: draft_reply" in result
    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    parsed = _parse_raw_message(create_kwargs["body"]["message"]["raw"])

    assert parsed["In-Reply-To"] is None
    assert parsed["References"] is None
    assert "threadId" not in create_kwargs["body"]["message"]


# ---------------------------------------------------------------------------
# URL-based attachment tests
# ---------------------------------------------------------------------------


def test_try_read_local_attachment_reads_from_storage(tmp_path, monkeypatch):
    """MCP attachment URLs should be resolved from local storage."""
    import core.attachment_storage as storage_mod

    monkeypatch.setattr(storage_mod, "STORAGE_DIR", tmp_path)

    # Write a fake attachment file matching the naming convention.
    file_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    (tmp_path / f"report_{file_id[:8]}.pdf").write_bytes(b"%PDF-fake")

    storage = storage_mod.AttachmentStorage()
    monkeypatch.setattr(storage_mod, "_attachment_storage", storage)

    # Manually register metadata so get_attachment_path works.
    from datetime import datetime, timedelta

    storage._metadata[file_id] = {
        "file_path": str(tmp_path / f"report_{file_id[:8]}.pdf"),
        "filename": "report.pdf",
        "mime_type": "application/pdf",
        "size": 9,
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(hours=1),
    }

    result = _try_read_local_attachment(f"/attachments/{file_id}")
    assert result is not None
    data, filename, mime_type = result
    assert data == b"%PDF-fake"
    assert filename == "report.pdf"
    assert mime_type == "application/pdf"


def test_try_read_local_attachment_returns_none_for_non_attachment_url():
    """Non-attachment URLs should return None (fall through to HTTP fetch)."""
    assert _try_read_local_attachment("https://example.com/file.pdf") is None


def test_try_read_local_attachment_rejects_untrusted_origin(tmp_path, monkeypatch):
    file_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"%PDF-fake")

    storage = gmail_tools.get_attachment_storage()
    monkeypatch.setattr(
        storage,
        "get_attachment_metadata",
        lambda requested_file_id: (
            {
                "filename": "report.pdf",
                "mime_type": "application/pdf",
            }
            if requested_file_id == file_id
            else None
        ),
    )
    monkeypatch.setattr(
        storage,
        "get_attachment_path",
        lambda requested_file_id: file_path if requested_file_id == file_id else None,
    )
    monkeypatch.delenv("WORKSPACE_EXTERNAL_URL", raising=False)

    result = _try_read_local_attachment(f"https://evil.example/attachments/{file_id}")
    assert result is None


def test_try_read_local_attachment_requires_metadata_without_scan_fallback(
    tmp_path, monkeypatch
):
    file_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    monkeypatch.setattr(gmail_tools, "STORAGE_DIR", tmp_path)
    (tmp_path / f"report_{file_id[:8]}.pdf").write_bytes(b"%PDF-fake")

    storage = gmail_tools.get_attachment_storage()
    monkeypatch.setattr(storage, "get_attachment_metadata", lambda _file_id: None)
    monkeypatch.setattr(storage, "get_attachment_path", lambda _file_id: None)

    result = _try_read_local_attachment(f"/attachments/{file_id}")
    assert result is None


def test_try_read_local_attachment_checks_file_size_before_reading(
    tmp_path, monkeypatch
):
    file_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    file_path = tmp_path / "large.bin"
    file_path.write_bytes(b"0123456789")

    storage = gmail_tools.get_attachment_storage()
    monkeypatch.setattr(
        storage,
        "get_attachment_metadata",
        lambda requested_file_id: (
            {
                "filename": "large.bin",
                "mime_type": "application/octet-stream",
            }
            if requested_file_id == file_id
            else None
        ),
    )
    monkeypatch.setattr(
        storage,
        "get_attachment_path",
        lambda requested_file_id: file_path if requested_file_id == file_id else None,
    )
    monkeypatch.setattr(gmail_tools, "MAX_EMAIL_ATTACHMENT_BYTES", 5)

    with pytest.raises(ValueError, match="Attachment exceeds 5 bytes"):
        _try_read_local_attachment(f"/attachments/{file_id}")


@pytest.mark.asyncio
async def test_resolve_url_attachments_fetches_external_url(monkeypatch):
    """External URLs should be fetched via streamed SSRF-safe download."""
    fake_response = _FakeStreamResponse(
        200,
        headers={"content-type": "application/pdf"},
        chunks=[b"file-", b"bytes"],
    )

    monkeypatch.setattr(
        gmail_tools, "ssrf_safe_stream", _mock_stream_response(fake_response)
    )

    attachments = [{"url": "https://example.com/report.pdf"}]
    resolved = await _resolve_url_attachments(attachments)

    assert len(resolved) == 1
    assert resolved[0]["_resolved_bytes"] == b"file-bytes"
    assert resolved[0]["filename"] == "report.pdf"
    assert resolved[0]["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_resolve_url_attachments_preserves_non_url_entries():
    """Entries with path or content should pass through unchanged."""
    attachments = [
        {"path": "/some/file.txt"},
        {"content": "aGVsbG8=", "filename": "hello.txt"},
    ]
    resolved = await _resolve_url_attachments(attachments)
    assert resolved == attachments


@pytest.mark.asyncio
async def test_resolve_url_attachments_uses_provided_filename(monkeypatch):
    """User-specified filename should take precedence over URL-derived name."""
    fake_response = _FakeStreamResponse(
        200,
        headers={"content-type": "text/plain"},
        chunks=[b"data"],
    )

    monkeypatch.setattr(
        gmail_tools, "ssrf_safe_stream", _mock_stream_response(fake_response)
    )

    attachments = [{"url": "https://example.com/abc123", "filename": "my_report.txt"}]
    resolved = await _resolve_url_attachments(attachments)
    assert resolved[0]["filename"] == "my_report.txt"


@pytest.mark.asyncio
async def test_resolve_url_attachments_rejects_oversized(monkeypatch):
    """Attachments exceeding 25 MB should be skipped (passed through for error)."""
    big_data = b"x" * (26 * 1024 * 1024)
    fake_response = _FakeStreamResponse(
        200,
        headers={"content-type": "application/octet-stream"},
        chunks=[big_data],
    )

    monkeypatch.setattr(
        gmail_tools, "ssrf_safe_stream", _mock_stream_response(fake_response)
    )

    attachments = [{"url": "https://example.com/huge.bin"}]
    resolved = await _resolve_url_attachments(attachments)
    # Should pass through the original dict (no _resolved_bytes).
    assert "_resolved_bytes" not in resolved[0]
    assert resolved[0]["url"] == "https://example.com/huge.bin"


@pytest.mark.asyncio
async def test_draft_gmail_message_with_url_attachment(monkeypatch):
    """End-to-end: draft_gmail_message should accept a URL attachment."""
    fake_response = _FakeStreamResponse(
        200,
        headers={"content-type": "application/pdf"},
        chunks=[b"pdf-content-here"],
    )

    monkeypatch.setattr(
        gmail_tools, "ssrf_safe_stream", _mock_stream_response(fake_response)
    )

    mock_service = Mock()
    mock_service.users().drafts().create().execute.return_value = {"id": "draft_url"}

    result = await _unwrap(draft_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="URL attachment test",
        body="See attached from URL.",
        attachments=[{"url": "https://example.com/doc.pdf", "filename": "doc.pdf"}],
        include_signature=False,
    )

    assert "Draft created with 1 attachment(s)! Draft ID: draft_url" in result

    create_kwargs = (
        mock_service.users.return_value.drafts.return_value.create.call_args.kwargs
    )
    raw_bytes = base64.urlsafe_b64decode(create_kwargs["body"]["message"]["raw"])
    assert b"Content-Disposition: attachment;" in raw_bytes
    assert b"doc.pdf" in raw_bytes


@pytest.mark.asyncio
async def test_send_gmail_message_with_url_attachment(monkeypatch):
    """End-to-end: send_gmail_message should accept a URL attachment."""
    fake_response = _FakeStreamResponse(
        200,
        headers={"content-type": "application/pdf"},
        chunks=[b"pdf-content-here"],
    )

    monkeypatch.setattr(
        gmail_tools, "ssrf_safe_stream", _mock_stream_response(fake_response)
    )

    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "msg_url"}

    result = await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="URL attachment test",
        body="See attached from URL.",
        attachments=[{"url": "https://example.com/doc.pdf", "filename": "doc.pdf"}],
        include_signature=False,
    )

    assert "Email sent with 1 attachment(s)! Message ID: msg_url" in result

    create_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    raw_bytes = base64.urlsafe_b64decode(create_kwargs["body"]["raw"])
    assert b"Content-Disposition: attachment;" in raw_bytes
    assert b"doc.pdf" in raw_bytes


@pytest.mark.asyncio
async def test_send_gmail_message_autofills_reply_headers_from_thread():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
    )

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Re: Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        include_signature=False,
    )

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    parsed = _parse_raw_message(send_kwargs["body"]["raw"])
    assert parsed["In-Reply-To"] == "<msg2@example.com>"
    assert parsed["References"] == "<msg1@example.com> <msg2@example.com>"


@pytest.mark.asyncio
async def test_send_gmail_message_preserves_caller_reply_headers():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = _thread_response(
        "<msg1@example.com>",
        "<msg2@example.com>",
    )

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Re: Meeting tomorrow",
        body="Thanks.",
        thread_id="thread123",
        in_reply_to="<msg1@example.com>",
        references="<msg1@example.com>",
        include_signature=False,
    )

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    parsed = _parse_raw_message(send_kwargs["body"]["raw"])
    assert parsed["In-Reply-To"] == "<msg1@example.com>"
    assert parsed["References"] == "<msg1@example.com>"


@pytest.mark.asyncio
async def test_send_gmail_message_quotes_original_when_requested():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="Alice Example <alice@example.com>",
                text="Original plain text",
            )
        ]
    }

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Re: Meeting tomorrow",
        body="Thanks for the update.",
        thread_id="thread123",
        quote_original=True,
        include_signature=False,
    )

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    parsed = _parse_raw_message(send_kwargs["body"]["raw"])
    payload = parsed.get_content()
    assert "Alice Example <alice@example.com> wrote:" in payload
    assert "> Original plain text" in payload


@pytest.mark.asyncio
async def test_send_gmail_message_reply_all_derives_recipients():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="Alice Example <alice@example.com>",
                to_value="user@example.com, bob@example.com",
                cc_value="carol@example.com",
            )
        ]
    }

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        subject="Re: Meeting tomorrow",
        body="Thanks all.",
        thread_id="thread123",
        reply_all=True,
        include_signature=False,
    )

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    parsed = _parse_raw_message(send_kwargs["body"]["raw"])
    assert parsed["To"] == "alice@example.com"
    # The authenticated account is dropped; everyone else on the message is kept.
    assert parsed["Cc"] == "bob@example.com, carol@example.com"


@pytest.mark.asyncio
async def test_send_gmail_message_reply_all_prefers_reply_to_and_explicit_cc():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="noreply@example.com",
                reply_to="alice@example.com",
                to_value="user@example.com, bob@example.com",
            )
        ]
    }

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        subject="Re: Meeting tomorrow",
        body="Thanks.",
        thread_id="thread123",
        reply_all=True,
        cc="explicit@example.com",
        include_signature=False,
    )

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    parsed = _parse_raw_message(send_kwargs["body"]["raw"])
    assert parsed["To"] == "alice@example.com"
    assert parsed["Cc"] == "explicit@example.com"


@pytest.mark.asyncio
async def test_send_gmail_message_reply_all_moves_sender_to_cc_when_to_is_explicit():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="Alice Example <alice@example.com>",
                to_value="user@example.com, bob@example.com",
                cc_value="carol@example.com",
            )
        ]
    }

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="dave@example.com",
        subject="Re: Meeting tomorrow",
        body="Looping in Dave.",
        thread_id="thread123",
        reply_all=True,
        include_signature=False,
    )

    send_kwargs = (
        mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    )
    parsed = _parse_raw_message(send_kwargs["body"]["raw"])
    assert parsed["To"] == "dave@example.com"
    # Redirecting To must not drop Alice from the reply-all entirely.
    assert parsed["Cc"] == "alice@example.com, bob@example.com, carol@example.com"


@pytest.mark.asyncio
async def test_send_gmail_message_reply_all_never_addresses_the_account_itself():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="user@example.com",
                to_value="alice@example.com",
                cc_value="bob@example.com",
            )
        ]
    }

    mock_service.users.return_value.messages.return_value.send.reset_mock()

    # The message being replied to was sent by the account, so there is no
    # sender to reply to; deriving one would address the account itself.
    with pytest.raises(UserInputError, match="Could not derive a recipient"):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            subject="Re: Meeting tomorrow",
            body="One more thing.",
            thread_id="thread123",
            reply_all=True,
            include_signature=False,
        )

    mock_service.users.return_value.messages.return_value.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_gmail_message_reply_all_excludes_the_send_as_alias_from_to():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent_reply"}
    mock_service.users().threads().get().execute.return_value = {
        "messages": [
            _thread_message(
                "<msg1@example.com>",
                from_value="Alias <alias@example.com>",
                to_value="alice@example.com",
                cc_value="bob@example.com",
            )
        ]
    }

    mock_service.users.return_value.messages.return_value.send.reset_mock()

    with pytest.raises(UserInputError, match="Could not derive a recipient"):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            from_email="alias@example.com",
            subject="Re: Meeting tomorrow",
            body="One more thing.",
            thread_id="thread123",
            reply_all=True,
            include_signature=False,
        )

    mock_service.users.return_value.messages.return_value.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_gmail_message_reply_all_requires_a_thread_id():
    mock_service = Mock()

    with pytest.raises(UserInputError, match="'reply_all' requires a thread_id"):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            to="recipient@example.com",
            subject="Hello",
            body="Hi there!",
            reply_all=True,
            include_signature=False,
        )

    mock_service.users.return_value.messages.return_value.send.assert_not_called()
    # Validation runs before the thread fetch, so no API call is made at all.
    mock_service.users.return_value.threads.return_value.get.assert_not_called()


@pytest.mark.asyncio
async def test_send_gmail_message_requires_to_without_reply_all():
    mock_service = Mock()

    with pytest.raises(UserInputError, match="'to' is required"):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            subject="Hello",
            body="Hi there!",
        )


@pytest.mark.asyncio
async def test_send_gmail_message_requires_to_when_forwarding():
    mock_service = Mock()

    # 'to' is optional on the signature only so reply_all can derive it; a
    # forward has no thread to derive from and must still be rejected here
    # rather than by Gmail's API.
    with pytest.raises(UserInputError, match="'to' is required when forwarding"):
        await _unwrap(send_gmail_message)(
            service=mock_service,
            user_google_email="user@example.com",
            body="FYI - see below.",
            forward_message_id="abc123",
        )

    mock_service.users.return_value.messages.return_value.get.assert_not_called()


@pytest.mark.asyncio
async def test_send_gmail_message_does_not_fetch_thread_for_a_new_message():
    mock_service = Mock()
    mock_service.users().messages().send().execute.return_value = {"id": "sent123"}
    mock_service.users.return_value.threads.return_value.get.reset_mock()

    await _unwrap(send_gmail_message)(
        service=mock_service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Hello",
        body="Hi there!",
        include_signature=False,
    )

    assert mock_service.users.return_value.threads.return_value.get.call_count == 0
