"""Tests for Gmail body_format support across helper and public tool APIs."""

import base64
import inspect
from email import message_from_bytes
from email.policy import SMTP
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError

import gmail.gmail_tools as gmail_tools
from core.utils import UserInputError
from gmail.gmail_helpers import (
    _signature_html_to_text,
    _wrap_signature_html,
    html_to_text_preserving_breaks,
)
from gmail.gmail_tools import (
    _extract_message_bodies,
    _format_body_content,
    _forward_gmail_message_impl,
    _html_to_text,
    _prepare_gmail_message,
    draft_gmail_message,
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


def test_get_gmail_message_content_preserves_existing_positional_arguments():
    signature = inspect.signature(_unwrap(get_gmail_message_content))
    bound = signature.bind(
        Mock(), "msg-1", "user@example.com", "html", True, "metadata"
    )

    assert bound.arguments["body_format"] == "html"
    assert bound.arguments["full"] is True
    assert bound.arguments["format"] == "metadata"


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


def _build_service(
    *, message_responses=None, thread_responses=None, attachment_responses=None
):
    message_responses = message_responses or {}
    thread_responses = thread_responses or {}
    attachment_responses = attachment_responses or {}

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

    def attachment_get(**kwargs):
        request = Mock()
        response = attachment_responses[(kwargs["messageId"], kwargs["id"])]
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        return request

    service.users().messages().get.side_effect = message_get
    service.users().messages().attachments().get.side_effect = attachment_get
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

    def test_appends_html_links_missing_from_plain_text(self):
        html = '<p>Ready.</p><a href="https://example.com/export/abc">Download Data</a>'
        result = _format_body_content(
            "Ready.\n\nDownload Data", html, include_links=True
        )
        assert result == (
            "Ready.\n\nDownload Data\n\n--- LINKS ---\n"
            "- Download Data: https://example.com/export/abc"
        )

    def test_appends_links_to_html_fallback(self):
        html = '<p>Hi</p> <a href=" https://x.example/?a=1&amp;b=2 ">Go</a>'
        result = _format_body_content("", html, include_links=True)
        assert result == "Hi Go\n\n--- LINKS ---\n- Go: https://x.example/?a=1&b=2"

    def test_link_listing_skips_known_duplicate_and_non_http_urls(self):
        html = (
            '<a href="https://known.example">Known</a>'
            '<a href="mailto:a@example.com">Mail</a>'
            '<a href="javascript:void(0)">JS</a>'
            '<a href="#top">Top</a>'
            "<a>No href</a>"
            '<a href="HTTPS://new.example/x"><img src="logo.png"></a>'
            '<a href="HTTPS://new.example/x">Ünïcode  label</a>'
            '<a href="http://plain.example">http://plain.example</a>'
        )
        result = _format_body_content(
            "See https://known.example", html, include_links=True
        )
        assert result == (
            "See https://known.example\n\n--- LINKS ---\n"
            "- Ünïcode label: HTTPS://new.example/x\n"
            "- http://plain.example"
        )

    @pytest.mark.parametrize(
        ("text", "listed"),
        [
            ("Visit https://example.com.", False),
            ("Home <https://example.com>", False),
            ("Home (https://example.com).", False),
            ("Unsub: https://example.com/u", True),
            ("Go to https://example.com.au/x", True),
            ("Go to https://example.com:8443/x", True),
            ("See https://example.com(1)", True),
            ("Ref [https://example.com]", False),
            ("Next https://example.com/?u=x", True),
        ],
    )
    def test_lists_link_unless_text_shows_the_exact_url(self, text, listed):
        html = '<a href="https://example.com">Home</a>'
        result = _format_body_content(text, html, include_links=True)
        links = "\n\n--- LINKS ---\n- Home: https://example.com"
        assert result == (text + links if listed else text)

    @pytest.mark.parametrize(
        ("html", "links"),
        [
            (
                '<a href="https://a.example">A</a><![foo[ x ]]>'
                '<a href="https://b.example">B</a>',
                "- A: https://a.example\n- B: https://b.example",
            ),
            (
                '<a href="https://a.example">A</a><![ ]>'
                '<a href="https://b.example">B</a>',
                "- A: https://a.example\n- B: https://b.example",
            ),
            (
                '<a href="https://a.example">A <![foo[ x ]]> more</a>',
                "- A more: https://a.example",
            ),
        ],
    )
    def test_keeps_links_around_a_malformed_marked_section(self, html, links):
        result = _format_body_content("Body", html, include_links=True)
        assert result == f"Body\n\n--- LINKS ---\n{links}"

    def test_normalizes_line_breaks_in_href_and_label(self):
        html = '<a href="https://example.com/a\n\tb">Split<br>label</a>'
        result = _format_body_content("Body", html, include_links=True)
        assert result == "Body\n\n--- LINKS ---\n- Split label: https://example.com/ab"

    def test_link_section_keeps_whole_urls_within_the_limit(self):
        html = "".join(
            f'<a href="https://example.com/{i:04}">L</a>' for i in range(2000)
        )
        result = _format_body_content("Body", html, include_links=True)
        entries = result.split("\n\n--- LINKS ---\n")[1].split("\n")
        assert entries[-2] == f"- L: https://example.com/{len(entries) - 2:04}"
        assert entries[-1] == f"[{2001 - len(entries)} more links omitted]"

    def test_lists_links_when_html_has_no_text(self):
        html = '<a href="https://example.com/x"><img src="logo.png"></a>'
        result = _format_body_content("", html, include_links=True)
        assert result == (
            "[No readable content found]\n\n--- LINKS ---\n- https://example.com/x"
        )

    def test_no_link_section_without_http_links(self):
        assert (
            _format_body_content("Hi", '<a href="#x">Hi</a>', include_links=True)
            == "Hi"
        )
        assert _format_body_content("Hi", "", include_links=True) == "Hi"

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

    def test_separates_adjacent_table_cells(self):
        signature = (
            '<table><tr><td><a href="mailto:j@example.com">j@example.com</a></td>'
            '<td><a href="https://cal.example.com">Book a call</a></td></tr></table>'
        )

        assert _signature_html_to_text(signature) == "j@example.com Book a call"

    def test_cell_separator_adds_no_extra_whitespace(self):
        signature = (
            "<table><tr><td>Jane<br></td></tr>"
            "<tr><td>Founder</td><td></td><td>Example </td></tr></table>"
        )

        assert _signature_html_to_text(signature) == "Jane\nFounder Example"

    def test_separates_cells_with_omitted_end_tags(self):
        signature = "<table><tr><th>Name<th>Title<tr><td>Jane<td>Founder</table>"

        assert _signature_html_to_text(signature) == "Name Title\nJane Founder"


class TestHtmlSignatureSeparator:
    SIGNATURE = "<div>Jane</div>"
    WRAPPED = _wrap_signature_html(SIGNATURE)

    @pytest.mark.parametrize(
        ("body", "separator"),
        [
            ("Hello", "<br><br>"),
            ("<b>Hello</b>", "<br><br>"),
            ("<div>Hello</div>", "<br>"),
            ("<table><tr><td>Hello</td></tr></table>", "<br>"),
            ("<p>Hello</p>", ""),
            ("<P>Hello</P >\n", ""),
            ("<div><ul><li>Hello</li></ul></div>", ""),
            ("<p>Hello</p><span>there</span>", "<br><br>"),
            ("Hello<br>", "<br>"),
            ("<p>Hello</p><HR />\n", "<br>"),
            ('<p style="margin:0">Hello</p>', "<br>"),
            ("<style>p{margin:0}</style><p>Hello</p>", "<br>"),
            ("<style>.label{color:red}</style><p>Hello</p>", ""),
            ("<p>Hello</p><!-- footer -->\n", ""),
            ("<div>Hello</div> <!-- a --><!-- b -->", "<br>"),
            ("", ""),
        ],
    )
    def test_appended_signature_gets_one_blank_line(self, body, separator):
        result = gmail_tools._append_signature_to_body(body, "html", self.SIGNATURE)

        assert result == f"{body}{separator}{self.WRAPPED}"

    @pytest.mark.parametrize("body", ["Hello", "<div>Hello</div>", "<p>Hello</p>"])
    def test_plain_alternative_keeps_blank_line_before_signature(self, body):
        result = gmail_tools._append_signature_to_body(body, "html", self.SIGNATURE)

        assert html_to_text_preserving_breaks(result) == "Hello\n\nJane"

    def test_quoted_reply_signature_follows_block_body(self):
        result = gmail_tools._build_quoted_reply_body(
            "<p>Thanks</p>", "html", self.SIGNATURE, {"html_body": "<p>Hi</p>"}
        )

        assert result.startswith(f"<p>Thanks</p>{self.WRAPPED}")


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

    @pytest.mark.parametrize(
        ("content_type", "raw", "expected"),
        [
            ('text/plain; charset="iso-8859-1"', "Café".encode("latin-1"), "Café"),
            ("text/plain; charset=windows-1252", "“naïve”".encode("cp1252"), "“naïve”"),
            (
                "text/plain; charset=ISO-2022-JP",
                "日本語".encode("iso-2022-jp"),
                "日本語",
            ),
            ("text/plain; charset=windows-1252", b"caf\xe9\x81", "café"),
            ("text/plain; charset=windows-1252", "Café".encode(), "Café"),
            ("text/plain; charset=iso-8859-1", "“naïve”".encode("cp1252"), "“naïve”"),
            ("text/plain; charset=us-ascii", "Café".encode(), "Café"),
            ("text/plain; charset=us-ascii", "“Café”".encode("cp1252"), "“Café”"),
            ("text/plain; charset=x-unknown", "Café".encode(), "Café"),
            ("text/plain; charset=x-unknown", b"caf\xe9", "caf"),
            ("text/plain; charset=gbk", "一".encode("gbk"), "一"),
            ("text/plain", "Café".encode(), "Café"),
        ],
    )
    def test_decodes_declared_charset(self, content_type, raw, expected):
        part = {
            "mimeType": "text/plain",
            "headers": [{"name": "Content-Type", "value": content_type}],
            "body": {"data": base64.urlsafe_b64encode(raw).decode()},
        }
        multipart = {"mimeType": "multipart/alternative", "parts": [part]}
        assert _extract_message_bodies(part)["text"] == expected
        assert _extract_message_bodies(multipart)["text"] == expected


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
async def test_get_gmail_message_content_metadata_format_returns_headers_only():
    """The singular tool accepts the batch tool's format argument (#1152)."""
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        format="metadata",
    )

    assert "From: sender@example.com" in result
    assert "--- BODY ---" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("options", "error"),
    [
        ({"body_format": "html"}, "require format='full'"),
        ({"body_format": "raw"}, "require format='full'"),
        ({"full": True}, "full=True requires format='full'"),
        ({"include_headers": False}, "include_headers=False requires format='full'"),
    ],
)
async def test_get_gmail_message_content_rejects_metadata_with_body_options(
    options, error
):
    with pytest.raises(UserInputError, match=error):
        await _unwrap(get_gmail_message_content)(
            service=_build_service(),
            message_id="msg-1",
            user_google_email="user@example.com",
            format="metadata",
            **options,
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
@pytest.mark.parametrize(
    ("text", "html", "body"),
    [
        (
            "Ready.\r\nDownload Data\r\n",
            '<p>Ready.</p><a href="https://example.com/export/abc">Download Data</a>',
            "Ready.\r\nDownload Data\r\n",
        ),
        ("", '<p>Hi</p> <a href="https://example.com/x">Go</a>', "Hi Go"),
    ],
)
async def test_get_gmail_message_content_omits_links_by_default(text, html, body):
    service = _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "full"): _message_response("msg-1", text=text, html=html),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
    )

    assert result == (
        "Subject: Example subject\n"
        "From: sender@example.com\n"
        "Date: Fri, 28 Mar 2026 10:00:00 -0400\n"
        "Message-ID: <message@example.com>\n"
        "To: recipient@example.com\n"
        "Cc: cc@example.com\n"
        f"\n--- BODY ---\n{body}"
    )


@pytest.mark.asyncio
async def test_get_gmail_thread_content_lists_html_links():
    html = '<p>Ready.</p><a href="https://example.com/export/abc">Download Data</a>'
    service = _build_service(
        thread_responses={
            ("thread-1", "full"): {
                "messages": [
                    _thread_message("msg-1", text="Ready.\nDownload Data", html=html)
                ]
            }
        },
    )

    result = await _unwrap(get_gmail_thread_content)(
        service=service,
        thread_id="thread-1",
        user_google_email="user@example.com",
        include_links=True,
    )

    assert "- Download Data: https://example.com/export/abc" in result


@pytest.mark.asyncio
async def test_get_gmail_messages_content_batch_lists_html_links():
    html = '<p>Ready.</p><a href="https://example.com/export/abc">Download Data</a>'
    service = _build_service(
        message_responses={
            ("msg-1", "full"): _message_response(
                "msg-1", text="Ready.\nDownload Data", html=html
            ),
        }
    )

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="user@example.com",
        include_links=True,
    )

    assert "- Download Data: https://example.com/export/abc" in result


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
async def test_full_export_rejects_size_estimate_before_fetch(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    metadata = _metadata_response("msg-large")
    metadata["sizeEstimate"] = 500
    service = _build_service(
        message_responses={
            ("msg-large", "metadata"): metadata,
            ("msg-large", "raw"): {"raw": _encode("should not be fetched")},
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-large",
        user_google_email="user@example.com",
        body_format="raw",
        full=True,
    )

    assert result.startswith("Error:")
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in result
    request_formats = [
        call.kwargs["format"] for call in service.users().messages().get.call_args_list
    ]
    assert request_formats == ["metadata"]


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
async def test_full_export_text_lists_html_links(stdio_storage):
    service = _build_service(
        message_responses={
            ("msg-2", "metadata"): _metadata_response("msg-2"),
            ("msg-2", "full"): _message_response(
                "msg-2", text="Plain body", html='<a href="https://x.example">X</a>'
            ),
        }
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-2",
        user_google_email="user@example.com",
        full=True,
        include_links=True,
    )

    with open(_saved_path(result), "rb") as fh:
        assert fh.read().decode() == (
            "Plain body\n\n--- LINKS ---\n- X: https://x.example"
        )


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


SPAM_SUBJECT = "\U0001f525\U0001f525 WIN BIG \U0001f525\U0001f525"
SPAM_RAW = (
    f"Subject: {SPAM_SUBJECT}\r\nFrom: Spammer <spam@example.com>\r\n\r\nspam body"
)


def _spam_service(message_id: str):
    headers = _headers(Subject=SPAM_SUBJECT, From="Spammer <spam@example.com>")
    return _build_service(
        message_responses={
            (message_id, "metadata"): _metadata_response(message_id, headers=headers),
            (message_id, "full"): _message_response(
                message_id, text="spam body", headers=headers
            ),
            (message_id, "raw"): {"raw": _encode(SPAM_RAW)},
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_format", "saved"), [("text", "spam body"), ("raw", SPAM_RAW)]
)
async def test_full_export_without_headers_keeps_subject_out(
    stdio_storage, body_format, saved
):
    result = await _unwrap(get_gmail_message_content)(
        service=_spam_service("spam-1"),
        message_id="spam-1",
        user_google_email="user@example.com",
        body_format=body_format,
        full=True,
        include_headers=False,
    )

    assert SPAM_SUBJECT not in result
    assert "Spammer" not in result
    assert "From:" not in result
    saved_path = _saved_path(result)
    assert Path(saved_path).name.startswith("spam-1_")
    with open(saved_path, "rb") as fh:
        assert fh.read().decode() == saved


@pytest.mark.asyncio
async def test_inline_content_without_headers_keeps_subject_out():
    result = await _unwrap(get_gmail_message_content)(
        service=_spam_service("spam-2"),
        message_id="spam-2",
        user_google_email="user@example.com",
        include_headers=False,
    )

    assert result.startswith("\n--- BODY ---\nspam body")
    assert SPAM_SUBJECT not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(("full", "stateless"), [(False, False), (True, True)])
async def test_inline_raw_without_headers_is_rejected(monkeypatch, full, stateless):
    monkeypatch.setattr(gmail_tools, "is_stateless_mode", lambda: stateless)
    monkeypatch.setattr(gmail_tools, "gcs_files_enabled", lambda: False)
    service = _spam_service("spam-3")

    with pytest.raises(UserInputError, match="inline raw MIME"):
        await _unwrap(get_gmail_message_content)(
            service=service,
            message_id="spam-3",
            user_google_email="user@example.com",
            body_format="raw",
            full=full,
            include_headers=False,
        )
    service.users().messages().get.assert_not_called()


def _attachment_data(raw: bytes) -> dict:
    return {"data": base64.urlsafe_b64encode(raw).decode().rstrip("="), "size": 64}


def _stored_part(mime_type: str, attachment_id: str, charset=None, **extra) -> dict:
    part = {"mimeType": mime_type, "body": {"attachmentId": attachment_id, "size": 64}}
    if charset:
        part["headers"] = [
            {"name": "Content-Type", "value": f"{mime_type}; charset={charset}"}
        ]
    return {**part, **extra}


def _stored_body_service(parts, attachment_responses=None):
    message = {
        "id": "msg-1",
        "payload": {
            "headers": _headers(),
            "mimeType": "multipart/alternative",
            "parts": parts,
        },
    }
    return _build_service(
        message_responses={
            ("msg-1", "metadata"): _metadata_response("msg-1"),
            ("msg-1", "full"): message,
        },
        thread_responses={("thread-1", "full"): {"messages": [message]}},
        attachment_responses={
            ("msg-1", "att-text"): _attachment_data("Plain café".encode()),
            ("msg-1", "att-html"): _attachment_data(
                "<p>Café “naïve”</p>".encode("cp1252")
            ),
            **(attachment_responses or {}),
        },
    )


def _fetched_attachment_ids(service):
    attachment_get = service.users().messages().attachments().get
    return [call.kwargs["id"] for call in attachment_get.call_args_list]


def _stored_body_parts():
    return [
        _stored_part("text/plain", "att-text"),
        _stored_part("text/html", "att-html", charset="windows-1252"),
    ]


STORED_BODY_READERS = {
    "message": (get_gmail_message_content, {"message_id": "msg-1"}),
    "messages_batch": (get_gmail_messages_content_batch, {"message_ids": ["msg-1"]}),
    "thread": (get_gmail_thread_content, {"thread_id": "thread-1"}),
    "threads_batch": (get_gmail_threads_content_batch, {"thread_ids": ["thread-1"]}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", STORED_BODY_READERS)
@pytest.mark.parametrize(
    ("body_format", "expected"),
    [("text", "Plain café"), ("html", "<p>Café “naïve”</p>")],
)
async def test_read_tools_fetch_attachment_backed_bodies(reader, body_format, expected):
    tool, kwargs = STORED_BODY_READERS[reader]
    service = _stored_body_service(_stored_body_parts())

    result = await _unwrap(tool)(
        service=service,
        user_google_email="user@example.com",
        body_format=body_format,
        **kwargs,
    )

    assert expected in result
    assert sorted(_fetched_attachment_ids(service)) == ["att-html", "att-text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body_format", "expected"),
    [("text", "Plain café"), ("html", "<p>Café “naïve”</p>")],
)
async def test_full_export_fetches_attachment_backed_bodies(
    stdio_storage, body_format, expected
):
    result = await _unwrap(get_gmail_message_content)(
        service=_stored_body_service(_stored_body_parts()),
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format=body_format,
        full=True,
    )

    with open(_saved_path(result), "rb") as fh:
        assert fh.read().decode() == expected


@pytest.mark.asyncio
async def test_attachment_backed_html_only_body_is_converted_to_text():
    service = _stored_body_service(
        [_stored_part("text/html", "att-html", charset="windows-1252")]
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service, message_id="msg-1", user_google_email="user@example.com"
    )

    assert result.endswith("\n--- BODY ---\nCafé “naïve”")


@pytest.mark.asyncio
async def test_inline_body_parts_are_not_refetched():
    service = _stored_body_service(
        [
            {"mimeType": "text/plain", "body": {"data": _encode("Inline text")}},
            _stored_part("text/html", "att-html", charset="windows-1252"),
        ]
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="html",
    )

    assert result.endswith("\n--- BODY ---\n<p>Café “naïve”</p>")
    assert _fetched_attachment_ids(service) == ["att-html"]


@pytest.mark.asyncio
async def test_file_attachments_are_not_fetched_as_bodies():
    service = _stored_body_service(
        [
            {"mimeType": "text/plain", "body": {"data": _encode("Body text")}},
            _stored_part("text/html", "att-html", filename="page.html"),
        ]
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="html",
    )

    assert "\n--- BODY ---\nBody text\n\n--- ATTACHMENTS ---\n1. page.html" in result
    assert _fetched_attachment_ids(service) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("boom"), {"size": 0}, {"data": "!"}, {"data": "=="}, {"data": "A"}],
)
async def test_failed_body_fetch_keeps_the_other_body(failure):
    service = _stored_body_service(
        _stored_body_parts(), attachment_responses={("msg-1", "att-html"): failure}
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="html",
    )

    assert result.endswith("\n--- BODY ---\nPlain café")


@pytest.mark.asyncio
async def test_body_fetch_respects_max_file_bytes(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "32")
    service = _stored_body_service(_stored_body_parts())

    await _unwrap(get_gmail_message_content)(
        service=service, message_id="msg-1", user_google_email="user@example.com"
    )

    assert _fetched_attachment_ids(service) == []


@pytest.mark.asyncio
async def test_body_fetch_checks_the_response_size_when_none_is_declared(
    monkeypatch,
):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "32")
    part = _stored_part("text/plain", "att-text")
    del part["body"]["size"]
    service = _stored_body_service([part])

    result = await _unwrap(get_gmail_message_content)(
        service=service, message_id="msg-1", user_google_email="user@example.com"
    )

    assert _fetched_attachment_ids(service) == ["att-text"]
    assert "Plain café" not in result


@pytest.mark.asyncio
async def test_failed_body_fetch_falls_through_to_the_next_part():
    service = _stored_body_service(
        [
            _stored_part("text/html", "att-broken"),
            _stored_part("text/html", "att-html", charset="windows-1252"),
        ],
        attachment_responses={("msg-1", "att-broken"): RuntimeError("boom")},
    )

    result = await _unwrap(get_gmail_message_content)(
        service=service,
        message_id="msg-1",
        user_google_email="user@example.com",
        body_format="html",
    )

    assert result.endswith("\n--- BODY ---\n<p>Café “naïve”</p>")
    assert _fetched_attachment_ids(service) == ["att-broken", "att-html"]


def _created_draft(service):
    create = service.users().drafts().create
    raw = create.call_args.kwargs["body"]["message"]["raw"]
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=SMTP)


@pytest.mark.asyncio
async def test_forward_includes_attachment_backed_bodies():
    service = _stored_body_service(_stored_body_parts())

    await _forward_gmail_message_impl(
        service=service,
        message_id="msg-1",
        to="recipient@example.com",
        user_google_email="user@example.com",
        as_draft=True,
    )

    draft = _created_draft(service)
    assert "Plain café" in draft.get_body(preferencelist=("plain",)).get_content()
    assert "Café “naïve”" in draft.get_body(preferencelist=("html",)).get_content()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "too_large, failure",
    [
        (False, RuntimeError("boom")),
        (True, RuntimeError("boom")),
        (False, {"data": "=="}),
        (False, {"data": "A"}),
    ],
)
async def test_forward_aborts_when_a_body_cannot_be_fetched(
    monkeypatch, too_large, failure
):
    if too_large:
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "32")
    service = _stored_body_service(
        _stored_body_parts(),
        attachment_responses={("msg-1", "att-html"): failure},
    )

    with pytest.raises(ToolError, match="original message body"):
        await _forward_gmail_message_impl(
            service=service,
            message_id="msg-1",
            to="recipient@example.com",
            user_google_email="user@example.com",
            as_draft=True,
        )

    service.users().drafts().create.assert_not_called()


@pytest.mark.asyncio
async def test_reply_quote_fetches_only_the_target_body():
    def stored_message(message_id, attachment_id):
        return {
            "id": message_id,
            "payload": {
                "headers": _headers(**{"Message-ID": f"<{message_id}@example.com>"}),
                "mimeType": "multipart/alternative",
                "parts": [_stored_part("text/plain", attachment_id)],
            },
        }

    service = _build_service(
        thread_responses={
            ("thread-1", "full"): {
                "messages": [
                    stored_message("msg-0", "att-old"),
                    stored_message("msg-1", "att-text"),
                ]
            }
        },
        attachment_responses={
            ("msg-1", "att-text"): _attachment_data("Plain café".encode())
        },
    )
    service.users().settings().sendAs().list().execute.return_value = {"sendAs": []}

    await _unwrap(draft_gmail_message)(
        service=service,
        user_google_email="user@example.com",
        to="recipient@example.com",
        subject="Re: Example subject",
        body="Thanks",
        thread_id="thread-1",
        quote_original=True,
        include_signature=False,
    )

    assert "Plain café" in _created_draft(service).get_body(("plain",)).get_content()
    assert _fetched_attachment_ids(service) == ["att-text"]
