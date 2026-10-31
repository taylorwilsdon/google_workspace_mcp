"""Historical paging keeps revision identity separate from current-file caching."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastmcp.tools import ToolResult

from core.file_limits import PagedContentCache
import gdrive.drive_tools as drive


def _unwrap(tool):
    """Reach the implementation while keeping authentication out of unit tests."""
    function = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


def _text(result):
    """Extract the same text from string and image-bearing tool responses."""
    if isinstance(result, str):
        return result
    return "\n".join(block.text for block in result.content if block.type == "text")


@pytest.fixture
def history(tmp_path, monkeypatch):
    """Provide real extraction and isolated caching without any Google requests."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", "12")
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "128")
    monkeypatch.setenv("WORKSPACE_MCP_PAGED_CONTENT_CACHE_BYTES", "1048576")
    cache = PagedContentCache(ttl_seconds=600)
    monkeypatch.setattr(drive, "_paged_content_cache", cache)
    monkeypatch.setattr(drive, "is_stateless_mode", lambda: False)
    metadata = {
        "name": "current.txt",
        "mimeType": "text/plain",
        "webViewLink": "https://drive.google.com/file/d/file123",
        "version": "100",
        "capabilities": {"canDownload": True},
        "size": "1000000",
    }
    resolved = AsyncMock(return_value=("file123", metadata))
    monkeypatch.setattr(drive, "resolve_drive_item", resolved)
    payloads = {
        "old-a": b"A historic version with enough text for several pages.",
        "old-b": b"B different history that must never reuse version A.",
        "empty": b"",
    }
    paths = []
    output_mime = {"value": "text/plain"}

    async def download(service, file_id, revision_id, **kwargs):
        """Create a fresh historical payload so every acquired file can be checked."""
        path = tmp_path / f"revision-{len(paths)}"
        path.write_bytes(payloads[revision_id])
        paths.append(path)
        return {
            "path": path,
            "file_id": file_id,
            "file_name": metadata["name"],
            "revision_id": revision_id,
            "revision_modified_time": "2026-08-28T13:00:00Z",
            "output_filename": "historical.txt",
            "output_mime_type": output_mime["value"],
        }

    acquisition = AsyncMock(side_effect=download)
    monkeypatch.setattr(drive, "download_drive_revision_to_temp", acquisition)
    current_bytes = b"CURRENT data belongs only to the current version." * 2
    current_download = AsyncMock(return_value=current_bytes)
    current_request = Mock(return_value="current-request")
    monkeypatch.setattr(drive, "download_media_bytes", current_download)
    monkeypatch.setattr(drive, "_media_request", current_request)
    service = Mock()
    service.files().get().execute.return_value = {"version": "100"}
    service.reset_mock()

    async def read(revision_id=None, **kwargs):
        """Read current or historical content through the actual tool implementation."""
        return await _unwrap(drive.get_drive_file_content)(
            service=service,
            user_google_email="reader@example.com",
            file_id="file123",
            revision_id=revision_id,
            **kwargs,
        )

    return SimpleNamespace(
        read=read,
        cache=cache,
        current_key=("reader@example.com", "file123", "100", None),
        current_bytes=current_bytes,
        current_download=current_download,
        current_request=current_request,
        service=service,
        acquisition=acquisition,
        payloads=payloads,
        paths=paths,
        metadata=metadata,
        output_mime=output_mime,
    )


@pytest.mark.asyncio
async def test_revision_pages_bypass_current_cache_and_keep_selected_identity(history):
    """A populated current cache cannot supply or be replaced by historical pages."""
    current = history.current_bytes.decode()
    history.cache.put(history.current_key, current, max_bytes=1048576)
    with (
        patch.object(history.cache, "get", wraps=history.cache.get) as cached_read,
        patch.object(history.cache, "put", wraps=history.cache.put) as cached_write,
    ):
        first = _text(await history.read("old-a"))
        second = _text(await history.read("old-a", offset=12))
        different = _text(await history.read("old-b", offset=12))
    assert history.payloads["old-a"].decode()[:12] in first
    assert history.payloads["old-a"].decode()[12:24] in second
    assert history.payloads["old-b"].decode()[12:24] in different
    assert "Revision: old-a (2026-08-28T13:00:00Z)" in second
    assert "Revision: old-b (2026-08-28T13:00:00Z)" in different
    assert "Version: 100" not in first + second + different
    assert "CURRENT" not in first + second + different
    cached_read.assert_not_called()
    cached_write.assert_not_called()
    assert history.cache.get(history.current_key) == current
    history.current_request.assert_not_called()
    history.current_download.assert_not_awaited()
    history.service.files.assert_not_called()
    assert history.acquisition.await_count == 3
    assert all(
        call.kwargs["max_bytes"] == 128 for call in history.acquisition.await_args_list
    )
    assert all(not path.exists() for path in history.paths)


@pytest.mark.asyncio
async def test_historical_page_does_not_seed_the_cache_for_a_current_read(history):
    """A following current offset request still downloads current content."""
    await history.read("old-a")
    assert history.cache.get(history.current_key) is None
    history.metadata["size"] = str(len(history.current_bytes))
    current = _text(await history.read(offset=12))
    assert history.current_bytes.decode()[12:24] in current
    assert "Version: 100" in current
    assert "Revision:" not in current
    history.current_download.assert_awaited_once()
    assert all(not path.exists() for path in history.paths)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 12])
async def test_historical_full_content_uses_revision_mime_and_returns_the_suffix(
    history, offset, monkeypatch
):
    """Full historical reads ignore the page cap and the current file's MIME."""
    history.metadata["mimeType"] = "application/pdf"
    parse_current_pdf = Mock(side_effect=AssertionError("parsed the current MIME"))
    monkeypatch.setattr(drive, "extract_pdf_text", parse_current_pdf)
    result = _text(await history.read("old-a", offset=offset, full_content=True))
    assert (
        result.split("--- CONTENT ---\n", 1)[1]
        == history.payloads["old-a"].decode()[offset:]
    )
    assert "Type: text/plain)" in result
    assert "Version: 100" not in result
    assert "[Truncated:" not in result
    parse_current_pdf.assert_not_called()
    history.current_download.assert_not_awaited()
    assert all(not path.exists() for path in history.paths)


@pytest.mark.asyncio
async def test_empty_historical_content_never_falls_back_to_current_content(history):
    """An empty selected revision is still a successful historical read."""
    result = _text(await history.read("empty"))
    assert "Revision: empty (2026-08-28T13:00:00Z)" in result
    assert result.split("--- CONTENT ---\n", 1)[1] == ""
    history.current_request.assert_not_called()
    history.current_download.assert_not_awaited()
    assert all(not path.exists() for path in history.paths)


@pytest.mark.asyncio
async def test_historical_svg_images_are_attached_only_on_the_first_page(
    history, monkeypatch
):
    """Historical SVG follows the existing first-page image delivery behavior."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "2048")
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
    )
    history.payloads["svg"] = (
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,'
        + base64.b64encode(png).decode()
        + '"/></svg>'
    ).encode()
    history.output_mime["value"] = "image/svg+xml"
    first = await history.read("svg")
    second = await history.read("svg", offset=12)
    assert isinstance(first, ToolResult)
    assert any(block.type == "image" for block in first.content)
    assert isinstance(second, str)
    assert "Revision: svg (2026-08-28T13:00:00Z)" in second
    history.current_download.assert_not_awaited()
    assert all(not path.exists() for path in history.paths)
