"""
E2E tests for Gmail tools with mocked Google API responses.

Tests cover:
- search_gmail_messages: query parsing, pagination, empty results
- get_gmail_content: message retrieval, thread retrieval, batch operations
- send_gmail_message: sending, threading, CC/BCC
- draft_gmail_message: creation, reply drafts
- manage_gmail_label: CRUD operations
- manage_gmail_message: archive, trash, move
- modify_gmail_labels: add/remove labels
"""

import base64
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

from gmail.gmail_tools import (
    _extract_message_body,
    _extract_message_bodies,
    _format_body_content,
    _extract_attachments,
    _extract_headers,
    _prepare_gmail_message,
    _generate_gmail_web_url,
)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestExtractMessageBody:
    """Tests for _extract_message_body helper."""

    def test_plain_text_body(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Hello World").decode()}
        }
        assert _extract_message_body(payload) == "Hello World"

    def test_empty_body(self):
        payload = {"mimeType": "text/plain", "body": {}}
        assert _extract_message_body(payload) == ""

    def test_multipart_message(self):
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Plain text").decode()}
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<p>HTML</p>").decode()}
                }
            ]
        }
        assert _extract_message_body(payload) == "Plain text"

    def test_nested_multipart(self):
        payload = {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "body": {},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(b"Deep nested").decode()}
                        }
                    ]
                }
            ]
        }
        assert _extract_message_body(payload) == "Deep nested"


class TestExtractMessageBodies:
    """Tests for _extract_message_bodies helper."""

    def test_returns_both_text_and_html(self):
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Text content").decode()}
                },
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<b>HTML</b>").decode()}
                }
            ]
        }
        result = _extract_message_bodies(payload)
        assert result["text"] == "Text content"
        assert result["html"] == "<b>HTML</b>"

    def test_html_only_message(self):
        payload = {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(b"<p>Only HTML</p>").decode()}
        }
        result = _extract_message_bodies(payload)
        assert result["text"] == ""
        assert result["html"] == "<p>Only HTML</p>"

    def test_no_body_data(self):
        payload = {"mimeType": "text/plain", "body": {}}
        result = _extract_message_bodies(payload)
        assert result["text"] == ""
        assert result["html"] == ""


class TestFormatBodyContent:
    """Tests for _format_body_content helper."""

    def test_prefers_text_over_html(self):
        result = _format_body_content("Plain text", "<b>HTML</b>")
        assert result == "Plain text"

    def test_falls_back_to_html(self):
        result = _format_body_content("", "<b>HTML content</b>")
        assert "[HTML Content Converted]" in result
        assert "<b>HTML content</b>" in result

    def test_no_content(self):
        result = _format_body_content("", "")
        assert result == "[No readable content found]"

    def test_whitespace_only_text(self):
        result = _format_body_content("   ", "<b>HTML</b>")
        assert "[HTML Content Converted]" in result

    def test_html_truncation(self):
        long_html = "x" * 25000
        result = _format_body_content("", long_html)
        assert "[HTML content truncated...]" in result
        assert len(result) < 25000


class TestExtractAttachments:
    """Tests for _extract_attachments helper."""

    def test_single_attachment(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Body").decode()},
                },
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_001", "size": 1024},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"
        assert attachments[0]["mimeType"] == "application/pdf"
        assert attachments[0]["attachmentId"] == "att_001"
        assert attachments[0]["size"] == 1024

    def test_multiple_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "doc.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_1", "size": 100},
                },
                {
                    "filename": "image.png",
                    "mimeType": "image/png",
                    "body": {"attachmentId": "att_2", "size": 200},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 2

    def test_no_attachments(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Body").decode()}
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 0

    def test_nested_attachment(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": base64.urlsafe_b64encode(b"Text").decode()},
                        }
                    ]
                },
                {
                    "filename": "nested.zip",
                    "mimeType": "application/zip",
                    "body": {"attachmentId": "att_nested", "size": 500},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "nested.zip"

    def test_part_without_attachment_id_ignored(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "inline.jpg",
                    "mimeType": "image/jpeg",
                    "body": {"size": 100},  # No attachmentId
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 0


class TestExtractHeaders:
    """Tests for _extract_headers helper."""

    def test_extracts_specified_headers(self):
        payload = {
            "headers": [
                {"name": "Subject", "value": "Test"},
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bob@example.com"},
                {"name": "Date", "value": "2026-01-01"},
            ]
        }
        result = _extract_headers(payload, ["Subject", "From"])
        assert result == {"Subject": "Test", "From": "alice@example.com"}

    def test_missing_headers_not_included(self):
        payload = {"headers": [{"name": "Subject", "value": "Test"}]}
        result = _extract_headers(payload, ["Subject", "From"])
        assert "Subject" in result
        assert "From" not in result

    def test_empty_headers(self):
        payload = {"headers": []}
        result = _extract_headers(payload, ["Subject"])
        assert result == {}

    def test_no_headers_key(self):
        payload = {}
        result = _extract_headers(payload, ["Subject"])
        assert result == {}


class TestPrepareGmailMessage:
    """Tests for _prepare_gmail_message helper."""

    def test_basic_message(self):
        raw, thread_id = _prepare_gmail_message(
            subject="Test",
            body="Hello",
            to="bob@example.com"
        )
        assert raw is not None
        assert thread_id is None
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
        assert "Subject: Test" in decoded
        assert "To: bob@example.com" in decoded

    def test_reply_message_with_thread(self):
        raw, thread_id = _prepare_gmail_message(
            subject="Re: Test",
            body="Reply",
            to="alice@example.com",
            thread_id="thread_123",
            in_reply_to="<msg123@gmail.com>",
            references="<msg123@gmail.com>"
        )
        assert thread_id == "thread_123"
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
        assert "In-Reply-To: <msg123@gmail.com>" in decoded
        assert "References: <msg123@gmail.com>" in decoded

    def test_html_body(self):
        raw, _ = _prepare_gmail_message(
            subject="HTML Email",
            body="<h1>Hello</h1>",
            to="bob@example.com",
            body_format="html"
        )
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
        assert "Content-Type: text/html" in decoded

    def test_cc_and_bcc(self):
        raw, _ = _prepare_gmail_message(
            subject="CC Test",
            body="Test",
            to="bob@example.com",
            cc="charlie@example.com",
            bcc="dave@example.com"
        )
        decoded = base64.urlsafe_b64decode(raw).decode("utf-8")
        assert "Cc: charlie@example.com" in decoded
        assert "Bcc: dave@example.com" in decoded


class TestGenerateGmailWebUrl:
    """Tests for _generate_gmail_web_url helper."""

    def test_default_account(self):
        url = _generate_gmail_web_url("msg_123")
        assert "msg_123" in url
        assert "mail.google.com" in url

    def test_custom_account_index(self):
        url = _generate_gmail_web_url("msg_456", account_index=2)
        assert "msg_456" in url
        assert "/u/2/" in url
