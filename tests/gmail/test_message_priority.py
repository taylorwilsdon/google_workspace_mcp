"""Tests for the optional message priority on send, draft and forward.

'high' and 'low' add the Importance, X-Priority and Priority headers on both
message assembly paths (EmailMessage with attachments, and the Gmail-web
faithful path without them); 'normal' adds nothing.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.policy import SMTP

import pytest

from gmail.gmail_helpers import _prepare_gmail_message_web, _priority_headers
from gmail.gmail_tools import _prepare_gmail_message


def _parse(raw: str):
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=SMTP)


def test_priority_headers_high():
    assert _priority_headers("high") == [
        ("Importance", "High"),
        ("X-Priority", "1 (Highest)"),
        ("Priority", "urgent"),
    ]


def test_priority_headers_low():
    assert _priority_headers("low") == [
        ("Importance", "Low"),
        ("X-Priority", "5 (Lowest)"),
        ("Priority", "non-urgent"),
    ]


@pytest.mark.parametrize("priority", [None, "normal"])
def test_priority_headers_normal_adds_nothing(priority):
    assert _priority_headers(priority) == []


def test_priority_headers_rejects_unknown_value():
    with pytest.raises(ValueError):
        _priority_headers("urgent")


def test_prepare_gmail_message_high_priority_sets_headers():
    raw, _, _, _ = _prepare_gmail_message(
        subject="Hello",
        body="Body",
        to="alex@example.com",
        priority="high",
    )
    message = _parse(raw)

    assert message["Importance"] == "High"
    assert message["X-Priority"] == "1 (Highest)"
    assert message["Priority"] == "urgent"


def test_prepare_gmail_message_default_has_no_priority_headers():
    raw, _, _, _ = _prepare_gmail_message(
        subject="Hello", body="Body", to="alex@example.com"
    )
    message = _parse(raw)

    assert message["Importance"] is None
    assert message["X-Priority"] is None
    assert message["Priority"] is None


def test_prepare_gmail_message_web_low_priority_sets_headers():
    raw = _prepare_gmail_message_web(
        subject="Hello",
        plain_body="Body",
        html_body="<div>Body</div>",
        to="alex@example.com",
        priority="low",
    )
    message = _parse(raw)

    assert message["Importance"] == "Low"
    assert message["X-Priority"] == "5 (Lowest)"
    assert message["Priority"] == "non-urgent"


def test_prepare_gmail_message_web_default_has_no_priority_headers():
    raw = _prepare_gmail_message_web(
        subject="Hello",
        plain_body="Body",
        html_body="<div>Body</div>",
        to="alex@example.com",
    )
    message = _parse(raw)

    assert message["X-Priority"] is None
