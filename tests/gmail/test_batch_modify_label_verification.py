"""Tests for honest result reporting in `batch_modify_gmail_message_labels`.

Gmail's `users.messages.batchModify` answers `204 No Content` and silently
ignores IDs it does not recognise, so the call itself cannot distinguish a
sweep that changed everything from one that changed nothing. The tool used to
report `Labels updated for N messages` where N was simply `len(message_ids)` —
a confident, indistinguishable success for wrong or stale IDs.

These tests pin the corrected behaviour: the read-back classifies every ID, and
nothing is claimed for IDs Gmail ignored.
"""

import os
import sys
from unittest.mock import Mock

import httplib2
import pytest
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import batch_modify_gmail_message_labels


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
                self._callback(request_id, request.execute(), None)
            except Exception as exc:  # noqa: BLE001 - mirrors the batch callback contract
                self._callback(request_id, None, exc)


def _service(read_results, batch_available=True):
    """Build a mock Gmail service.

    `read_results` maps message ID -> either a `labelIds` list (success) or an
    Exception to raise from the read-back.
    """

    def message_get(**kwargs):
        outcome = read_results[kwargs["id"]]
        request = Mock()
        if isinstance(outcome, Exception):
            request.execute.side_effect = outcome
        else:
            request.execute.return_value = {"id": kwargs["id"], "labelIds": outcome}
        return request

    service = Mock()
    service.users().messages().get.side_effect = message_get
    if batch_available:
        service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(
            callback
        )
    else:
        service.new_batch_http_request.side_effect = RuntimeError("batch unavailable")
    return service


async def _run(service, **kwargs):
    return await _unwrap(batch_modify_gmail_message_labels)(
        service=service, user_google_email="user@example.com", **kwargs
    )


@pytest.mark.asyncio
async def test_unrecognised_id_is_not_reported_as_success():
    """The regression: a thread ID where a message ID belongs.

    Gmail ignores it and returns 204; the read-back 404s. The result must say
    so instead of claiming the label was applied.
    """
    service = _service({"thread-id": HttpError(_FakeResp(404), b"{}")})

    result = await _run(service, message_ids=["thread-id"], add_label_ids=["TRASH"])

    assert "Applied: 0/1" in result
    assert "No such message (1)" in result
    assert "thread-id" in result
    assert "THREAD id where a MESSAGE id is required" in result
    assert "requested change could not be verified" in result
    assert "Gmail ignored" not in result
    # The old wording must not come back.
    assert "Labels updated for 1 messages" not in result


@pytest.mark.asyncio
async def test_mixed_batch_counts_only_what_landed():
    service = _service(
        {
            "msg-good": ["INBOX", "TRASH"],
            "msg-gone": HttpError(_FakeResp(404), b"{}"),
        }
    )

    result = await _run(
        service, message_ids=["msg-good", "msg-gone"], add_label_ids=["TRASH"]
    )

    assert "Applied: 1/2" in result
    assert "No such message (1): msg-gone" in result
    assert "msg-good" not in result.split("No such message")[1]


@pytest.mark.asyncio
async def test_removal_that_did_not_take_is_reported_unchanged():
    """The message exists, but the label we asked to remove is still on it."""
    service = _service({"msg-1": ["INBOX", "UNREAD"]})

    result = await _run(service, message_ids=["msg-1"], remove_label_ids=["UNREAD"])

    assert "Applied: 0/1" in result
    assert "Unchanged (1): msg-1" in result


@pytest.mark.asyncio
async def test_read_back_failure_is_reported_as_unverified():
    """A 500 on the read-back is not a 404 — we cannot claim either outcome."""
    service = _service({"msg-1": HttpError(_FakeResp(500), b"{}")})

    result = await _run(service, message_ids=["msg-1"], add_label_ids=["TRASH"])

    assert "Applied: 0/1" in result
    assert "Could not verify (1): msg-1" in result
    assert "may or may not have been applied" in result


@pytest.mark.asyncio
async def test_missing_batch_result_is_not_applied_for_remove_only_request():
    """No callback data cannot prove that an absent label was removed."""
    service = _service({"msg-1": ["INBOX"]})

    class _BatchWithoutCallbacks:
        def add(self, request, request_id):
            pass

        def execute(self):
            pass

    service.new_batch_http_request.side_effect = lambda callback: (
        _BatchWithoutCallbacks()
    )

    result = await _run(service, message_ids=["msg-1"], remove_label_ids=["UNREAD"])

    assert "Applied: 0/1" in result
    assert "Could not verify (1): msg-1" in result


@pytest.mark.asyncio
async def test_retryable_batch_read_is_retried_sequentially(monkeypatch):
    monkeypatch.setattr("gmail.gmail_tools.GMAIL_RATE_LIMIT_BACKOFF", 0)
    monkeypatch.setattr("gmail.gmail_tools.GMAIL_REQUEST_DELAY", 0)
    outcomes = iter([HttpError(_FakeResp(429), b"{}"), ["TRASH"]])
    service = _service({})

    def message_get(**kwargs):
        outcome = next(outcomes)
        request = Mock()
        if isinstance(outcome, Exception):
            request.execute.side_effect = outcome
        else:
            request.execute.return_value = {"id": kwargs["id"], "labelIds": outcome}
        return request

    service.users().messages().get.side_effect = message_get

    result = await _run(service, message_ids=["msg-1"], add_label_ids=["TRASH"])

    assert "Applied: 1/1" in result
    assert service.users().messages().get.call_count == 2


@pytest.mark.asyncio
async def test_verify_false_makes_no_reads_and_says_so():
    """Opting out must be explicit about what it did not check."""
    service = _service({})

    result = await _run(
        service, message_ids=["msg-1", "msg-2"], add_label_ids=["TRASH"], verify=False
    )

    assert "Requested label changes for 2 message ID(s)" in result
    assert "NOT VERIFIED" in result
    service.users().messages().get.assert_not_called()


@pytest.mark.asyncio
async def test_sequential_fallback_classifies_when_batch_is_unavailable(monkeypatch):
    monkeypatch.setattr("gmail.gmail_tools.GMAIL_REQUEST_DELAY", 0)
    service = _service(
        {
            "msg-good": ["TRASH"],
            "msg-gone": HttpError(_FakeResp(404), b"{}"),
        },
        batch_available=False,
    )

    result = await _run(
        service, message_ids=["msg-good", "msg-gone"], add_label_ids=["TRASH"]
    )

    assert "Applied: 1/2" in result
    assert "No such message (1): msg-gone" in result


@pytest.mark.asyncio
async def test_batch_modify_is_still_called_once_with_all_ids():
    """Verification is additive — it must not replace the batchModify call."""
    service = _service({"msg-1": ["TRASH"], "msg-2": ["TRASH"]})

    await _run(service, message_ids=["msg-1", "msg-2"], add_label_ids=["TRASH"])

    service.users().messages().batchModify.assert_called_once_with(
        userId="me", body={"ids": ["msg-1", "msg-2"], "addLabelIds": ["TRASH"]}
    )


# --- thread_ids ------------------------------------------------------------------


def _http_error(status):
    resp = Mock(status=status, reason="err")
    return HttpError(resp, b"{}")


@pytest.fixture
def no_backoff(monkeypatch):
    """Skip the retry delays; record them instead."""
    delays = []

    async def _sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("gmail.gmail_helpers.asyncio.sleep", _sleep)
    return delays


def _thread_service(behaviour):
    """Mock service whose threads.modify(id=...) follows behaviour[id].

    Each entry is a list consumed one attempt at a time: an exception is
    raised, anything else is returned.
    """
    service = Mock()
    attempts = {tid: list(steps) for tid, steps in behaviour.items()}

    def thread_modify(**kwargs):
        step = attempts[kwargs["id"]].pop(0)
        request = Mock()
        if isinstance(step, Exception):
            request.execute.side_effect = step
        else:
            request.execute.return_value = step
        return request

    service.users().threads().modify.side_effect = thread_modify
    return service


@pytest.mark.asyncio
async def test_thread_ids_use_threads_modify_and_report_per_thread(no_backoff):
    service = Mock()

    def thread_modify(**kwargs):
        request = Mock()
        if kwargs["id"] == "t-gone":
            request.execute.side_effect = _http_error(404)
        elif kwargs["id"] == "t-bad":
            request.execute.side_effect = _http_error(500)
        else:
            request.execute.return_value = {"id": kwargs["id"]}
        return request

    service.users().threads().modify.side_effect = thread_modify
    result = await _run(
        service,
        thread_ids=["t-ok", "t-gone", "t-bad"],
        remove_label_ids=["INBOX"],
    )

    calls = [
        (c.kwargs["id"], c.kwargs["body"])
        for c in service.users().threads().modify.call_args_list
        if "body" in c.kwargs
    ]
    # 404 is final; the 500 is retried three times before it is reported.
    assert [tid for tid, _ in calls] == ["t-ok", "t-gone"] + ["t-bad"] * 4
    assert all(body == {"removeLabelIds": ["INBOX"]} for _, body in calls)
    service.users().messages().batchModify.assert_not_called()
    assert "Label changes for 3 thread ID(s): Removed labels: INBOX" in result
    assert "Applied: 1/3" in result
    assert "No such thread (1): t-gone" in result
    assert "MESSAGE id where a THREAD id is required" in result
    assert "Failed (1): t-bad (failed: HTTP 500)" in result


@pytest.mark.asyncio
async def test_message_and_thread_ids_can_be_combined():
    service = _service({"msg-1": ["INBOX", "TRASH"]})
    service.users().threads().modify.return_value.execute.return_value = {}
    result = await _run(
        service,
        message_ids=["msg-1"],
        thread_ids=["t-1"],
        add_label_ids=["TRASH"],
    )
    assert "Label changes for 1 message ID(s)" in result
    assert "Label changes for 1 thread ID(s)" in result


@pytest.mark.asyncio
async def test_requires_message_or_thread_ids():
    with pytest.raises(Exception, match="message_ids or thread_ids"):
        await _run(Mock(), add_label_ids=["TRASH"])


@pytest.mark.asyncio
async def test_thread_transport_error_is_recorded_and_loop_continues(no_backoff):
    service = _thread_service(
        {
            "t-1": [{}],
            "t-net": [httplib2.HttpLib2Error("connection reset")],
            "t-2": [{}],
        }
    )
    result = await _run(
        service, thread_ids=["t-1", "t-net", "t-2"], remove_label_ids=["UNREAD"]
    )
    assert "Applied: 2/3" in result
    assert "Failed (1): t-net (failed: HttpLib2Error)" in result


@pytest.mark.asyncio
async def test_thread_rate_limit_is_retried_before_failing(no_backoff):
    service = _thread_service(
        {
            "t-busy": [_http_error(429), _http_error(503), {}],
            "t-down": [_http_error(500)] * 4,
        }
    )
    result = await _run(
        service, thread_ids=["t-busy", "t-down"], add_label_ids=["STARRED"]
    )
    assert "Applied: 1/2" in result
    assert "Failed (1): t-down (failed: HTTP 500)" in result
    assert no_backoff == [1, 2, 1, 2, 4]


@pytest.mark.asyncio
async def test_message_failure_keeps_thread_report():
    service = _thread_service({"t-1": [{}]})
    service.users().messages().batchModify.return_value.execute.side_effect = (
        _http_error(500)
    )
    with pytest.raises(ToolError) as excinfo:
        await _run(
            service,
            message_ids=["msg-1"],
            thread_ids=["t-1"],
            add_label_ids=["TRASH"],
        )
    message = str(excinfo.value)
    assert "message_ids failed" in message
    assert "Label changes for 1 thread ID(s)" in message
    assert "Applied: 1/1" in message
