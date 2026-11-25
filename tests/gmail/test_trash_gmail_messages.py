"""Tests for `trash_gmail_messages`."""

import os
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gmail.gmail_tools import trash_gmail_messages


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeResp:
    status = 404
    reason = "fake"


class _FakeBatch:
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


def _service(read_results):
    """Mock Gmail service; `read_results` maps message ID -> labelIds or Exception."""

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
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    return service


async def _run(service, message_ids):
    return await _unwrap(trash_gmail_messages)(
        service=service, user_google_email="user@example.com", message_ids=message_ids
    )


@pytest.mark.asyncio
async def test_trash_adds_trash_label_and_reports_verified_count():
    service = _service({"m1": ["TRASH"], "m2": ["TRASH"]})

    result = await _run(service, ["m1", "m2"])

    service.users().messages().batchModify.assert_called_once_with(
        userId="me", body={"ids": ["m1", "m2"], "addLabelIds": ["TRASH"]}
    )
    assert "Moved 2/2 message(s) to Trash" in result
    assert "user@example.com" in result


@pytest.mark.asyncio
async def test_trash_does_not_claim_success_for_unrecognised_id():
    service = _service({"r-draft-id": HttpError(_FakeResp(), b"{}")})

    result = await _run(service, ["r-draft-id"])

    assert "Moved 0/1" in result
    assert "No such message (1)" in result
    assert "not draft or thread IDs" in result


@pytest.mark.asyncio
async def test_trash_dedupes_and_strips_ids():
    service = _service({"m1": ["TRASH"]})

    result = await _run(service, ["m1", " m1 ", ""])

    service.users().messages().batchModify.assert_called_once_with(
        userId="me", body={"ids": ["m1"], "addLabelIds": ["TRASH"]}
    )
    assert "Moved 1/1" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], ["", "  "]])
async def test_trash_rejects_empty_message_ids(ids):
    service = Mock()

    with pytest.raises(UserInputError, match="must not be empty"):
        await _run(service, ids)

    service.users.assert_not_called()


@pytest.mark.asyncio
async def test_trash_rejects_more_than_gmail_batch_limit():
    service = Mock()

    with pytest.raises(UserInputError, match="At most 1000"):
        await _run(service, [f"m{i}" for i in range(1001)])

    service.users.assert_not_called()
