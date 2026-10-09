"""Tests for the X-Attachment-Id / Content-ID headers on regular attachments."""

import base64
import re
from email import message_from_bytes
from email.policy import SMTP

from gmail.gmail_tools import _prepare_gmail_message


def _pdf(filename: str, payload: bytes) -> dict:
    return {
        "content": base64.b64encode(payload).decode(),
        "filename": filename,
        "mime_type": "application/pdf",
    }


def _attachment_parts(raw_b64url: str):
    msg = message_from_bytes(base64.urlsafe_b64decode(raw_b64url), policy=SMTP)
    return [p for p in msg.walk() if p.get_content_disposition() == "attachment"]


def test_each_regular_attachment_gets_a_unique_attachment_id():
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="attachment-id-test",
        body="three files",
        to="someone@example.com",
        attachments=[
            _pdf("presentation.pdf", b"%PDF-1.7 presentation"),
            _pdf("pricing.pdf", b"%PDF-1.7 pricing"),
            _pdf("contract.pdf", b"%PDF-1.7 contract"),
        ],
    )

    assert errors == []
    assert attached == 3

    parts = _attachment_parts(raw_b64)
    ids = [str(p.get("X-Attachment-Id", "")).strip() for p in parts]
    assert len(ids) == 3
    assert all(ids), f"empty X-Attachment-Id: {ids}"
    assert len(set(ids)) == 3, f"X-Attachment-Id not unique: {ids}"


def test_regular_attachment_content_id_matches_attachment_id():
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="attachment-id-test",
        body="one file",
        to="someone@example.com",
        attachments=[_pdf("presentation.pdf", b"%PDF-1.7 presentation")],
    )

    assert errors == []
    assert attached == 1

    (part,) = _attachment_parts(raw_b64)
    assert part["Content-ID"] == f"<{part['X-Attachment-Id']}>"


def _web_attachment_parts(raw_b64url: str):
    msg = message_from_bytes(base64.urlsafe_b64decode(raw_b64url), policy=SMTP)
    return [p for p in msg.walk() if p.get_content_disposition() == "attachment"]


def test_web_compose_regular_attachments_get_unique_matching_ids():
    """The Gmail-web path emits X-Attachment-Id and a matching Content-ID too."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="attachment-id-test",
        body="three files",
        to="someone@example.com",
        from_email="me@example.com",
        web_compose=True,
        attachments=[
            _pdf("presentation.pdf", b"%PDF-1.7 presentation"),
            _pdf("pricing.pdf", b"%PDF-1.7 pricing"),
            _pdf("contract.pdf", b"%PDF-1.7 contract"),
        ],
    )

    assert errors == []
    assert attached == 3
    parts = _web_attachment_parts(raw_b64)
    ids = [str(p.get("X-Attachment-Id", "")).strip() for p in parts]
    assert len(ids) == 3
    assert all(re.fullmatch(r"f_[0-9a-f]{10}", i) for i in ids), ids
    assert len(set(ids)) == 3, f"X-Attachment-Id not unique: {ids}"
    for p in parts:
        assert p["Content-ID"] == f"<{p['X-Attachment-Id']}>"


def test_web_attachment_part_header_order_matches_gmail_web():
    """Gmail web orders a regular attachment's headers as asserted here."""
    from gmail.gmail_web_mime import assemble_mixed

    raw = assemble_mixed(
        [("From", "a@example.com"), ("To", "b@example.com"), ("Subject", "S")],
        "plain",
        "<div>html</div>",
        [{"filename": "a.pdf", "mime_type": "application/pdf", "data": b"%PDF"}],
        "000000000000aaaaaaaaaaaaaaaa",
        "000000000000bbbbbbbbbbbbbbbb",
    )
    msg = message_from_bytes(raw.encode(), policy=SMTP)
    (part,) = [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    assert list(part.keys()) == [
        "Content-Type",
        "Content-Disposition",
        "Content-Transfer-Encoding",
        "Content-ID",
        "X-Attachment-Id",
    ]
