"""Tests for extract_pdf_text and encode_image_content in core.utils."""

import base64
import io
import pytest

from tests.helpers import _make_minimal_pdf
from core.utils import IMAGE_MIME_TYPES, encode_image_content, extract_pdf_text


# ---------------------------------------------------------------------------
# extract_pdf_text
# ---------------------------------------------------------------------------


def test_extract_pdf_text_valid():
    pdf_bytes = _make_minimal_pdf("Hello World")
    result = extract_pdf_text(pdf_bytes)
    assert result is not None
    assert "Hello World" in result


def test_extract_pdf_text_corrupted():
    result = extract_pdf_text(b"this is not a pdf")
    assert result is None


def test_extract_pdf_text_empty():
    """A PDF with a blank page (no text) returns None."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)

    result = extract_pdf_text(buf.getvalue())
    assert result is None


# ---------------------------------------------------------------------------
# encode_image_content
# ---------------------------------------------------------------------------


def test_encode_image_content_png():
    raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    result = encode_image_content(raw, "image/png")
    assert result.startswith("[base64_image:image/png]")
    encoded_part = result[len("[base64_image:image/png]") :]
    assert base64.b64decode(encoded_part) == raw


def test_encode_image_content_jpeg():
    raw = b"\xff\xd8\xff" + b"\x00" * 50
    result = encode_image_content(raw, "image/jpeg")
    assert result.startswith("[base64_image:image/jpeg]")
    encoded_part = result[len("[base64_image:image/jpeg]") :]
    assert base64.b64decode(encoded_part) == raw


def test_encode_image_content_rejects_non_image_mime():
    """encode_image_content should raise ValueError for non-image MIME types."""
    raw = b"some content"
    with pytest.raises(ValueError) as exc_info:
        encode_image_content(raw, "application/pdf")
    assert "Expected image/* MIME type" in str(exc_info.value)
    assert "application/pdf" in str(exc_info.value)


# ---------------------------------------------------------------------------
# IMAGE_MIME_TYPES constant
# ---------------------------------------------------------------------------


def test_image_mime_types_contains_common():
    for mt in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        assert mt in IMAGE_MIME_TYPES
