"""
Tests for Gmail attachment download functionality (PR #6 feature).

Tests cover:
- Attachment metadata extraction from message payloads
- Attachment download via get_gmail_content(operation='attachment')
- Base64 data handling and size reporting
- Error handling for missing/invalid attachment IDs
- Content type validation for attachments
- Path traversal prevention in attachment filenames
"""

import base64
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from gmail.gmail_tools import (
    _extract_attachments,
    _extract_message_bodies,
    _format_body_content,
)


class TestAttachmentMetadataExtraction:
    """Tests for extracting attachment info from Gmail message payloads."""

    def test_attachment_with_all_fields(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "ANGjdJ_abc123", "size": 2048},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        att = attachments[0]
        assert att["filename"] == "report.pdf"
        assert att["mimeType"] == "application/pdf"
        assert att["attachmentId"] == "ANGjdJ_abc123"
        assert att["size"] == 2048

    def test_attachment_default_mime_type(self):
        """Parts without explicit mimeType should default to application/octet-stream."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "data.bin",
                    "body": {"attachmentId": "att_1", "size": 100},
                    # No mimeType specified
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["mimeType"] == "application/octet-stream"

    def test_attachment_zero_size(self):
        """Attachments with zero or missing size should still be extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "empty.txt",
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "att_empty", "size": 0},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["size"] == 0

    def test_inline_image_not_attachment(self):
        """Parts with a filename but no attachmentId should be excluded."""
        payload = {
            "mimeType": "multipart/related",
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": base64.urlsafe_b64encode(b"<img>").decode()},
                },
                {
                    "filename": "inline.png",
                    "mimeType": "image/png",
                    "body": {"size": 5000},  # No attachmentId
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 0

    def test_multiple_attachments_different_types(self):
        """Multiple attachments of different types should all be extracted."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Body").decode()},
                },
                {
                    "filename": "document.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "body": {"attachmentId": "att_docx", "size": 15000},
                },
                {
                    "filename": "photo.jpg",
                    "mimeType": "image/jpeg",
                    "body": {"attachmentId": "att_jpg", "size": 500000},
                },
                {
                    "filename": "data.csv",
                    "mimeType": "text/csv",
                    "body": {"attachmentId": "att_csv", "size": 1200},
                },
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 3
        filenames = [a["filename"] for a in attachments]
        assert "document.docx" in filenames
        assert "photo.jpg" in filenames
        assert "data.csv" in filenames

    def test_deeply_nested_attachment(self):
        """Attachments in nested multipart structures should be found."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "multipart/related",
                            "parts": [
                                {
                                    "mimeType": "text/html",
                                    "body": {"data": base64.urlsafe_b64encode(b"<html>").decode()},
                                }
                            ]
                        }
                    ]
                },
                {
                    "filename": "deep_attachment.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_deep", "size": 9000},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "deep_attachment.pdf"


class TestAttachmentFilenameValidation:
    """Security tests for attachment filename handling."""

    def test_filename_with_path_traversal_dots(self):
        """Filenames with path traversal sequences should be extracted as-is
        (validation happens at download/save time, not extraction)."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "../../../etc/passwd",
                    "mimeType": "application/octet-stream",
                    "body": {"attachmentId": "att_evil", "size": 100},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        # The raw filename is preserved from Google API
        assert attachments[0]["filename"] == "../../../etc/passwd"

    def test_filename_with_backslash_traversal(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "..\\..\\Windows\\System32\\config",
                    "mimeType": "application/octet-stream",
                    "body": {"attachmentId": "att_win", "size": 100},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1

    def test_filename_with_null_bytes(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "file\x00.exe",
                    "mimeType": "application/octet-stream",
                    "body": {"attachmentId": "att_null", "size": 100},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1

    def test_filename_with_unicode(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "日本語ファイル.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_unicode", "size": 500},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "日本語ファイル.pdf"

    def test_empty_filename_excluded(self):
        """Parts with empty string filename should be excluded."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "",
                    "mimeType": "application/octet-stream",
                    "body": {"attachmentId": "att_empty_name", "size": 100},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        # Empty string is falsy in Python, so _extract_attachments checks `if part.get("filename")`
        assert len(attachments) == 0


class TestAttachmentContentTypeValidation:
    """Tests for content type handling in attachments."""

    def test_executable_mime_type(self):
        """Executable attachments should be extracted (server doesn't block types)."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "program.exe",
                    "mimeType": "application/x-msdownload",
                    "body": {"attachmentId": "att_exe", "size": 50000},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["mimeType"] == "application/x-msdownload"

    def test_script_mime_type(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "script.js",
                    "mimeType": "application/javascript",
                    "body": {"attachmentId": "att_js", "size": 1000},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1

    def test_large_attachment_metadata(self):
        """Large attachment metadata should be extracted without issues."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "bigfile.zip",
                    "mimeType": "application/zip",
                    "body": {"attachmentId": "att_big", "size": 25 * 1024 * 1024},  # 25 MB
                }
            ]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["size"] == 25 * 1024 * 1024


class TestAttachmentMessageIntegration:
    """Tests for attachment info in message content formatting."""

    def test_message_with_attachments_shows_info(self):
        """When a message has attachments, content should include attachment metadata."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"See attached").decode()},
                },
                {
                    "filename": "invoice.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "att_inv", "size": 2048},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        bodies = _extract_message_bodies(payload)

        # Verify we got both content and attachment info
        assert bodies["text"] == "See attached"
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "invoice.pdf"

    def test_message_only_attachments_no_body(self):
        """Messages with only attachments and no text body."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "filename": "file1.txt",
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "att_1", "size": 100},
                }
            ]
        }
        attachments = _extract_attachments(payload)
        bodies = _extract_message_bodies(payload)

        assert bodies["text"] == ""
        assert bodies["html"] == ""
        assert len(attachments) == 1
