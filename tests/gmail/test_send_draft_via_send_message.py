"""send_gmail_message(draft_id=...) — sending an existing draft, zero new tools.

The "commit" half of compose-review-send lives as a parameter on the EXISTING
send tool, not as a separate tool: drafts.send is surfaced behind draft_id.
The draft already carries its content, so combining draft_id with any content
or addressing argument is rejected by name before any API call.
"""

import os
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError  # noqa: E402
from gmail.gmail_tools import send_gmail_message  # noqa: E402


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _FakeResp:
    def __init__(self, status: int):
        self.status = status
        self.reason = "Not Found" if status == 404 else "Error"


def _http_error(status: int) -> HttpError:
    return HttpError(_FakeResp(status), b"{}")


async def _send(service, **kwargs):
    kwargs.setdefault("user_google_email", "user@example.com")
    return await _unwrap(send_gmail_message)(service=service, **kwargs)


@pytest.mark.asyncio
class TestSendDraft:
    async def test_sends_via_drafts_send_with_exact_call(self):
        service = Mock()
        service.users().drafts().send().execute.return_value = {
            "id": "m-9",
            "threadId": "t-9",
        }
        service.users().drafts().send.reset_mock()

        result = await _send(service, draft_id="r-1")

        service.users().drafts().send.assert_called_once_with(
            userId="me", body={"id": "r-1"}
        )
        service.users().messages().send.assert_not_called()
        assert "Draft r-1 sent" in result and "m-9" in result

    async def test_not_found_becomes_actionable_error(self):
        service = Mock()
        service.users().drafts().send().execute.side_effect = _http_error(404)

        with pytest.raises(UserInputError, match="list_drafts"):
            await _send(service, draft_id="r-gone")

    async def test_invalid_id_also_actionable(self):
        """Gmail answers a malformed/stale draft id with 400 — same guidance."""
        service = Mock()
        service.users().drafts().send().execute.side_effect = _http_error(400)

        with pytest.raises(UserInputError, match="not found"):
            await _send(service, draft_id="not-a-draft-id")

    async def test_server_errors_pass_through(self):
        service = Mock()
        service.users().drafts().send().execute.side_effect = _http_error(500)

        with pytest.raises(HttpError):
            await _send(service, draft_id="r-1")

    async def test_content_args_alongside_draft_id_rejected_by_name(self):
        """The draft is sent AS-IS; content args would be silently ignored, so
        they are rejected before any API call, pointing at action='update'."""
        service = Mock()

        with pytest.raises(UserInputError, match=r"body, subject.*action='update'"):
            await _send(service, draft_id="r-1", subject="New", body="Text")

        service.users.assert_not_called()

    async def test_forward_alongside_draft_id_rejected(self):
        service = Mock()

        with pytest.raises(UserInputError, match="forward_message_id"):
            await _send(service, draft_id="r-1", forward_message_id="m-2")

        service.users.assert_not_called()
