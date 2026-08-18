"""Tests asserting that _forward_gmail_message_impl produces Gmail-web faithful
output: correct MIME shape, HTML probes, and plain scaffold — both with and
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

from gmail.gmail_tools import _forward_gmail_message_impl, _prepare_gmail_message_web
from tools.golden_skeleton import extract_skeleton

FIX = pathlib.Path(__file__).parent / "fixtures"

# ---------------------------------------------------------------------------
# Helpers (mirror test_forward_gmail_message.py utilities)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# MIME shape tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_no_attachment_mime_shape():
    """No-attachment forward → top-level multipart/alternative (golden_forward)."""
    golden = json.loads((FIX / "golden_forward.json").read_text())
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
async def test_forward_with_attachment_mime_shape():
    """With-attachment forward → multipart/mixed → [alternative] + attachment part."""
    golden = json.loads((FIX / "golden_forward_attach.json").read_text())
    att_raw = b"%PDF-1.4 fake content"
    att_b64 = base64.urlsafe_b64encode(att_raw).decode()

    msg = _create_mock_message(
        text_body="See attached document.",
        html_body="<div>See attached document.</div>",
        attachments=[
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    svc = _create_mock_service(msg, attachments_data=[{"data": att_b64}])

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg2",
        to="recipient@example.com",
        include_attachments=True,
        user_google_email="me@example.com",
    )

    sk = _skeleton(_decode_sent_raw(svc))
    top = sk["mime_tree"][0]
    assert top["content_type"] == golden["mime_shape"]["content_type"]
    assert top["content_type"] == "multipart/mixed"

    alt = top["parts"][0]
    assert alt["content_type"] == golden["mime_shape"]["parts"][0]["content_type"]
    assert alt["content_type"] == "multipart/alternative"
    assert alt["parts"][0]["content_type"] == "text/plain"
    assert alt["parts"][1]["content_type"] == "text/html"

    attach = top["parts"][1]
    assert attach["content_type"] == golden["mime_shape"]["parts"][1]["content_type"]
    assert attach["disposition"] == golden["mime_shape"]["parts"][1]["disposition"]
    assert attach["disposition"] == "attachment"


# ---------------------------------------------------------------------------
# HTML probe tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_html_probes_no_attachment():
    """HTML part must pass all golden forward html probes (no attachment)."""
    golden = json.loads((FIX / "golden_forward.json").read_text())
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
async def test_forward_html_probes_with_attachment():
    """HTML probes must match golden_forward_attach fixture when attachments present."""
    golden = json.loads((FIX / "golden_forward_attach.json").read_text())
    att_raw = b"binary content"
    att_b64 = base64.urlsafe_b64encode(att_raw).decode()

    msg = _create_mock_message(
        text_body="Body text.",
        html_body="<div>Body HTML.</div>",
        attachments=[
            {
                "filename": "file.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    svc = _create_mock_service(msg, attachments_data=[{"data": att_b64}])

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg4",
        to="recipient@example.com",
        include_attachments=True,
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


# ---------------------------------------------------------------------------
# Plain scaffold: no "> " quoting
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Subject derivation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# No-HTML original: synthesized HTML part
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Attachment download failure raises
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _prepare_gmail_message_web: no-attachment param is backward-compatible
# ---------------------------------------------------------------------------


def test_prepare_web_no_attachments_param_absent_vs_none_same_structure():
    """Calling _prepare_gmail_message_web without attachments= and with
    attachments=None must both produce multipart/alternative (same structure)."""
    result_default, *_ = _prepare_gmail_message_web(
        subject="Test",
        plain_body="plain",
        html_body="<div>html</div>",
        to="r@example.com",
        from_email="s@example.com",
    )
    result_none, *_ = _prepare_gmail_message_web(
        subject="Test",
        plain_body="plain",
        html_body="<div>html</div>",
        to="r@example.com",
        from_email="s@example.com",
        attachments=None,
    )
    sk_default = _skeleton(base64.urlsafe_b64decode(result_default))
    sk_none = _skeleton(base64.urlsafe_b64decode(result_none))
    assert sk_default["mime_tree"][0]["content_type"] == "multipart/alternative"
    assert sk_none["mime_tree"][0]["content_type"] == "multipart/alternative"
    # Both should have the same structural child types
    assert [p["content_type"] for p in sk_default["mime_tree"][0]["parts"]] == [
        p["content_type"] for p in sk_none["mime_tree"][0]["parts"]
    ]


def test_prepare_web_no_attachments_produces_alternative():
    """_prepare_gmail_message_web with no attachments → multipart/alternative."""
    raw_b64, *_ = _prepare_gmail_message_web(
        subject="Subj",
        plain_body="plain",
        html_body="<div>html</div>",
        to="r@example.com",
        from_email="s@example.com",
    )
    sk = _skeleton(base64.urlsafe_b64decode(raw_b64))
    assert sk["mime_tree"][0]["content_type"] == "multipart/alternative"


def test_prepare_web_with_attachments_produces_mixed():
    """_prepare_gmail_message_web with attachments → multipart/mixed."""
    raw_b64, count, errors = _prepare_gmail_message_web(
        subject="Fwd: doc",
        plain_body="plain",
        html_body="<div>html</div>",
        to="r@example.com",
        from_email="s@example.com",
        attachments=[
            {"filename": "a.pdf", "mime_type": "application/pdf", "data": b"%PDF-1.4"}
        ],
    )
    assert count == 1
    assert errors == []
    sk = _skeleton(base64.urlsafe_b64decode(raw_b64))
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/mixed"
    assert top["parts"][0]["content_type"] == "multipart/alternative"
    assert top["parts"][1]["content_type"] == "application/pdf"
    assert top["parts"][1]["disposition"] == "attachment"


# ---------------------------------------------------------------------------
# Note placement tests (HTML-format note and no-note/no-HTML-original)
# ---------------------------------------------------------------------------


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
