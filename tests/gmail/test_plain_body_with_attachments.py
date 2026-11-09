"""A plain body sent with attachments carries an HTML alternative.

A text/plain-only draft opens in Gmail's plain-text compose mode, and Gmail
hard-wraps every line at ~76 characters when the user sends it from there.
The attachment path must produce the same two body parts as the
attachment-free Gmail-web path.
"""

import base64
from email import message_from_bytes
from email.policy import SMTP

from gmail.gmail_helpers import _derive_web_bodies
from gmail.gmail_tools import _prepare_gmail_message

LONG_LINE = (
    "Thanks for your message. I am happy to move forward and I attach the "
    "draft agreement, which your lawyers can review or replace with their own."
)
BODY = f"Hi Jane,\n\n{LONG_LINE}\n\nBest,\nAlex"


def _parse(raw_b64url: str):
    return message_from_bytes(base64.urlsafe_b64decode(raw_b64url), policy=SMTP)


def _prepare(**overrides):
    fields = {
        "subject": "Agreement",
        "body": BODY,
        "to": "jane@example.com",
        "attachments": [
            {
                "content": base64.b64encode(b"%PDF-1.7 agreement").decode(),
                "filename": "agreement.pdf",
                "mime_type": "application/pdf",
            }
        ],
    }
    fields.update(overrides)
    raw_b64, _, attached, errors = _prepare_gmail_message(**fields)
    assert errors == []
    assert attached == 1
    return _parse(raw_b64)


def test_plain_body_with_attachment_has_html_alternative():
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


def test_plain_body_html_alternative_follows_direction():
    msg = _prepare(body="שלום\n\nתודה", direction="auto")
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert html.startswith('<div dir="rtl">')

    msg = _prepare(direction="rtl")
    html = msg.get_body(preferencelist=("html",)).get_content()
    assert html.startswith('<div dir="rtl">')
