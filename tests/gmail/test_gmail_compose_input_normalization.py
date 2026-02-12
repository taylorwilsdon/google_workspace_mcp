"""
Regression tests for Gmail compose input normalization.
"""

import pytest
from fastapi import Body as BodyParam

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import _normalize_compose_inputs


def test_normalize_compose_inputs_unwraps_fastapi_body_values():
    result = _normalize_compose_inputs(
        to=BodyParam("recipient@example.com"),
        subject=BodyParam("Subject"),
        body=BodyParam("Hello"),
        body_format=BodyParam("plain"),
        cc=BodyParam(None),
        bcc=BodyParam(None),
        from_name=BodyParam(None),
        from_email=BodyParam(None),
        thread_id=BodyParam(None),
        in_reply_to=BodyParam(None),
        references=BodyParam(None),
        attachments=BodyParam(None),
        require_to=True,
    )

    assert result["to"] == "recipient@example.com"
    assert result["subject"] == "Subject"
    assert result["body"] == "Hello"
    assert result["body_format"] == "plain"
    assert result["attachments"] is None


def test_normalize_compose_inputs_rejects_non_list_attachments():
    with pytest.raises(ValueError, match="attachments must be a list"):
        _normalize_compose_inputs(
            to="recipient@example.com",
            subject="Subject",
            body="Hello",
            body_format="plain",
            cc=None,
            bcc=None,
            from_name=None,
            from_email=None,
            thread_id=None,
            in_reply_to=None,
            references=None,
            attachments={"filename": "bad"},
            require_to=True,
        )


def test_normalize_compose_inputs_requires_to_for_send():
    with pytest.raises(ValueError, match="Recipient email address is required"):
        _normalize_compose_inputs(
            to=BodyParam(...),
            subject="Subject",
            body="Hello",
            body_format="plain",
            cc=None,
            bcc=None,
            from_name=None,
            from_email=None,
            thread_id=None,
            in_reply_to=None,
            references=None,
            attachments=None,
            require_to=True,
        )
