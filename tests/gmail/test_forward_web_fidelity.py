"""Tests asserting that _forward_gmail_message_impl produces Gmail-web faithful
output: correct MIME shape, HTML probes, and plain scaffold - both with and
without attachments.
"""

import base64
import json
import pathlib
import sys
import os
import pytest
from unittest.mock import Mock


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


from gmail.gmail_tools import _forward_gmail_message_impl
from tools.golden_skeleton import extract_skeleton


FIX = pathlib.Path(__file__).parent / "fixtures"


def _decode_sent_raw(mock_service) -> bytes:
    """Return the raw bytes of the message passed to messages().send()."""
    raw = mock_service.users().messages().send.call_args.kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw)


def _skeleton(raw_bytes: bytes) -> dict:
    return extract_skeleton(raw_bytes)


def _create_mock_message(
    subject="Original Subject",
    from_addr="Alice Sender <alice@example.com>",
    to_addr="bob@example.com",
    date="Mon, 1 Jan 2024 10:00:00 -0000",
    text_body=None,
    html_body=None,
    attachments=None,
):
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_addr},
        {"name": "To", "value": to_addr},
        {"name": "Date", "value": date},
    ]
    parts = []
    if text_body:
        enc = base64.urlsafe_b64encode(text_body.encode()).decode()
        parts.append({"mimeType": "text/plain", "body": {"data": enc}})
    if html_body:
        enc = base64.urlsafe_b64encode(html_body.encode()).decode()
        parts.append({"mimeType": "text/html", "body": {"data": enc}})
    if attachments:
        for att in attachments:
            parts.append(
                {
                    "filename": att["filename"],
                    "mimeType": att["mimeType"],
                    "body": {
                        "attachmentId": att["attachmentId"],
                        "size": att.get("size", 100),
                    },
                }
            )
    if parts:
        payload = {"mimeType": "multipart/mixed", "headers": headers, "parts": parts}
    else:
        enc = base64.urlsafe_b64encode(b"").decode()
        payload = {"mimeType": "text/plain", "headers": headers, "body": {"data": enc}}
    return {"payload": payload}


def _create_mock_service(message, attachments_data=None, sent_message_id="sent_fwd"):
    mock = Mock()
    mock.users().messages().get().execute.return_value = message
    if attachments_data:
        mock.users().messages().attachments().get().execute.side_effect = (
            attachments_data
        )
    else:
        mock.users().messages().attachments().get().execute.return_value = {"data": ""}
    mock.users().messages().send().execute.return_value = {"id": sent_message_id}
    return mock


@pytest.mark.asyncio
async def test_forward_no_attachment_mime_shape():
    """No-attachment forward → top-level multipart/alternative (golden_forward)."""
    golden = json.loads((FIX / "golden_forward.json").read_text(encoding="utf-8"))
    msg = _create_mock_message(
        text_body="Lorem ipsum dolor sit amet.",
        html_body="<div>Lorem ipsum dolor sit amet.</div>",
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg1",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sk = _skeleton(_decode_sent_raw(svc))
    top = sk["mime_tree"][0]
    assert top["content_type"] == golden["mime_shape"]["content_type"]
    assert top["content_type"] == "multipart/alternative"
    children = top["parts"]
    assert children[0]["content_type"] == "text/plain"
    assert children[1]["content_type"] == "text/html"


@pytest.mark.asyncio
async def test_forward_html_probes_no_attachment():
    """HTML part must pass all golden forward html probes (no attachment)."""
    golden = json.loads((FIX / "golden_forward.json").read_text(encoding="utf-8"))
    msg = _create_mock_message(
        text_body="Some original content.",
        html_body="<div>Some original content.</div>",
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg3",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sk = _skeleton(_decode_sent_raw(svc))
    probes = sk["html_probes"]
    assert (
        probes["has_gmail_quote_container"]
        == golden["html_probes"]["has_gmail_quote_container"]
    )
    assert (
        probes["has_gmail_sendername"] == golden["html_probes"]["has_gmail_sendername"]
    )
    assert (
        probes["has_blockquote_gmail_quote"]
        == golden["html_probes"]["has_blockquote_gmail_quote"]
    )
    assert (
        probes["has_forwarded_literal"]
        == golden["html_probes"]["has_forwarded_literal"]
    )

    # Explicit polarity assertions (no blockquote, has container+sendername+fwd).
    assert probes["has_gmail_quote_container"] is True
    assert probes["has_gmail_sendername"] is True
    assert probes["has_blockquote_gmail_quote"] is False
    assert probes["has_forwarded_literal"] is True


@pytest.mark.asyncio
async def test_forward_plain_not_quoted():
    """Original plain body must appear verbatim (not > -quoted) in the plain part."""
    msg = _create_mock_message(
        text_body="First line.\nSecond line.",
        html_body="<div>First line.</div>",
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg5",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sk = _skeleton(_decode_sent_raw(svc))
    # plain_structure must contain no QUOTE lines
    for label in sk["plain_structure"]:
        assert not label.startswith("QUOTE"), f"Unexpected quote line: {label!r}"

    # Forwarded block must appear
    assert any(line.startswith("FWD_SEP:") for line in sk["plain_structure"])
    assert any(line.startswith("FWD_HDR: From:") for line in sk["plain_structure"])


@pytest.mark.asyncio
async def test_forward_subject_prefixed():
    """Subject gets 'Fwd: ' prefix when not already prefixed."""
    msg = _create_mock_message(subject="Meeting Notes")
    svc = _create_mock_service(msg)
    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg6",
        to="r@example.com",
        user_google_email="me@example.com",
    )
    from email import message_from_bytes

    sent = message_from_bytes(_decode_sent_raw(svc))
    assert sent["Subject"] == "Fwd: Meeting Notes"


@pytest.mark.asyncio
async def test_forward_subject_override_respected():
    """Explicit subject parameter overrides auto-derived 'Fwd: ...' subject."""
    msg = _create_mock_message(subject="Original")
    svc = _create_mock_service(msg)
    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg7",
        to="r@example.com",
        subject="My Custom Subject",
        user_google_email="me@example.com",
    )
    from email import message_from_bytes

    sent = message_from_bytes(_decode_sent_raw(svc))
    assert sent["Subject"] == "My Custom Subject"


@pytest.mark.asyncio
async def test_forward_subject_no_double_prefix():
    """Subject already starting with 'Fwd:' must not be double-prefixed."""
    msg = _create_mock_message(subject="Fwd: Already forwarded")
    svc = _create_mock_service(msg)
    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg8",
        to="r@example.com",
        user_google_email="me@example.com",
    )
    from email import message_from_bytes

    sent = message_from_bytes(_decode_sent_raw(svc))
    assert sent["Subject"] == "Fwd: Already forwarded"


@pytest.mark.asyncio
async def test_forward_plain_only_original_has_valid_html_part():
    """Original with no HTML body still produces a valid HTML part in the forward."""
    msg = _create_mock_message(
        text_body="Just plain text here.\nSecond line.",
        html_body=None,
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg9",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    sk = _skeleton(_decode_sent_raw(svc))
    top = sk["mime_tree"][0]
    # Should still be multipart/alternative with both parts
    assert top["content_type"] == "multipart/alternative"
    assert any(p["content_type"] == "text/html" for p in top["parts"])
    # HTML probes must still pass
    probes = sk["html_probes"]
    assert probes["has_gmail_quote_container"] is True
    assert probes["has_forwarded_literal"] is True
    assert probes["has_blockquote_gmail_quote"] is False


@pytest.mark.asyncio
async def test_forward_attachment_download_failure_raises():
    """A failed attachment download must abort rather than send a partial forward."""
    msg = _create_mock_message(
        text_body="See attached.",
        attachments=[
            {
                "filename": "doc.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    svc = _create_mock_service(
        msg, attachments_data=[Exception("download failed")], sent_message_id="never"
    )

    with pytest.raises(Exception, match="Failed to include requested attachment"):
        await _forward_gmail_message_impl(
            service=svc,
            message_id="msg10",
            to="r@example.com",
            include_attachments=True,
            user_google_email="me@example.com",
        )


@pytest.mark.asyncio
async def test_forward_html_note_newlines_converted_but_original_untouched():
    """Bare newlines in an HTML note become <br>; the forwarded original keeps its
    markup byte-for-byte."""
    from email import message_from_bytes

    original_html = "<div>\n<span>Hello</span>\n<span>world</span>\n</div>"
    msg = _create_mock_message(text_body="Hello world", html_body=original_html)
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg_note_newlines",
        to="recipient@example.com",
        forward_message="FYI\n\nsee below",
        forward_message_format="html",
        user_google_email="me@example.com",
    )

    parsed = message_from_bytes(_decode_sent_raw(svc))
    html_part = next(p for p in parsed.walk() if p.get_content_type() == "text/html")
    # SMTP policy emits CRLF; compare against the LF the caller passed.
    html_payload = (
        html_part.get_payload(decode=True).decode("utf-8").replace("\r\n", "\n")
    )
    assert "FYI<br><br>\nsee below" in html_payload
    assert original_html in html_payload


@pytest.mark.asyncio
async def test_forward_html_note_placement():
    """HTML-format note appears before the gmail_quote_container div in HTML and
    before the forwarded separator in plain."""
    from email import message_from_bytes

    msg = _create_mock_message(
        text_body="Lorem ipsum original plain.",
        html_body="<div>Lorem ipsum original html.</div>",
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg_note_html",
        to="recipient@example.com",
        forward_message="<b>see below</b>",
        forward_message_format="html",
        user_google_email="me@example.com",
    )

    raw = _decode_sent_raw(svc)

    # HTML part: note before gmail_quote_container
    parsed = message_from_bytes(raw)
    # Walk to find HTML part
    html_payload = None
    for part in parsed.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            html_payload = payload.decode("utf-8", errors="replace") if payload else ""
            break
    assert html_payload is not None, "No text/html part found"
    note_pos = html_payload.find("<b>see below</b>")
    container_pos = html_payload.find('class="gmail_quote gmail_quote_container"')
    assert note_pos != -1, "Note HTML not found in HTML part"
    assert container_pos != -1, "gmail_quote_container not found in HTML part"
    assert note_pos < container_pos, "Note must appear before gmail_quote_container"

    # Plain part: note text before forwarded separator
    plain_payload = None
    for part in parsed.walk():
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            plain_payload = payload.decode("utf-8", errors="replace") if payload else ""
            break
    assert plain_payload is not None, "No text/plain part found"
    note_text_pos = plain_payload.find("see below")
    sep_pos = plain_payload.find("---------- Forwarded message")
    assert note_text_pos != -1, "Extracted note text not found in plain part"
    assert sep_pos != -1, "Forwarded separator not found in plain part"
    assert note_text_pos < sep_pos, "Note text must appear before forwarded separator"


@pytest.mark.asyncio
async def test_forward_no_note_plain_only_original():
    """No note + plain-only original → valid multipart/alternative with both parts;
    HTML contains gmail_quote_container with no leading note div."""
    from email import message_from_bytes

    msg = _create_mock_message(
        text_body="Just plain body. No html original.",
        html_body=None,
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg_no_note_plain_only",
        to="recipient@example.com",
        user_google_email="me@example.com",
    )

    raw = _decode_sent_raw(svc)
    sk = _skeleton(raw)

    # MIME shape: multipart/alternative
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/alternative"
    assert any(p["content_type"] == "text/plain" for p in top["parts"])
    assert any(p["content_type"] == "text/html" for p in top["parts"])

    # HTML probes: gmail_quote_container present, no blockquote
    probes = sk["html_probes"]
    assert probes["has_gmail_quote_container"] is True
    assert probes["has_blockquote_gmail_quote"] is False
    assert probes["has_forwarded_literal"] is True

    # HTML part: gmail_quote_container present and no note div immediately before it
    parsed = message_from_bytes(raw)
    html_payload = None
    for part in parsed.walk():
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True)
            html_payload = payload.decode("utf-8", errors="replace") if payload else ""
            break
    assert html_payload is not None
    assert 'class="gmail_quote gmail_quote_container"' in html_payload
    # No-note path: the outer wrapper must start with <br> (no note div injected before
    # the forwarded container).  Structure: '<div dir="ltr"><br><div ...'
    assert html_payload.startswith('<div dir="ltr"><br>'), (
        f"Expected no-note HTML to start with outer wrapper + bare <br>, got: {html_payload[:80]!r}"
    )


@pytest.mark.asyncio
async def test_forward_html_note_plain_keeps_paragraph_breaks():
    """An HTML note's text/plain rendering keeps block boundaries."""
    from email import message_from_bytes

    msg = _create_mock_message(
        text_body="Lorem ipsum original plain.",
        html_body="<div>Lorem ipsum original html.</div>",
    )
    svc = _create_mock_service(msg)

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg_note_paras",
        to="recipient@example.com",
        forward_message="<p>First.</p><p>Second.</p>",
        forward_message_format="html",
        user_google_email="me@example.com",
    )

    parsed = message_from_bytes(_decode_sent_raw(svc))
    plain = next(
        p.get_payload(decode=True).decode("utf-8")
        for p in parsed.walk()
        if p.get_content_type() == "text/plain"
    ).replace("\r\n", "\n")
    assert "First.Second." not in plain
    assert "First.\nSecond." in plain
