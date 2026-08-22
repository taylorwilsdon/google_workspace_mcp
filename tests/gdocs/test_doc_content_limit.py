"""Finding 12: get_doc_content must not buffer an unbounded Drive download.

The `MediaIoBaseDownload` loop ran to completion whatever the size, so the whole
export landed in a `BytesIO` before anything looked at it.
"""

from unittest.mock import Mock

import pytest

import gdocs.docs_tools as docs_tools


def _unwrap(tool):
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


get_doc_content = _unwrap(docs_tools.get_doc_content)


class _ChunkedDownloader:
    """MediaIoBaseDownload stand-in that writes `chunk` bytes per next_chunk()."""

    def __init__(self, fh, _request, chunk=b"x" * 32, chunks=4):
        self._fh = fh
        self._chunk = chunk
        self._remaining = chunks
        self.calls = 0

    def next_chunk(self):
        self.calls += 1
        self._fh.write(self._chunk)
        self._remaining -= 1
        return None, self._remaining <= 0


def _drive_service(mime_type="text/plain"):
    service = Mock()
    service.files().get().execute.return_value = {
        "mimeType": mime_type,
        "name": "doc.txt",
        "webViewLink": "https://drive.example/doc",
    }
    return service


@pytest.mark.asyncio
async def test_download_is_abandoned_at_the_limit(monkeypatch):
    monkeypatch.setattr(docs_tools, "MAX_DOC_CONTENT_BYTES", 64)
    created = {}

    def _factory(fh, request):
        created["downloader"] = _ChunkedDownloader(
            fh, request, chunk=b"x" * 32, chunks=100
        )
        return created["downloader"]

    monkeypatch.setattr(docs_tools, "MediaIoBaseDownload", _factory)

    with pytest.raises(ValueError, match="exceeds the 64 byte limit"):
        await get_doc_content(
            drive_service=_drive_service(),
            docs_service=Mock(),
            user_google_email="user@example.com",
            document_id="doc-1",
        )

    # Stopped early instead of reading all 100 chunks.
    assert created["downloader"].calls == 3


@pytest.mark.asyncio
async def test_content_within_the_limit_is_returned(monkeypatch):
    monkeypatch.setattr(docs_tools, "MAX_DOC_CONTENT_BYTES", 1024)

    def _factory(fh, request):
        return _ChunkedDownloader(fh, request, chunk=b"hello ", chunks=2)

    monkeypatch.setattr(docs_tools, "MediaIoBaseDownload", _factory)

    result = await get_doc_content(
        drive_service=_drive_service(),
        docs_service=Mock(),
        user_google_email="user@example.com",
        document_id="doc-1",
    )

    assert "hello hello" in result
