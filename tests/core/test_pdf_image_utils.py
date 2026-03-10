"""Tests for extract_pdf_text and encode_image_content in core.utils."""

import base64


from core.utils import IMAGE_MIME_TYPES, encode_image_content, extract_pdf_text


# ---------------------------------------------------------------------------
# extract_pdf_text
# ---------------------------------------------------------------------------


def _make_minimal_pdf(text: str = "Hello World") -> bytes:
    """Build a tiny valid PDF with one page containing *text* using pypdf."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        DictionaryObject,
        DecodedStreamObject,
        NameObject,
    )

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)

    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode())

    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")

    font_res = DictionaryObject()
    font_res[NameObject("/F1")] = font_dict

    resources = DictionaryObject()
    resources[NameObject("/Font")] = font_res

    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)

    import io

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


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
    import io

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


# ---------------------------------------------------------------------------
# IMAGE_MIME_TYPES constant
# ---------------------------------------------------------------------------


def test_image_mime_types_contains_common():
    for mt in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        assert mt in IMAGE_MIME_TYPES
