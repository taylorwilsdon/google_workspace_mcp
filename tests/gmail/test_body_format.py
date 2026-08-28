"""Tests for Gmail body_format support across helper and public tool APIs."""

import base64
from email import message_from_bytes
from email.policy import SMTP
from pathlib import Path
from unittest.mock import Mock

import pytest

import gmail.gmail_tools as gmail_tools
from core.utils import UserInputError
from gmail.gmail_helpers import _signature_html_to_text
from gmail.gmail_tools import (
    _extract_message_bodies,
    _format_body_content,
    _html_to_text,
    _prepare_gmail_message,
    get_gmail_message_content,
    get_gmail_messages_content_batch,
    get_gmail_thread_content,
    get_gmail_threads_content_batch,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _headers(**overrides):
    header_map = {
        "Subject": "Example subject",
        "From": "sender@example.com",
        "To": "recipient@example.com",
        "Cc": "cc@example.com",
        "Message-ID": "<message@example.com>",
        "Date": "Fri, 28 Mar 2026 10:00:00 -0400",
    }
    header_map.update(overrides)
    return [{"name": name, "value": value} for name, value in header_map.items()]


def _payload(headers=None, text=None, html=None):
    payload = {"headers": headers or _headers()}
    parts = []
    if text is not None:
        parts.append({"mimeType": "text/plain", "body": {"data": _encode(text)}})
    if html is not None:
        parts.append({"mimeType": "text/html", "body": {"data": _encode(html)}})
    if parts:
        payload["mimeType"] = "multipart/alternative"
        payload["parts"] = parts
    return payload


def _message_response(message_id: str, text="", html="", headers=None):
    return {
        "id": message_id,
        "payload": _payload(headers=headers, text=text, html=html),
    }


def _metadata_response(message_id: str, headers=None):
    return {
        "id": message_id,
        "payload": {"headers": headers or _headers()},
    }


def _thread_message(message_id: str, text="", html="", headers=None):
    return {
        "id": message_id,
        "payload": _payload(headers=headers, text=text, html=html),
    }


class _FakeBatch:
    def __init__(self, callback):
        self._callback = callback
        self._requests = []

    def add(self, request, request_id):
        self._requests.append((request_id, request))

    def execute(self):
        for request_id, request in self._requests:
            try:
                response = request.execute()
                self._callback(request_id, response, None)
            except Exception as exc:
                self._callback(request_id, None, exc)


def _build_service(*, message_responses=None, thread_responses=None):
    message_responses = message_responses or {}
    thread_responses = thread_responses or {}

    service = Mock()

    def message_get(**kwargs):
        request = Mock()
        response = message_responses[(kwargs["id"], kwargs["format"])]
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        return request

    def thread_get(**kwargs):
        request = Mock()
        response = thread_responses[(kwargs["id"], kwargs["format"])]
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        return request

    service.users().messages().get.side_effect = message_get
    service.users().threads().get.side_effect = thread_get
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    return service


class TestFormatBodyContentTextMode:
    """Verify default 'text' body_format preserves existing behavior."""

    def test_returns_text_body_when_available(self):
        result = _format_body_content("Hello world", "<b>Hello world</b>")
        assert result == "Hello world"

    def test_returns_text_body_default_format(self):
        result = _format_body_content(
            "Hello world", "<b>Hello world</b>", body_format="text"
        )
        assert result == "Hello world"

    def test_falls_back_to_html_when_text_empty(self):
        result = _format_body_content("", "<p>HTML content here</p>")
        assert "HTML content here" in result

    def test_returns_no_content_when_both_empty(self):
        result = _format_body_content("", "")
        assert result == "[No readable content found]"

    def test_detects_low_value_placeholder_text(self):
        low_value = "Your client does not support HTML messages"
        html = "<p>This is the actual email content with much more detail</p>"
        result = _format_body_content(low_value, html)
        assert "actual email content" in result

    def test_truncates_long_html_fallback(self):
        long_html = "<p>" + "x" * 25000 + "</p>"
        result = _format_body_content("", long_html)
        assert "[Content truncated...]" in result

    def test_html_to_text_separates_br_text(self):
        assert _html_to_text("<div>Best,<br>Alice</div>") == "Best, Alice"

    def test_html_to_text_ignores_br_inside_skipped_tags(self):
        assert _html_to_text("<script>x<br>y</script><p>Visible</p>") == "Visible"


class TestSignatureHtmlToText:
    def test_preserves_explicit_signature_line_breaks(self):
        signature = "<p>Best,</p><p><br></p><p>Alice</p>"

        assert _signature_html_to_text(signature) == "Best,\n\nAlice"

    def test_separates_nested_block_content(self):
        signature = "<div>Name<div>Title</div><div>Phone</div></div>"

        assert _signature_html_to_text(signature) == "Name\nTitle\nPhone"

    def test_collapses_source_formatting_whitespace(self):
        signature = """<div>Acme
            Corporation</div>
        <div>Engineering</div>"""

        assert _signature_html_to_text(signature) == "Acme Corporation\nEngineering"


class TestHtmlPlainTextAlternative:
    """The text/plain alternative of an outgoing HTML message is what non-HTML
    clients display, so it must keep the author's block structure."""

    @staticmethod
    def _parts(body: str) -> dict:
        raw_b64, _, _, _ = _prepare_gmail_message(
            subject="format test",
            body=body,
            to="recipient@example.com",
            body_format="html",
        )
        message = message_from_bytes(base64.urlsafe_b64decode(raw_b64), policy=SMTP)
        return {
            part.get_content_type(): part.get_payload(decode=True).decode()
            for part in message.walk()
            if part.get_content_maintype() != "multipart"
        }

    def test_html_part_is_not_escaped(self):
        body = "<p>First paragraph <strong>bold</strong>.</p><p>Second paragraph.</p>"

        assert self._parts(body)["text/html"].strip() == body

    def test_plain_part_keeps_paragraph_boundaries(self):
        body = "<p>First paragraph <strong>bold</strong>.</p><p>Second paragraph.</p>"

        plain = self._parts(body)["text/plain"]

        assert plain.split() == ["First", "paragraph", "bold.", "Second", "paragraph."]
        assert "bold.Second" not in plain

    def test_plain_part_keeps_line_break_tags(self):
        plain = self._parts("<div>Best,<br>Alice</div>")["text/plain"]

        assert plain.strip().splitlines() == ["Best,", "Alice"]

    def test_plain_part_separates_headings_from_following_text(self):
        plain = self._parts("<h1>Quarterly update</h1><h2>Revenue</h2>")["text/plain"]

        assert plain.strip().splitlines() == ["Quarterly update", "Revenue"]

    def test_plain_part_separates_semantic_sections(self):
        body = "<section>Section one.</section><section>Section two.</section>"

        plain = self._parts(body)["text/plain"]

        assert plain.strip().splitlines() == ["Section one.", "Section two."]
        assert "one.Section" not in plain

    def test_plain_part_keeps_body_ending_in_incomplete_entity(self):
        # HTMLParser withholds a trailing "&" as a possibly-incomplete entity,
        # so an unflushed parser drops the whole tail of the body.
        plain = self._parts("<p>Tom &amp</p>")["text/plain"]

        assert plain.strip() == "Tom &"


class TestFormatBodyContentHtmlMode:
    """Verify 'html' body_format returns raw HTML."""

    def test_returns_raw_html_body(self):
        html = "<div><b>Hello</b> <em>world</em></div>"
        result = _format_body_content("Hello world", html, body_format="html")
        assert result == html

    def test_returns_html_without_conversion(self):
        html = "<table><tr><td>Cell</td></tr></table>"
        result = _format_body_content("Cell", html, body_format="html")
        assert "<table>" in result
        assert "<td>Cell</td>" in result

    def test_falls_back_to_text_when_no_html(self):
        result = _format_body_content("Plain text only", "", body_format="html")
        assert result == "Plain text only"

    def test_returns_no_content_when_both_empty(self):
        result = _format_body_content("", "", body_format="html")
        assert result == "[No readable content found]"

    def test_strips_whitespace_from_html(self):
        result = _format_body_content("text", "  <b>html</b>  ", body_format="html")
        assert result == "<b>html</b>"

    def test_truncates_long_html(self):
        long_html = "<div>" + "x" * 25000 + "</div>"
        result = _format_body_content("text", long_html, body_format="html")
        assert "[Content truncated...]" in result
        assert len(result) < len(long_html)

    def test_preserves_html_entities(self):
        html = "<p>Price: &lt;$100 &amp; free shipping</p>"
        result = _format_body_content("", html, body_format="html")
        assert "&lt;" in result
        assert "&amp;" in result

    def test_preserves_style_and_script_tags(self):
        html = "<style>body { color: red; }</style><p>Content</p>"
        result = _format_body_content("Content", html, body_format="html")
        assert "<style>" in result
        assert "color: red" in result

    def test_whitespace_only_html_falls_back_to_text(self):
        result = _format_body_content("Fallback text", "   \n\t  ", body_format="html")
        assert result == "Fallback text"


class TestExtractMessageBodies:
    """Verify _extract_message_bodies extracts both text and HTML parts."""

    def test_extracts_text_and_html_from_multipart(self):
        payload = {
            "mimeType": "multipart/alternative",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _encode("Plain text")},
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": _encode("<b>HTML</b>")},
                },
            ],
        }
        bodies = _extract_message_bodies(payload)
        assert bodies["text"] == "Plain text"
        assert bodies["html"] == "<b>HTML</b>"

    def test_extracts_text_only(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": _encode("Just text")},
        }
        bodies = _extract_message_bodies(payload)
        assert bodies["text"] == "Just text"
        assert bodies["html"] == ""

    def test_extracts_html_only(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": _encode("<p>Just HTML</p>")},
        }
        bodies = _extract_message_bodies(payload)
        assert bodies["text"] == ""
        assert bodies["html"] == "<p>Just HTML</p>"

    def test_handles_empty_payload(self):
        bodies = _extract_message_bodies({})
        assert bodies["text"] == ""
        assert bodies["html"] == ""

    def test_handles_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _encode("Nested text")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _encode("<p>Nested HTML</p>")},
                        },
                    ],
                },
            ],
        }
        bodies = _extract_message_bodies(payload)
        assert bodies["text"] == "Nested text"
        assert bodies["html"] == "<p>Nested HTML</p>"


@pytest.mark.asyncio
async def test_get_gmail_message_content_returns_raw_mime():
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "raw"): {"raw": _encode("Raw MIME body")},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="raw",
    )

    assert "--- RAW MIME ---" in result
    assert "Raw MIME body" in result
    assert "From: sender@example.com" in result
    assert "Date: Fri, 28 Mar 2026 10:00:00 -0400" in result
    assert "To: recipient@example.com" in result
    assert "Cc: cc@example.com" in result
    assert "From:    " not in result


@pytest.mark.asyncio
async def test_get_gmail_message_content_reports_raw_decode_errors():
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "raw"): {"raw": "a"},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="raw",
    )

    assert "[Failed to decode raw MIME:" in result


@pytest.mark.asyncio
async def test_get_gmail_message_content_truncates_raw_mime(monkeypatch):
    monkeypatch.setattr(gmail_tools, "RAW_BODY_TRUNCATE_LIMIT", 12)
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "raw"): {"raw": _encode("x" * 32)},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="raw",
    )

    assert "--- RAW MIME ---" in result
    assert "[Content truncated...]" in result


@pytest.mark.asyncio
async def test_get_gmail_messages_content_batch_supports_raw_format():
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "raw"): {"raw": _encode("Batch raw MIME body")},
        }
    )

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="user@example.com",
        body_format="raw",
    )

    assert "Retrieved 1 messages" in result
    assert "--- RAW MIME ---" in result
    assert "Batch raw MIME body" in result

    formats = [
        call.kwargs["format"]
        for call in service.users.return_value.messages.return_value.get.call_args_list
    ]
    assert formats.count("metadata") == 1
    assert formats.count("raw") == 1


@pytest.mark.asyncio
async def test_get_gmail_messages_content_batch_default_text_format():
    service = _build_service(
        message_responses={
            ("msg-1", "full"): _message_response(
                "msg-1", text="Plain text body", html="<p>HTML body</p>"
            ),
        }
    )

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="user@example.com",
    )

    assert "Plain text body" in result
    assert "--- BODY ---" in result
    assert "--- RAW MIME ---" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("body_format", ["html", "raw"])
async def test_get_gmail_messages_content_batch_rejects_metadata_with_body_format(
    body_format,
):
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
        }
    )

    with pytest.raises(UserInputError, match="require format='full'"):
        await _unwrap(get_gmail_messages_content_batch)(
            service=service,
            message_ids=["msg-1"],
            user_google_email="user@example.com",
            format="metadata",
            body_format=body_format,
        )


@pytest.mark.asyncio
async def test_get_gmail_thread_content_supports_raw_format():
    service = _build_service(
        message_responses={
            ("msg-1", "raw"): {"raw": _encode("Thread raw MIME 1")},
            ("msg-2", "raw"): {"raw": _encode("Thread raw MIME 2")},
        },
        thread_responses={
            (
                "thread-1",
                "full",
            ): {
                "messages": [
                    _thread_message("msg-1", text="Plain 1", html="<p>HTML 1</p>"),
                    _thread_message("msg-2", text="Plain 2", html="<p>HTML 2</p>"),
                ]
            }
        },
    )

    result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="thread-1",
        user_google_email="user@example.com",
        body_format="raw",
    )

    assert result.count("--- RAW MIME ---") == 2
    assert "Thread raw MIME 1" in result
    assert "Thread raw MIME 2" in result


@pytest.mark.asyncio
async def test_get_gmail_threads_content_batch_supports_raw_format():
    service = _build_service(
        message_responses={
            ("msg-1", "raw"): {"raw": _encode("Batch thread raw MIME")},
        },
        thread_responses={
            (
                "thread-1",
                "full",
            ): {
                "messages": [
                    _thread_message("msg-1", text="Plain 1", html="<p>HTML 1</p>")
                ]
            }
        },
    )

    result = await _unwrap(get_gmail_threads_content_batch)(
        service=service,
        thread_ids=["thread-1"],
        user_google_email="user@example.com",
        body_format="raw",
    )

    assert "Retrieved 1 threads:" in result
    assert "--- RAW MIME ---" in result
    assert "Batch thread raw MIME" in result


@pytest.mark.asyncio
async def test_get_gmail_message_content_preserves_html_format():
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            (
                "msg-1",
                "full",
            ): _message_response(
                "msg-1", text="Plain fallback", html="<p><b>HTML</b></p>"
            ),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="html",
    )

    assert "<p><b>HTML</b></p>" in result
    assert "From: sender@example.com" in result
    assert "Date: Fri, 28 Mar 2026 10:00:00 -0400" in result
    assert "To: recipient@example.com" in result
    assert "Cc: cc@example.com" in result
    assert "From:    " not in result


@pytest.fixture
def stdio_storage(monkeypatch, tmp_path):
    """Route attachment storage to a temp dir and force stdio (file-path) delivery."""
    import core.attachment_storage as attachment_storage

    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(attachment_storage, "_attachment_storage", None)
    monkeypatch.setattr(gmail_tools, "get_transport_mode", lambda: "stdio")
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: False)
    return tmp_path


def _saved_path(result: str) -> str:
    """Pull the on-disk path out of a stdio-mode full-export response."""
    marker = "📎 Saved to: "
    line = next(ln for ln in result.splitlines() if marker in ln)
    return line.split(marker, 1)[1].strip()


def _unpadded(text: str) -> str:
    """base64url without padding, exactly as the Gmail API returns raw content."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.mark.asyncio
async def test_full_export_raw_saves_complete_eml(stdio_storage):
    # Use unpadded base64url (as Gmail actually returns) to exercise padding repair.
    raw_mime = "From: sender@example.com\r\n\r\nComplete raw MIME body!"
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "raw"): {"raw": _unpadded(raw_mime)},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    # Body content must NOT be inlined in the response.
    assert "Complete raw MIME body" not in result
    assert "--- RAW MIME ---" not in result
    assert "--- FULL MESSAGE EXPORT ---" in result
    assert "Format: eml" in result
    assert "Subject: Example subject" in result

    # The saved file must hold the complete, decoded message.
    with open(_saved_path(result), "rb") as fh:
        assert fh.read().decode() == raw_mime


@pytest.mark.asyncio
async def test_full_export_text_uses_plaintext(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-2", "metadata"): _metadata_response("msg-2"),
            ("msg-2", "full"): _message_response(
                "msg-2", text="Plain body", html="<b>Plain body</b>"
            ),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-2",
        user_google_email="user@example.com",
        full=True,
    )

    assert "Plain body" not in result
    assert "Format: txt" in result
    with open(_saved_path(result), "rb") as fh:
        assert fh.read().decode() == "Plain body"


@pytest.mark.asyncio
async def test_full_export_html_saves_raw_html(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-3", "metadata"): _metadata_response("msg-3"),
            ("msg-3", "full"): _message_response(
                "msg-3", text="fallback", html="<p><b>Rich HTML</b></p>"
            ),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-3",
        user_google_email="user@example.com",
        body_format="html",
        full=True,
    )

    assert "Rich HTML" not in result
    with open(_saved_path(result), "rb") as fh:
        assert fh.read().decode() == "<p><b>Rich HTML</b></p>"


@pytest.mark.asyncio
async def test_full_export_preserves_edge_whitespace(stdio_storage):
    """A complete export must not strip leading/trailing body content."""
    html_with_edges = "\n\n  <html><body>Body</body></html>  \n"
    text_with_edges = "\n   Leading and trailing kept.   \n"
    service = _build_service(
        message_responses={
            ("msg-ws", "metadata"): _metadata_response("msg-ws"),
            ("msg-ws", "full"): _message_response(
                "msg-ws", text=text_with_edges, html=html_with_edges
            ),
        }
    )

    html_result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-ws",
        user_google_email="user@example.com",
        body_format="html",
        full=True,
    )
    with open(_saved_path(html_result), "rb") as fh:
        assert fh.read().decode() == html_with_edges

    text_result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-ws",
        user_google_email="user@example.com",
        body_format="text",
        full=True,
    )
    with open(_saved_path(text_result), "rb") as fh:
        assert fh.read().decode() == text_with_edges


@pytest.mark.asyncio
async def test_full_export_does_not_truncate(stdio_storage):
    """The whole point: full=True never applies the 20,000-char cap."""
    long_html = "<p>" + ("A" * 50000) + "</p>"
    service = _build_service(
        message_responses={
            ("msg-4", "metadata"): _metadata_response("msg-4"),
            ("msg-4", "full"): _message_response("msg-4", text="", html=long_html),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-4",
        user_google_email="user@example.com",
        body_format="html",
        full=True,
    )

    assert "[Content truncated...]" not in result
    with open(_saved_path(result), "rb") as fh:
        saved = fh.read().decode()
    assert saved == long_html
    assert len(saved) > 20000


@pytest.mark.asyncio
async def test_full_export_http_returns_url(monkeypatch, tmp_path):
    import core.attachment_storage as attachment_storage

    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(attachment_storage, "_attachment_storage", None)
    monkeypatch.setattr(gmail_tools, "get_transport_mode", lambda: "streamable-http")
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        gmail_tools,
        "get_attachment_url",
        lambda file_id: f"https://example.test/attachments/{file_id}",
    )

    service = _build_service(
        message_responses={
            ("msg-5", "metadata"): _metadata_response("msg-5"),
            ("msg-5", "raw"): {"raw": _encode("raw body")},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-5",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    assert "https://example.test/attachments/" in result
    assert "raw body" not in result


@pytest.mark.asyncio
async def test_full_export_inlines_content_in_stateless_mode(monkeypatch):
    """Without file storage, full=True still delivers the complete message — inline."""
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: True)
    raw_mime = "From: sender@example.com\r\n\r\nComplete raw MIME body!"
    service = _build_service(
        message_responses={
            ("msg-6", "metadata"): _metadata_response("msg-6"),
            ("msg-6", "raw"): {"raw": _unpadded(raw_mime)},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-6",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    assert "--- BODY (COMPLETE, NOT TRUNCATED) ---" in result
    assert raw_mime in result
    assert "Format: eml" in result
    assert "Subject: Example subject" in result
    # No file was written, so nothing to point at.
    assert "Saved to:" not in result
    assert "Download URL:" not in result


@pytest.mark.asyncio
async def test_full_inline_in_stateless_mode_does_not_truncate(monkeypatch):
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: True)
    long_html = "<p>" + ("x" * 30000) + "</p>"
    service = _build_service(
        message_responses={
            ("msg-6c", "metadata"): _metadata_response("msg-6c"),
            ("msg-6c", "full"): _message_response("msg-6c", text="", html=long_html),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-6c",
        user_google_email="user@example.com",
        body_format="html",
        full=True,
    )

    assert long_html in result
    assert "[Content truncated...]" not in result


@pytest.mark.asyncio
async def test_inline_path_unaffected_by_stateless_mode(monkeypatch):
    """full=False is pure-read and must keep working in stateless deployments."""
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: True)
    service = _build_service(
        message_responses={
            ("msg-6b", "metadata"): _metadata_response("msg-6b"),
            ("msg-6b", "full"): _message_response("msg-6b", text="Inline body"),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-6b",
        user_google_email="user@example.com",
    )

    assert "Inline body" in result


@pytest.mark.asyncio
async def test_full_export_empty_raw_errors(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-7", "metadata"): _metadata_response("msg-7"),
            ("msg-7", "raw"): {"raw": ""},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-7",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    assert result.startswith("Error:")
    assert "no raw content" in result


@pytest.mark.asyncio
async def test_full_export_no_body_errors(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-8", "metadata"): _metadata_response("msg-8"),
            ("msg-8", "full"): _message_response("msg-8", text="", html=""),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-8",
        user_google_email="user@example.com",
        full=True,
    )

    assert result.startswith("Error:")
    assert "no readable body" in result


@pytest.mark.asyncio
async def test_full_export_text_converts_html_when_no_plaintext(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-9", "metadata"): _metadata_response("msg-9"),
            ("msg-9", "full"): _message_response(
                "msg-9", text="", html="<p>Converted body</p>"
            ),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-9",
        user_google_email="user@example.com",
        full=True,
    )

    with open(_saved_path(result), "rb") as fh:
        assert fh.read().decode() == "Converted body"


@pytest.mark.asyncio
async def test_full_export_html_falls_back_to_text_and_labels_it(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-10", "metadata"): _metadata_response("msg-10"),
            ("msg-10", "full"): _message_response(
                "msg-10", text="Only plaintext here", html=""
            ),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-10",
        user_google_email="user@example.com",
        body_format="html",
        full=True,
    )

    assert "No HTML body present" in result
    assert "Format: txt" in result
    saved_path = _saved_path(result)
    assert saved_path.endswith(".txt")
    with open(saved_path, "rb") as fh:
        assert fh.read().decode() == "Only plaintext here"


@pytest.mark.asyncio
async def test_full_export_sanitizes_subject_filename(stdio_storage):
    headers = _headers(Subject="../../etc/passwd")
    service = _build_service(
        message_responses={
            ("msg-11", "metadata"): _metadata_response("msg-11", headers=headers),
            ("msg-11", "raw"): {"raw": _encode("raw body")},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-11",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    saved_path = _saved_path(result)
    # No path traversal escapes the storage dir.
    assert Path(saved_path).parent == Path(stdio_storage)
    assert "/" not in Path(saved_path).name.replace(str(stdio_storage), "")


@pytest.mark.asyncio
async def test_full_export_long_subject_does_not_crash(stdio_storage):
    headers = _headers(Subject="X" * 900)
    service = _build_service(
        message_responses={
            ("msg-12", "metadata"): _metadata_response("msg-12", headers=headers),
            ("msg-12", "raw"): {"raw": _encode("raw body")},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-12",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    saved_path = _saved_path(result)
    assert Path(saved_path).exists()
    assert len(Path(saved_path).name) < 255
