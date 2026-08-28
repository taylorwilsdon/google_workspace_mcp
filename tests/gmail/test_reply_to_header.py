"""Regression test for the Reply-To header gap.

get_gmail_message_content (and get_gmail_thread_content, covered separately in
test_get_gmail_thread_content_analysis.py) silently dropped Reply-To: it was
never in GMAIL_METADATA_HEADERS, so Gmail's metadataHeaders filter excluded it
from the API response before this project's formatting code ever saw it.
"""

from __future__ import annotations

from gmail.gmail_helpers import GMAIL_METADATA_HEADERS
from gmail.gmail_tools import _format_message_header_lines


def test_reply_to_in_metadata_headers_whitelist():
    """Reply-To must be requested from the Gmail API or it never arrives."""
    assert "Reply-To" in GMAIL_METADATA_HEADERS


def test_format_message_header_lines_includes_reply_to_when_present():
    """When Reply-To is in the headers dict, it must appear in the output."""
    headers = {
        "Subject": "Test",
        "From": "Alex <alex@example.com>",
        "Reply-To": "alex-support@example.com",
        "Date": "Mon, 14 Apr 2026 09:00:00 -0400",
    }

    lines = _format_message_header_lines(headers)

    assert "Reply-To: alex-support@example.com" in lines


def test_format_message_header_lines_omits_reply_to_when_absent():
    """Without Reply-To in the headers dict, no Reply-To line is emitted."""
    headers = {
        "Subject": "Test",
        "From": "Alex <alex@example.com>",
        "Date": "Mon, 14 Apr 2026 09:00:00 -0400",
    }

    lines = _format_message_header_lines(headers)

    assert not any(line.startswith("Reply-To:") for line in lines)
