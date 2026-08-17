"""draft_gmail_message lifecycle: action='update' / 'delete' on the one existing tool.

Zero new tools by design — update and delete are Gmail API methods
(users.drafts.update / users.drafts.delete) surfaced as an action parameter,
matching the server's manage_* consolidation style. All three actions ride the
gmail.compose scope the tool already declares, so no deployment re-consents.

The exact-call assertions are deliberate (not just "it returned text"): a Mock
accepts any kwargs, and an unsupported parameter slipping into a drafts.* call
is precisely the class of bug an unconstrained mock hides.
"""

import base64
import os
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError  # noqa: E402
from gmail.gmail_tools import draft_gmail_message  # noqa: E402


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
        self.reason = "Not Found" if status == 404 else "Error"


def _http_error(status: int) -> HttpError:
    return HttpError(_FakeResp(status), b"{}")


def _decoded_raw(body: dict) -> str:
    return base64.urlsafe_b64decode(body["message"]["raw"]).decode(
        "utf-8", errors="replace"
    )


async def _call(service, **kwargs):
    kwargs.setdefault("user_google_email", "user@example.com")
    kwargs.setdefault("include_signature", False)
    return await _unwrap(draft_gmail_message)(service=service, **kwargs)


class TestActionValidation:
    """Bad action/draft_id combinations fail BEFORE any API call."""

    @pytest.mark.asyncio
    async def test_create_rejects_draft_id(self):
        service = Mock()
        with pytest.raises(UserInputError, match="action='update'"):
            await _call(service, subject="Hi", body="B", draft_id="r-1")
        service.users.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_requires_draft_id(self):
        service = Mock()
        with pytest.raises(UserInputError, match="requires 'draft_id'"):
            await _call(service, action="update", subject="Hi", body="B")
        service.users.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_requires_draft_id(self):
        service = Mock()
        with pytest.raises(UserInputError, match="requires 'draft_id'"):
            await _call(service, action="delete")
        service.users.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_rejects_content_args_naming_them(self):
        """Content args with delete mean the caller probably wanted update —
        refuse loudly BEFORE an unrecoverable delete, and name the culprits."""
        service = Mock()
        with pytest.raises(UserInputError, match=r"body, subject.*action='update'"):
            await _call(service, action="delete", draft_id="r-1", subject="S", body="B")
        service.users.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_rejects_forward_message_id(self):
        service = Mock()
        with pytest.raises(UserInputError, match="forward"):
            await _call(
                service,
                action="update",
                draft_id="r-1",
                forward_message_id="m-9",
                to="a@example.com",
            )
        service.users.assert_not_called()


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_calls_drafts_delete_exactly(self):
        service = Mock()
        service.users().drafts().delete().execute.return_value = ""
        service.users().drafts().delete.reset_mock()

        result = await _call(service, action="delete", draft_id="r-1")

        service.users().drafts().delete.assert_called_once_with(userId="me", id="r-1")
        assert "permanently deleted" in result
        assert "cannot be recovered" in result

    @pytest.mark.asyncio
    async def test_delete_404_becomes_actionable_error(self):
        service = Mock()
        service.users().drafts().delete().execute.side_effect = _http_error(404)

        with pytest.raises(UserInputError, match="not found"):
            await _call(service, action="delete", draft_id="r-gone")

    @pytest.mark.asyncio
    async def test_delete_non_404_is_not_masked(self):
        service = Mock()
        service.users().drafts().delete().execute.side_effect = _http_error(500)

        with pytest.raises(HttpError):
            await _call(service, action="delete", draft_id="r-1")


class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_calls_drafts_update_with_full_message(self):
        """update goes to drafts.update (NOT create), keeps the caller's draft
        ID, and carries the rebuilt raw message."""
        service = Mock()
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-3"},
        }
        service.users().drafts().update.reset_mock()

        result = await _call(
            service,
            action="update",
            draft_id="r-1",
            subject="Hello v2",
            body="Updated wording.",
            to="rcpt@example.com",
        )

        service.users().drafts().update.assert_called_once()
        kwargs = service.users().drafts().update.call_args.kwargs
        assert kwargs["userId"] == "me"
        assert kwargs["id"] == "r-1"
        raw = _decoded_raw(kwargs["body"])
        assert "Hello v2" in raw and "rcpt@example.com" in raw
        service.users().drafts().create.assert_not_called()
        assert "Draft updated" in result and "r-1" in result

    @pytest.mark.asyncio
    async def test_update_threads_like_create(self):
        """A reply update with explicit headers carries threadId, same as create."""
        service = Mock()
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-3"},
        }
        service.users().drafts().update.reset_mock()

        await _call(
            service,
            action="update",
            draft_id="r-1",
            subject="Re: Hi",
            body="B",
            to="rcpt@example.com",
            thread_id="t-3",
            in_reply_to="<orig@example.com>",
            references="<orig@example.com>",
        )

        body = service.users().drafts().update.call_args.kwargs["body"]
        assert body["message"]["threadId"] == "t-3"

    @pytest.mark.asyncio
    async def test_update_404_becomes_actionable_error(self):
        service = Mock()
        service.users().drafts().update().execute.side_effect = _http_error(404)

        with pytest.raises(UserInputError, match="not found"):
            await _call(
                service,
                action="update",
                draft_id="r-stale",
                subject="S",
                body="B",
                to="rcpt@example.com",
            )


class TestCreateUnchanged:
    @pytest.mark.asyncio
    async def test_default_action_still_creates(self):
        """No action arg → identical behavior to before: drafts.create, 'created'."""
        service = Mock()
        service.users().drafts().create().execute.return_value = {
            "id": "r-new",
            "message": {"id": "m-1", "threadId": "t-1"},
        }
        service.users().drafts().create.reset_mock()

        result = await _call(service, subject="Hi", body="B", to="rcpt@example.com")

        service.users().drafts().create.assert_called_once()
        assert service.users().drafts().create.call_args.kwargs["userId"] == "me"
        service.users().drafts().update.assert_not_called()
        assert "Draft created" in result and "r-new" in result
