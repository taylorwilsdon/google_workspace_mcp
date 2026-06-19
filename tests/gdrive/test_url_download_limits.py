from contextlib import asynccontextmanager

import pytest

import gdrive.drive_helpers as drive_helpers


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
        "https://example.com/file.pdf"
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
        await drive_helpers._download_url_to_bytes("https://example.com/file.bin")
