"""End-to-end Gmail-web fidelity of the MIME a forward puts on the wire."""

import base64
import json
import pathlib
import re
from email import message_from_bytes

import pytest

from gmail.gmail_helpers import _prepare_gmail_message_web
from gmail.gmail_tools import _forward_gmail_message_impl
from tests.gmail.golden_skeleton import extract_skeleton
from tests.gmail.test_forward_gmail_message import (
    create_mock_message,
    create_mock_service,
    get_body_text,
)

GOLDEN_FORWARD = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "golden_forward.json").read_text(
        encoding="utf-8"
    )
)


async def _forward_raw(message, **kwargs) -> bytes:
    """Forward ``message`` and return the raw bytes handed to messages().send()."""
    service = create_mock_service(message)
    await _forward_gmail_message_impl(
        service=service,
        message_id="msg1",
        to="recipient@example.com",
        user_google_email="me@example.com",
        **kwargs,
    )
    raw = service.users().messages().send.call_args.kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html_body", ["<div>Lorem ipsum dolor sit amet.</div>", None], ids=["html", "plain"]
)
async def test_forward_matches_golden_skeleton(html_body):
    """Both an HTML and a plain-only original forward as Gmail web does."""
    skeleton = extract_skeleton(
        await _forward_raw(
            create_mock_message(
                from_addr="Alice Sender <alice@example.com>",
                text_body="Lorem ipsum dolor sit amet.\nSecond line.",
                html_body=html_body,
            )
        )
    )

    top = skeleton["mime_tree"][0]
    golden_shape = GOLDEN_FORWARD["mime_shape"]
    assert top["content_type"] == golden_shape["content_type"]
    assert [p["content_type"] for p in top["parts"]] == [
        p["content_type"] for p in golden_shape["parts"]
    ]
    assert skeleton["html_probes"] == GOLDEN_FORWARD["html_probes"]
    plain = skeleton["plain_structure"]
    assert "FWD_SEP: ---------- Forwarded message ---------" in plain
    assert any(line.startswith("FWD_HDR: From:") for line in plain)
    assert not any(line.startswith("QUOTE") for line in plain)


@pytest.mark.asyncio
async def test_forward_crlf_plain_original_and_note_emit_no_bare_cr():
    """Gmail returns CRLF plain bodies; splitting them on LF for the HTML part
    must not leave a lone CR on the wire (RFC 5322 forbids it)."""
    raw = await _forward_raw(
        create_mock_message(text_body="Line one.\r\nLine two."),
        forward_message="Note one.\r\nNote two.",
    )

    assert re.search(rb"\r(?!\n)", raw) is None
    html = get_body_text(message_from_bytes(raw), subtype="html")
    assert "Line one." in html and "Line two." in html


@pytest.mark.asyncio
async def test_forward_html_note_leads_both_parts():
    """An HTML note precedes the forwarded block and keeps its paragraph breaks
    in the text/plain part."""
    sent = message_from_bytes(
        await _forward_raw(
            create_mock_message(
                text_body="Original plain.", html_body="<div>Original html.</div>"
            ),
            forward_message="<p>First.</p><p>Second.</p>",
            forward_message_format="html",
        )
    )

    html = get_body_text(sent, subtype="html")
    assert html.index("<p>First.</p><p>Second.</p>") < html.index(
        'class="gmail_quote gmail_quote_container"'
    )
    plain = get_body_text(sent, subtype="plain").replace("\r\n", "\n")
    assert plain.startswith("First.\nSecond.\n\n---------- Forwarded message")


@pytest.mark.asyncio
async def test_forward_with_attachment_mime_shape():
    """With-attachment forward → multipart/mixed → [alternative] + attachment part."""
    golden = json.loads(
        (
            pathlib.Path(__file__).parent / "fixtures" / "golden_forward_attach.json"
        ).read_text(encoding="utf-8")
    )
    att_raw = b"%PDF-1.4 fake content"
    att_b64 = base64.urlsafe_b64encode(att_raw).decode()

    msg = create_mock_message(
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
    svc = create_mock_service(msg, attachments_data=[{"data": att_b64}])

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg2",
        to="recipient@example.com",
        include_attachments=True,
        user_google_email="me@example.com",
    )

    sk = extract_skeleton(
        base64.urlsafe_b64decode(
            svc.users().messages().send.call_args.kwargs["body"]["raw"]
        )
    )
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


@pytest.mark.asyncio
async def test_forward_refuses_to_send_when_builder_drops_an_attachment(monkeypatch):
    """A downloaded attachment the MIME builder could not include must fail the
    forward instead of sending an incomplete message reported as complete."""
    import gmail.gmail_tools as gt
    from core.utils import UserInputError

    att_b64 = base64.urlsafe_b64encode(b"%PDF-1.4 fake content").decode()
    msg = create_mock_message(
        text_body="See attached.",
        attachments=[
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "attachmentId": "att1",
            }
        ],
    )
    svc = create_mock_service(msg, attachments_data=[{"data": att_b64}])
    real_split = gt._split_resolved_attachments

    def dropping_split(resolved):
        inline, _regular, _count, _errors = real_split(resolved)
        return inline, [], 0, ["report.pdf: boom"]

    monkeypatch.setattr(gt, "_split_resolved_attachments", dropping_split)

    with pytest.raises(UserInputError, match="0/1 attached.*report.pdf: boom"):
        await _forward_gmail_message_impl(
            service=svc,
            message_id="msg_drop",
            to="recipient@example.com",
            include_attachments=True,
            user_google_email="me@example.com",
        )
    send_calls = svc.users.return_value.messages.return_value.send.call_args_list
    assert not [c for c in send_calls if "body" in c.kwargs]


@pytest.mark.asyncio
async def test_forward_html_probes_with_attachment():
    """HTML probes must match golden_forward_attach fixture when attachments present."""
    golden = json.loads(
        (
            pathlib.Path(__file__).parent / "fixtures" / "golden_forward_attach.json"
        ).read_text(encoding="utf-8")
    )
    att_raw = b"binary content"
    att_b64 = base64.urlsafe_b64encode(att_raw).decode()

    msg = create_mock_message(
        from_addr="Jane Roe <jane@example.com>",
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
    svc = create_mock_service(msg, attachments_data=[{"data": att_b64}])

    await _forward_gmail_message_impl(
        service=svc,
        message_id="msg4",
        to="recipient@example.com",
        include_attachments=True,
        user_google_email="me@example.com",
    )

    sk = extract_skeleton(
        base64.urlsafe_b64decode(
            svc.users().messages().send.call_args.kwargs["body"]["raw"]
        )
    )
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


def _web_skeleton(**kwargs):
    raw_b64 = _prepare_gmail_message_web(
        subject="Fwd: doc",
        plain_body="plain",
        html_body="<div>html</div>",
        to="r@example.com",
        from_email="s@example.com",
        **kwargs,
    )
    return extract_skeleton(base64.urlsafe_b64decode(raw_b64))["mime_tree"][0]


def test_prepare_web_no_attachments_produces_alternative():
    """No parts, or empty part lists, keep the plain multipart/alternative."""
    for kwargs in ({}, {"inline_parts": [], "attachment_parts": []}):
        top = _web_skeleton(**kwargs)
        assert top["content_type"] == "multipart/alternative"
        assert [p["content_type"] for p in top["parts"]] == ["text/plain", "text/html"]


def test_prepare_web_with_attachments_produces_mixed():
    """A regular attachment wraps the alternative in multipart/mixed."""
    top = _web_skeleton(
        attachment_parts=[
            {"filename": "a.pdf", "mime_type": "application/pdf", "data": b"%PDF-1.4"}
        ]
    )
    assert top["content_type"] == "multipart/mixed"
    assert top["parts"][0]["content_type"] == "multipart/alternative"
    assert top["parts"][1]["content_type"] == "application/pdf"
    assert top["parts"][1]["disposition"] == "attachment"
