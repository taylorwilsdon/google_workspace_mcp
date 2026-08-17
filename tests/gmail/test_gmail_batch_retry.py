"""Tests for retryable-error handling in Gmail batch fetches (429 / 5xx / quota).

Covers:
- `_is_retryable_error` classification.
- The batch redo loop: sub-requests that fail with a retryable error are
  re-fetched (only the failed IDs), and the results are merged so the final
  output contains every requested message.
"""

import os
import ssl
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_helpers import _is_retryable_error
from gmail.gmail_tools import (
    _fetch_message_with_retry,
    get_gmail_messages_content_batch,
    get_gmail_threads_content_batch,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeResp:
    """Minimal stand-in for an httplib2 Response (status + reason)."""

    def __init__(self, status: int):
        self.status = status
        self.reason = "fake"


class TestIsRetryableError:
    def test_429_is_retryable(self):
        assert _is_retryable_error(HttpError(_FakeResp(429), b"{}"))

    def test_5xx_is_retryable(self):
        assert _is_retryable_error(HttpError(_FakeResp(502), b"{}"))
        assert _is_retryable_error(HttpError(_FakeResp(503), b"{}"))

    def test_ssl_error_is_retryable(self):
        assert _is_retryable_error(ssl.SSLError("handshake failed"))

    def test_404_is_not_retryable(self):
        assert not _is_retryable_error(HttpError(_FakeResp(404), b"{}"))

    def test_403_rate_limit_marker_is_retryable(self):
        body = b'{"error":{"errors":[{"reason":"userRateLimitExceeded"}]}}'
        assert _is_retryable_error(HttpError(_FakeResp(403), body))

    def test_403_plain_denied_is_not_retryable(self):
        body = b'{"error":{"errors":[{"reason":"forbidden"}]}}'
        assert not _is_retryable_error(HttpError(_FakeResp(403), body))

    def test_non_http_errors_are_not_retryable(self):
        assert not _is_retryable_error(ValueError("boom"))
        assert not _is_retryable_error(RuntimeError("boom"))


class _FakeBatch:
    """Batch that invokes each request's execute() and calls back per request."""

    def __init__(self, callback):
        self._callback = callback
        self._requests = []

    def add(self, request, request_id):
        self._requests.append((request_id, request))

    def execute(self):
        for request_id, request in self._requests:
            try:
                response = request.execute()
                self._callback(request_id, response, None)
            except Exception as exc:
                self._callback(request_id, None, exc)


def _headers(subject="Example subject"):
    return [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": "sender@example.com"},
        {"name": "To", "value": "recipient@example.com"},
        {"name": "Message-ID", "value": "<msg@example.com>"},
        {"name": "Date", "value": "Fri, 28 Mar 2026 10:00:00 -0400"},
    ]


def _message_response(message_id, text="body"):
    return {
        "id": message_id,
        "payload": {
            "headers": _headers(f"Subject {message_id}"),
            "mimeType": "text/plain",
            "body": {"data": _b64(text)},
        },
    }


def _thread_response(thread_id, message_id):
    return {
        "id": thread_id,
        "messages": [
            {
                "id": message_id,
                "payload": {
                    "headers": _headers(f"Subject {thread_id}"),
                    "mimeType": "text/plain",
                    "body": {"data": _b64("thread body")},
                },
            }
        ],
    }


def _b64(text):
    import base64

    return base64.urlsafe_b64encode(text.encode()).decode()


@pytest.mark.asyncio
async def test_batch_refetches_only_rate_limited_sub_requests(monkeypatch):
    """A 429 sub-request is re-fetched; successful IDs are fetched exactly once."""
    # Track every message_get call per ID.
    calls = {"msg-1": 0, "msg-2": 0}

    def message_get(**kwargs):
        mid = kwargs["id"]
        calls[mid] += 1
        request = Mock()
        # msg-2 fails with 429 on its FIRST call only; retries succeed.
        if mid == "msg-2" and calls[mid] == 1:
            request.execute.side_effect = HttpError(_FakeResp(429), b"{}")
        else:
            request.execute.return_value = _message_response(mid)
        return request

    service = Mock()
    service.users().messages().get.side_effect = message_get
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)

    # Keep the redo backoff near-zero so the test runs fast.
    monkeypatch.setattr("gmail.gmail_tools.GMAIL_RATE_LIMIT_BACKOFF", 0)

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1", "msg-2"],
        user_google_email="user@example.com",
    )

    # No error stub remains and both messages are present.
    assert "⚠️" not in result
    assert "Subject msg-1" in result
    assert "Subject msg-2" in result

    # Retry contract: msg-1 (success) fetched once; msg-2 (429) fetched twice
    # (initial + redo). Successful IDs must NOT be re-fetched.
    assert calls == {"msg-1": 1, "msg-2": 2}


@pytest.mark.asyncio
async def test_batch_does_not_refetch_non_retryable_errors(monkeypatch):
    """A non-retryable error (404) is NOT re-fetched and stays as an error stub."""
    calls = {"msg-1": 0, "missing": 0}

    def message_get(**kwargs):
        mid = kwargs["id"]
        calls[mid] += 1
        request = Mock()
        if mid == "missing":
            request.execute.side_effect = HttpError(_FakeResp(404), b"{}")
        else:
            request.execute.return_value = _message_response(mid)
        return request

    service = Mock()
    service.users().messages().get.side_effect = message_get
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    monkeypatch.setattr("gmail.gmail_tools.GMAIL_RATE_LIMIT_BACKOFF", 0)

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1", "missing"],
        user_google_email="user@example.com",
    )

    assert "Subject msg-1" in result
    assert "⚠️ Message missing" in result

    # Retry contract: the 404 message is fetched exactly once (no re-fetch).
    assert calls == {"msg-1": 1, "missing": 1}


@pytest.mark.asyncio
async def test_thread_batch_refetches_rate_limited_thread(monkeypatch):
    """Regression: a 429 on one thread sub-request (batch succeeds) is re-fetched.

    The redo loop runs even when batch.execute() succeeds, so the per-thread
    fetch helper must live outside the batch-exception handler (previously it
    was nested in `except`, causing UnboundLocalError).
    """
    calls = {"thread-1": 0, "thread-2": 0}

    def thread_get(**kwargs):
        tid = kwargs["id"]
        calls[tid] += 1
        request = Mock()
        # thread-2 fails with 429 on its FIRST call only; retries succeed.
        if tid == "thread-2" and calls[tid] == 1:
            request.execute.side_effect = HttpError(_FakeResp(429), b"{}")
        else:
            request.execute.return_value = _thread_response(tid, f"msg-{tid}")
        return request

    service = Mock()
    service.users().threads().get.side_effect = thread_get
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    monkeypatch.setattr("gmail.gmail_tools.GMAIL_RATE_LIMIT_BACKOFF", 0)

    result = await _unwrap(get_gmail_threads_content_batch)(
        service=service,
        thread_ids=["thread-1", "thread-2"],
        user_google_email="user@example.com",
    )

    assert "⚠️" not in result
    assert "Subject thread-1" in result
    assert "Subject thread-2" in result
    assert calls == {"thread-1": 1, "thread-2": 2}


@pytest.mark.asyncio
async def test_message_retry_uses_three_backoffs(monkeypatch):
    """Three retries use the documented 1s/2s/4s exponential backoff."""
    attempts = 0
    sleeps = []

    def message_get(**kwargs):
        request = Mock()

        def execute():
            nonlocal attempts
            attempts += 1
            if attempts <= 3:
                raise HttpError(_FakeResp(429), b"{}")
            return _message_response(kwargs["id"])

        request.execute.side_effect = execute
        return request

    async def record_sleep(delay):
        sleeps.append(delay)

    service = Mock()
    service.users().messages().get.side_effect = message_get
    monkeypatch.setattr("asyncio.sleep", record_sleep)

    _, message, error = await _fetch_message_with_retry(
        service,
        message_id="msg-1",
        message_format="full",
        log_prefix="test",
    )

    assert error is None
    assert message["id"] == "msg-1"
    assert attempts == 4
    assert sleeps == [1, 2, 4]


@pytest.mark.asyncio
async def test_message_batch_failure_does_not_restart_exhausted_retries(monkeypatch):
    """A global batch failure gets one sequential retry cycle, not two."""
    attempts = 0

    def message_get(**kwargs):
        request = Mock()

        def execute():
            nonlocal attempts
            attempts += 1
            raise HttpError(_FakeResp(429), b"{}")

        request.execute.side_effect = execute
        return request

    async def no_sleep(_delay):
        return None

    batch = Mock()
    batch.execute.side_effect = RuntimeError("batch transport failed")
    service = Mock()
    service.users().messages().get.side_effect = message_get
    service.new_batch_http_request.return_value = batch
    monkeypatch.setattr("asyncio.sleep", no_sleep)

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="user@example.com",
    )

    assert "⚠️ Message msg-1" in result
    assert attempts == 4


@pytest.mark.asyncio
async def test_thread_batch_failure_does_not_restart_exhausted_retries(monkeypatch):
    """Thread fallback also gets only one sequential retry cycle."""
    attempts = 0

    def thread_get(**_kwargs):
        request = Mock()

        def execute():
            nonlocal attempts
            attempts += 1
            raise HttpError(_FakeResp(429), b"{}")

        request.execute.side_effect = execute
        return request

    async def no_sleep(_delay):
        return None

    batch = Mock()
    batch.execute.side_effect = RuntimeError("batch transport failed")
    service = Mock()
    service.users().threads().get.side_effect = thread_get
    service.new_batch_http_request.return_value = batch
    monkeypatch.setattr("asyncio.sleep", no_sleep)

    result = await _unwrap(get_gmail_threads_content_batch)(
        service=service,
        thread_ids=["thread-1"],
        user_google_email="user@example.com",
    )

    assert "⚠️ Thread thread-1" in result
    assert attempts == 4
