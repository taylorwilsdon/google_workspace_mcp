"""Thread readers must never show a draft or trashed message as sent mail.

Gmail's threads.get returns every message in a thread, drafts and trashed
drafts included. Before this fix both thread tools printed them as ordinary
"=== Message N ===" entries with no labels, so a deleted draft read as a
second copy of the sent email.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, Mock

import pytest

from gmail.gmail_helpers import (
    _analyze_thread_ownership_impl,
    format_thread_status_counts,
    thread_message_status,
)
from gmail.gmail_tools import (
    get_gmail_thread_content,
    get_gmail_threads_content_batch,
)

USER = "finance@example.com"


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _message(message_id, labels, sender, date, body, subject="Re: Invoice"):
    return {
        "id": message_id,
        "labelIds": labels,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "Client <client@example.com>"},
                {"name": "Date", "value": date},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


def _thread(thread_id="t1"):
    """A received question, two abandoned drafts, the sent reply, a reply back."""
    finance = f"Finance <{USER}>"
    client = "Client <client@example.com>"
    return {
        "id": thread_id,
        "messages": [
            _message(
                "m1",
                ["INBOX", "IMPORTANT"],
                client,
                "Tue, 10 Nov 2026 14:00:00 -0600",
                "Can you send the invoice?",
                subject="Invoice",
            ),
            _message(
                "m2",
                ["DRAFT"],
                finance,
                "Wed, 11 Nov 2026 09:20:00 -0500",
                "Draft one, never sent.",
            ),
            _message(
                "m3",
                ["DRAFT", "TRASH"],
                finance,
                "Wed, 11 Nov 2026 09:25:41 -0500",
                "Draft two, deleted.",
            ),
            _message(
                "m4",
                ["SENT"],
                finance,
                "Wed, 11 Nov 2026 09:39:21 -0500",
                "Invoice attached.",
            ),
            _message(
                "m5",
                ["INBOX"],
                client,
                "Wed, 11 Nov 2026 08:44:14 -0600",
                "Thanks, got it.",
            ),
        ],
    }


def _service(thread):
    service = MagicMock()
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = thread
    return service


def _batch_service(threads):
    class _FakeBatch:
        def __init__(self, callback):
            self._callback = callback
            self._requests = []

        def add(self, request, request_id):
            self._requests.append((request_id, request))

        def execute(self):
            for request_id, request in self._requests:
                self._callback(request_id, request.execute(), None)

    def thread_get(**kwargs):
        request = Mock()
        request.execute.return_value = threads[kwargs["id"]]
        return request

    service = Mock()
    service.users().threads().get.side_effect = thread_get
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    return service


@pytest.mark.parametrize(
    "labels, status",
    [
        (["SENT"], "sent"),
        (["SENT", "INBOX"], "sent"),
        (["INBOX"], "received"),
        ([], "received"),
        (None, "received"),
        (["DRAFT"], "draft"),
        (["DRAFT", "TRASH"], "draft in trash"),
        (["SENT", "TRASH"], "sent in trash"),
        (["INBOX", "TRASH"], "received in trash"),
    ],
)
def test_thread_message_status(labels, status):
    assert thread_message_status({"labelIds": labels}) == status


def test_status_counts_are_ordered_and_pluralized():
    statuses = ["draft in trash", "received", "sent", "draft in trash", "received"]
    assert format_thread_status_counts(statuses) == (
        "1 sent, 2 received, 2 drafts in trash"
    )


@pytest.mark.asyncio
async def test_thread_content_hides_drafts_and_trash_by_default():
    result = await _unwrap(get_gmail_thread_content)(
        service=_service(_thread()), thread_id="t1", user_google_email=USER
    )

    lines = result.splitlines()
    assert lines[:4] == [
        "Thread ID: t1",
        "Subject: Invoice",
        "Messages: 3 (1 sent, 2 received)",
        "Not shown: 2 (1 draft, 1 draft in trash); "
        "pass include_drafts=True to show drafts and trashed messages",
    ]
    assert "=== Message 1 [RECEIVED] ===" in lines
    assert "=== Message 2 [SENT] ===" in lines
    assert "=== Message 3 [RECEIVED] ===" in lines
    assert "Labels: SENT" in lines
    assert "Draft one" not in result
    assert "Draft two" not in result
    assert "DRAFT" not in result


@pytest.mark.asyncio
async def test_thread_content_marks_drafts_and_trash_when_included():
    result = await _unwrap(get_gmail_thread_content)(
        service=_service(_thread()),
        thread_id="t1",
        user_google_email=USER,
        include_drafts=True,
    )

    lines = result.splitlines()
    assert lines[2] == "Messages: 5 (1 sent, 2 received, 1 draft, 1 draft in trash)"
    assert not any(line.startswith("Not shown:") for line in lines)
    headers = [line for line in lines if line.startswith("=== Message")]
    assert headers == [
        "=== Message 1 [RECEIVED] ===",
        "=== Message 2 [DRAFT, NOT SENT] ===",
        "=== Message 3 [DRAFT IN TRASH, NOT SENT] ===",
        "=== Message 4 [SENT] ===",
        "=== Message 5 [RECEIVED] ===",
    ]
    draft_entry = lines[lines.index("=== Message 3 [DRAFT IN TRASH, NOT SENT] ===") + 1]
    assert draft_entry == "Labels: DRAFT, TRASH"
    assert "Draft two, deleted." in result


@pytest.mark.asyncio
async def test_thread_content_with_only_drafts_says_so():
    thread = _thread()
    thread["messages"] = thread["messages"][1:3]

    result = await _unwrap(get_gmail_thread_content)(
        service=_service(thread), thread_id="t1", user_google_email=USER
    )

    assert result.splitlines()[2:4] == [
        "Messages: 0",
        "Not shown: 2 (1 draft, 1 draft in trash); "
        "pass include_drafts=True to show drafts and trashed messages",
    ]
    assert "=== Message" not in result


@pytest.mark.asyncio
async def test_raw_format_fetches_only_shown_messages(monkeypatch):
    fetched = []

    async def fake_raw(service, message_ids, log_prefix):
        fetched.extend(message_ids)
        return {mid: f"raw {mid}" for mid in message_ids}

    monkeypatch.setattr("gmail.gmail_tools._fetch_raw_message_contents", fake_raw)

    await _unwrap(get_gmail_thread_content)(
        service=_service(_thread()),
        thread_id="t1",
        user_google_email=USER,
        body_format="raw",
    )

    assert fetched == ["m1", "m4", "m5"]


@pytest.mark.asyncio
async def test_analysis_ignores_drafts_and_trash_whatever_include_drafts_is():
    thread = _thread()
    # A trashed reply from the client must not put the ball in the user's court.
    thread["messages"].append(
        _message(
            "m6",
            ["TRASH"],
            "Client <client@example.com>",
            "Thu, 12 Nov 2026 09:00:00 -0600",
            "Ignore this one.",
        )
    )
    thread["messages"][4]["labelIds"] = ["SENT"]
    thread["messages"][4]["payload"]["headers"][0]["value"] = f"Finance <{USER}>"

    for include_drafts in (False, True):
        result = await _unwrap(get_gmail_thread_content)(
            service=_service(thread),
            thread_id="t1",
            user_google_email=USER,
            include_analysis=True,
            include_drafts=include_drafts,
        )
        analysis = result["analysis"]
        assert analysis["excluded_drafts"] == 2
        assert analysis["excluded_trashed"] == 1
        assert analysis["ball_in_court_of"] == "them"
        assert analysis["message_count_by_sender"] == {
            "client@example.com": 1,
            USER: 2,
        }


def test_analysis_all_drafts_or_trash_has_no_last_sender():
    thread = _thread()
    thread["messages"] = thread["messages"][1:3]

    analysis = _analyze_thread_ownership_impl(thread, USER)

    assert analysis["last_sender"] is None
    assert analysis["ball_in_court_of"] is None
    assert analysis["excluded_drafts"] == 2
    assert analysis["excluded_trashed"] == 0


@pytest.mark.asyncio
async def test_batch_hides_drafts_and_trash_by_default():
    other = _thread("t2")
    other["messages"] = [other["messages"][0]]
    service = _batch_service({"t1": _thread(), "t2": other})

    result = await _unwrap(get_gmail_threads_content_batch)(
        service=service, thread_ids=["t1", "t2"], user_google_email=USER
    )

    assert "Messages: 3 (1 sent, 2 received)" in result
    assert "Not shown: 2 (1 draft, 1 draft in trash)" in result
    assert "Messages: 1 (1 received)" in result
    assert "Draft one" not in result
    assert "Draft two" not in result


@pytest.mark.asyncio
async def test_batch_marks_drafts_and_trash_when_included():
    service = _batch_service({"t1": _thread()})

    result = await _unwrap(get_gmail_threads_content_batch)(
        service=service,
        thread_ids=["t1"],
        user_google_email=USER,
        include_drafts=True,
    )

    assert "Messages: 5 (1 sent, 2 received, 1 draft, 1 draft in trash)" in result
    assert "=== Message 2 [DRAFT, NOT SENT] ===" in result
    assert "=== Message 3 [DRAFT IN TRASH, NOT SENT] ===" in result
    assert "=== Message 4 [SENT] ===" in result
    assert "Not shown:" not in result
