"""Tests for inline image attachments via content_id key.

When an attachment dict carries a `content_id` key, the resulting email
must use multipart/related so the HTML body can reference the image via
`cid:` URLs (RFC 2392).
"""

import base64
import asyncio
import logging
from email import message_from_bytes
from email.policy import SMTP
from types import SimpleNamespace

import gmail.gmail_tools as gmail_tools
from gmail.gmail_tools import _prepare_gmail_message, _resolve_url_attachments


# Minimal 1x1 PNG (89 bytes). Used as fake image payload.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNg"
    "YGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="
)


def _decode_raw(raw_b64url: str):
    """Decode the urlsafe-base64 raw message back into an EmailMessage tree."""
    raw_bytes = base64.urlsafe_b64decode(raw_b64url)
    return message_from_bytes(raw_bytes, policy=SMTP)


def _walk_parts(msg):
    """Yield every MIME part (including multiparts and leaves)."""
    yield msg
    if msg.is_multipart():
        for part in msg.iter_parts():
            yield from _walk_parts(part)


def test_content_id_attachment_uses_multipart_related():
    """An attachment with content_id must produce multipart/related with
    the image carrying Content-ID header and inline disposition."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="inline-img-test",
        body=('<html><body><p>see image:</p><img src="cid:fig-001"></body></html>'),
        body_format="html",
        to="someone@example.com",
        attachments=[
            {
                "content": _TINY_PNG_B64,
                "filename": "fig-001.png",
                "mime_type": "image/png",
                "content_id": "fig-001",
            }
        ],
    )

    assert errors == []
    assert attached == 1

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))
    content_types = [p.get_content_type() for p in parts]

    # Must contain a multipart/related anywhere in the tree.
    assert "multipart/related" in content_types, (
        f"expected multipart/related in tree, got {content_types}"
    )

    # Find the image part by content type.
    image_parts = [p for p in parts if p.get_content_type() == "image/png"]
    assert len(image_parts) == 1, f"expected 1 image part, got {len(image_parts)}"
    img = image_parts[0]

    # Content-ID header must match the requested cid (after stripping
    # angle brackets, since stdlib may add them).
    cid_header = img.get("Content-ID", "")
    assert cid_header.strip("<>") == "fig-001", (
        f"Content-ID mismatch: got {cid_header!r}"
    )

    # Inline disposition (not attachment).
    disposition = img.get("Content-Disposition", "")
    assert disposition.startswith("inline"), (
        f"expected inline disposition, got {disposition!r}"
    )


def test_url_based_inline_attachment_preserves_content_id(monkeypatch):
    """A URL-resolved attachment must keep content_id so it remains inline."""

    async def fake_download_attachment_bytes(url):
        assert url == "https://example.com/hero.png"
        return (
            base64.b64decode(_TINY_PNG_B64),
            SimpleNamespace(headers={"content-type": "image/png"}),
        )

    monkeypatch.setattr(
        gmail_tools, "_download_attachment_bytes", fake_download_attachment_bytes
    )
    attachments = asyncio.run(
        _resolve_url_attachments(
            [
                {
                    "url": "https://example.com/hero.png",
                    "filename": "hero.png",
                    "mime_type": "image/png",
                    "content_id": "hero",
                }
            ]
        )
    )

    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="url-inline-img-test",
        body=('<html><body><img src="cid:hero"></body></html>'),
        body_format="html",
        to="someone@example.com",
        attachments=attachments,
    )

    assert errors == []
    assert attached == 1

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))
    content_types = [p.get_content_type() for p in parts]
    assert "multipart/related" in content_types

    image_parts = [p for p in parts if p.get_content_type() == "image/png"]
    assert len(image_parts) == 1
    cid_header = image_parts[0].get("Content-ID", "")
    assert cid_header.strip("<>") == "hero"


def test_content_id_rejects_control_characters():
    """Unsafe content_id values must not reach MIME headers."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="unsafe-cid-test",
        body='<html><body><img src="cid:hero"></body></html>',
        body_format="html",
        to="someone@example.com",
        attachments=[
            {
                "content": _TINY_PNG_B64,
                "filename": "hero.png",
                "mime_type": "image/png",
                "content_id": "hero\r\nX-Injected: bad",
            }
        ],
    )

    assert attached == 0
    assert len(errors) == 1
    assert "content_id contains invalid control characters" in errors[0]

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))
    assert [p for p in parts if p.get_content_type() == "image/png"] == []


def test_multiple_inline_images_share_one_multipart_related():
    """Two or more inline images must coexist under a single multipart/related,
    not crash with 'Cannot convert alternative to related'."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="multi-inline-test",
        body=(
            "<html><body>"
            '<p>fig 1: <img src="cid:img1"></p>'
            '<p>fig 2: <img src="cid:img2"></p>'
            "</body></html>"
        ),
        body_format="html",
        to="someone@example.com",
        attachments=[
            {
                "content": _TINY_PNG_B64,
                "filename": "a.png",
                "mime_type": "image/png",
                "content_id": "img1",
            },
            {
                "content": _TINY_PNG_B64,
                "filename": "b.png",
                "mime_type": "image/png",
                "content_id": "img2",
            },
        ],
    )

    assert errors == [], f"unexpected errors: {errors}"
    assert attached == 2, f"expected 2 attachments, got {attached}"

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))
    content_types = [p.get_content_type() for p in parts]

    # Exactly ONE multipart/related — both images live inside it.
    assert content_types.count("multipart/related") == 1, (
        f"expected exactly 1 multipart/related, got {content_types}"
    )

    # Both images present with correct Content-IDs.
    image_parts = [p for p in parts if p.get_content_type() == "image/png"]
    assert len(image_parts) == 2, f"expected 2 image parts, got {len(image_parts)}"
    cids = {(p.get("Content-ID") or "").strip("<>") for p in image_parts}
    assert cids == {"img1", "img2"}, f"cid mismatch: {cids}"

    # Both have inline disposition.
    for img in image_parts:
        disposition = img.get("Content-Disposition", "")
        assert disposition.startswith("inline"), (
            f"expected inline disposition, got {disposition!r}"
        )


def test_attachment_without_content_id_still_uses_mixed():
    """Backward compat: an attachment dict without content_id must still
    produce multipart/mixed with attachment disposition (legacy path)."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="legacy-attachment-test",
        body="<html><body><p>file attached</p></body></html>",
        body_format="html",
        to="someone@example.com",
        attachments=[
            {
                "content": _TINY_PNG_B64,
                "filename": "report.png",
                "mime_type": "image/png",
                # NO content_id key
            }
        ],
    )

    assert errors == []
    assert attached == 1

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))
    content_types = [p.get_content_type() for p in parts]

    # Legacy path: multipart/mixed, no multipart/related.
    assert "multipart/mixed" in content_types
    assert "multipart/related" not in content_types

    # Image part exists, no Content-ID, attachment disposition.
    image_parts = [p for p in parts if p.get_content_type() == "image/png"]
    assert len(image_parts) == 1
    img = image_parts[0]
    assert img.get("Content-ID") is None, (
        f"unexpected Content-ID on legacy attachment: {img.get('Content-ID')!r}"
    )
    disposition = img.get("Content-Disposition", "")
    assert disposition.startswith("attachment"), (
        f"expected attachment disposition, got {disposition!r}"
    )


def test_mixed_inline_and_legacy_attachments():
    """Both an inline image (content_id) and a regular attachment in the
    same email must coexist correctly: inline image lands in
    multipart/related, regular attachment in multipart/mixed."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="mixed-test",
        body=('<html><body><img src="cid:hero"><p>and a doc:</p></body></html>'),
        body_format="html",
        to="someone@example.com",
        attachments=[
            {
                "content": _TINY_PNG_B64,
                "filename": "hero.png",
                "mime_type": "image/png",
                "content_id": "hero",
            },
            {
                "content": _TINY_PNG_B64,
                "filename": "appendix.png",
                "mime_type": "image/png",
                # NO content_id
            },
        ],
    )

    assert errors == []
    assert attached == 2

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))
    content_types = [p.get_content_type() for p in parts]

    # Both wrappers present.
    assert "multipart/mixed" in content_types
    assert "multipart/related" in content_types

    # One image with Content-ID = hero, one without.
    image_parts = [p for p in parts if p.get_content_type() == "image/png"]
    assert len(image_parts) == 2
    cids = {(p.get("Content-ID") or "").strip("<>") for p in image_parts}
    assert cids == {"hero", ""}, f"expected one cid 'hero' and one empty, got {cids}"


def test_plaintext_body_with_content_id_fallback():
    """When body_format is 'plain' (no text/html part), the inline image
    must still attach via the plain-text message fallback target."""
    raw_b64, _, attached, errors = _prepare_gmail_message(
        subject="plaintext-inline-test",
        body="See the attached image.",
        body_format="plain",
        to="someone@example.com",
        attachments=[
            {
                "content": _TINY_PNG_B64,
                "filename": "chart.png",
                "mime_type": "image/png",
                "content_id": "chart",
            }
        ],
    )

    assert errors == []
    assert attached == 1

    msg = _decode_raw(raw_b64)
    parts = list(_walk_parts(msg))

    # Image part exists with correct Content-ID and inline disposition.
    image_parts = [p for p in parts if p.get_content_type() == "image/png"]
    assert len(image_parts) == 1, f"expected 1 image part, got {len(image_parts)}"
    img = image_parts[0]
    cid_header = img.get("Content-ID", "")
    assert cid_header.strip("<>") == "chart", f"Content-ID mismatch: got {cid_header!r}"
    disposition = img.get("Content-Disposition", "")
    assert disposition.startswith("inline"), (
        f"expected inline disposition, got {disposition!r}"
    )


def test_duplicate_content_id_logs_warning(caplog):
    """Two attachments with the same content_id should both attach but
    produce a warning about the duplicate."""
    with caplog.at_level(logging.WARNING, logger="gmail.gmail_tools"):
        raw_b64, _, attached, errors = _prepare_gmail_message(
            subject="dup-cid-test",
            body=('<html><body><img src="cid:same"><img src="cid:same"></body></html>'),
            body_format="html",
            to="someone@example.com",
            attachments=[
                {
                    "content": _TINY_PNG_B64,
                    "filename": "a.png",
                    "mime_type": "image/png",
                    "content_id": "same",
                },
                {
                    "content": _TINY_PNG_B64,
                    "filename": "b.png",
                    "mime_type": "image/png",
                    "content_id": "same",
                },
            ],
        )

    assert errors == []
    assert attached == 2

    # Verify the duplicate warning was logged.
    warning_messages = [
        r.message for r in caplog.records if r.levelno == logging.WARNING
    ]
    assert any("Duplicate content_id" in m for m in warning_messages), (
        f"expected duplicate content_id warning, got: {warning_messages}"
    )
