"""End-to-end Gmail-web fidelity of the MIME a forward puts on the wire."""

import base64
import json
import pathlib
import re
from email import message_from_bytes

import pytest

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
