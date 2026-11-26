"""Thread search uses one provider page and metadata-only enrichment."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.server import server
from core.tool_tier_loader import get_tools_for_tier
from core.utils import UserInputError
from gmail.gmail_tools import search_gmail_threads


def _unwrap(tool: Any) -> Any:
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service(page: dict, metadata: dict[str, Any]) -> MagicMock:
    service = MagicMock()
    threads = service.users.return_value.threads.return_value
    threads.list.return_value.execute.return_value = page

    def get(**kwargs: Any) -> MagicMock:
        request = MagicMock()
        response = metadata[kwargs["id"]]
        if isinstance(response, Exception):
            request.execute.side_effect = response
        else:
            request.execute.return_value = response
        return request

    threads.get.side_effect = get
    return service


async def _search(service: MagicMock, **kwargs: Any) -> str:
    return await _unwrap(search_gmail_threads)(
        service=service,
        query="from:vendor@example.com",
        user_google_email="reader@example.com",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_page_and_metadata_only_latest_message() -> None:
    # Latest by numeric internalDate, not position, lexical order or Date header.
    messages = [
        {
            "internalDate": "100",
            "payload": {
                "headers": [
                    {"name": "sUbJeCt", "value": "Latest subject"},
                    {"name": "FROM", "value": "last@example.com"},
                    {"name": "date", "value": "Thu, 1 Jan 1970 00:00:00 +0000"},
                ],
            },
        },
        {
            "internalDate": "99",
            "payload": {
                "headers": [{"name": "Subject", "value": "Older subject"}],
            },
        },
    ]
    service = _service(
        {
            "threads": [{"id": "abc123", "snippet": "Thread preview"}],
            "nextPageToken": "next",
        },
        {"abc123": {"messages": messages}},
    )

    result = await _search(service, page_size=50, page_token="previous")

    threads = service.users.return_value.threads.return_value
    threads.list.assert_called_once_with(
        userId="me", q="from:vendor@example.com", maxResults=50, pageToken="previous"
    )
    threads.get.assert_called_once_with(
        userId="me",
        id="abc123",
        format="metadata",
        metadataHeaders=["Subject", "From", "Date"],
        fields="messages(internalDate,payload/headers)",
    )
    service.users.return_value.messages.assert_not_called()
    assert "Thread ID: abc123" in result
    assert "/mail/u/reader@example.com/#all/abc123" in result
    assert "Snippet: Thread preview" in result
    assert "Message count: 2" in result
    assert "Latest Subject: Latest subject" in result
    assert "Latest From: last@example.com" in result
    assert "Latest Date: Thu, 1 Jan 1970 00:00:00 +0000" in result
    assert "Older subject" not in result
    assert "next_page_token: next" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [{}, {"threads": []}, {"nextPageToken": "continue"}])
async def test_empty_page_preserves_continuation(page: dict) -> None:
    service = _service(page, {})
    result = await _search(service)
    assert "Found 0 threads" in result
    assert ("next_page_token: continue" in result) == ("nextPageToken" in page)
    threads = service.users.return_value.threads.return_value
    threads.list.assert_called_once_with(
        userId="me", q="from:vendor@example.com", maxResults=10
    )
    threads.get.assert_not_called()


@pytest.mark.asyncio
async def test_long_and_empty_threads_missing_headers() -> None:
    service = _service(
        {"threads": [{"id": "long"}, {"id": "empty"}]},
        {
            "long": {"messages": [{"internalDate": str(n)} for n in range(60)]},
            "empty": {},
        },
    )
    result = await _search(service)
    assert "Message count: 60" in result
    assert "Message count: 0" in result
    assert "Latest Subject: (no subject)" in result
    assert "Latest From: (unknown sender)" in result
    assert "Latest Date: (unknown date)" in result
    assert "next_page_token" not in result
    assert service.users.return_value.threads.return_value.get.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status,attempts", [(404, 1), (429, 4), (503, 4)])
async def test_enrichment_failure_retains_rows_and_token(
    monkeypatch: pytest.MonkeyPatch, status: int, attempts: int
) -> None:
    monkeypatch.setattr("gmail.gmail_helpers.asyncio.sleep", AsyncMock())
    error = HttpError(
        httplib2.Response({"status": status}), b'{"error":{"message":"unavailable"}}'
    )
    service = _service(
        {
            "threads": [{"id": "failed", "snippet": "still visible"}, {"id": "ok"}],
            "nextPageToken": "next",
        },
        {"failed": error, "ok": {"messages": [{"internalDate": "1"}]}},
    )
    result = await _search(service)
    assert result.index("Thread ID: failed") < result.index("Thread ID: ok")
    assert "Snippet: still visible" in result
    assert "unavailable (metadata fetch failed)" in result
    assert "Message count: 1" in result
    assert "next_page_token: next" in result
    assert (
        service.users.return_value.threads.return_value.get.call_count == attempts + 1
    )


@pytest.mark.asyncio
async def test_list_failure_does_not_look_like_empty_results() -> None:
    service = _service({}, {})
    error = HttpError(
        httplib2.Response({"status": 403}), b'{"error":{"message":"forbidden"}}'
    )
    service.users.return_value.threads.return_value.list.return_value.execute.side_effect = error
    with pytest.raises(HttpError):
        await _search(service)
    service.users.return_value.threads.return_value.get.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [0, -1, 101])
async def test_invalid_page_size_never_calls_provider(page_size: int) -> None:
    service = MagicMock()
    with pytest.raises(UserInputError, match="between 1 and 100"):
        await _search(service, page_size=page_size)
    service.users.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("page_size", [1, 100])
async def test_page_size_boundaries(page_size: int) -> None:
    service = _service({}, {})
    await _search(service, page_size=page_size)
    assert (
        service.users.return_value.threads.return_value.list.call_args.kwargs[
            "maxResults"
        ]
        == page_size
    )


@pytest.mark.asyncio
async def test_registered_read_only_tool_and_tier() -> None:
    tool = next(
        tool
        for tool in await server.list_tools()
        if tool.name == "search_gmail_threads"
    )
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.destructive_hint is False
    assert set(tool.parameters["properties"]) == {
        "query",
        "user_google_email",
        "page_size",
        "page_token",
    }
    assert "search_gmail_threads" in get_tools_for_tier("extended", ["gmail"])
    assert "search_gmail_threads" not in get_tools_for_tier("core", ["gmail"])
