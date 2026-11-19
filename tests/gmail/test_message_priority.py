"""Tests for the optional message priority on send, draft and forward."""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from gmail.gmail_helpers import _prepare_gmail_message_web, _priority_headers
from gmail.gmail_tools import (
    _prepare_gmail_message,
    draft_gmail_message,
    send_gmail_message,
)

ATTACHMENT = {"content": base64.b64encode(b"hi").decode(), "filename": "a.txt"}


def _parse(raw: str) -> EmailMessage:
    return message_from_bytes(base64.urlsafe_b64decode(raw), policy=SMTP)


def _unwrap(tool: Any) -> Callable[..., Any]:
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service() -> Mock:
    service = Mock()
    send_as = service.users.return_value.settings.return_value.sendAs.return_value
    send_as.list.return_value.execute.return_value = {"sendAs": []}
    messages = service.users.return_value.messages.return_value
    messages.get.return_value.execute.return_value = {
        "payload": {
            "headers": [{"name": "Subject", "value": "Original"}],
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"Original body").decode()},
        }
    }
    messages.send.return_value.execute.return_value = {"id": "msg_1"}
    service.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": "draft_1"
    }
    return service


def test_priority_headers_high() -> None:
    assert _priority_headers("high") == (
        ("Importance", "High"),
        ("X-Priority", "1 (Highest)"),
        ("Priority", "urgent"),
    )


def test_priority_headers_low() -> None:
    assert _priority_headers("low") == (
        ("Importance", "Low"),
        ("X-Priority", "5 (Lowest)"),
        ("Priority", "non-urgent"),
    )


def test_priority_headers_normal_adds_nothing() -> None:
    assert _priority_headers("normal") == ()


def test_priority_headers_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        _priority_headers("urgent")


def test_prepare_gmail_message_high_priority_sets_headers() -> None:
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


def test_prepare_gmail_message_default_has_no_priority_headers() -> None:
    raw, _, _, _ = _prepare_gmail_message(
        subject="Hello", body="Body", to="alex@example.com"
    )
    message = _parse(raw)

    assert message["Importance"] is None
    assert message["X-Priority"] is None
    assert message["Priority"] is None


def test_prepare_gmail_message_web_low_priority_sets_headers() -> None:
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


def test_prepare_gmail_message_web_default_has_no_priority_headers() -> None:
    raw = _prepare_gmail_message_web(
        subject="Hello",
        plain_body="Body",
        html_body="<div>Body</div>",
        to="alex@example.com",
    )
    message = _parse(raw)

    assert message["X-Priority"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [send_gmail_message, draft_gmail_message])
@pytest.mark.parametrize(
    "content",
    [{"subject": "Hello", "body": "Body"}, {"forward_message_id": "orig_1"}],
    ids=["compose", "forward"],
)
@pytest.mark.parametrize("attachments", [None, [ATTACHMENT]], ids=["web", "mime"])
async def test_tools_carry_priority_on_every_assembly_path(
    tool: Any,
    content: dict[str, str],
    attachments: list[dict[str, str]] | None,
) -> None:
    service = _service()

    await _unwrap(tool)(
        service=service,
        user_google_email="user@example.com",
        to="alex@example.com",
        include_signature=False,
        attachments=attachments,
        priority="low",
        **content,
    )

    users = service.users.return_value
    if tool is draft_gmail_message:
        sent = users.drafts.return_value.create.call_args.kwargs["body"]["message"]
    else:
        sent = users.messages.return_value.send.call_args.kwargs["body"]
    assert _parse(sent["raw"])["X-Priority"] == "5 (Lowest)"
