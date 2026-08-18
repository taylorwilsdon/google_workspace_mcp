"""Tests asserting that send/draft reply with attachments produces Gmail-web
faithful output: correct MIME shape, gmail_quote reply trail in HTML, and
parity between sent and drafted messages.

All fixtures use synthetic @example.com addresses and fake payloads only.
"""

import base64
import json
import pathlib
import sys
import os

import pytest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import (
    send_gmail_message,
    draft_gmail_message,
    _split_resolved_attachments,
)
from core.utils import UserInputError
from tools.golden_skeleton import extract_skeleton

FIX = pathlib.Path(__file__).parent / "fixtures"

# Minimal 1x1 PNG bytes — fake inline image payload.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
    "YGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="
)
_TINY_PNG_B64 = base64.b64encode(_TINY_PNG).decode()

# Fake PDF bytes.
_FAKE_PDF = b"%PDF-1.4 fake content"
_FAKE_PDF_B64 = base64.b64encode(_FAKE_PDF).decode()


# ---------------------------------------------------------------------------
# Service mocks
# ---------------------------------------------------------------------------


def _make_thread_context(
    message_id="<parent@example.com>",
    from_addr="Alice <alice@example.com>",
    date="Mon, 1 Jan 2024 10:00:00 +0000",
    text_body="Original message body.",
    html_body="<div>Original message body.</div>",
):
    """Return a minimal thread context dict as produced by _fetch_thread_reply_context."""
    return {
        "message_ids": [message_id],
        "messages": [
            {
                "headers": {
                    "From": from_addr,
                    "Date": date,
                    "Message-ID": message_id,
                },
            }
        ],
        "target": {
            "from": from_addr,
            "date": date,
            "text_body": text_body,
            "html_body": html_body,
            "reply_to": None,
            "subject": "Original Subject",
        },
    }


def _gmail_service(sent_id="sent123", draft_id="draft123"):
    svc = Mock()
    svc.users().messages().send().execute.return_value = {"id": sent_id}
    svc.users().drafts().create().execute.return_value = {"id": draft_id}
    svc.users().settings().sendAs().list().execute.return_value = {"sendAs": []}
    return svc


def _people_service():
    """Empty people service — no contact resolution."""
    svc = Mock()
    svc.people().searchContacts().execute.return_value = {"results": []}
    return svc


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _raw_sent(svc) -> bytes:
    raw = svc.users().messages().send.call_args.kwargs["body"]["raw"]
    return base64.urlsafe_b64decode(raw)


def _raw_drafted(svc) -> bytes:
    raw = svc.users.return_value.drafts.return_value.create.call_args.kwargs["body"][
        "message"
    ]["raw"]
    return base64.urlsafe_b64decode(raw)


def _skeleton(raw: bytes) -> dict:
    return extract_skeleton(raw)


def _html_of(raw: bytes) -> str:
    """Return the decoded HTML part text from a raw MIME message."""
    from email import message_from_bytes
    from email.policy import SMTP as _SMTP

    msg = message_from_bytes(raw, policy=_SMTP)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_content()
    return ""


# ---------------------------------------------------------------------------
# 1. Send reply WITH a regular attachment → mixed(alternative, attachment) + reply trail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_with_attachment_mime_shape_and_reply_trail():
    """Sent reply with a regular attachment must produce:
    - top-level multipart/mixed
    - first child: multipart/alternative
    - second child: application/pdf attachment
    - HTML body contains the gmail_quote_container reply trail
    """
    golden = json.loads((FIX / "golden_reply_attach.json").read_text())
    ctx = _make_thread_context()
    gmail = _gmail_service()
    people = _people_service()

    with patch(
        "gmail.gmail_tools._fetch_thread_reply_context",
        new=AsyncMock(return_value=ctx),
    ):
        result = await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=people,
            user_google_email="bob@example.com",
            to="alice@example.com",
            subject="Re: Original Subject",
            body="Thanks for the note.",
            thread_id="thread1",
            in_reply_to="<parent@example.com>",
            attachments=[
                {
                    "filename": "report.pdf",
                    "content": _FAKE_PDF_B64,
                    "mime_type": "application/pdf",
                }
            ],
            include_signature=False,
        )

    assert "Email sent" in result
    raw = _raw_sent(gmail)
    sk = _skeleton(raw)

    # MIME shape must match golden
    top = sk["mime_tree"][0]
    assert top["content_type"] == golden["mime_shape"]["content_type"]
    assert top["content_type"] == "multipart/mixed"
    alt = top["parts"][0]
    assert alt["content_type"] == golden["mime_shape"]["parts"][0]["content_type"]
    assert alt["content_type"] == "multipart/alternative"
    attach = top["parts"][1]
    assert attach["content_type"] == "application/pdf"
    assert attach["disposition"] == "attachment"

    # HTML must carry the gmail_quote reply trail (this was the KEY missing piece)
    html = _html_of(raw)
    assert 'class="gmail_quote gmail_quote_container"' in html, (
        "HTML body must contain gmail_quote_container reply trail"
    )
    assert 'class="gmail_quote"' in html
    assert 'class="gmail_attr"' in html


# ---------------------------------------------------------------------------
# 2. Draft reply WITH a regular attachment → same structure as sent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_reply_with_attachment_matches_send_structure():
    """Draft reply with a regular attachment must have the same MIME skeleton
    and reply trail as the equivalent sent reply."""
    ctx = _make_thread_context()
    gmail_svc = _gmail_service()
    people = _people_service()

    kwargs = dict(
        people_service=people,
        user_google_email="bob@example.com",
        to="alice@example.com",
        subject="Re: Original Subject",
        body="Thanks for the note.",
        thread_id="thread1",
        in_reply_to="<parent@example.com>",
        attachments=[
            {
                "filename": "report.pdf",
                "content": _FAKE_PDF_B64,
                "mime_type": "application/pdf",
            }
        ],
        include_signature=False,
    )

    draft_kwargs = dict(**kwargs, quote_original=True)

    with patch(
        "gmail.gmail_tools._fetch_thread_reply_context",
        new=AsyncMock(return_value=ctx),
    ):
        await _unwrap(send_gmail_message)(
            service=gmail_svc,
            **kwargs,
        )

    sent_raw = _raw_sent(gmail_svc)
    sent_sk = _skeleton(sent_raw)

    draft_svc = _gmail_service()
    with patch(
        "gmail.gmail_tools._fetch_thread_reply_context",
        new=AsyncMock(return_value=ctx),
    ):
        await _unwrap(draft_gmail_message)(
            service=draft_svc,
            **draft_kwargs,
        )

    draft_raw = _raw_drafted(draft_svc)
    draft_sk = _skeleton(draft_raw)

    # Top-level structure must match
    assert sent_sk["mime_tree"][0]["content_type"] == "multipart/mixed"
    assert draft_sk["mime_tree"][0]["content_type"] == "multipart/mixed"

    # Both must have the reply trail
    sent_html = _html_of(sent_raw)
    draft_html = _html_of(draft_raw)
    assert 'class="gmail_quote gmail_quote_container"' in sent_html
    assert 'class="gmail_quote gmail_quote_container"' in draft_html


# ---------------------------------------------------------------------------
# 3. Reply with an inline attachment (content_id) → related nesting + Content-ID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_with_inline_attachment_mime_shape():
    """Reply with an inline attachment (content_id set) must produce:
    - top-level multipart/related (no regular attachments present)
    - child: multipart/alternative
    - child: inline image with Content-ID header

    Matches the golden_inline.json shape (inline-only variant: related > [alt, inline]).
    """
    ctx = _make_thread_context()
    gmail = _gmail_service()
    people = _people_service()

    with patch(
        "gmail.gmail_tools._fetch_thread_reply_context",
        new=AsyncMock(return_value=ctx),
    ):
        await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=people,
            user_google_email="bob@example.com",
            to="alice@example.com",
            subject="Re: Original Subject",
            body='See image: <img src="cid:fig-001">',
            body_format="html",
            thread_id="thread1",
            in_reply_to="<parent@example.com>",
            attachments=[
                {
                    "filename": "fig-001.png",
                    "content": _TINY_PNG_B64,
                    "mime_type": "image/png",
                    "content_id": "fig-001",
                }
            ],
            include_signature=False,
        )

    raw = _raw_sent(gmail)
    sk = _skeleton(raw)
    top = sk["mime_tree"][0]

    # Inline-only: multipart/related at top
    assert top["content_type"] == "multipart/related"
    alt = top["parts"][0]
    assert alt["content_type"] == "multipart/alternative"
    img = top["parts"][1]
    assert img["content_type"] == "image/png"
    assert img.get("disposition") == "inline"

    # Verify Content-ID header in raw bytes
    assert b"Content-ID:" in raw


# ---------------------------------------------------------------------------
# 3b. Reply with BOTH inline AND regular attachments → mixed > [related, attach]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_inline_and_regular_attachment():
    """Reply with both inline and regular attachments must produce:
    multipart/mixed → [multipart/related → [alternative, inline], attachment]
    matching the golden_inline.json outer shape.
    """
    ctx = _make_thread_context()
    golden = json.loads((FIX / "golden_inline.json").read_text())
    gmail = _gmail_service()
    people = _people_service()

    with patch(
        "gmail.gmail_tools._fetch_thread_reply_context",
        new=AsyncMock(return_value=ctx),
    ):
        await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=people,
            user_google_email="bob@example.com",
            to="alice@example.com",
            subject="Re: Original Subject",
            body='Image: <img src="cid:fig-001"> and doc attached.',
            body_format="html",
            thread_id="thread1",
            in_reply_to="<parent@example.com>",
            attachments=[
                {
                    "filename": "fig-001.png",
                    "content": _TINY_PNG_B64,
                    "mime_type": "image/png",
                    "content_id": "fig-001",
                },
                {
                    "filename": "doc.pdf",
                    "content": _FAKE_PDF_B64,
                    "mime_type": "application/pdf",
                },
            ],
            include_signature=False,
        )

    raw = _raw_sent(gmail)
    sk = _skeleton(raw)
    top = sk["mime_tree"][0]

    # Outer structure matches golden_inline top-level
    assert top["content_type"] == golden["mime_shape"]["content_type"]
    assert top["content_type"] == "multipart/mixed"

    related = top["parts"][0]
    assert related["content_type"] == golden["mime_shape"]["parts"][0]["content_type"]
    assert related["content_type"] == "multipart/related"

    alt = related["parts"][0]
    assert alt["content_type"] == "multipart/alternative"

    # Regular attachment is second child of mixed
    attach = top["parts"][1]
    assert attach["content_type"] == "application/pdf"
    assert attach["disposition"] == "attachment"


# ---------------------------------------------------------------------------
# 4. "No valid attachments" UserInputError still raised when all error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_reply_raises_when_all_attachments_error():
    """UserInputError must be raised when all resolved attachments have errors."""
    ctx = _make_thread_context()
    gmail = _gmail_service()
    people = _people_service()

    with patch(
        "gmail.gmail_tools._fetch_thread_reply_context",
        new=AsyncMock(return_value=ctx),
    ):
        with pytest.raises(UserInputError, match="No valid attachments were added"):
            await _unwrap(send_gmail_message)(
                service=gmail,
                people_service=people,
                user_google_email="bob@example.com",
                to="alice@example.com",
                subject="Re: Original Subject",
                body="See attached.",
                thread_id="thread1",
                in_reply_to="<parent@example.com>",
                attachments=[
                    {
                        "filename": "broken.pdf",
                        "error": "fetch failed: connection timeout",
                        "error_type": "NetworkError",
                    }
                ],
                include_signature=False,
            )


# ---------------------------------------------------------------------------
# 5. _split_resolved_attachments unit tests
# ---------------------------------------------------------------------------


def test_split_classifies_inline_and_regular():
    """_split_resolved_attachments classifies content_id entries as inline."""
    inline_parts, attach_parts, count, errors = _split_resolved_attachments(
        [
            {
                "_resolved_bytes": _TINY_PNG,
                "filename": "logo.png",
                "mime_type": "image/png",
                "content_id": "logo-cid",
            },
            {
                "_resolved_bytes": _FAKE_PDF,
                "filename": "doc.pdf",
                "mime_type": "application/pdf",
            },
        ]
    )
    assert count == 2
    assert errors == []
    assert len(inline_parts) == 1
    assert len(attach_parts) == 1
    assert inline_parts[0]["content_id"] == "logo-cid"
    assert attach_parts[0]["filename"] == "doc.pdf"


def test_split_skips_error_entries():
    """Error entries must be reported in errors and not counted."""
    inline_parts, attach_parts, count, errors = _split_resolved_attachments(
        [
            {
                "filename": "broken.pdf",
                "error": "download failed",
                "error_type": "NetworkError",
            },
            {
                "_resolved_bytes": _FAKE_PDF,
                "filename": "ok.pdf",
                "mime_type": "application/pdf",
            },
        ]
    )
    assert count == 1
    assert len(errors) == 1
    assert "broken.pdf" in errors[0]
    assert len(attach_parts) == 1


def test_split_duplicate_content_id_warns_and_both_counted(caplog):
    """Duplicate content_id emits a warning but both parts are still counted
    (matching the pre-existing seen_content_ids warning-only behavior)."""
    import logging

    with caplog.at_level(logging.WARNING, logger="gmail.gmail_tools"):
        inline_parts, attach_parts, count, errors = _split_resolved_attachments(
            [
                {
                    "_resolved_bytes": _TINY_PNG,
                    "filename": "img1.png",
                    "mime_type": "image/png",
                    "content_id": "dup-cid",
                },
                {
                    "_resolved_bytes": _TINY_PNG,
                    "filename": "img2.png",
                    "mime_type": "image/png",
                    "content_id": "dup-cid",
                },
            ]
        )
    assert count == 2
    assert any("Duplicate content_id" in r.message for r in caplog.records)


def test_split_handles_data_key():
    """_split_resolved_attachments must accept ``data`` bytes key (forward path)."""
    inline_parts, attach_parts, count, errors = _split_resolved_attachments(
        [
            {
                "data": _FAKE_PDF,
                "filename": "doc.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    assert count == 1
    assert errors == []
    assert attach_parts[0]["data"] == _FAKE_PDF


def test_split_handles_content_base64():
    """_split_resolved_attachments must decode base64 ``content`` key."""
    inline_parts, attach_parts, count, errors = _split_resolved_attachments(
        [
            {
                "content": _FAKE_PDF_B64,
                "filename": "doc.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    assert count == 1
    assert errors == []
    assert attach_parts[0]["data"] == _FAKE_PDF


# ---------------------------------------------------------------------------
# 6. Non-reply send WITH attachment must NOT get a quote trail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_non_reply_with_attachment_no_quote_trail():
    """A brand-new send (no thread_id) with a regular attachment must NOT
    contain a gmail_quote_container reply trail in the HTML body, and the
    top-level MIME structure must be multipart/mixed.
    """
    gmail = _gmail_service()
    people = _people_service()

    result = await _unwrap(send_gmail_message)(
        service=gmail,
        people_service=people,
        user_google_email="bob@example.com",
        to="carol@example.com",
        subject="Hello",
        body="New message with attachment.",
        attachments=[
            {
                "filename": "report.pdf",
                "content": _FAKE_PDF_B64,
                "mime_type": "application/pdf",
            }
        ],
        include_signature=False,
    )

    assert "Email sent" in result
    raw = _raw_sent(gmail)

    # Top-level must be multipart/mixed (has regular attachment)
    sk = _skeleton(raw)
    top = sk["mime_tree"][0]
    assert top["content_type"] == "multipart/mixed"

    # HTML must NOT contain any reply quote trail
    html = _html_of(raw)
    assert "gmail_quote_container" not in html, (
        "Non-reply send must not include a gmail_quote_container in HTML"
    )


# ---------------------------------------------------------------------------
# 7. Draft with all-erroring attachments raises UserInputError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_raises_when_all_attachments_error():
    """UserInputError must be raised when all resolved attachments have errors
    in the draft path (mirrors test_send_reply_raises_when_all_attachments_error).
    """
    gmail = _gmail_service()
    people = _people_service()

    with pytest.raises(UserInputError, match="No valid attachments were added"):
        await _unwrap(draft_gmail_message)(
            service=gmail,
            people_service=people,
            user_google_email="bob@example.com",
            to="alice@example.com",
            subject="Broken draft",
            body="See attached.",
            attachments=[
                {
                    "filename": "broken.pdf",
                    "error": "fetch failed: connection timeout",
                    "error_type": "NetworkError",
                }
            ],
            include_signature=False,
        )


# ---------------------------------------------------------------------------
# 8. Sole content attachment with missing filename → non-empty error Detail
# ---------------------------------------------------------------------------


def test_split_missing_filename_on_content_attachment_records_error():
    """A content-based attachment with no filename must be recorded in
    attachment_errors (not silently dropped), and the error must mention
    'missing filename' so the caller's UserInputError Detail is non-empty.
    """
    inline_parts, attach_parts, count, errors = _split_resolved_attachments(
        [
            {
                "content": _FAKE_PDF_B64,
                # intentionally no "filename" key
                "mime_type": "application/pdf",
            }
        ]
    )
    assert count == 0
    assert len(errors) == 1, "Expected exactly one error entry"
    assert errors[0], "Error string must be non-empty"
    assert "filename" in errors[0].lower(), (
        f"Error must mention 'filename', got: {errors[0]!r}"
    )


@pytest.mark.asyncio
async def test_send_missing_filename_gives_nonempty_details():
    """When the only attachment is a content-based one with no filename,
    UserInputError must be raised AND its message must contain a non-empty
    Details section (proving fix #1: the silent-skip path now records errors).
    """
    gmail = _gmail_service()
    people = _people_service()

    with pytest.raises(UserInputError) as exc_info:
        await _unwrap(send_gmail_message)(
            service=gmail,
            people_service=people,
            user_google_email="bob@example.com",
            to="alice@example.com",
            subject="Missing filename test",
            body="See attached.",
            attachments=[
                {
                    "content": _FAKE_PDF_B64,
                    # intentionally no "filename" key
                    "mime_type": "application/pdf",
                }
            ],
            include_signature=False,
        )

    msg = str(exc_info.value)
    assert "No valid attachments were added" in msg
    assert "Details:" in msg, (
        f"UserInputError must contain 'Details:' with a reason, got: {msg!r}"
    )
    # The Details section must not be empty — something after "Details: "
    details_part = msg.split("Details:", 1)[1].strip()
    assert details_part, f"Details section must not be empty, got: {msg!r}"


# ---------------------------------------------------------------------------
# 9. file_path not found → error recorded in attachment_errors (Finding 2)
# ---------------------------------------------------------------------------


def test_split_nonexistent_path_records_file_not_found_error(tmp_path):
    """A file_path that does not exist must appear in attachment_errors
    as a 'file not found' entry, not be silently skipped.
    """
    missing = str(tmp_path / "does_not_exist.pdf")
    _, _, count, errors = _split_resolved_attachments(
        [
            {
                "path": missing,
                "filename": "does_not_exist.pdf",
                "mime_type": "application/pdf",
            }
        ]
    )
    assert count == 0
    assert len(errors) == 1, f"Expected 1 error entry, got {len(errors)}: {errors}"
    assert "file not found" in errors[0].lower(), (
        f"Error must mention 'file not found', got: {errors[0]!r}"
    )
