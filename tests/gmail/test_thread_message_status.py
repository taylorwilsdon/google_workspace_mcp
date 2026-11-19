"""Thread readers mark draft and trashed messages."""

from __future__ import annotations

import base64
from typing import Any, Callable, cast
from unittest.mock import MagicMock

import pytest

from gmail.gmail_helpers import thread_message_status
from gmail.gmail_tools import get_gmail_thread_content

USER = "finance@example.com"


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return cast(Callable[..., Any], fn)


def _message(message_id: str, labels: list[str], body: str) -> dict[str, Any]:
    """Build one threads.get(format='full') message with a plain-text body."""
    return {
        "id": message_id,
        "labelIds": labels,
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": f"Finance <{USER}>"},
                {"name": "Date", "value": "Wed, 11 Nov 2026 09:20:00 -0500"},
                {"name": "Subject", "value": "Invoice"},
            ],
            "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
        },
    }


@pytest.mark.parametrize(
    "labels, status",
    [
        (["SENT"], ""),
        (["SENT", "INBOX"], ""),
        (["INBOX"], ""),
        ([], ""),
        (None, ""),
        (["DRAFT"], " [DRAFT, NOT SENT]"),
        (["DRAFT", "TRASH"], " [DRAFT, NOT SENT, IN TRASH]"),
        (["SENT", "TRASH"], " [IN TRASH]"),
        (["INBOX", "TRASH"], " [IN TRASH]"),
    ],
)
def test_thread_message_status(labels: list[str] | None, status: str) -> None:
    assert thread_message_status({"labelIds": labels}) == status


@pytest.mark.asyncio
async def test_thread_content_marks_draft_and_trashed_messages() -> None:
    thread = {
        "id": "t1",
        "messages": [
            _message("m1", ["INBOX"], "Can you send the invoice?"),
            _message("m2", ["DRAFT", "TRASH"], "Deleted draft."),
            _message("m3", ["SENT"], "Invoice attached."),
        ],
    }
    service = MagicMock()
    service.users.return_value.threads.return_value.get.return_value.execute.return_value = thread

    result = await _unwrap(get_gmail_thread_content)(
        service=service, thread_id="t1", user_google_email=USER
    )

    assert [line for line in result.splitlines() if line.startswith("===")] == [
        "=== Message 1 ===",
        "=== Message 2 [DRAFT, NOT SENT, IN TRASH] ===",
        "=== Message 3 ===",
    ]
