"""Tests for PDF and image handling in get_drive_file_content."""

import base64
import io
from unittest.mock import Mock, patch

import pytest

from tests.helpers import _make_minimal_pdf
from gdrive.drive_tools import get_drive_file_content


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.fixture
def mock_resolve():
    with patch("gdrive.drive_tools.resolve_drive_item") as m:
        m.return_value = (
            "file123",
            {
                "name": "test_file",
                "mimeType": "application/pdf",
                "webViewLink": "https://drive.google.com/file/file123",
            },
        )
        yield m


@pytest.fixture
def mock_resolve_image():
    with patch("gdrive.drive_tools.resolve_drive_item") as m:
        m.return_value = (
            "img456",
            {
                "name": "photo.png",
                "mimeType": "image/png",
                "webViewLink": "https://drive.google.com/file/img456",
            },
        )
        yield m


class _FakeDownloader:
    def __init__(self, fh, data):
        fh.write(data)
        fh.seek(0)

    def next_chunk(self):
        return None, True


def _patch_downloader(content_bytes):
    """Patch MediaIoBaseDownload to write content_bytes into the BytesIO handle."""
    return patch(
        "gdrive.drive_tools.MediaIoBaseDownload",
        side_effect=lambda fh, req: _FakeDownloader(fh, content_bytes),
    )


# ---------------------------------------------------------------------------
# PDF tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_drive_file_content_pdf(mock_resolve):
    pdf_bytes = _make_minimal_pdf("Contract clause 1")
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    with _patch_downloader(pdf_bytes):
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert "Contract clause 1" in result
    assert "--- CONTENT ---" in result


@pytest.mark.asyncio
async def test_get_drive_file_content_pdf_empty(mock_resolve):
    """Empty/scanned PDF falls back to guidance message."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    empty_pdf = buf.getvalue()

    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    with _patch_downloader(empty_pdf):
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert "get_drive_file_download_url" in result


# ---------------------------------------------------------------------------
# Image tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_drive_file_content_image(mock_resolve_image):
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    with _patch_downloader(image_bytes):
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="img456",
        )

    assert "[base64_image:image/png]" in result
    # Verify base64 portion decodes to original bytes
    b64_part = result.split("[base64_image:image/png]")[1].strip()
    assert base64.b64decode(b64_part) == image_bytes
