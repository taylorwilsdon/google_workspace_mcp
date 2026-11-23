"""Integration tests for the Gmail-web faithful send path.

All Gmail API calls are mocked. Synthetic data only (example.com / example.org,
generic names).
"""

import base64
import quopri
import re
from email import message_from_string
from unittest.mock import Mock

import pytest

from gmail.gmail_tools import send_gmail_message


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _raw_sent(mock_service) -> str:
    kwargs = mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    raw = kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw.encode()).decode("utf-8")


def _decode_bodies(raw: str) -> str:
    headers, _, body = raw.partition("\r\n\r\n")
    decoded = quopri.decodestring(body.encode("utf-8")).decode("utf-8")
    return f"{headers}\r\n\r\n{decoded}"


def _part(raw: str, content_type: str) -> str:
    """Return a decoded body part, whichever CTE (QP or base64) it uses."""
    return "".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in message_from_string(raw).walk()
        if part.get_content_type() == content_type
    )


def _gmail_service():
    service = Mock()
    service.users().messages().send().execute.return_value = {"id": "sent123"}
    # No signature.
    service.users().settings().sendAs().list().execute.return_value = {"sendAs": []}
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "direction", "expected"),
    [
        ("שלום עולם\n\nזה גוף ההודעה", "auto", "rtl"),
        ("Hello there", "auto", "ltr"),
        # An explicit direction wins even when the first strong char is LTR.
        ("OK שלום עולם", "rtl", "rtl"),
    ],
)
async def test_send_body_direction(body, direction, expected):
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body=body,
        direction=direction,
        include_signature=False,
    )

    html = _part(_raw_sent(gmail), "text/html")
    assert html.startswith(f'<div dir="{expected}">')


@pytest.mark.asyncio
async def test_send_new_message_html_looks_hand_typed():
    """The new-body HTML matches Gmail's typed structure, with no tool markup
    (class/style/p/data attributes, utility-CSS tokens) or mailer headers."""
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="First line\n\nSecond line",
        include_signature=False,
    )

    msg = _decode_bodies(_raw_sent(gmail))

    # The authored new-body block.
    block = re.search(r'(<div dir="ltr">.*?</div>)\r?\n?--', msg, re.DOTALL)
    assert block, msg
    new_body = block.group(1)

    # Typed Gmail structure: per-line <div>, blank line as <div><br></div>.
    assert "<div>First line</div>" in new_body
    assert "<div><br></div>" in new_body
    assert "<div>Second line</div>" in new_body

    # No fingerprints inside the new-body block.
    assert "class=" not in new_body
    assert "style=" not in new_body
    assert "<p " not in new_body and "<p>" not in new_body
    assert "data-" not in new_body
    for token in (
        "gmail-font-",
        "whitespace-normal",
        "break-words",
        "leading-[",
        "list-disc",
        "pl-",
    ):
        assert token not in msg

    # No vendor mailer headers anywhere.
    header_block = msg.split("\r\n\r\n", 1)[0].lower()
    assert "x-mailer" not in header_block
    assert "user-agent" not in header_block


def _gmail_service_with_send_as(display_name: str, email: str):
    service = _gmail_service()
    service.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {
                "sendAsEmail": email,
                "displayName": display_name,
                "isPrimary": True,
                "signature": "",
            }
        ]
    }
    return service


@pytest.mark.asyncio
async def test_send_as_display_name_populates_from():
    """The From line uses the Gmail Send-As displayName (what web shows), fetched
    once alongside the signature."""
    gmail = _gmail_service_with_send_as("Grace Hopper", "grace@example.org")

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=True,
    )

    raw = _raw_sent(gmail)
    assert "From: Grace Hopper <grace@example.org>" in raw


@pytest.mark.asyncio
async def test_reply_builds_gmail_quote_from_parent():
    gmail = _gmail_service()
    # Parent thread fetch (full, with bodies) for the quote + auto-threading.
    thread_full = {
        "messages": [
            {
                "id": "p1",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<parent@example.com>"},
                        {"name": "From", "value": "Ada Lovelace <ada@example.com>"},
                        {"name": "Subject", "value": "Project sync"},
                        {
                            "name": "Date",
                            "value": "Tue, 7 Apr 2026 19:19:00 +0000",
                        },
                    ],
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {"data": _encode("Original line")},
                        },
                        {
                            "mimeType": "text/html",
                            "body": {"data": _encode("<div>Original line</div>")},
                        },
                    ],
                },
            }
        ]
    }
    gmail.users().threads().get().execute.return_value = thread_full
    # Reset call count so the assertion below measures only the send's fetch
    # (the setup line above already invoked .get() once).
    gmail.users.return_value.threads.return_value.get.reset_mock()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
        quote_original=True,
    )

    msg = _decode_bodies(_raw_sent(gmail))
    # The thread is fetched exactly once (shared by auto-threading + the quote).
    assert gmail.users.return_value.threads.return_value.get.call_count == 1
    # Auto-populated reply headers from the thread.
    assert "In-Reply-To: <parent@example.com>" in msg
    assert "References: <parent@example.com>" in " ".join(msg.split())
    # gmail_quote container + exact attribution + verbatim parent html.
    assert "gmail_quote gmail_quote_container" in msg
    assert "On Tue, 7 Apr 2026 at 19:19, Ada Lovelace" in msg
    assert "<div>Original line</div>" in msg
    assert "> Original line" in msg


@pytest.mark.asyncio
async def test_reply_without_parent_sends_without_quote():
    gmail = _gmail_service()
    gmail.users().threads().get().execute.side_effect = RuntimeError("fetch failed")

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
        quote_original=True,
    )

    msg = _decode_bodies(_raw_sent(gmail))
    assert "Email sent" in result
    assert "gmail_quote_container" not in msg
    # Still multipart with both parts.
    assert 'text/plain; charset="UTF-8"' in msg
    assert 'text/html; charset="UTF-8"' in msg


@pytest.mark.asyncio
async def test_send_rejects_crlf_header_injection_in_subject():
    """A CR/LF-laden subject must be rejected, not folded into extra headers."""
    gmail = _gmail_service()

    with pytest.raises(ValueError):
        await _unwrap(send_gmail_message)(
            service=gmail,
            user_google_email="grace@example.org",
            to="ada@example.com",
            subject="Meeting\r\nBcc: sneaky@example.com",
            body="Hello there",
            include_signature=False,
        )


@pytest.mark.asyncio
async def test_send_html_body_plain_part_keeps_paragraph_breaks():
    """<p>First.</p><p>Second.</p> must not flatten to 'First.Second.'."""
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Paragraphs",
        body="<p>First.</p><p>Second.</p>",
        body_format="html",
        include_signature=False,
    )

    plain = _part(_raw_sent(gmail), "text/plain")
    assert "First.Second." not in plain
    assert "First.\nSecond." in plain.replace("\r\n", "\n")
