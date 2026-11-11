"""Tests for PDF and image helpers in core.utils."""

import base64
import io
import pytest
from mcp.types import ImageContent

from tests.helpers import _make_minimal_pdf
from core.utils import (
    IMAGE_MIME_TYPES,
    INLINE_IMAGE_MIME_TYPES,
    SVG_MIME_TYPE,
    encode_image_content,
    extract_pdf_text,
    image_content_from_bytes,
    sniff_inline_image_mime_type,
    svg_content,
)


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
# image_content_from_bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mime_type", sorted(IMAGE_MIME_TYPES))
def test_image_content_from_bytes_round_trips(mime_type):
    raw = b"\x89PNG\r\n\x1a\n" + bytes(range(256))
    result = image_content_from_bytes(raw, mime_type)
    assert isinstance(result, ImageContent)
    assert result.type == "image"
    assert result.mime_type == mime_type
    assert base64.b64decode(result.data) == raw


def test_image_content_from_bytes_rejects_non_image_mime():
    with pytest.raises(ValueError) as exc_info:
        image_content_from_bytes(b"some content", "application/pdf")
    assert "Expected image/* MIME type" in str(exc_info.value)
    assert "application/pdf" in str(exc_info.value)


# ---------------------------------------------------------------------------
# IMAGE_MIME_TYPES constant
# ---------------------------------------------------------------------------


def test_image_mime_types_contains_common():
    for mt in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        assert mt in IMAGE_MIME_TYPES


def test_inline_image_mime_types_is_model_api_allowlist():
    assert INLINE_IMAGE_MIME_TYPES == {
        "image/png",
        "image/jpeg",
        "image/gif",
        "image/webp",
    }
    assert INLINE_IMAGE_MIME_TYPES <= IMAGE_MIME_TYPES


# ---------------------------------------------------------------------------
# sniff_inline_image_mime_type and svg_content
# ---------------------------------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(200))
JPEG = b"\xff\xd8\xff\xe0" + bytes(range(200))
GIF = b"GIF89a" + bytes(range(200))
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + bytes(range(200))
FONT = b"wOF2" + bytes(range(200))


def _b64(data):
    return base64.b64encode(data).decode("ascii")


@pytest.mark.parametrize(
    "data, expected",
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (GIF, "image/gif"),
        (b"GIF87a" + bytes(10), "image/gif"),
        (WEBP, "image/webp"),
        (b"BM" + bytes(64), None),
        (b"II*\x00" + bytes(64), None),
        (b"<svg></svg>", None),
        (FONT, None),
        (b"RIFF\x00\x00\x00\x00WAVE", None),
        (b"", None),
    ],
    ids=[
        "png",
        "jpeg",
        "gif89a",
        "gif87a",
        "webp",
        "bmp",
        "tiff",
        "svg",
        "font",
        "riff-wav",
        "empty",
    ],
)
def test_sniff_inline_image_mime_type(data, expected):
    assert sniff_inline_image_mime_type(data) == expected


def test_svg_mime_type_is_not_inline():
    assert SVG_MIME_TYPE in IMAGE_MIME_TYPES
    assert SVG_MIME_TYPE not in INLINE_IMAGE_MIME_TYPES


def test_svg_content_returns_markup_unchanged():
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Ünïcode ✓</text></svg>'
    result = svg_content(svg.encode("utf-8"))
    assert result.markup == svg
    assert result.images == []


def test_svg_content_attaches_embedded_images_in_order():
    svg = (
        f'<svg><image href="data:image/png;base64,{_b64(PNG)}"/>'
        f"<image xlink:href='data:image/jpeg;base64,{_b64(JPEG)}'/>"
        f'<image href="data:image/webp;base64,{_b64(WEBP)}"/></svg>'
    )
    result = svg_content(svg.encode())
    assert result.markup == (
        f'<svg><image href="data:image/png;base64,[embedded image 1, {len(PNG)} bytes, attached as image content]"/>'
        f"<image xlink:href='data:image/jpeg;base64,[embedded image 2, {len(JPEG)} bytes, attached as image content]'/>"
        f'<image href="data:image/webp;base64,[embedded image 3, {len(WEBP)} bytes, attached as image content]"/></svg>'
    )
    assert [image.mime_type for image in result.images] == [
        "image/png",
        "image/jpeg",
        "image/webp",
    ]
    assert [base64.b64decode(image.data) for image in result.images] == [
        PNG,
        JPEG,
        WEBP,
    ]


def test_svg_content_attaches_every_embedded_image():
    """No cap: an SVG with many embedded images attaches all of them."""
    svg = (
        "<svg>"
        + "".join(
            f'<image href="data:image/png;base64,{_b64(PNG + bytes([i]))}"/>'
            for i in range(25)
        )
        + "</svg>"
    )
    result = svg_content(svg.encode())
    assert len(result.images) == 25
    assert "[embedded image 25, " in result.markup


def test_svg_content_sniffs_type_instead_of_trusting_the_uri():
    """A JPEG labelled image/png is attached as image/jpeg."""
    svg = f'<image href="data:image/png;base64,{_b64(JPEG)}"/>'
    result = svg_content(svg.encode())
    assert [image.mime_type for image in result.images] == ["image/jpeg"]
    assert result.markup == (
        f'<image href="data:image/png;base64,[embedded image 1, {len(JPEG)} bytes, attached as image content]"/>'
    )


def test_svg_content_omits_payloads_that_are_not_attachable_images():
    """Fonts, BMP, and anything labelled as an image that isn't one are omitted."""
    bmp = b"BM" + bytes(range(200))
    svg = (
        f"<style>@font-face{{src:url(data:font/woff2;charset=utf-8;base64,{_b64(FONT)})}}</style>"
        f'<image href="data:image/bmp;base64,{_b64(bmp)}"/>'
        f'<image href="data:image/png;base64,{_b64(FONT)}"/>'
        f'<image href="data:;base64,{_b64(FONT)}"/>'
    )
    result = svg_content(svg.encode())
    assert result.images == []
    assert result.markup == (
        f"<style>@font-face{{src:url(data:font/woff2;base64,[{len(FONT)} bytes omitted])}}</style>"
        f'<image href="data:image/bmp;base64,[{len(bmp)} bytes omitted]"/>'
        f'<image href="data:image/png;base64,[{len(FONT)} bytes omitted]"/>'
        f'<image href="data:;base64,[{len(FONT)} bytes omitted]"/>'
    )


def test_svg_content_omits_invalid_base64():
    """Malformed padding fails to decode, so nothing is attached."""
    payload = "A" * 70 + "=A"
    svg = f'<image href="data:image/png;base64,{payload}"/>'
    result = svg_content(svg.encode())
    assert result.images == []
    assert "invalid base64 omitted" in result.markup
    assert payload not in result.markup


def test_svg_content_decodes_whitespace_wrapped_payload():
    """SVG tools often wrap long base64 payloads across lines."""
    encoded = _b64(PNG)
    wrapped = "\n".join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    svg = f'<svg><image href="data:image/png;base64,{wrapped}"/></svg>'
    result = svg_content(svg.encode())
    assert [base64.b64decode(image.data) for image in result.images] == [PNG]
    assert result.markup == (
        f'<svg><image href="data:image/png;base64,[embedded image 1, {len(PNG)} bytes, attached as image content]"/></svg>'
    )


@pytest.mark.parametrize("line_break", ["&#10;", "&#xA;", "&#13;&#10;"])
def test_svg_content_decodes_payload_wrapped_with_character_references(line_break):
    """XML serializers write a line break inside an attribute as a reference."""
    encoded = _b64(PNG)
    wrapped = line_break.join(encoded[i : i + 76] for i in range(0, len(encoded), 76))
    svg = f'<svg><image href="data:image/png;base64,{wrapped}"/></svg>'
    result = svg_content(svg.encode())
    assert [base64.b64decode(image.data) for image in result.images] == [PNG]
    assert result.markup == (
        f'<svg><image href="data:image/png;base64,[embedded image 1, {len(PNG)} bytes, attached as image content]"/></svg>'
    )


def test_svg_content_matches_base64_marker_case_insensitively():
    svg = f'<svg><image href="data:image/png;BASE64,{_b64(PNG)}"/></svg>'
    result = svg_content(svg.encode())
    assert [base64.b64decode(image.data) for image in result.images] == [PNG]
    assert result.markup == (
        f'<svg><image href="data:image/png;base64,[embedded image 1, {len(PNG)} bytes, attached as image content]"/></svg>'
    )


@pytest.mark.parametrize("leading", ["\n", " \n  ", "&#10;"])
def test_svg_content_decodes_payload_after_leading_whitespace(leading):
    svg = f'<svg><image href="data:image/png;base64,{leading}{_b64(PNG)}"/></svg>'
    result = svg_content(svg.encode())
    assert [base64.b64decode(image.data) for image in result.images] == [PNG]
    assert result.markup == (
        f'<svg><image href="data:image/png;base64,[embedded image 1, {len(PNG)} bytes, attached as image content]"/></svg>'
    )


def test_svg_content_leaves_non_base64_data_uris():
    """Percent-encoded data URIs are already text, so they are kept."""
    svg = '<svg><image href="data:image/svg+xml,%3Csvg%3E%3C/svg%3E"/></svg>'
    result = svg_content(svg.encode())
    assert result.markup == svg
    assert result.images == []


def test_svg_content_returns_none_for_non_utf8():
    assert svg_content(b"\x1f\x8b\x08\x00\xff\xfe") is None


def test_svg_content_keeps_short_payloads():
    """Eliding a tiny payload would make the text longer, not shorter."""
    svg = '<svg><image href="data:image/png;base64,QUJD"/></svg>'
    result = svg_content(svg.encode())
    assert result.markup == svg
    assert result.images == []


def test_svg_content_does_not_consume_following_text():
    """The match stops at the end of the payload, not at the next whitespace run."""
    svg = f'<image href="data:image/png;base64,{_b64(PNG)}"/> \n <text>Next</text>'
    result = svg_content(svg.encode())
    assert result.markup == (
        f'<image href="data:image/png;base64,[embedded image 1, {len(PNG)} bytes, attached as image content]"/>'
        " \n <text>Next</text>"
    )
