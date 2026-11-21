"""Tests that get_drive_file_download_url exports Google Drawings.

Drive refuses get_media on a Drawing, so it must be exported: PNG by default,
or PDF, JPEG or SVG.
"""

import base64
from unittest.mock import Mock, patch

import pytest

from gdrive.drive_helpers import drawing_export_target
from gdrive.drive_tools import get_drive_file_download_url


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeDownloader:
    def __init__(self, fh, request, chunksize=None):
        self._fh = fh

    def next_chunk(self):
        self._fh.write(b"drawing-bytes")
        return None, True


@pytest.mark.parametrize(
    "export_format, expected",
    [
        (None, ("png", "image/png")),
        ("png", ("png", "image/png")),
        ("PDF", ("pdf", "application/pdf")),
        ("jpg", ("jpg", "image/jpeg")),
        ("jpeg", ("jpg", "image/jpeg")),
        ("svg", ("svg", "image/svg+xml")),
        ("docx", ("png", "image/png")),
    ],
)
def test_drawing_export_target(export_format, expected):
    assert drawing_export_target(export_format) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name, export_format, mime_type, filename",
    [
        ("Sketch", None, "image/png", "Sketch.png"),
        ("Sketch", "pdf", "application/pdf", "Sketch.pdf"),
        ("Sketch", "svg", "image/svg+xml", "Sketch.svg"),
        ("Blueprint.v2", None, "image/png", "Blueprint.v2.png"),
        ("Sketch.png", "png", "image/png", "Sketch.png"),
    ],
)
async def test_download_url_exports_drawing(name, export_format, mime_type, filename):
    mock_service = Mock()
    mock_service.files().export_media.return_value = "req"
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.drawing",
        "webViewLink": "https://docs.google.com/drawings/d/draw123",
    }

    with (
        patch(
            "gdrive.drive_tools.resolve_drive_item",
            return_value=("draw123", metadata),
        ),
        patch("gdrive.drive_helpers.MediaIoBaseDownload", _FakeDownloader),
        patch("gdrive.drive_tools.is_stateless_mode", return_value=True),
        patch("gdrive.drive_tools.gcs_files_enabled", return_value=False),
        patch("gdrive.drive_tools.get_stateless_inline_max_bytes", return_value=1024),
    ):
        result = await _unwrap(get_drive_file_download_url)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="draw123",
            export_format=export_format,
        )

    mock_service.files().export_media.assert_called_with(
        fileId="draw123", mimeType=mime_type
    )
    text, resource = result.content
    assert f"exported to {mime_type}" in text.text
    assert resource.resource.uri == f"gdrive://draw123/{filename}"
    assert base64.b64decode(resource.resource.blob) == b"drawing-bytes"
