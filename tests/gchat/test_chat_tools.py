"""
Unit tests for Google Chat MCP tools — attachment support
"""

import asyncio
import base64
import inspect
import ssl
from urllib.parse import urlparse

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))


def _make_message(text="Hello", attachments=None, msg_name="spaces/S/messages/M"):
    """Build a minimal Chat API message dict for testing."""
    msg = {
        "name": msg_name,
        "text": text,
        "createTime": "2025-01-01T00:00:00Z",
        "sender": {"name": "users/123", "displayName": "Test User"},
    }
    if attachments is not None:
        msg["attachment"] = attachments
    return msg


def _make_attachment(
    name="spaces/S/messages/M/attachments/A",
    content_name="image.png",
    content_type="image/png",
    resource_name="spaces/S/attachments/A",
):
    att = {
        "name": name,
        "contentName": content_name,
        "contentType": content_type,
        "source": "UPLOADED_CONTENT",
    }
    if resource_name:
        att["attachmentDataRef"] = {"resourceName": resource_name}
    return att


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original async function."""
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# ---------------------------------------------------------------------------
# get_messages: attachment metadata appears in output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_get_messages_shows_attachment_metadata(mock_resolve):
    """When a message has attachments, get_messages should surface their metadata."""
    mock_resolve.return_value = "Test User"

    att = _make_attachment()
    msg = _make_message(attachments=[att])

    chat_service = Mock()
    chat_service.spaces().get().execute.return_value = {"displayName": "Test Space"}
    chat_service.spaces().messages().list().execute.return_value = {"messages": [msg]}

    people_service = Mock()

    from gchat.chat_tools import get_messages

    result = await _unwrap(get_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        space_id="spaces/S",
    )

    assert "[attachment 0: image.png (image/png)]" in result
    assert "download_chat_attachment" in result


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_get_messages_no_attachments_unchanged(mock_resolve):
    """Messages without attachments should not include attachment lines."""
    mock_resolve.return_value = "Test User"

    msg = _make_message(text="Plain text message")

    chat_service = Mock()
    chat_service.spaces().get().execute.return_value = {"displayName": "Test Space"}
    chat_service.spaces().messages().list().execute.return_value = {"messages": [msg]}

    people_service = Mock()

    from gchat.chat_tools import get_messages

    result = await _unwrap(get_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        space_id="spaces/S",
    )

    assert "Plain text message" in result
    assert "[attachment" not in result


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_get_messages_multiple_attachments(mock_resolve):
    """Multiple attachments should each appear with their index."""
    mock_resolve.return_value = "Test User"

    attachments = [
        _make_attachment(content_name="photo.jpg", content_type="image/jpeg"),
        _make_attachment(
            name="spaces/S/messages/M/attachments/B",
            content_name="doc.pdf",
            content_type="application/pdf",
        ),
    ]
    msg = _make_message(attachments=attachments)

    chat_service = Mock()
    chat_service.spaces().get().execute.return_value = {"displayName": "Test Space"}
    chat_service.spaces().messages().list().execute.return_value = {"messages": [msg]}

    people_service = Mock()

    from gchat.chat_tools import get_messages

    result = await _unwrap(get_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        space_id="spaces/S",
    )

    assert "[attachment 0: photo.jpg (image/jpeg)]" in result
    assert "[attachment 1: doc.pdf (application/pdf)]" in result


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_get_messages_exposes_message_filter_and_forwards_it(mock_resolve):
    """get_messages should expose message_filter publicly and pass it to the Chat API."""
    mock_resolve.return_value = "Test User"

    msg = _make_message(text="Filtered message")
    chat_service = Mock()
    chat_service.spaces().get().execute.return_value = {"displayName": "Test Space"}
    chat_service.spaces().messages().list().execute.return_value = {"messages": [msg]}
    people_service = Mock()

    from gchat.chat_tools import get_messages

    public_fn = getattr(get_messages, "fn", get_messages)
    params = inspect.signature(public_fn).parameters

    assert "message_filter" in params
    assert "filter" not in params

    result = await _unwrap(get_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        space_id="spaces/S",
        message_filter="thread.name = spaces/S/threads/T",
    )

    assert "Filtered message" in result
    list_kwargs = chat_service.spaces().messages().list.call_args.kwargs
    assert list_kwargs["filter"] == "thread.name = spaces/S/threads/T"


@pytest.mark.asyncio
async def test_get_messages_resolves_senders_sequentially(monkeypatch):
    """get_messages should avoid concurrent People API sender resolution."""
    state = {"current": 0, "max": 0}

    async def fake_resolve(_people_service, sender_obj):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        await asyncio.sleep(0.01)
        try:
            return f"Resolved {sender_obj['name']}"
        finally:
            state["current"] -= 1

    msg_one = _make_message(text="First message", msg_name="spaces/S/messages/M1")
    msg_one["sender"] = {"name": "users/1"}
    msg_two = _make_message(text="Second message", msg_name="spaces/S/messages/M2")
    msg_two["sender"] = {"name": "users/2"}

    chat_service = Mock()
    chat_service.spaces().get().execute.return_value = {"displayName": "Test Space"}
    chat_service.spaces().messages().list().execute.return_value = {
        "messages": [msg_one, msg_two]
    }
    people_service = Mock()

    monkeypatch.setattr("gchat.chat_tools._resolve_sender", fake_resolve)

    from gchat.chat_tools import get_messages

    result = await _unwrap(get_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        space_id="spaces/S",
    )

    assert "Resolved users/1" in result
    assert "Resolved users/2" in result
    assert state["max"] == 1


# ---------------------------------------------------------------------------
# search_messages: attachment indicator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_search_messages_shows_attachment_indicator(mock_resolve):
    """search_messages should show [attachment: filename] for messages with attachments."""
    mock_resolve.return_value = "Test User"

    att = _make_attachment(content_name="report.pdf", content_type="application/pdf")
    msg = _make_message(text="Here is the report", attachments=[att])
    msg["_space_name"] = "General"

    chat_service = Mock()
    chat_service.spaces().list().execute.return_value = {
        "spaces": [{"name": "spaces/S", "displayName": "General"}]
    }
    chat_service.spaces().messages().list().execute.return_value = {"messages": [msg]}

    people_service = Mock()

    from gchat.chat_tools import search_messages

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        query="report",
    )

    assert "[attachment: report.pdf (application/pdf)]" in result


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_search_messages_combines_filters_and_uses_page_size(mock_resolve):
    """Cross-space search should honor page_size and only send supported API filters."""
    mock_resolve.return_value = "Test User"

    msg = _make_message(text="Deploy finished")
    msg["_space_name"] = "General"

    chat_service = Mock()
    chat_service.spaces().list().execute.return_value = {
        "spaces": [{"name": "spaces/S", "displayName": "General"}]
    }
    chat_service.spaces().messages().list().execute.return_value = {"messages": [msg]}
    people_service = Mock()

    from gchat.chat_tools import search_messages

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        query="deploy",
        time_filter='createTime > "2026-03-18T00:00:00-03:00"',
        page_size=7,
    )

    assert 'text "deploy" and createTime > "2026-03-18T00:00:00-03:00"' in result
    list_kwargs = chat_service.spaces().messages().list.call_args.kwargs
    assert list_kwargs["pageSize"] == 7
    assert list_kwargs["filter"] == 'createTime > "2026-03-18T00:00:00-03:00"'


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_search_messages_query_only_filters_client_side_without_api_filter(
    mock_resolve,
):
    """Query-only search should avoid unsupported Chat API text filters."""
    mock_resolve.return_value = "Test User"

    matching = _make_message(text="Deploy finished")
    matching["_space_name"] = "General"
    non_matching = _make_message(text="Lunch plans")
    non_matching["_space_name"] = "General"

    chat_service = Mock()
    chat_service.spaces().list().execute.return_value = {
        "spaces": [{"name": "spaces/S", "displayName": "General"}]
    }
    chat_service.spaces().messages().list().execute.return_value = {
        "messages": [matching, non_matching]
    }
    people_service = Mock()

    from gchat.chat_tools import search_messages

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        query="deploy",
    )

    assert "Deploy finished" in result
    assert "Lunch plans" not in result
    list_kwargs = chat_service.spaces().messages().list.call_args.kwargs
    assert list_kwargs["pageSize"] == 25
    assert "filter" not in list_kwargs


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_search_messages_limits_parallel_space_fetches(mock_resolve, monkeypatch):
    """Cross-space search should cap concurrent threaded Chat API calls."""
    mock_resolve.return_value = "Test User"

    from gchat.chat_tools import _SEARCH_MESSAGES_MAX_CONCURRENT_SPACE_FETCHES
    from gchat.chat_tools import search_messages

    state = {"current": 0, "max": 0}

    async def fake_to_thread(fn, *args, **kwargs):
        state["current"] += 1
        state["max"] = max(state["max"], state["current"])
        await asyncio.sleep(0.01)
        try:
            return fn(*args, **kwargs)
        finally:
            state["current"] -= 1

    monkeypatch.setattr("gchat.chat_tools.asyncio.to_thread", fake_to_thread)

    spaces = [{"name": f"spaces/S{i}", "displayName": f"Space {i}"} for i in range(5)]

    def list_messages(**kwargs):
        parent = kwargs["parent"]
        request = Mock()
        request.execute.return_value = {
            "messages": [_make_message(text=f"message from {parent}")]
        }
        return request

    chat_service = Mock()
    chat_service.spaces().list().execute.return_value = {"spaces": spaces}
    chat_service.spaces().messages().list.side_effect = list_messages
    people_service = Mock()

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        query="message",
        max_spaces=len(spaces),
    )

    assert "Found 5 messages matching 'text \"message\"'" in result
    assert state["max"] <= _SEARCH_MESSAGES_MAX_CONCURRENT_SPACE_FETCHES


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_search_messages_retries_ssl_per_space_without_restarting_search(
    mock_resolve, monkeypatch
):
    """A transient SSL failure in one space should retry only that space."""
    mock_resolve.return_value = "Test User"

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("gchat.chat_tools.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("gchat.chat_tools.asyncio.sleep", AsyncMock())

    from gchat.chat_tools import search_messages

    attempt_counts = {"spaces/S1": 0, "spaces/S2": 0}

    def list_messages(**kwargs):
        parent = kwargs["parent"]
        request = Mock()

        def execute():
            attempt_counts[parent] += 1
            if parent == "spaces/S1" and attempt_counts[parent] < 3:
                raise ssl.SSLError("read operation timed out")
            return {"messages": [_make_message(text=f"message from {parent}")]}

        request.execute.side_effect = execute
        return request

    chat_service = Mock()
    chat_service.spaces().list().execute.return_value = {
        "spaces": [
            {"name": "spaces/S1", "displayName": "Space 1"},
            {"name": "spaces/S2", "displayName": "Space 2"},
        ]
    }
    chat_service.spaces().messages().list.side_effect = list_messages
    people_service = Mock()

    result = await _unwrap(search_messages)(
        chat_service=chat_service,
        people_service=people_service,
        user_google_email="test@example.com",
        query="message",
        max_spaces=2,
    )

    assert "Found 2 messages matching 'text \"message\"'" in result
    assert attempt_counts == {"spaces/S1": 3, "spaces/S2": 1}
    assert chat_service.spaces().list().execute.call_count == 1


@pytest.mark.asyncio
@patch("gchat.chat_tools._resolve_sender", new_callable=AsyncMock)
async def test_search_messages_raises_transient_error_when_all_spaces_ssl_fail(
    mock_resolve, monkeypatch
):
    """If every searched space fails transiently, surface a transient network error."""
    mock_resolve.return_value = "Test User"

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("gchat.chat_tools.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("gchat.chat_tools.asyncio.sleep", AsyncMock())

    from core.utils import TransientNetworkError
    from gchat.chat_tools import search_messages

    def list_messages(**kwargs):  # noqa: ARG001
        request = Mock()
        request.execute.side_effect = ssl.SSLError("connection reset")
        return request

    chat_service = Mock()
    chat_service.spaces().list().execute.return_value = {
        "spaces": [{"name": "spaces/S1", "displayName": "Space 1"}]
    }
    chat_service.spaces().messages().list.side_effect = list_messages
    people_service = Mock()

    with pytest.raises(TransientNetworkError, match="transient SSL error"):
        await _unwrap(search_messages)(
            chat_service=chat_service,
            people_service=people_service,
            user_google_email="test@example.com",
            query="message",
        )


# ---------------------------------------------------------------------------
# download_chat_attachment: edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_no_attachments():
    """Should return a clear message when the message has no attachments."""
    service = Mock()
    service.spaces().messages().get().execute.return_value = _make_message()

    from gchat.chat_tools import download_chat_attachment

    result = await _unwrap(download_chat_attachment)(
        service=service,
        user_google_email="test@example.com",
        message_id="spaces/S/messages/M",
    )

    assert "No attachments found" in result


@pytest.mark.asyncio
async def test_download_invalid_index():
    """Should return an error for out-of-range attachment_index."""
    msg = _make_message(attachments=[_make_attachment()])
    service = Mock()
    service.spaces().messages().get().execute.return_value = msg

    from gchat.chat_tools import download_chat_attachment

    result = await _unwrap(download_chat_attachment)(
        service=service,
        user_google_email="test@example.com",
        message_id="spaces/S/messages/M",
        attachment_index=5,
    )

    assert "Invalid attachment_index" in result
    assert "1 attachment(s)" in result


class _FakeStreamResponse:
    """Minimal httpx streaming response for download_chat_attachment."""

    def __init__(self, status_code=200, chunks=(), headers=None):
        self.status_code = status_code
        self._chunks = list(chunks)
        self.headers = headers or {}

    async def aread(self):
        return b"".join(self._chunks)

    async def aiter_bytes(self, chunk_size=None):  # noqa: ARG002
        for chunk in self._chunks:
            yield chunk


class _FakeStreamingClient:
    """httpx.AsyncClient stand-in exposing .stream() as an async context manager."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.stream_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def stream(self, method, url, **kwargs):
        self.stream_calls.append((method, url, kwargs))
        error = self._error
        response = self._response

        class _Ctx:
            async def __aenter__(self):
                if error is not None:
                    raise error
                return response

            async def __aexit__(self, *exc_info):
                return False

        return _Ctx()

    @property
    def last_url(self):
        return self.stream_calls[-1][1]

    @property
    def last_headers(self):
        return self.stream_calls[-1][2]["headers"]


@pytest.mark.asyncio
async def test_download_uses_api_media_endpoint():
    """Should always use chat.googleapis.com media endpoint, not downloadUri."""
    fake_bytes = b"fake image content"
    att = _make_attachment()
    # Even with a downloadUri present, we should use the API endpoint
    att["downloadUri"] = "https://chat.google.com/api/get_attachment_url?bad=url"
    msg = _make_message(attachments=[att])

    service = Mock()
    service.spaces().messages().get().execute.return_value = msg
    service._http.credentials.token = "fake-access-token"

    from gchat.chat_tools import download_chat_attachment

    saved = Mock()
    saved.path = "/tmp/image_abc.png"
    saved.file_id = "abc"

    mock_client = _FakeStreamingClient(_FakeStreamResponse(200, chunks=[fake_bytes]))

    with (
        patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client),
        patch("auth.oauth_config.is_stateless_mode", return_value=False),
        patch("core.config.get_transport_mode", return_value="stdio"),
        patch("core.attachment_storage.get_attachment_storage") as mock_get_storage,
    ):
        mock_get_storage.return_value.save_attachment.return_value = saved

        result = await _unwrap(download_chat_attachment)(
            service=service,
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "image.png" in result
    assert "/tmp/image_abc.png" in result
    assert "Saved to:" in result

    # Verify we used the API endpoint with attachmentDataRef.resourceName
    url_used = mock_client.last_url
    parsed = urlparse(url_used)
    assert parsed.scheme == "https"
    assert parsed.hostname == "chat.googleapis.com"
    assert "alt=media" in url_used
    assert "spaces/S/attachments/A" in parsed.path
    assert "/messages/" not in parsed.path

    # Verify Bearer token
    assert mock_client.last_headers["Authorization"] == "Bearer fake-access-token"

    # Verify save_attachment was called with correct base64 data
    save_args = mock_get_storage.return_value.save_attachment.call_args
    assert save_args.kwargs["filename"] == "image.png"
    assert save_args.kwargs["mime_type"] == "image/png"
    decoded = base64.urlsafe_b64decode(save_args.kwargs["base64_data"])
    assert decoded == fake_bytes


@pytest.mark.asyncio
async def test_download_falls_back_to_att_name():
    """When attachmentDataRef is missing, should fall back to attachment name."""
    fake_bytes = b"fetched content"
    att = _make_attachment(name="spaces/S/messages/M/attachments/A", resource_name=None)
    msg = _make_message(attachments=[att])

    service = Mock()
    service.spaces().messages().get().execute.return_value = msg
    service._http.credentials.token = "fake-access-token"

    saved = Mock()
    saved.path = "/tmp/image_fetched.png"
    saved.file_id = "f1"

    mock_client = _FakeStreamingClient(_FakeStreamResponse(200, chunks=[fake_bytes]))

    from gchat.chat_tools import download_chat_attachment

    with (
        patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client),
        patch("auth.oauth_config.is_stateless_mode", return_value=False),
        patch("core.config.get_transport_mode", return_value="stdio"),
        patch("core.attachment_storage.get_attachment_storage") as mock_get_storage,
    ):
        mock_get_storage.return_value.save_attachment.return_value = saved

        result = await _unwrap(download_chat_attachment)(
            service=service,
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "image.png" in result
    assert "/tmp/image_fetched.png" in result

    # Falls back to attachment name when no attachmentDataRef
    assert "spaces/S/messages/M/attachments/A" in mock_client.last_url


@pytest.mark.asyncio
async def test_download_http_mode_returns_url():
    """In HTTP mode, should return a download URL instead of file path."""
    fake_bytes = b"image data"
    att = _make_attachment()
    msg = _make_message(attachments=[att])

    service = Mock()
    service.spaces().messages().get().execute.return_value = msg
    service._http.credentials.token = "fake-token"

    mock_client = _FakeStreamingClient(_FakeStreamResponse(200, chunks=[fake_bytes]))

    saved = Mock()
    saved.path = "/tmp/image_alt.png"
    saved.file_id = "alt1"

    from gchat.chat_tools import download_chat_attachment

    with (
        patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client),
        patch("auth.oauth_config.is_stateless_mode", return_value=False),
        patch("core.config.get_transport_mode", return_value="http"),
        patch("core.attachment_storage.get_attachment_storage") as mock_get_storage,
        patch(
            "core.attachment_storage.get_attachment_url",
            return_value="http://localhost:8005/attachments/alt1",
        ),
    ):
        mock_get_storage.return_value.save_attachment.return_value = saved

        result = await _unwrap(download_chat_attachment)(
            service=service,
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "Download URL:" in result
    assert "expire after 1 hour" in result


@pytest.mark.asyncio
async def test_download_returns_error_on_failure():
    """When download fails, should return a clear error message."""
    att = _make_attachment()
    att["downloadUri"] = "https://storage.googleapis.com/fake?alt=media"
    msg = _make_message(attachments=[att])

    service = Mock()
    service.spaces().messages().get().execute.return_value = msg
    service._http.credentials.token = "fake-token"

    mock_client = _FakeStreamingClient(error=Exception("connection refused"))

    from gchat.chat_tools import download_chat_attachment

    with patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client):
        result = await _unwrap(download_chat_attachment)(
            service=service,
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "Failed to download" in result
    assert "connection refused" in result


# ---------------------------------------------------------------------------
# Finding 1: download_chat_attachment must bound what it pulls into memory
# ---------------------------------------------------------------------------


def _download_service():
    service = Mock()
    service.spaces().messages().get().execute.return_value = _make_message(
        attachments=[_make_attachment()]
    )
    service._http.credentials.token = "fake-token"
    return service


@pytest.mark.asyncio
async def test_download_rejects_declared_oversized_attachment(monkeypatch):
    """An oversized Content-Length is refused before any body is read."""
    from gchat.chat_tools import download_chat_attachment

    monkeypatch.setattr("gchat.chat_tools.MAX_CHAT_ATTACHMENT_BYTES", 16)
    response = _FakeStreamResponse(
        200, chunks=[b"never-read"], headers={"content-length": "999999"}
    )
    mock_client = _FakeStreamingClient(response)

    with patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client):
        result = await _unwrap(download_chat_attachment)(
            service=_download_service(),
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "exceeds the 16 byte limit" in result
    assert "declared 999999 bytes" in result


@pytest.mark.asyncio
async def test_download_aborts_when_streamed_bytes_exceed_limit(monkeypatch):
    """Content-Length is server-supplied and may be absent, so chunks are counted."""
    from gchat.chat_tools import download_chat_attachment

    monkeypatch.setattr("gchat.chat_tools.MAX_CHAT_ATTACHMENT_BYTES", 16)
    # No content-length header at all: only the streaming count can catch this.
    response = _FakeStreamResponse(200, chunks=[b"x" * 10, b"y" * 10, b"z" * 10])
    mock_client = _FakeStreamingClient(response)

    with patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client):
        result = await _unwrap(download_chat_attachment)(
            service=_download_service(),
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "exceeds the 16 byte limit" in result
    assert "download aborted" in result


@pytest.mark.asyncio
async def test_download_accepts_attachment_within_limit(monkeypatch):
    """The limit must not break normal downloads."""
    from gchat.chat_tools import download_chat_attachment

    monkeypatch.setattr("gchat.chat_tools.MAX_CHAT_ATTACHMENT_BYTES", 64)
    response = _FakeStreamResponse(200, chunks=[b"a" * 10, b"b" * 10])
    mock_client = _FakeStreamingClient(response)

    saved = Mock()
    saved.path = "/tmp/image_ok.png"
    saved.file_id = "ok"

    with (
        patch("gchat.chat_tools.httpx.AsyncClient", return_value=mock_client),
        patch("auth.oauth_config.is_stateless_mode", return_value=False),
        patch("core.config.get_transport_mode", return_value="stdio"),
        patch("core.attachment_storage.get_attachment_storage") as mock_get_storage,
    ):
        mock_get_storage.return_value.save_attachment.return_value = saved
        result = await _unwrap(download_chat_attachment)(
            service=_download_service(),
            user_google_email="test@example.com",
            message_id="spaces/S/messages/M",
            attachment_index=0,
        )

    assert "20 bytes" in result
    assert "/tmp/image_ok.png" in result
