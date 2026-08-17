"""Tests that get_drive_file_download_url streams to disk instead of buffering.

Buffering the payload in memory made peak RSS a multiple of the file size, which
took whole machines down on multi-gigabyte downloads (#994). These tests pin the
streaming contract: the download handle is a real file, the temp file is always
cleaned up, and nothing base64-encodes the payload on the way to storage.
"""

import io
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import core.attachment_storage as attachment_storage
from core.attachment_storage import AttachmentStorage
from gdrive.drive_tools import _download_file_to_temp, get_drive_file_download_url


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeDownloader:
    """Stand-in for MediaIoBaseDownload that records the handle it was given."""

    handles = []

    def __init__(self, fh, request, chunksize=None):
        type(self).handles.append(fh)
        self._fh = fh
        self._data = _FakeDownloader.data

    def next_chunk(self):
        self._fh.write(self._data)
        return None, True


def _patch_downloader(content_bytes):
    _FakeDownloader.data = content_bytes
    _FakeDownloader.handles = []
    return patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Point attachment storage at a scratch directory."""
    storage_dir = tmp_path / "attachments"
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", storage_dir)
    instance = AttachmentStorage()
    monkeypatch.setattr(attachment_storage, "get_attachment_storage", lambda: instance)
    monkeypatch.setattr("gdrive.drive_tools.get_attachment_storage", lambda: instance)
    return instance


@pytest.fixture
def mock_resolve():
    with patch("gdrive.drive_tools.resolve_drive_item") as m:
        m.return_value = (
            "file123",
            {
                "name": "clip.mp4",
                "mimeType": "video/mp4",
                "webViewLink": "https://drive.google.com/file/file123",
            },
        )
        yield m


@pytest.mark.asyncio
async def test_download_to_temp_writes_to_a_real_file():
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    with _patch_downloader(b"payload"):
        tmp_path = await _download_file_to_temp(mock_service, "file123")

    try:
        assert tmp_path.read_bytes() == b"payload"
        # The whole point: the download target must not be an in-memory buffer.
        assert not isinstance(_FakeDownloader.handles[0], io.BytesIO)
    finally:
        tmp_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_download_to_temp_removes_temp_file_on_failure():
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    class _Boom(_FakeDownloader):
        def next_chunk(self):
            raise RuntimeError("network died")

    with patch("gdrive.drive_tools.MediaIoBaseDownload", _Boom):
        _FakeDownloader.handles = []
        with pytest.raises(RuntimeError):
            await _download_file_to_temp(mock_service, "file123")

    assert not Path(_Boom.handles[0].name).exists()


@pytest.mark.asyncio
async def test_download_url_moves_payload_into_storage(mock_resolve, storage):
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    with (
        _patch_downloader(b"video-bytes"),
        patch("gdrive.drive_tools.is_stateless_mode", return_value=False),
        patch("gdrive.drive_tools.get_transport_mode", return_value="stdio"),
    ):
        result = await _unwrap(get_drive_file_download_url)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    saved = Path(result.split("📎 Saved to: ")[1].splitlines()[0].strip())
    assert saved.read_bytes() == b"video-bytes"
    assert "11 bytes" in result
    # The temp file was moved, not copied, so nothing is left behind.
    assert not Path(_FakeDownloader.handles[0].name).exists()


@pytest.mark.asyncio
async def test_download_url_stateless_mode_previews_and_cleans_up(mock_resolve):
    mock_service = Mock()
    mock_service.files().get_media.return_value = "req"

    with (
        _patch_downloader(b"x" * 500),
        patch("gdrive.drive_tools.is_stateless_mode", return_value=True),
    ):
        result = await _unwrap(get_drive_file_download_url)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert "Stateless mode" in result
    assert "500 bytes" in result
    assert not Path(_FakeDownloader.handles[0].name).exists()
