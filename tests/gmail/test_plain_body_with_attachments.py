"""A plain body sent with attachments carries an HTML alternative."""

import base64
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any, cast

import pytest

from gmail.gmail_helpers import _derive_web_bodies
from gmail.gmail_tools import (
    _prepare_gmail_message,
    draft_gmail_message,
    send_gmail_message,
)
from tests.gmail.test_draft_gmail_message import _mock_gmail_service, _unwrap

LONG_LINE = (
    "Thanks for your message. I am happy to move forward and I attach the "
    "draft agreement, which your lawyers can review or replace with their own."
)
BODY = f"Hi Jane,\n\n{LONG_LINE}\n\nBest,\nAlex"
ATTACHMENT = {
    "content": base64.b64encode(b"%PDF-1.7 agreement").decode(),
    "filename": "agreement.pdf",
    "mime_type": "application/pdf",
}


def _parse(raw_b64url: str) -> EmailMessage:
    """Decode a base64url raw message into an EmailMessage."""
    return cast(
        EmailMessage,
        message_from_bytes(base64.urlsafe_b64decode(raw_b64url), policy=SMTP),
    )


def _prepare(**overrides: Any) -> EmailMessage:
    """Build a plain-body message with one PDF attachment and parse it back."""
    fields: dict[str, Any] = {
        "subject": "Agreement",
        "body": BODY,
        "to": "jane@example.com",
        "attachments": [ATTACHMENT],
    }
    fields.update(overrides)
    raw_b64, _, attached, errors = _prepare_gmail_message(**fields)
    assert errors == []
    assert attached == 1
    return _parse(raw_b64)


def test_plain_body_with_attachment_has_html_alternative() -> None:
    """The attachment path adds the same HTML part as the Gmail-web path."""
    msg = _prepare()

    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    assert plain is not None and html is not None
    assert plain.get_content().rstrip("\r\n").replace("\r\n", "\n") == BODY

    expected_plain, expected_html = _derive_web_bodies(BODY, "plain", "auto")
    assert html.get_content().rstrip("\r\n") == expected_html
    # Long lines stay whole: no hard wrap introduced in either part.
    assert LONG_LINE in plain.get_content()
    assert f"<div>{LONG_LINE}</div>" in html.get_content()

    (attachment,) = [
        p for p in msg.walk() if p.get_content_disposition() == "attachment"
    ]
    assert attachment.get_filename() == "agreement.pdf"


def test_plain_body_html_alternative_follows_direction() -> None:
    """The HTML part honours the requested or detected text direction."""
    msg = _prepare(body="שלום\n\nתודה", direction="auto")
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert html.startswith('<div dir="rtl">')

    msg = _prepare(direction="rtl")
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert html.startswith('<div dir="rtl">')


@pytest.mark.asyncio
@pytest.mark.parametrize("as_draft", [False, True])
async def test_send_and_draft_pass_direction_to_attachment_path(as_draft: bool) -> None:
    """Both tools hand an explicit direction to the attachment path."""
    service = _mock_gmail_service()
    await _unwrap(draft_gmail_message if as_draft else send_gmail_message)(
        service=service,
        user_google_email="user@example.com",
        to="jane@example.com",
        subject="Agreement",
        body=BODY,
        attachments=[ATTACHMENT],
        direction="rtl",
        include_signature=False,
    )

    users = service.users.return_value
    if as_draft:
        raw = users.drafts.return_value.create.call_args.kwargs["body"]["message"][
            "raw"
        ]
    else:
        raw = users.messages.return_value.send.call_args.kwargs["body"]["raw"]
    html = _parse(raw).get_body(preferencelist=("html",)).get_content()
    assert html.startswith('<div dir="rtl">')
