"""Integration tests for the Gmail-web faithful send/draft path.

Mocks all Google API calls (People + Gmail parent fetch); never hits the
network. Synthetic data only (example.com / example.org, generic names).
"""

import base64
import quopri
import re
from unittest.mock import Mock
import pytest
from gmail.gmail_tools import send_gmail_message


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _raw_sent(mock_service) -> str:
    kwargs = mock_service.users.return_value.messages.return_value.send.call_args.kwargs
    raw = kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw.encode()).decode("utf-8")


def _decode_bodies(raw: str) -> str:
    headers, _, body = raw.partition("\r\n\r\n")
    decoded = quopri.decodestring(body.encode("utf-8")).decode("utf-8")
    return f"{headers}\r\n\r\n{decoded}"


def _html_part(raw: str) -> str:
    """Return the decoded text/html part, regardless of CTE (QP or base64).

    Heavily non-ASCII bodies (Hebrew, Arabic) are base64-encoded by choose_cte,
    which the QP-only ``_decode_bodies`` cannot decode; let the stdlib parser
    handle either encoding.
    """
    from email import message_from_string

    parsed = message_from_string(raw)
    return "".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in parsed.walk()
        if part.get_content_type() == "text/html"
    )


def _gmail_service():
    service = Mock()
    service.users().messages().send().execute.return_value = {"id": "sent123"}
    # No signature.
    service.users().settings().sendAs().list().execute.return_value = {"sendAs": []}
    return service


@pytest.mark.asyncio
async def test_send_hebrew_body_renders_rtl():
    """A Hebrew body auto-detects as RTL so Gmail right-aligns it."""
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="שלום עולם\n\nזה גוף ההודעה",
        include_signature=False,
    )

    html = _html_part(_raw_sent(gmail))
    assert '<div dir="rtl">' in html
    assert '<div dir="ltr">' not in html


@pytest.mark.asyncio
async def test_send_english_body_stays_ltr():
    """An English body stays LTR (byte-identical to historical output)."""
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="Hello there",
        include_signature=False,
    )

    html = _html_part(_raw_sent(gmail))
    assert '<div dir="ltr">' in html
    assert '<div dir="rtl">' not in html


@pytest.mark.asyncio
async def test_send_direction_override_forces_rtl():
    """Explicit direction='rtl' wins even when the first strong char is LTR."""
    gmail = _gmail_service()

    await _unwrap(send_gmail_message)(
        service=gmail,
        user_google_email="grace@example.org",
        to="ada@example.com",
        subject="Project sync",
        body="OK שלום עולם",
        direction="rtl",
        include_signature=False,
    )

    html = _html_part(_raw_sent(gmail))
    assert '<div dir="rtl">' in html


@pytest.mark.asyncio
async def test_send_new_message_html_has_no_ai_or_tool_fingerprints():
    """Goal 2: the authored HTML must look hand-typed in Gmail web, not pasted
    from a tool. No class/style/p/data attributes in the new-body block, and
    none of the known AI/tool fingerprint tokens; no vendor mailer headers."""
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
        "claude",
        "whitespace-normal",
        "break-words",
        "leading-[",
        "list-disc",
        "pl-",
    ):
        assert token not in msg.lower() if token == "claude" else token not in msg

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


def _plain_part(raw: str) -> str:
    from email import message_from_string

    parsed = message_from_string(raw)
    return "".join(
        part.get_payload(decode=True).decode("utf-8")
        for part in parsed.walk()
        if part.get_content_type() == "text/plain"
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

    plain = _plain_part(_raw_sent(gmail))
    assert "First.Second." not in plain
    assert "First.\nSecond." in plain.replace("\r\n", "\n")


def test_prepare_web_html_body_plain_part_keeps_paragraph_breaks():
    from gmail.gmail_tools import _prepare_gmail_message

    raw_b64, *_ = _prepare_gmail_message(
        subject="Paragraphs",
        body="<p>First.</p><p>Second.</p>",
        to="ada@example.com",
        body_format="html",
        from_email="grace@example.org",
        web_compose=True,
    )
    plain = _plain_part(base64.urlsafe_b64decode(raw_b64).decode("utf-8"))
    assert "First.Second." not in plain
    assert "First.\nSecond." in plain.replace("\r\n", "\n")
