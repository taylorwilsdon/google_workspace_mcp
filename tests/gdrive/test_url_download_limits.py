from contextlib import asynccontextmanager

import pytest

import gdrive.drive_helpers as drive_helpers
from core import attachment_storage, config


class _FakeStreamResponse:
    def __init__(self, status_code=200, headers=None, chunks=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []

    async def aiter_bytes(self, chunk_size=0):
        for chunk in self._chunks:
            yield chunk


def _mock_stream_response(response):
    @asynccontextmanager
    async def _stream(_url):
        yield response

    return _stream


@pytest.mark.asyncio
async def test_download_url_to_bytes_streams_content(monkeypatch):
    response = _FakeStreamResponse(
        200,
        headers={"Content-Type": "application/pdf"},
        chunks=[b"abc", b"def"],
    )
    monkeypatch.setattr(
        drive_helpers, "_ssrf_safe_stream", _mock_stream_response(response)
    )

    data_file, content_type = await drive_helpers._download_url_to_bytes(
        "https://example.com/file.pdf", upload_type_param="mime_type"
    )

    with data_file:
        assert data_file.read() == b"abcdef"
    assert content_type == "application/pdf"


@pytest.mark.asyncio
async def test_download_url_to_bytes_enforces_max_size(monkeypatch):
    response = _FakeStreamResponse(200, chunks=[b"abcd", b"efgh"])
    monkeypatch.setattr(
        drive_helpers, "_ssrf_safe_stream", _mock_stream_response(response)
    )
    monkeypatch.setattr(drive_helpers, "MAX_DOWNLOAD_BYTES", 6)

    with pytest.raises(
        ValueError,
        match=r"Download from example\.com/file\.bin exceeded 6 byte limit",
    ):
        await drive_helpers._download_url_to_bytes(
            "https://example.com/file.bin", upload_type_param="mime_type"
        )


@pytest.fixture
def stored_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(attachment_storage, "STORAGE_DIR", tmp_path)
    monkeypatch.setattr(attachment_storage, "_attachment_storage", None)
    return attachment_storage.get_attachment_storage().save_attachment_bytes(
        b"%PDF-stored", "report.pdf", "application/pdf"
    )


@pytest.mark.asyncio
async def test_download_url_to_bytes_reads_own_attachment_from_storage(
    stored_attachment, monkeypatch
):
    def _no_fetch(_url):
        raise AssertionError("own attachment links must not be fetched over HTTP")

    monkeypatch.setattr(drive_helpers, "_ssrf_safe_stream", _no_fetch)
    url = (
        f"{config.WORKSPACE_MCP_BASE_URI}:{config.WORKSPACE_MCP_PORT}"
        f"/attachments/{stored_attachment.file_id}"
    )

    data_file, content_type = await drive_helpers._download_url_to_bytes(
        url, upload_type_param="mime_type"
    )

    with data_file:
        assert data_file.read() == b"%PDF-stored"
    assert content_type == "application/pdf"


@pytest.mark.asyncio
async def test_download_url_to_bytes_fetches_untrusted_attachment_origin(
    stored_attachment, monkeypatch
):
    response = _FakeStreamResponse(200, chunks=[b"remote"])
    monkeypatch.setattr(
        drive_helpers, "_ssrf_safe_stream", _mock_stream_response(response)
    )

    data_file, _content_type = await drive_helpers._download_url_to_bytes(
        f"https://evil.example/attachments/{stored_attachment.file_id}",
        upload_type_param="mime_type",
    )

    with data_file:
        assert data_file.read() == b"remote"
