"""Tests for WORKSPACE_MCP_MAX_FILE_BYTES and download_media_bytes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from core.file_limits import (
    DEFAULT_GMAIL_BODY_MAX_CHARS,
    DEFAULT_MAX_OFFICE_XML_BYTES,
    DEFAULT_STATELESS_INLINE_MAX_BYTES,
    FileTooLargeError,
    download_http_url_bytes,
    download_media_bytes,
    ensure_within_file_size_limit,
    get_gmail_body_max_chars,
    get_max_file_bytes,
    get_max_office_xml_bytes,
    get_stateless_inline_max_bytes,
    validate_file_limit_settings,
)


class _FakeDownloader:
    """Writes all bytes on init; next_chunk reports done immediately."""

    def __init__(self, fh, _request, data: bytes, chunksize=None):
        fh.write(data)
        fh.seek(0)
        self._fh = fh
        self.chunksize = chunksize

    def next_chunk(self):
        return None, True


def test_get_max_file_bytes_default_uncapped(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    assert get_max_file_bytes() is None


def test_get_max_file_bytes_zero_uncapped(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "0")
    assert get_max_file_bytes() is None


def test_get_max_file_bytes_positive(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "5242880")
    assert get_max_file_bytes() == 5242880


def test_get_max_file_bytes_invalid_fails_closed(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "nope")
    with pytest.raises(ValueError, match="WORKSPACE_MCP_MAX_FILE_BYTES"):
        get_max_file_bytes()


def test_get_max_file_bytes_negative_fails_closed(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "-1")
    with pytest.raises(ValueError, match="WORKSPACE_MCP_MAX_FILE_BYTES"):
        get_max_file_bytes()


def test_ensure_within_file_size_limit_noop_when_uncapped(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    ensure_within_file_size_limit(10**12, file_name="huge.bin")


def test_ensure_within_file_size_limit_raises(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    with pytest.raises(FileTooLargeError) as exc:
        ensure_within_file_size_limit(
            101,
            file_name="huge.bin",
            file_id="abc",
            web_view_link="https://drive.google.com/file/d/abc",
        )
    msg = str(exc.value)
    assert "101" in msg
    assert "100" in msg
    assert "huge.bin" in msg
    assert "https://drive.google.com/file/d/abc" in msg
    assert "get_doc_content" in msg


@pytest.mark.asyncio
async def test_download_media_bytes_uncapped(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    data = b"hello-world"
    observed_chunksizes = []

    def make_downloader(fh, req, chunksize=None):
        observed_chunksizes.append(chunksize)
        return _FakeDownloader(fh, req, data, chunksize=chunksize)

    with patch(
        "core.file_limits.MediaIoBaseDownload",
        side_effect=make_downloader,
    ):
        result = await download_media_bytes(Mock())
    assert result == data
    assert observed_chunksizes == [256 * 1024]


def _mock_stream_response(
    *,
    body: bytes = b"",
    status_code: int = 200,
    headers: dict | None = None,
):
    """Build an async context-manager stream response for httpx.AsyncClient."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason_phrase = "OK" if status_code < 400 else "Error"
    resp.headers = httpx.Headers(headers or {})

    async def _aiter_bytes(chunk_size=65536):
        for i in range(0, len(body), chunk_size):
            yield body[i : i + chunk_size]

    async def _aread():
        return body

    resp.aiter_bytes = _aiter_bytes
    resp.aread = _aread

    stream_cm = AsyncMock()
    stream_cm.__aenter__.return_value = resp
    stream_cm.__aexit__.return_value = None

    client = MagicMock()
    client.stream.return_value = stream_cm

    client_cm = AsyncMock()
    client_cm.__aenter__.return_value = client
    client_cm.__aexit__.return_value = None
    return client_cm, resp


@pytest.mark.asyncio
async def test_download_media_bytes_rejects_via_content_length(monkeypatch):
    """Drive export often sends Content-Length and ignores Range — reject before body."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    request = Mock(
        uri="https://www.googleapis.com/drive/v3/files/x/export", method="GET"
    )
    request.headers = {}
    request.http = Mock(credentials=None)

    client_cm, _resp = _mock_stream_response(
        body=b"x" * 5000,  # would be large if read
        headers={"content-length": "276690"},
    )

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        with pytest.raises(FileTooLargeError) as exc:
            await download_media_bytes(request, file_name="CV_Oleg_Kulyk", file_id="f1")

    assert "276,690" in str(exc.value) or "276690" in str(exc.value)
    assert "CV_Oleg_Kulyk" in str(exc.value)
    # Body must not have been consumed when Content-Length rejects.
    # aiter_bytes is never awaited if we raise first — verify stream entered.
    client_cm.__aenter__.assert_awaited()


@pytest.mark.asyncio
async def test_download_media_bytes_aborts_mid_stream(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "15")
    request = Mock(
        uri="https://www.googleapis.com/drive/v3/files/x?alt=media", method="GET"
    )
    request.headers = {}
    request.http = Mock(credentials=None)

    # No Content-Length: must abort while streaming.
    client_cm, _resp = _mock_stream_response(body=b"a" * 40)

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        with pytest.raises(FileTooLargeError) as exc:
            await download_media_bytes(request, file_name="stream.bin")

    assert "stream.bin" in str(exc.value)


@pytest.mark.asyncio
async def test_download_media_bytes_capped_success(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    request = Mock(
        uri="https://www.googleapis.com/drive/v3/files/x?alt=media", method="GET"
    )
    request.headers = {}
    request.http = Mock(credentials=None)

    data = b"ok-payload"
    client_cm, _resp = _mock_stream_response(
        body=data, headers={"content-length": str(len(data))}
    )

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        result = await download_media_bytes(request)

    assert result == data
    sent_headers = client_cm.__aenter__.return_value.stream.call_args.kwargs["headers"]
    assert sent_headers["Accept-Encoding"] == "identity"
    assert sum(key.lower() == "accept-encoding" for key in sent_headers) == 1


@pytest.mark.asyncio
async def test_capped_http_error_body_is_bounded(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "32")
    client_cm, resp = _mock_stream_response(body=b"x" * 1000, status_code=500)
    resp.request = httpx.Request("GET", "https://example.test/media")
    resp.extensions = {}
    resp.aread = AsyncMock(side_effect=resp.aread)

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await download_http_url_bytes("https://example.test/media")

    assert exc.value.response.content == b"x" * 32
    resp.aread.assert_not_awaited()


@pytest.mark.asyncio
async def test_capped_media_refreshes_and_retries_once_on_401(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    credentials = Mock()

    def authorize(_request, _method, _uri, headers):
        headers["Authorization"] = "Bearer refreshed"

    credentials.before_request.side_effect = authorize
    request = Mock(uri="https://example.test/media", method="GET", headers={})
    request.http = Mock(credentials=credentials)

    first_client, first_resp = _mock_stream_response(
        body=b"unauthorized", status_code=401
    )
    first_resp.request = httpx.Request("GET", request.uri)
    first_resp.extensions = {}
    second_client, _second_resp = _mock_stream_response(
        body=b"ok", headers={"content-length": "2"}
    )

    with patch(
        "core.file_limits.httpx.AsyncClient",
        side_effect=[first_client, second_client],
    ):
        result = await download_media_bytes(request)

    assert result == b"ok"
    credentials.refresh.assert_called_once()
    assert credentials.before_request.call_count == 2


@pytest.mark.asyncio
async def test_encoded_content_length_is_not_compared_to_decoded_limit(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "10")
    request = Mock(uri="https://example/media", method="GET")
    request.headers = {"accept-encoding": "gzip, deflate"}
    request.http = Mock(credentials=None)

    # A gzip wire representation can be larger than a small decoded file.
    client_cm, _resp = _mock_stream_response(
        body=b"ok",
        headers={"content-length": "22", "content-encoding": "gzip"},
    )

    with patch("core.file_limits.httpx.AsyncClient", return_value=client_cm):
        result = await download_media_bytes(request)

    assert result == b"ok"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_MAX_OFFICE_XML_BYTES),
        ("", DEFAULT_MAX_OFFICE_XML_BYTES),
        ("0", None),
        (" 4096 ", 4096),
    ],
)
def test_get_max_office_xml_bytes(monkeypatch, raw, expected):
    """Unlike the download cap, unset means the default limit, not no limit."""
    if raw is None:
        monkeypatch.delenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", raising=False)
    else:
        monkeypatch.setenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", raw)
    assert get_max_office_xml_bytes() == expected


@pytest.mark.parametrize("raw", ["-1", "ten", "1.5"])
def test_get_max_office_xml_bytes_rejects_invalid(monkeypatch, raw):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", raw)
    with pytest.raises(ValueError, match="WORKSPACE_MCP_MAX_OFFICE_XML_BYTES"):
        get_max_office_xml_bytes()


@pytest.mark.parametrize(
    ("inline", "file_cap", "expected"),
    [
        (None, None, DEFAULT_STATELESS_INLINE_MAX_BYTES),
        ("", "", DEFAULT_STATELESS_INLINE_MAX_BYTES),
        ("0", None, 0),
        (" 4096 ", "0", 4096),
        ("4096", "100", 100),
        (None, "100", 100),
    ],
)
def test_get_stateless_inline_max_bytes(monkeypatch, inline, file_cap, expected):
    """Inlining buffers the file, so the in-memory download cap bounds it too."""
    for name, raw in (
        ("WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES", inline),
        ("WORKSPACE_MCP_MAX_FILE_BYTES", file_cap),
    ):
        if raw is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, raw)
    assert get_stateless_inline_max_bytes() == expected


@pytest.mark.parametrize("raw", ["-1", "10MB"])
def test_get_stateless_inline_max_bytes_rejects_invalid(monkeypatch, raw):
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES", raw)
    with pytest.raises(ValueError, match="WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES"):
        get_stateless_inline_max_bytes()
    with pytest.raises(ValueError, match="WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES"):
        validate_file_limit_settings()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_GMAIL_BODY_MAX_CHARS),
        ("", DEFAULT_GMAIL_BODY_MAX_CHARS),
        ("0", None),
        (" 50000 ", 50000),
    ],
)
def test_get_gmail_body_max_chars(monkeypatch, raw, expected):
    """Unset keeps the historical 20,000-character cap; 0 disables truncation."""
    if raw is None:
        monkeypatch.delenv("WORKSPACE_MCP_GMAIL_BODY_MAX_CHARS", raising=False)
    else:
        monkeypatch.setenv("WORKSPACE_MCP_GMAIL_BODY_MAX_CHARS", raw)
    assert DEFAULT_GMAIL_BODY_MAX_CHARS == 20000
    assert get_gmail_body_max_chars() == expected


@pytest.mark.parametrize("raw", ["-1", "ten", "1.5", "20k"])
def test_get_gmail_body_max_chars_rejects_invalid(monkeypatch, raw):
    monkeypatch.setenv("WORKSPACE_MCP_GMAIL_BODY_MAX_CHARS", raw)
    with pytest.raises(ValueError, match="WORKSPACE_MCP_GMAIL_BODY_MAX_CHARS"):
        get_gmail_body_max_chars()
    with pytest.raises(ValueError, match="WORKSPACE_MCP_GMAIL_BODY_MAX_CHARS"):
        validate_file_limit_settings()
