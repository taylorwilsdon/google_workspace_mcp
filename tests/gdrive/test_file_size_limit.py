"""Tool-level coverage for WORKSPACE_MCP_MAX_FILE_BYTES on Drive downloads."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest

from gdrive.drive_tools import get_drive_file_content, get_drive_file_download_url


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_get_drive_file_content_rejects_declared_oversized(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    mock_service = Mock()

    with patch("gdrive.drive_tools.resolve_drive_item") as resolve:
        resolve.return_value = (
            "file123",
            {
                "name": "huge.bin",
                "mimeType": "application/octet-stream",
                "webViewLink": "https://drive.google.com/file/d/file123",
                "size": "500",
            },
        )
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert result.startswith("Error:")
    assert "huge.bin" in result
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in result
    mock_service.files().get_media.assert_not_called()


@pytest.mark.asyncio
async def test_get_drive_file_download_url_allows_disk_streamed_oversized(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "20")
    mock_service = Mock()
    downloaded = tmp_path / "download.bin"
    downloaded.write_bytes(b"x" * 500)

    with (
        patch("gdrive.drive_tools.resolve_drive_item") as resolve,
        patch(
            "gdrive.drive_tools._download_file_to_temp",
            new=AsyncMock(return_value=downloaded),
        ) as download,
        patch("gdrive.drive_tools.is_stateless_mode", return_value=True),
    ):
        resolve.return_value = (
            "file123",
            {
                "name": "blob.bin",
                "mimeType": "application/octet-stream",
                "webViewLink": "https://drive.google.com/file/d/file123",
                "size": "500",
            },
        )
        result = await _unwrap(get_drive_file_download_url)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert "File downloaded successfully!" in result
    assert "500 bytes" in result
    download.assert_awaited_once_with(mock_service, "file123", None)


@pytest.mark.asyncio
async def test_get_drive_file_content_allows_under_limit(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    mock_service = Mock()
    mock_service.files().get_media.return_value = Mock(uri="https://example/media")
    data = b"hello under limit"

    with (
        patch("gdrive.drive_tools.resolve_drive_item") as resolve,
        patch(
            "gdrive.drive_tools.download_media_bytes",
            new=AsyncMock(return_value=data),
        ),
    ):
        resolve.return_value = (
            "file123",
            {
                "name": "note.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive.google.com/file/d/file123",
                "size": str(len(data)),
            },
        )
        result = await _unwrap(get_drive_file_content)(
            service=mock_service,
            user_google_email="user@example.com",
            file_id="file123",
        )

    assert "hello under limit" in result
    assert not result.startswith("Error:")
