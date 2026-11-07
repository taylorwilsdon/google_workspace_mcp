"""Env-gated limits for in-memory Google file / attachment downloads.

``WORKSPACE_MCP_MAX_FILE_BYTES`` caps how many bytes a tool may buffer into
process memory (Drive MediaIo downloads, Gmail attachments, etc.).

Default is disabled (``0`` / unset) so existing deployments keep uncapped
behavior. Set a positive integer (e.g. ``5242880`` for 5 MiB) to enable.

``WORKSPACE_MCP_MAX_OFFICE_XML_BYTES`` independently caps both the XML expanded
from one Office file and the text extracted from it. It is on by default;
``0`` disables it.

``WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES`` caps the file a stateless-mode
download returns inline. It defaults to 10 MiB and never exceeds
``WORKSPACE_MCP_MAX_FILE_BYTES``.

``WORKSPACE_MCP_MAX_CONTENT_CHARS`` sets the default page size of the text
``get_drive_file_content`` returns; the rest is available by calling again with
an offset, and a client can pass ``full_content=True`` for all of it in one
call. It defaults to 40,000 characters; ``0`` returns whole content by default.
"""

from __future__ import annotations

import asyncio
import io
import os
import sys
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any, Optional

import httpx
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

_ENV_NAME = "WORKSPACE_MCP_MAX_FILE_BYTES"
_OFFICE_XML_ENV_NAME = "WORKSPACE_MCP_MAX_OFFICE_XML_BYTES"
_STATELESS_INLINE_ENV_NAME = "WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES"
_CONTENT_CHARS_ENV_NAME = "WORKSPACE_MCP_MAX_CONTENT_CHARS"
_PAGED_CONTENT_CACHE_ENV_NAME = "WORKSPACE_MCP_PAGED_CONTENT_CACHE_BYTES"

# Office files are ZIP archives, so the download cap above bounds only the
# COMPRESSED size, and XML compresses by orders of magnitude. Unlike the
# download cap this one is on by default: the download cap is opt-in to protect
# deployments that need large downloads, and nothing comparable depends on
# unbounded expansion.
#
# This counts expanded XML bytes, not memory. The parsed tree is much larger:
# measured with tracemalloc on 1 MiB of XML, peak allocation was about 14x the
# XML size for paragraphs of short text and about 30x for empty elements. The
# default therefore allows a worst case in the hundreds of MiB per extraction;
# small containers should set it lower.
DEFAULT_MAX_OFFICE_XML_BYTES = 25 * 1024 * 1024  # 25 MiB

# Base64 inflates an inline payload by a third, so this is ~13.4 MiB on the wire.
DEFAULT_STATELESS_INLINE_MAX_BYTES = 10 * 1024 * 1024  # 10 MiB

DEFAULT_MAX_CONTENT_CHARS = 40_000

DEFAULT_PAGED_CONTENT_CACHE_BYTES = 32 * 1024 * 1024  # 32 MiB

# Keep the uncapped path from asking httplib2 to materialize its 100 MiB
# default response chunk. This does not impose a total-size limit; it only
# bounds the transient allocation made by each MediaIoBaseDownload request.
_MEDIA_DOWNLOAD_CHUNK_SIZE_BYTES = 256 * 1024  # 256 KiB

# Stream read size when a byte cap is active. Keep small so we can abort
# near the ceiling even when Content-Length is absent.
_STREAM_READ_SIZE_BYTES = 64 * 1024  # 64 KiB

# Error bodies are diagnostic data, not file payloads. They must still be
# bounded or an upstream/proxy error can bypass the configured memory guard.
_MAX_ERROR_BODY_BYTES = 64 * 1024  # 64 KiB


class FileTooLargeError(ValueError):
    """Raised when a download would exceed ``WORKSPACE_MCP_MAX_FILE_BYTES``."""


def get_max_file_bytes() -> Optional[int]:
    """Return the configured max download size in bytes, or ``None`` if uncapped.

    Parsing rules:
    - unset, empty, or ``0`` → uncapped (``None``)
    - positive int → that many bytes
    - invalid or negative value → raise ``ValueError``
    """
    value = _byte_count_from_env(_ENV_NAME)
    return value or None


def get_max_office_xml_bytes() -> Optional[int]:
    """Return the cap on bytes expanded out of one Office file, or ``None``.

    Parsing rules:
    - unset or empty → ``DEFAULT_MAX_OFFICE_XML_BYTES``
    - ``0`` → uncapped (``None``)
    - positive int → that many bytes
    - invalid or negative value → raise ``ValueError``
    """
    value = _byte_count_from_env(_OFFICE_XML_ENV_NAME)
    if value is None:
        return DEFAULT_MAX_OFFICE_XML_BYTES
    return value or None


def get_stateless_inline_max_bytes() -> int:
    """Return the largest file a stateless-mode download may return inline.

    Parsing rules:
    - unset or empty → ``DEFAULT_STATELESS_INLINE_MAX_BYTES``
    - ``0`` → nothing is inlined
    - positive int → that many bytes
    - invalid or negative value → raise ``ValueError``

    Inlining holds the whole file in memory, so ``WORKSPACE_MCP_MAX_FILE_BYTES``
    lowers this when set.
    """
    value = _byte_count_from_env(_STATELESS_INLINE_ENV_NAME)
    if value is None:
        value = DEFAULT_STATELESS_INLINE_MAX_BYTES
    max_file_bytes = get_max_file_bytes()
    return min(value, max_file_bytes) if max_file_bytes else value


def get_max_content_chars() -> Optional[int]:
    """Return the default page size for content reads in characters, or ``None``
    for whole content.

    Parsing rules:
    - unset or empty → ``DEFAULT_MAX_CONTENT_CHARS``
    - ``0`` → whole content (``None``)
    - positive int → that many characters
    - invalid or negative value → raise ``ValueError``
    """
    value = _byte_count_from_env(_CONTENT_CHARS_ENV_NAME, unit="character")
    if value is None:
        return DEFAULT_MAX_CONTENT_CHARS
    return value or None


def get_paged_content_cache_bytes() -> int:
    """Return the memory paged content may be kept in between calls.

    Parsing rules:
    - unset or empty → ``DEFAULT_PAGED_CONTENT_CACHE_BYTES``
    - ``0`` → nothing is kept
    - positive int → that many bytes
    - invalid or negative value → raise ``ValueError``
    """
    value = _byte_count_from_env(_PAGED_CONTENT_CACHE_ENV_NAME)
    return DEFAULT_PAGED_CONTENT_CACHE_BYTES if value is None else value


def validate_file_limit_settings() -> None:
    """Raise ``ValueError`` if any file-limit environment setting is invalid."""
    get_max_file_bytes()
    get_max_office_xml_bytes()
    get_stateless_inline_max_bytes()
    get_max_content_chars()
    get_paged_content_cache_bytes()


def _byte_count_from_env(name: str, *, unit: str = "byte") -> Optional[int]:
    """Parse a non-negative count from ``name``; ``None`` when unset or empty."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"Invalid {name}={raw!r}; expected a non-negative integer {unit} count."
        ) from exc
    if value < 0:
        raise ValueError(
            f"Invalid {name}={raw!r}; expected a non-negative integer {unit} count."
        )
    return value


def page_content_text(
    text: str,
    *,
    offset: int = 0,
    max_chars: Optional[int] = None,
    alternative: str = "",
) -> str:
    """Return up to ``max_chars`` of ``text`` from ``offset``, plus a read-on note
    with the next offset when more follows. ``None`` means no limit."""
    total = len(text)
    if offset > total:
        return (
            f"[offset={offset:,} is past the end of the content "
            f"({total:,} characters).]"
        )
    if max_chars is None or total - offset <= max_chars:
        return text[offset:]

    page = text[offset : offset + max_chars]
    line_end = page.rfind("\n") + 1
    if line_end >= max_chars / 2:
        page = page[:line_end]
    end = offset + len(page)
    return (
        f"{page}\n\n[Truncated: showing characters {offset:,} to {end:,} of "
        f"{total:,}; {total - end:,} more follow (default page size "
        f"{max_chars:,}, set by {_CONTENT_CHARS_ENV_NAME}). Call again with offset={end} to "
        f"read on{alternative}.]"
    )


class PagedContentCache:
    """TTL and LRU cache of paged text, bounded by ``max_bytes`` of memory.

    A single entry may take at most a quarter of ``max_bytes``.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, str, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key: Hashable) -> Optional[str]:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, text, _ = entry
        if self._clock() - stored_at > self._ttl_seconds:
            self._drop(key)
            return None
        self._entries.move_to_end(key)
        return text

    def put(self, key: Hashable, text: str, *, max_bytes: int) -> None:
        now = self._clock()
        for stale in [
            k for k, (t, _, _) in self._entries.items() if now - t > self._ttl_seconds
        ]:
            self._drop(stale)
        if key in self._entries:
            self._drop(key)
        size = sys.getsizeof(text)
        if size > max_bytes // 4:
            return
        self._entries[key] = (now, text, size)
        self._bytes += size
        while self._bytes > max_bytes:
            self._drop(next(iter(self._entries)))

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def _drop(self, key: Hashable) -> None:
        _, _, size = self._entries.pop(key)
        self._bytes -= size


def format_file_too_large_message(
    *,
    size_bytes: int,
    max_bytes: int,
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
) -> str:
    """Build an agent-friendly error that points at alternatives, not file surgery."""
    label = f'"{file_name}"' if file_name else f"this {kind}"
    id_part = f" (ID: {file_id})" if file_id else ""
    link_part = ""
    if web_view_link and web_view_link != "#":
        link_part = f"\nOpen in Google Drive: {web_view_link}"

    return (
        f"Error: {label}{id_part} is too large to load into this MCP server "
        f"({size_bytes:,} bytes; limit is {max_bytes:,} bytes via {_ENV_NAME}).\n"
        f"Full binary download through this tool is not available for oversized "
        f"{kind}s.{link_part}\n"
        "Alternatives:\n"
        "- Use get_doc_content / get_doc_as_markdown for Google Docs\n"
        "- Use read_sheet_values for Google Sheets\n"
        "- Use get_drive_file_content for text-oriented exports when under the limit\n"
        "- Open the Drive link above for large binaries (video, zip, large PDF, etc.)"
    )


def ensure_within_file_size_limit(
    size_bytes: Optional[int],
    *,
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
    max_bytes: Optional[int] = None,
) -> None:
    """Raise ``FileTooLargeError`` if a declared size exceeds the configured cap."""
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    if limit is None or size_bytes is None:
        return
    try:
        declared = int(size_bytes)
    except (TypeError, ValueError):
        return
    if declared > limit:
        raise FileTooLargeError(
            format_file_too_large_message(
                size_bytes=declared,
                max_bytes=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )
        )


def _raise_too_large(
    *,
    size_bytes: int,
    max_bytes: int,
    file_name: Optional[str],
    file_id: Optional[str],
    web_view_link: Optional[str],
    kind: str,
) -> None:
    raise FileTooLargeError(
        format_file_too_large_message(
            size_bytes=size_bytes,
            max_bytes=max_bytes,
            file_name=file_name,
            file_id=file_id,
            web_view_link=web_view_link,
            kind=kind,
        )
    )


def _authorize_headers(
    request_obj: Any, *, force_refresh: bool = False
) -> dict[str, str]:
    """Copy request headers and authorize them like ``AuthorizedHttp`` does."""
    headers = dict(getattr(request_obj, "headers", None) or {})
    http = getattr(request_obj, "http", None)
    credentials = getattr(http, "credentials", None) if http is not None else None
    if credentials is None:
        return headers

    # Reuse AuthorizedHttp's refresh transport when available. A fallback
    # google-auth Request owns a requests.Session, so close it deterministically.
    auth_request = getattr(http, "_request", None)
    owned_auth_request = auth_request is None
    if owned_auth_request:
        auth_request = GoogleAuthRequest()
    try:
        if force_refresh:
            credentials.refresh(auth_request)
        method = (getattr(request_obj, "method", None) or "GET").upper()
        uri = getattr(request_obj, "uri", None) or ""
        credentials.before_request(auth_request, method, uri, headers)
    finally:
        if owned_auth_request:
            auth_request.session.close()
    return headers


async def _download_media_bytes_uncapped(request_obj: Any) -> bytes:
    """Download via MediaIoBaseDownload (full response may be buffered per chunk)."""
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(
        fh, request_obj, chunksize=_MEDIA_DOWNLOAD_CHUNK_SIZE_BYTES
    )
    done = False
    while not done:
        _status, done = await asyncio.to_thread(downloader.next_chunk)
    return fh.getvalue()


async def _accumulate_capped_stream(
    resp: httpx.Response,
    *,
    limit: int,
    file_name: Optional[str],
    file_id: Optional[str],
    web_view_link: Optional[str],
    kind: str,
) -> bytes:
    """Read an open streamed response, aborting once past ``limit``."""
    content_encoding = resp.headers.get("content-encoding", "").strip().lower()
    content_length = resp.headers.get("content-length")
    # Content-Length describes the encoded wire representation, whereas
    # aiter_bytes() yields decoded bytes. It is only comparable to the file-byte
    # limit for identity responses. Capped requests ask for identity below, but
    # keep this guard for non-compliant intermediaries.
    if content_length is not None and content_encoding in {"", "identity"}:
        try:
            declared = int(content_length)
        except ValueError:
            declared = None
        if declared is not None and declared > limit:
            # Close without reading the body into process memory.
            _raise_too_large(
                size_bytes=declared,
                max_bytes=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )

    read_size = max(1, min(_STREAM_READ_SIZE_BYTES, limit))
    # BytesIO.getvalue() exposes its immutable backing bytes without the
    # bytearray -> bytes full-size copy that previously doubled peak memory at
    # successful completion.
    buf = io.BytesIO()
    buffered_size = 0
    async for chunk in resp.aiter_bytes(chunk_size=read_size):
        if not chunk:
            continue
        next_size = buffered_size + len(chunk)
        if next_size > limit:
            _raise_too_large(
                size_bytes=next_size,
                max_bytes=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )
        buf.write(chunk)
        buffered_size = next_size
    return buf.getvalue()


def _http_status_error(
    resp: httpx.Response, *, content: Optional[bytes] = None
) -> httpx.HTTPStatusError:
    """Build an HTTPStatusError for a completed (possibly streamed) response."""
    if content is not None:
        # A streaming response does not expose .content until it has been read
        # completely. Build a small completed response containing only the
        # bounded diagnostic prefix so downstream error conversion stays safe.
        resp = httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=content,
            request=resp.request,
            extensions=resp.extensions,
        )
    return httpx.HTTPStatusError(
        f"Client error '{resp.status_code} {resp.reason_phrase}' for url '{resp.request.url}'",
        request=resp.request,
        response=resp,
    )


async def _read_bounded_error_body(resp: httpx.Response, *, limit: int) -> bytes:
    """Read at most ``limit`` decoded bytes from an HTTP error response."""
    body = bytearray()
    async for chunk in resp.aiter_bytes(
        chunk_size=max(1, min(_STREAM_READ_SIZE_BYTES, limit))
    ):
        remaining = limit - len(body)
        if remaining <= 0:
            break
        body.extend(chunk[:remaining])
        if len(body) >= limit:
            break
    return bytes(body)


async def download_http_url_bytes(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    method: str = "GET",
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
    max_bytes: Optional[int] = None,
) -> bytes:
    """Download ``url`` into memory, honoring ``WORKSPACE_MCP_MAX_FILE_BYTES``.

    When a cap is set, streams and aborts on Content-Length or mid-body.
    Raises ``FileTooLargeError`` when over the limit, or
    ``httpx.HTTPStatusError`` for non-2xx responses.
    """
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    req_headers = dict(headers or {})
    timeout = httpx.Timeout(120.0, connect=30.0)
    method = method.upper()

    if limit is not None:
        # googleapiclient media requests advertise gzip by default. httpx then
        # reports the compressed Content-Length but yields decompressed chunks,
        # making the two size checks use different units and allowing the
        # decoder to allocate far beyond the cap before a chunk is yielded.
        req_headers = {
            key: value
            for key, value in req_headers.items()
            if key.lower() != "accept-encoding"
        }
        req_headers["Accept-Encoding"] = "identity"

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if limit is None:
            resp = await client.request(method, url, headers=req_headers)
            if resp.status_code >= 400:
                raise _http_status_error(resp)
            return resp.content

        async with client.stream(method, url, headers=req_headers) as resp:
            if resp.status_code >= 400:
                error_limit = max(1, min(_MAX_ERROR_BODY_BYTES, limit))
                error_body = await _read_bounded_error_body(resp, limit=error_limit)
                raise _http_status_error(resp, content=error_body)
            return await _accumulate_capped_stream(
                resp,
                limit=limit,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
            )


async def _download_media_bytes_capped(
    request_obj: Any,
    *,
    limit: int,
    file_name: Optional[str],
    file_id: Optional[str],
    web_view_link: Optional[str],
    kind: str,
) -> bytes:
    """Stream a media URL and abort before buffering past ``limit``.

    ``MediaIoBaseDownload`` cannot enforce this for Drive ``export_media``:
    exports often ignore ``Range`` and return HTTP 200 with the entire body,
    which httplib2 buffers into ``content`` before any size check runs.
    """
    uri = getattr(request_obj, "uri", None)
    if not uri:
        raise ValueError("media request is missing uri")

    method = (getattr(request_obj, "method", None) or "GET").upper()
    headers = await asyncio.to_thread(_authorize_headers, request_obj)
    try:
        try:
            return await download_http_url_bytes(
                uri,
                headers=headers,
                method=method,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
                max_bytes=limit,
            )
        except httpx.HTTPStatusError as exc:
            # Match google-auth's AuthorizedHttp behavior: a token can be
            # rejected after it passed the local expiry check, so refresh and
            # retry exactly once on 401 before surfacing an auth failure.
            http = getattr(request_obj, "http", None)
            credentials = (
                getattr(http, "credentials", None) if http is not None else None
            )
            if exc.response.status_code != 401 or credentials is None:
                raise
            headers = await asyncio.to_thread(
                _authorize_headers, request_obj, force_refresh=True
            )
            return await download_http_url_bytes(
                uri,
                headers=headers,
                method=method,
                file_name=file_name,
                file_id=file_id,
                web_view_link=web_view_link,
                kind=kind,
                max_bytes=limit,
            )
    except httpx.HTTPStatusError as exc:
        body = exc.response.content or b""
        httplib2_resp = type(
            "Response",
            (),
            {
                "status": exc.response.status_code,
                "reason": exc.response.reason_phrase,
            },
        )()
        raise HttpError(httplib2_resp, body, uri=uri) from exc


async def download_media_bytes(
    request_obj: Any,
    *,
    file_name: Optional[str] = None,
    file_id: Optional[str] = None,
    web_view_link: Optional[str] = None,
    kind: str = "file",
    max_bytes: Optional[int] = None,
) -> bytes:
    """Download a Drive ``get_media`` / ``export_media`` request into memory.

    When ``WORKSPACE_MCP_MAX_FILE_BYTES`` is set, streams the response and
    aborts as soon as Content-Length or buffered bytes exceed the cap — so
    Google Docs PDF exports (which ignore Range) cannot fill the cgroup.
    """
    limit = get_max_file_bytes() if max_bytes is None else max_bytes
    if limit is None:
        return await _download_media_bytes_uncapped(request_obj)
    return await _download_media_bytes_capped(
        request_obj,
        limit=limit,
        file_name=file_name,
        file_id=file_id,
        web_view_link=web_view_link,
        kind=kind,
    )
