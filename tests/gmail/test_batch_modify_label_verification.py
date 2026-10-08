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

import pytest
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
