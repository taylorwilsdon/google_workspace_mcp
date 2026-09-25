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
from typing import Dict, List, Optional
from unittest.mock import Mock, call

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


def _mock_service(draft_headers: Optional[List[Dict[str, str]]] = None) -> Mock:
    """A Gmail service mock with the Send-As settings lookup stubbed.

    draft_gmail_message resolves the account's default Send-As identity even when
    include_signature is False, so any test that gets as far as composing a
    message needs settings().sendAs().list() to return real data rather than a
    bare Mock (which is not iterable).

    action='update' also reads the existing draft to preserve its threading, so
    drafts().get() is stubbed too. ``draft_headers`` supplies the stored draft's
    headers: leave it None for an unthreaded draft, or pass In-Reply-To /
    References to model a reply draft.
    """
    service = Mock()
    service.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {"sendAsEmail": "user@example.com", "isDefault": True, "signature": ""}
        ]
    }
    service.users().drafts().get().execute.return_value = {
        "id": "r-1",
        "message": {
            "id": "m-1",
            "threadId": "t-1",
            "payload": {"headers": list(draft_headers or [])},
        },
    }
    return service


# A stored reply draft: threadId alone is not enough for Gmail to keep a
# message in its conversation, the RFC 2822 headers have to travel with it.
_THREADED_DRAFT_HEADERS = [
    {"name": "In-Reply-To", "value": "<parent@mail.example.com>"},
    {
        "name": "References",
        "value": "<root@mail.example.com> <parent@mail.example.com>",
    },
]


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
        service = _mock_service()
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
        service = _mock_service()
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
    async def test_update_preserves_threading_when_caller_omits_it(self):
        """THE regression this guards: drafts.update destroys and replaces the
        underlying message, so a reply draft updated with only new body text
        would silently leave its conversation. The existing threading must be
        read back and re-supplied — asserted on the REQUEST, not the fixture,
        because a fixture that merely returns a threadId proves nothing about
        what we sent."""
        service = _mock_service(draft_headers=_THREADED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-1"},
        }
        service.users().drafts().update.reset_mock()

        await _call(
            service,
            action="update",
            draft_id="r-1",
            subject="Re: Hi",
            body="Just the new wording.",
            to="rcpt@example.com",
        )

        body = service.users().drafts().update.call_args.kwargs["body"]
        assert body["message"]["threadId"] == "t-1"
        raw = _decoded_raw(body)
        assert "In-Reply-To: <parent@mail.example.com>" in raw
        assert "References: <root@mail.example.com> <parent@mail.example.com>" in raw

        # Pin the READ request too, not just the write. drafts.get accepts only
        # (userId, id, format) — it does NOT take metadataHeaders, unlike
        # messages.get/threads.get — and googleapiclient rejects unknown kwargs
        # at request-build time, before any network call. A bare Mock() swallows
        # them, so without this assertion an invalid kwarg passes CI and fails
        # for every real caller.
        # (the fixture itself calls .get() bare to install a return value, so
        # filter to the real, argument-bearing invocation)
        real_gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert real_gets == [call(userId="me", id="r-1", format="metadata")]

    @pytest.mark.asyncio
    async def test_update_of_unthreaded_draft_stays_unthreaded(self):
        """Gmail gives every message a threadId, including a standalone draft.
        Re-supplying that bare threadId without reply headers does not meet
        Gmail's criteria for thread membership, so it must NOT be sent."""
        service = _mock_service()  # no In-Reply-To / References
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()

        await _call(
            service,
            action="update",
            draft_id="r-1",
            subject="Standalone",
            body="Body",
            to="rcpt@example.com",
        )

        body = service.users().drafts().update.call_args.kwargs["body"]
        assert "threadId" not in body["message"]

    @pytest.mark.asyncio
    async def test_explicit_thread_id_wins_over_the_stored_one(self):
        """Inheriting must not block a deliberate re-thread.

        Also pins the read-skip: cc/bcc are passed as "" rather than omitted so
        that nothing is left to inherit, which is the only case where the
        stored draft does not need reading at all."""
        service = _mock_service(draft_headers=_THREADED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-9"},
        }
        service.users().drafts().update.reset_mock()
        service.users().drafts().get.reset_mock()

        await _call(
            service,
            action="update",
            draft_id="r-1",
            subject="Re: Elsewhere",
            body="B",
            to="rcpt@example.com",
            cc="",
            bcc="",
            thread_id="t-9",
            in_reply_to="<other@example.com>",
            references="<other@example.com>",
        )

        body = service.users().drafts().update.call_args.kwargs["body"]
        assert body["message"]["threadId"] == "t-9"
        # Every field the draft could supply was given explicitly, so there was
        # nothing to read back.
        service.users().drafts().get.assert_not_called()

    @pytest.mark.asyncio
    async def test_unreadable_draft_fails_loudly_rather_than_detaching(self):
        """If the existing threading cannot be read, refuse — proceeding would
        rebuild the message unthreaded with no signal to the caller.

        A 5xx is re-raised rather than retyped as a UserInputError: it is not
        the caller's input that is wrong, and handle_http_errors gives better
        advice for it (a 401/403 here means re-auth, not "pass thread_id")."""
        service = _mock_service()
        service.users().drafts().get().execute.side_effect = _http_error(500)

        with pytest.raises(HttpError):
            await _call(
                service,
                action="update",
                draft_id="r-1",
                subject="S",
                body="B",
                to="rcpt@example.com",
            )

        service.users().drafts().update.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_404_becomes_actionable_error(self):
        service = _mock_service()
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
        service = _mock_service()
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
        # The stale-Gmail-tab warning is for UPDATES only: a freshly created draft
        # has no earlier version for a browser to be holding.
        assert "refresh before touching it" not in result


@pytest.mark.asyncio
class TestTrashDraftNotice:
    """modify_gmail_message_labels warns when TRASH hits a message backing a DRAFT.

    Trashing a draft's underlying message leaves the draft listed — the trap that
    produced duplicate 'Draft, Draft' threads twice in real use. The notice costs
    nothing: the modify response already carries the post-modify labelIds.
    """

    async def _modify(self, service, **kwargs):
        from gmail.gmail_tools import modify_gmail_message_labels

        kwargs.setdefault("user_google_email", "user@example.com")
        return await _unwrap(modify_gmail_message_labels)(service=service, **kwargs)

    async def test_trashing_a_draft_message_appends_the_notice(self):
        service = Mock()
        service.users().messages().modify().execute.return_value = {
            "id": "m1",
            "labelIds": ["DRAFT", "TRASH"],
        }

        result = await self._modify(service, message_id="m1", add_label_ids=["TRASH"])

        assert "does NOT" in result and "remove the draft" in result
        assert "action='delete'" in result

    async def test_trashing_a_normal_message_stays_quiet(self):
        service = Mock()
        service.users().messages().modify().execute.return_value = {
            "id": "m2",
            "labelIds": ["TRASH"],
        }

        result = await self._modify(service, message_id="m2", add_label_ids=["TRASH"])

        assert "DRAFT" not in result

    async def test_non_trash_modify_on_a_draft_stays_quiet(self):
        service = Mock()
        service.users().messages().modify().execute.return_value = {
            "id": "m3",
            "labelIds": ["DRAFT", "STARRED"],
        }

        result = await self._modify(service, message_id="m3", add_label_ids=["STARRED"])

        assert "remove the draft" not in result


# A stored draft that already carries addressing. drafts.update replaces the
# message wholesale, so every one of these headers is lost unless re-sent.
_ADDRESSED_DRAFT_HEADERS = [
    {"name": "To", "value": "rcpt@example.com"},
    {"name": "Cc", "value": "watcher@example.com"},
    {"name": "Bcc", "value": "archive@example.com"},
    {"name": "Subject", "value": "Quarterly numbers"},
]


class TestUpdatePreservesAddressing:
    """Tri-state addressing on action='update'.

    Gmail's drafts.update has no partial-update mode: the supplied MIME becomes
    the draft, so anything omitted is destroyed. A model revising a draft passes
    the new body and nothing else, which used to silently strip the recipients.
    Omitted now means keep, "" means clear, a value means replace.

    Every assertion reads the REQUEST — the MIME actually handed to
    drafts().update — never the fixture. A fixture that returns a Cc proves
    nothing about whether one was sent.
    """

    @staticmethod
    def _service():
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()
        return service

    @staticmethod
    def _sent_raw(service) -> str:
        return _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])

    @pytest.mark.asyncio
    async def test_omitted_cc_is_preserved(self):
        service = self._service()
        await _call(service, action="update", draft_id="r-1", body="New wording.")
        assert "Cc: watcher@example.com" in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_empty_string_cc_clears_it(self):
        """The explicit-clear half. Without it, preserving would be a one-way
        door: a caller could add a Cc but never remove one."""
        service = self._service()
        await _call(
            service, action="update", draft_id="r-1", body="New wording.", cc=""
        )
        assert "Cc:" not in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_omitted_to_is_preserved(self):
        service = self._service()
        await _call(service, action="update", draft_id="r-1", body="New wording.")
        assert "To: rcpt@example.com" in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_supplied_to_replaces_the_stored_one(self):
        service = self._service()
        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            to="someone-else@example.com",
        )
        raw = self._sent_raw(service)
        assert "To: someone-else@example.com" in raw
        assert "rcpt@example.com" not in raw

    @pytest.mark.asyncio
    async def test_omitted_bcc_is_preserved(self):
        service = self._service()
        await _call(service, action="update", draft_id="r-1", body="New wording.")
        assert "Bcc: archive@example.com" in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_empty_string_bcc_clears_it(self):
        service = self._service()
        await _call(
            service, action="update", draft_id="r-1", body="New wording.", bcc=""
        )
        assert "Bcc:" not in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_subject_survives_even_when_to_is_supplied(self):
        """The asymmetry this change closes. The reply-context fetch that used
        to recover the subject was gated on `not to`, so passing a recipient
        silently cost the subject — supplying MORE information produced a WORSE
        result. The stored subject is now read directly."""
        service = self._service()
        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            to="rcpt@example.com",
        )
        assert "Subject: Quarterly numbers" in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_empty_string_subject_clears_it(self):
        service = self._service()
        await _call(
            service, action="update", draft_id="r-1", body="New wording.", subject=""
        )
        assert "Quarterly numbers" not in self._sent_raw(service)

    @pytest.mark.asyncio
    async def test_unthreaded_draft_keeps_its_addressing(self):
        """Preservation must not depend on the draft being part of a thread.
        The old recovery path required a threadId, so a standalone draft lost
        everything."""
        service = self._service()  # no In-Reply-To / References
        await _call(service, action="update", draft_id="r-1", body="New wording.")
        raw = self._sent_raw(service)
        assert "To: rcpt@example.com" in raw
        assert "Cc: watcher@example.com" in raw
        assert "Subject: Quarterly numbers" in raw
        body = service.users().drafts().update.call_args.kwargs["body"]
        assert "threadId" not in body["message"]

    @pytest.mark.asyncio
    async def test_create_does_not_inherit_anything(self):
        """action='create' has no draft to read, and must not acquire one."""
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().create().execute.return_value = {
            "id": "new-1",
            "message": {"id": "m-9"},
        }
        service.users().drafts().create.reset_mock()
        service.users().drafts().get.reset_mock()

        await _call(service, subject="Fresh", body="B", to="new@example.com")

        service.users().drafts().get.assert_not_called()
        raw = _decoded_raw(service.users().drafts().create.call_args.kwargs["body"])
        assert "watcher@example.com" not in raw
        assert "Quarterly numbers" not in raw


def _stub_thread(service, subject: str = "Thread subject") -> None:
    """Make the thread reply-context lookup return one real sent message."""
    service.users().threads().get().execute.return_value = {
        "messages": [
            {
                "id": "m-parent",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<parent@mail.example.com>"},
                        {"name": "Subject", "value": subject},
                        {"name": "From", "value": "sender@example.com"},
                    ]
                },
            }
        ]
    }


class TestUpdateClearBeatsReplyDerivation:
    """An explicit "" must not be quietly refilled from the thread.

    The create path derives a missing subject/recipient from the message being
    replied to. That is a feature, and it stays — but on update it must not
    override a caller who asked for the field to be empty, or the clear half of
    the contract silently fails in exactly the case (a threaded draft) where it
    is most likely to be used.
    """

    @staticmethod
    def _service(headers):
        service = _mock_service(draft_headers=headers)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-1"},
        }
        service.users().drafts().update.reset_mock()
        _stub_thread(service)
        return service

    # Threaded, and carrying no To — which is what lets the reply-context
    # lookup run at all.
    _HEADERS = _THREADED_DRAFT_HEADERS + [
        {"name": "Subject", "value": "Stored subject"}
    ]

    @pytest.mark.asyncio
    async def test_explicit_empty_subject_is_not_refilled_from_the_thread(self):
        service = self._service(self._HEADERS)
        await _call(
            service, action="update", draft_id="r-1", body="New wording.", subject=""
        )
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "Thread subject" not in raw
        assert "Stored subject" not in raw

    @pytest.mark.asyncio
    async def test_explicit_empty_to_is_not_refilled_from_the_thread(self):
        """Same guard, recipient side: the reply target's From/Reply-To must
        not resurrect a recipient the caller just cleared."""
        service = self._service(self._HEADERS)
        await _call(
            service, action="update", draft_id="r-1", body="New wording.", to=""
        )
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "sender@example.com" not in raw

    @pytest.mark.asyncio
    async def test_update_never_derives_from_the_reply_target(self):
        """On update the stored draft is the source of truth. A draft whose To
        was cleared by an EARLIER update has no stored To, so the next body-only
        update inherits nothing — and must not have the reply target's sender
        put back, which is what happened when only a same-call clear was
        guarded. "Omitted fields are kept" has to include kept-empty."""
        service = self._service(_THREADED_DRAFT_HEADERS)  # no stored To/Subject
        await _call(service, action="update", draft_id="r-1", body="New wording.")
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "sender@example.com" not in raw
        assert "Thread subject" not in raw

    @pytest.mark.asyncio
    async def test_update_does_not_derive_even_when_the_thread_is_fetched(self):
        """quote_original forces the reply-context fetch, so the reply target IS
        in hand — the case where skipping the fetch alone would not be enough."""
        service = self._service(_THREADED_DRAFT_HEADERS)  # no stored To/Subject
        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            quote_original=True,
        )
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "\nTo:" not in raw
        assert "Subject: Thread subject" not in raw

    @pytest.mark.asyncio
    async def test_create_still_derives_from_the_reply_target(self):
        """The control: the derivation is a create-path feature and stays one."""
        service = _mock_service()
        service.users().drafts().create().execute.return_value = {
            "id": "new-1",
            "message": {"id": "m-9", "threadId": "t-1"},
        }
        service.users().drafts().create.reset_mock()
        _stub_thread(service)

        await _call(service, body="Reply text.", thread_id="t-1")

        raw = _decoded_raw(service.users().drafts().create.call_args.kwargs["body"])
        assert "To: sender@example.com" in raw
        assert "Thread subject" in raw

    @pytest.mark.asyncio
    async def test_an_inherited_subject_is_not_re_prefixed(self):
        """ "Stored subject" became "Re: Stored subject" on every threaded
        rebuild, because the Re: prefixing meant for a subject being composed
        was applied to one being carried over."""
        service = self._service(self._HEADERS)
        await _call(service, action="update", draft_id="r-1", body="New wording.")
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "Subject: Stored subject" in raw
        assert "Re: Stored subject" not in raw

    @pytest.mark.asyncio
    async def test_a_cleared_subject_stays_cleared_on_a_threaded_rebuild(self):
        service = self._service(self._HEADERS)
        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            clear_fields=["subject"],
        )
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "Re:" not in raw

    @pytest.mark.asyncio
    async def test_a_supplied_subject_is_still_prefixed_for_a_reply(self):
        """Unchanged behaviour: Gmail needs matching subjects to keep a draft in
        its thread, so a subject composed in this call gets the prefix."""
        service = self._service(self._HEADERS)
        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            subject="Fresh subject",
        )
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "Subject: Re: Fresh subject" in raw


# ---------------------------------------------------------------------------
# action='update' with ONLY addressing: the stored message is patched in place
# rather than rebuilt, so the body and attachments survive.
# ---------------------------------------------------------------------------

_ATTACHMENT_BYTES = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4
_BODY_TEXT = "Numbers attached.\n\nLine two, with a trailing space \nand a café.\n"


def _stored_draft_raw(
    *,
    to: str = "rcpt@example.com",
    cc: str = "watcher@example.com",
    subject: str = "Quarterly numbers",
    threaded: bool = False,
) -> str:
    """A real multipart draft: text body + binary attachment, base64url encoded
    exactly as drafts.get(format='raw') returns it."""
    from email.message import EmailMessage as _EM
    from email.policy import SMTP as _SMTP

    msg = _EM(policy=_SMTP)
    msg["Subject"] = subject
    msg["From"] = "user@example.com"
    if to:
        msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if threaded:
        msg["In-Reply-To"] = "<parent@mail.example.com>"
        msg["References"] = "<root@mail.example.com> <parent@mail.example.com>"
    msg.set_content(_BODY_TEXT)
    msg.add_attachment(
        _ATTACHMENT_BYTES,
        maintype="image",
        subtype="png",
        filename="chart.png",
    )
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")


def _semantic_parts(raw_bytes: bytes) -> dict:
    """Decode a message down to what actually matters: the body text, and each
    attachment's filename, content type and decoded bytes. Byte-identical
    re-serialisation is NOT the bar — this is."""
    from email import message_from_bytes as _mfb
    from email.policy import SMTP as _SMTP

    msg = _mfb(raw_bytes, policy=_SMTP)
    body = None
    attachments = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        if filename:
            attachments.append(
                (filename, part.get_content_type(), part.get_payload(decode=True))
            )
        elif body is None:
            body = part.get_content()
    return {"body": body, "attachments": attachments}


def _patch_service(raw: Optional[str] = None, thread_id: Optional[str] = None) -> Mock:
    service = _mock_service()
    message = {"id": "m-1", "raw": raw if raw is not None else _stored_draft_raw()}
    if thread_id:
        message["threadId"] = thread_id
    service.users().drafts().get().execute.return_value = {
        "id": "r-1",
        "message": message,
    }
    service.users().drafts().update().execute.return_value = {
        "id": "r-1",
        "message": {"id": "m-2"},
    }
    service.users().drafts().update.reset_mock()
    service.users().drafts().get.reset_mock()
    return service


def _sent_message_bytes(service) -> bytes:
    body = service.users().drafts().update.call_args.kwargs["body"]
    return base64.urlsafe_b64decode(body["message"]["raw"])


class TestAddressingOnlyUpdatePatchesInPlace:
    """Adding a Cc must not cost the body.

    drafts.update replaces the whole message, so rebuilding is only safe when
    the caller supplied the content. When they supplied none of it, the stored
    message is fetched raw and its headers rewritten — nothing is regenerated,
    so the body, attachments and part structure come through untouched.
    """

    @pytest.mark.asyncio
    async def test_cc_change_preserves_body_and_attachment(self):
        service = _patch_service()
        original = _semantic_parts(base64.urlsafe_b64decode(_stored_draft_raw()))

        await _call(service, action="update", draft_id="r-1", cc="new@example.com")

        sent = _sent_message_bytes(service)
        after = _semantic_parts(sent)
        assert "Cc: new@example.com" in sent.decode("utf-8", "replace")
        assert "watcher@example.com" not in sent.decode("utf-8", "replace")
        # Semantic equality, decoded on both sides.
        assert after["body"] == original["body"]
        assert after["attachments"] == original["attachments"]
        assert after["attachments"][0][0] == "chart.png"
        assert after["attachments"][0][2] == _ATTACHMENT_BYTES

    @pytest.mark.asyncio
    async def test_empty_cc_removes_the_header(self):
        service = _patch_service()
        await _call(service, action="update", draft_id="r-1", cc="")
        sent = _sent_message_bytes(service).decode("utf-8", "replace")
        assert "Cc:" not in sent
        assert "watcher@example.com" not in sent

    @pytest.mark.asyncio
    async def test_subject_only_update_keeps_body_and_attachment(self):
        service = _patch_service()
        original = _semantic_parts(base64.urlsafe_b64decode(_stored_draft_raw()))

        await _call(service, action="update", draft_id="r-1", subject="Revised numbers")

        sent = _sent_message_bytes(service)
        assert "Subject: Revised numbers" in sent.decode("utf-8", "replace")
        after = _semantic_parts(sent)
        assert after["body"] == original["body"]
        assert after["attachments"] == original["attachments"]

    @pytest.mark.asyncio
    async def test_the_raw_fetch_is_the_only_read(self):
        """One call, and its shape is pinned. drafts.get takes (userId, id,
        format) and nothing else — an unsupported kwarg raises TypeError at
        request-build time, which a bare Mock would swallow."""
        service = _patch_service()
        await _call(service, action="update", draft_id="r-1", cc="new@example.com")
        real_gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert real_gets == [call(userId="me", id="r-1", format="raw")]

    @pytest.mark.asyncio
    async def test_thread_id_is_restated_for_a_threaded_draft(self):
        """threadId is a Draft.Message field, not a header, so it does not ride
        along in the raw bytes."""
        service = _patch_service(raw=_stored_draft_raw(threaded=True), thread_id="t-1")
        await _call(service, action="update", draft_id="r-1", cc="new@example.com")
        body = service.users().drafts().update.call_args.kwargs["body"]
        assert body["message"]["threadId"] == "t-1"

    @pytest.mark.asyncio
    async def test_unthreaded_draft_does_not_get_a_bare_thread_id(self):
        service = _patch_service(raw=_stored_draft_raw(), thread_id="t-1")
        await _call(service, action="update", draft_id="r-1", cc="new@example.com")
        body = service.users().drafts().update.call_args.kwargs["body"]
        assert "threadId" not in body["message"]

    @pytest.mark.asyncio
    async def test_response_says_which_path_ran(self):
        service = _patch_service()
        result = await _call(
            service, action="update", draft_id="r-1", cc="new@example.com"
        )
        assert "addressing only" in result
        assert "refresh before touching it" in result
        assert "body and attachments untouched" in result

    @pytest.mark.asyncio
    async def test_passing_a_body_takes_the_rebuild_path(self):
        """The boundary. A body means content was supplied, so the message is
        rebuilt — and the rebuild says so, because attachments are lost there."""
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()
        service.users().drafts().get.reset_mock()

        result = await _call(
            service,
            action="update",
            draft_id="r-1",
            body="Rewritten.",
            cc="new@example.com",
        )

        # Metadata read, not raw — the rebuild path's inheritance.
        real_gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert real_gets == [call(userId="me", id="r-1", format="metadata")]
        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "Rewritten." in raw
        assert "Cc: new@example.com" in raw
        # A's inheritance still applies on that path.
        assert "To: rcpt@example.com" in raw
        assert "message rebuilt" in result
        assert "refresh before touching it" in result

    @pytest.mark.asyncio
    async def test_create_never_reads_a_draft(self):
        service = _patch_service()
        service.users().drafts().create().execute.return_value = {
            "id": "new-1",
            "message": {"id": "m-9"},
        }
        service.users().drafts().create.reset_mock()
        service.users().drafts().get.reset_mock()

        await _call(service, subject="Fresh", body="B", to="new@example.com")

        service.users().drafts().get.assert_not_called()

    @pytest.mark.asyncio
    async def test_oversized_draft_is_refused_before_the_raw_fetch(self, monkeypatch):
        """Fail closed. Falling back to the rebuild path would silently discard
        the body and attachments this path exists to protect."""
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1024")
        service = _patch_service()
        service.users().drafts().get().execute.side_effect = [
            {"id": "r-1", "message": {"id": "m-1", "sizeEstimate": 20_000_000}},
            {"id": "r-1", "message": {"id": "m-1", "raw": _stored_draft_raw()}},
        ]
        service.users().drafts().get.reset_mock()

        # A configured cap rejecting the input is caller-correctable, so it is a
        # UserInputError (as on the forward-attachment path), and it says how to
        # proceed instead of surfacing as an unexpected failure.
        with pytest.raises(UserInputError, match="Nothing was written") as excinfo:
            await _call(service, action="update", draft_id="r-1", cc="new@example.com")
        assert "attachments" in str(excinfo.value)

        # Refused on the cheap probe; the raw fetch never happened.
        real_gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert real_gets == [call(userId="me", id="r-1", format="metadata")]
        service.users().drafts().update.assert_not_called()


class TestPatchRoundTripFidelity:
    """Direct unit tests on the re-serialisation, separate from the tool."""

    @pytest.mark.asyncio
    async def test_a_no_op_patch_is_byte_identical(self):
        """The cleanest fidelity evidence there is: parsing and re-serialising
        without mutating anything returns the original bytes exactly. So any
        difference observed after a real patch is attributable to the header
        that was deliberately changed, not to the round trip.

        The control input deliberately carries NO transport-added headers
        (Received etc.): the patch path strips those on purpose, so a message
        containing them legitimately differs. See TestPatchStripsTransportHeaders."""
        from gmail.gmail_tools import _patch_draft_addressing

        original = base64.urlsafe_b64decode(_stored_draft_raw())
        unchanged, _ = _patch_draft_addressing(
            original, to=None, cc=None, bcc=None, subject=None
        )
        assert unchanged == original

    @pytest.mark.asyncio
    async def test_a_real_patch_changes_only_that_header(self):
        from gmail.gmail_tools import _patch_draft_addressing

        original = base64.urlsafe_b64decode(_stored_draft_raw())
        patched, _ = _patch_draft_addressing(
            original, to=None, cc="new@example.com", bcc=None, subject=None
        )
        assert patched != original  # the Cc really did change
        before, after = _semantic_parts(original), _semantic_parts(patched)
        assert after["body"] == before["body"]
        assert after["attachments"] == before["attachments"]

    @pytest.mark.asyncio
    async def test_threadedness_is_reported_from_the_stored_headers(self):
        from gmail.gmail_tools import _patch_draft_addressing

        plain = base64.urlsafe_b64decode(_stored_draft_raw())
        reply = base64.urlsafe_b64decode(_stored_draft_raw(threaded=True))
        assert (
            _patch_draft_addressing(plain, to=None, cc="x@y.z", bcc=None, subject=None)[
                1
            ]
            is False
        )
        assert (
            _patch_draft_addressing(reply, to=None, cc="x@y.z", bcc=None, subject=None)[
                1
            ]
            is True
        )

    @pytest.mark.asyncio
    async def test_normalisation_alone_does_not_block_the_patch(self):
        """A bare-LF message re-serialises to different BYTES (CRLF) with no
        defects — it is valid, just normalised. Byte-identity was measured as a
        gate and rejected for exactly this reason: it would refuse ordinary
        drafts. The content is what must survive, and it does."""
        from gmail.gmail_tools import _patch_draft_addressing

        lf = b"Subject: hi\nFrom: a@b.c\nTo: d@e.f\n\nbody line\n"
        patched, _ = _patch_draft_addressing(
            lf, to=None, cc="new@example.com", bcc=None, subject=None
        )
        assert patched != lf  # normalised
        # The one semantic change normalisation makes: body line endings become
        # CRLF, which is what RFC 5322 requires on the wire. It cannot arise for
        # a draft fetched from Gmail — those are already CRLF, which is why the
        # realistic fixture above round-trips byte-for-byte.
        before = _semantic_parts(lf)["body"].replace("\n", "\r\n")
        assert _semantic_parts(patched)["body"] == before
        assert b"Cc: new@example.com" in patched

    @pytest.mark.asyncio
    async def test_structurally_broken_message_refuses_rather_than_corrupting(self):
        """Python's email parser almost never raises — it records a defect and
        guesses. Garbage comes back as a body-only message with
        MissingHeaderBodySeparatorDefect and re-serialises to different content.
        Falling back to a rebuild would silently drop the body, the exact loss
        this path exists to prevent, so it fails closed."""
        from gmail.gmail_tools import _patch_draft_addressing

        with pytest.raises(UserInputError, match="did not parse cleanly"):
            _patch_draft_addressing(
                b"\xff\xfe not a message at all \x00\x00",
                to=None,
                cc="x@y.z",
                bcc=None,
                subject=None,
            )


class TestUpdateMustChangeSomething:
    """An update carrying only draft_id used to wipe the draft.

    It passed every gate: the addressing-only path needs an addressing field,
    so it fell through to the rebuild path and reconstructed the message from
    empty arguments. The guard has to test for ABSENCE rather than falsiness —
    cc="" and subject="" are real updates that clear a field, and rejecting
    those would break the clear half of the contract.
    """

    @pytest.mark.asyncio
    async def test_bare_draft_id_is_rejected(self):
        service = _patch_service()
        with pytest.raises(UserInputError, match="nothing to change") as excinfo:
            await _call(service, action="update", draft_id="r-1")
        service.users().drafts().update.assert_not_called()
        service.users().drafts().get.assert_not_called()

        # An LLM caller acts on error text literally, so the recovery advice must
        # be something it can actually send. At least one MCP client was observed
        # to be unable to emit an empty string for an Optional[str] parameter (it
        # produced malformed JSON client-side), so steering a model toward ""
        # can send it into a retry loop. Name clear_fields; never "".
        message = str(excinfo.value)
        assert "clear_fields" in message
        assert "empty string" not in message

    @pytest.mark.asyncio
    async def test_clearing_the_cc_alone_is_a_real_update(self):
        """The falsiness trap: "" is not "unspecified"."""
        service = _patch_service()
        await _call(service, action="update", draft_id="r-1", cc="")
        sent = _sent_message_bytes(service).decode("utf-8", "replace")
        assert "Cc:" not in sent
        assert "watcher@example.com" not in sent

    @pytest.mark.asyncio
    async def test_clearing_the_subject_alone_is_a_real_update(self):
        service = _patch_service()
        await _call(service, action="update", draft_id="r-1", subject="")
        sent = _sent_message_bytes(service).decode("utf-8", "replace")
        assert "Quarterly numbers" not in sent

    @pytest.mark.asyncio
    async def test_create_with_nothing_is_not_touched_by_the_guard(self):
        """The guard is scoped to update; create has its own semantics and an
        empty create is not destructive."""
        service = _mock_service()
        service.users().drafts().create().execute.return_value = {
            "id": "new-1",
            "message": {"id": "m-9"},
        }
        service.users().drafts().create.reset_mock()

        result = await _call(service)

        service.users().drafts().create.assert_called_once()
        assert "Draft created" in result

    @pytest.mark.asyncio
    async def test_delete_with_only_draft_id_still_works(self):
        """delete takes ONLY draft_id by design — the guard must not catch it."""
        service = _mock_service()
        result = await _call(service, action="delete", draft_id="r-1")
        service.users().drafts().delete.assert_called_with(userId="me", id="r-1")
        assert "permanently deleted" in result


# ---------------------------------------------------------------------------
# action='list' — the only way to obtain a Draft ID you were not handed.
# ---------------------------------------------------------------------------


class _FakeBatch:
    """Stands in for googleapiclient's BatchHttpRequest.

    A bare Mock would let batch.execute() do nothing, the callback would never
    fire, and every metadata assertion would pass vacuously against
    '(metadata unavailable)'. This actually invokes the callback.
    """

    def __init__(self, callback, responses):
        self._callback = callback
        self._responses = responses
        self._ids: List[str] = []

    def add(self, request, request_id=None):
        self._ids.append(request_id)

    def execute(self):
        for request_id in self._ids:
            entry = self._responses.get(request_id)
            if isinstance(entry, Exception):
                self._callback(request_id, None, entry)
            else:
                self._callback(request_id, entry, None)


def _draft_meta(subject: str, to: str, snippet: str = "") -> dict:
    return {
        "message": {
            "snippet": snippet,
            "payload": {
                "headers": [
                    {"name": "Subject", "value": subject},
                    {"name": "To", "value": to},
                ]
            },
        }
    }


def _list_service(drafts, metadata=None, next_page_token=None) -> Mock:
    service = Mock()
    listing = {"drafts": drafts}
    if next_page_token:
        listing["nextPageToken"] = next_page_token
    service.users().drafts().list().execute.return_value = listing
    service.users().drafts().list.reset_mock()
    responses = metadata or {}
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(
        callback, responses
    )
    service.users().drafts().get.reset_mock()
    return service


_TWO_DRAFTS = [
    {"id": "r-1", "message": {"id": "m-1", "threadId": "t-1"}},
    {"id": "r-2", "message": {"id": "m-2", "threadId": "t-2"}},
]


class TestListDrafts:
    """drafts.list is the only call that reads the drafts resource, so this is
    the only lookup for a Draft ID. Search tools return Message IDs and Thread
    IDs, which drafts.update / drafts.delete / drafts.send all reject."""

    @pytest.mark.asyncio
    async def test_lists_every_id_plus_readable_metadata(self):
        service = _list_service(
            _TWO_DRAFTS,
            metadata={
                "r-1": _draft_meta("Quarterly numbers", "a@example.com", "Numbers..."),
                "r-2": _draft_meta("Re: Lunch", "b@example.com"),
            },
        )
        result = await _call(service, action="list")

        assert "Draft ID: r-1" in result and "Draft ID: r-2" in result
        assert "Message ID: m-1" in result
        assert "Thread ID: t-1" in result
        assert "Quarterly numbers" in result and "a@example.com" in result
        assert "Numbers..." in result

    @pytest.mark.asyncio
    async def test_the_list_request_uses_googles_wire_names(self):
        """Our parameters are page_size/page_token — Gmail's are maxResults and
        pageToken. The mapping has to happen, or paging silently ignores us."""
        service = _list_service(_TWO_DRAFTS)
        await _call(service, action="list", page_size=5, page_token="tok-1")
        assert service.users().drafts().list.call_args == call(
            userId="me", maxResults=5, pageToken="tok-1"
        )

    @pytest.mark.asyncio
    async def test_per_draft_reads_never_pass_metadataHeaders(self):
        """THE trap on this resource. users.drafts.get takes only
        (userId, id, format) — unlike users.messages.get and users.threads.get
        it has no metadataHeaders, and googleapiclient rejects unknown kwargs at
        request-build time. The fork was bitten by exactly this once, and this
        PR reintroduced it once."""
        service = _list_service(
            _TWO_DRAFTS, metadata={"r-1": _draft_meta("S", "a@b.c")}
        )
        await _call(service, action="list")
        gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert gets == [
            call(userId="me", id="r-1", format="metadata"),
            call(userId="me", id="r-2", format="metadata"),
        ]

    @pytest.mark.asyncio
    async def test_a_draft_whose_metadata_fails_is_still_listed(self):
        """Best effort: an ID the caller can act on is the point of the tool."""
        service = _list_service(
            _TWO_DRAFTS,
            metadata={
                "r-1": _draft_meta("Readable", "a@example.com"),
                "r-2": _http_error(500),
            },
        )
        result = await _call(service, action="list")
        assert "Draft ID: r-2" in result
        assert "metadata unavailable" in result

    @pytest.mark.asyncio
    async def test_no_drafts_says_where_ids_come_from(self):
        service = _list_service([])
        result = await _call(service, action="list")
        assert "No drafts found" in result
        assert "action='create'" in result

    @pytest.mark.asyncio
    async def test_next_page_token_is_surfaced(self):
        service = _list_service(_TWO_DRAFTS, next_page_token="tok-2")
        result = await _call(service, action="list")
        assert "page_token='tok-2'" in result

    @pytest.mark.asyncio
    async def test_draft_id_is_rejected(self):
        service = _list_service(_TWO_DRAFTS)
        with pytest.raises(UserInputError, match="takes no id"):
            await _call(service, action="list", draft_id="r-1")
        service.users().drafts().list.assert_not_called()

    @pytest.mark.asyncio
    async def test_content_arguments_are_rejected_by_name(self):
        service = _list_service(_TWO_DRAFTS)
        with pytest.raises(UserInputError, match="body, subject"):
            await _call(service, action="list", subject="S", body="B")
        service.users().drafts().list.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_placeholders_for_unset_arguments_are_tolerated(self):
        """Some clients send ""/[] for every argument they did not set. 'delete'
        already tolerates that (truthiness); 'list' rejecting it would leave
        those clients unable to list at all. "" only MEANS something on update."""
        service = _list_service(_TWO_DRAFTS)
        result = await _call(
            service, action="list", cc="", subject="", attachments=[], clear_fields=[]
        )
        assert "Found 2 draft(s)" in result

    @pytest.mark.asyncio
    async def test_snippets_are_html_unescaped(self):
        service = _list_service(
            _TWO_DRAFTS,
            metadata={"r-1": _draft_meta("S", "a@b.c", "it&#39;s &amp; done")},
        )
        result = await _call(service, action="list")
        assert "it's & done" in result
        assert "&#39;" not in result

    @pytest.mark.asyncio
    async def test_batch_failure_falls_back_to_sequential_reads(self):
        service = _list_service(_TWO_DRAFTS)
        service.new_batch_http_request.side_effect = RuntimeError("batch down")
        service.users().drafts().get().execute.return_value = _draft_meta(
            "Sequential subject", "seq@example.com"
        )
        service.users().drafts().get.reset_mock()

        result = await _call(service, action="list")

        assert result.count("Sequential subject") == 2
        gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert gets == [
            call(userId="me", id="r-1", format="metadata"),
            call(userId="me", id="r-2", format="metadata"),
        ]

    @pytest.mark.asyncio
    async def test_more_drafts_than_one_batch_are_all_read(self, monkeypatch):
        """Twelve drafts, batch size ten: the second chunk must not be dropped."""
        import gmail.gmail_tools as gt

        monkeypatch.setattr(gt, "GMAIL_REQUEST_DELAY", 0)
        drafts = [
            {"id": f"r-{i}", "message": {"id": f"m-{i}", "threadId": f"t-{i}"}}
            for i in range(12)
        ]
        service = _list_service(
            drafts,
            metadata={
                f"r-{i}": _draft_meta(f"Subject {i}", "a@b.c") for i in range(12)
            },
        )
        result = await _call(service, action="list")
        assert service.new_batch_http_request.call_count == 2
        for i in range(12):
            assert f"Subject {i}" in result
        assert "metadata unavailable" not in result

    @pytest.mark.asyncio
    async def test_page_size_is_bounded(self):
        service = _list_service(_TWO_DRAFTS)
        with pytest.raises(UserInputError, match="between 1 and 100"):
            await _call(service, action="list", page_size=500)
        with pytest.raises(UserInputError, match="between 1 and 100"):
            await _call(service, action="list", page_size=0)
        service.users().drafts().list.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_is_not_caught_by_the_no_op_update_guard(self):
        """The guard rejects an update with nothing to change; it must not fire
        for a list, which legitimately carries no content."""
        service = _list_service(_TWO_DRAFTS)
        result = await _call(service, action="list")
        assert "Found 2 draft(s)" in result


# ---------------------------------------------------------------------------
# clear_fields — the transport-safe way to clear. Some clients cannot send an
# empty string for an Optional[str] parameter at all, so "" alone is not enough.
# ---------------------------------------------------------------------------


class TestClearFields:
    @pytest.mark.asyncio
    async def test_clears_on_the_patch_path_with_body_and_attachment_intact(self):
        service = _patch_service()
        original = _semantic_parts(base64.urlsafe_b64decode(_stored_draft_raw()))

        result = await _call(
            service, action="update", draft_id="r-1", clear_fields=["cc"]
        )

        sent = _sent_message_bytes(service)
        text = sent.decode("utf-8", "replace")
        assert "Cc:" not in text and "watcher@example.com" not in text
        assert "To: rcpt@example.com" in text  # not named, not passed -> kept
        after = _semantic_parts(sent)
        assert after["body"] == original["body"]
        assert after["attachments"] == original["attachments"]
        # clear_fields alone must NOT force a rebuild (which drops attachments).
        assert "addressing only" in result
        gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert gets == [call(userId="me", id="r-1", format="raw")]

    @pytest.mark.asyncio
    async def test_clears_on_the_rebuild_path(self):
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()

        result = await _call(
            service,
            action="update",
            draft_id="r-1",
            body="Rewritten.",
            clear_fields=["cc", "bcc"],
        )

        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "Cc:" not in raw and "Bcc:" not in raw
        assert "To: rcpt@example.com" in raw  # still inherited
        assert "Subject: Quarterly numbers" in raw
        assert "message rebuilt" in result

    @pytest.mark.asyncio
    async def test_cleared_to_is_not_refilled_from_the_reply_target(self):
        """Same guard the "" spelling relies on: on a threaded draft the reply
        target's From would otherwise resurrect the recipient just cleared."""
        service = _mock_service(
            draft_headers=_THREADED_DRAFT_HEADERS
            + [{"name": "Subject", "value": "Stored subject"}]
        )
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-1"},
        }
        service.users().drafts().update.reset_mock()
        _stub_thread(service)

        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            clear_fields=["to"],
        )

        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "sender@example.com" not in raw
        assert "\nTo:" not in raw

    @pytest.mark.asyncio
    async def test_clearing_and_setting_the_same_field_is_an_error(self):
        service = _patch_service()
        with pytest.raises(UserInputError, match="not both"):
            await _call(
                service,
                action="update",
                draft_id="r-1",
                cc="x@example.com",
                clear_fields=["cc"],
            )
        service.users().drafts().update.assert_not_called()

    @pytest.mark.asyncio
    async def test_clear_fields_plus_empty_string_agree_and_are_allowed(self):
        service = _patch_service()
        await _call(
            service, action="update", draft_id="r-1", cc="", clear_fields=["cc"]
        )
        assert "Cc:" not in _sent_message_bytes(service).decode("utf-8", "replace")

    @pytest.mark.parametrize("action", ["create", "delete", "list"])
    @pytest.mark.asyncio
    async def test_rejected_outside_update(self, action):
        service = _list_service(_TWO_DRAFTS)
        kwargs = {"action": action, "clear_fields": ["cc"]}
        if action == "delete":
            kwargs["draft_id"] = "r-1"
        with pytest.raises(UserInputError, match="only applies to action='update'"):
            await _call(service, **kwargs)
        service.users().drafts().delete.assert_not_called()
        service.users().drafts().create.assert_not_called()
        service.users().drafts().list.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitespace_only_clears(self):
        service = _patch_service()
        await _call(service, action="update", draft_id="r-1", cc="   ")
        text = _sent_message_bytes(service).decode("utf-8", "replace")
        assert "Cc:" not in text

    @pytest.mark.asyncio
    async def test_a_padded_real_value_is_a_value_not_a_clear(self):
        """Blank-detection decides clear-vs-not and nothing else: a value with
        surrounding whitespace is still a value."""
        service = _patch_service()
        await _call(service, action="update", draft_id="r-1", cc="  new@example.com  ")
        text = _sent_message_bytes(service).decode("utf-8", "replace")
        assert "new@example.com" in text
        assert "watcher@example.com" not in text

    @pytest.mark.asyncio
    async def test_null_still_means_preserve(self):
        """Pins the strict-function-calling case. Clients such as OpenAI strict
        mode send an explicit null for EVERY unset optional parameter. If null
        ever came to mean "clear", a plain body-only update would wipe the
        recipients — the very bug the preserve contract fixes."""
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()

        await _call(
            service,
            action="update",
            draft_id="r-1",
            body="New wording.",
            to=None,
            cc=None,
            bcc=None,
            subject=None,
            clear_fields=None,
        )

        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "To: rcpt@example.com" in raw
        assert "Cc: watcher@example.com" in raw
        assert "Bcc: archive@example.com" in raw
        assert "Subject: Quarterly numbers" in raw

    @pytest.mark.asyncio
    async def test_empty_clear_fields_changes_nothing_so_the_no_op_guard_fires(self):
        service = _patch_service()
        with pytest.raises(UserInputError, match="nothing to change"):
            await _call(service, action="update", draft_id="r-1", clear_fields=[])

    @pytest.mark.asyncio
    async def test_a_typoed_field_name_fails_schema_validation(self):
        """The Literal is the guard: a misspelt field must be refused by the
        schema, not silently ignored (which would read as "cleared").

        Validates against the tool's REAL annotation, read off the function —
        not a copy of it, and not the global server registry, which other test
        modules filter and would make this order-dependent."""
        from typing import get_type_hints

        from pydantic import TypeAdapter, ValidationError

        hints = get_type_hints(_unwrap(draft_gmail_message), include_extras=True)
        adapter = TypeAdapter(hints["clear_fields"])

        assert adapter.validate_python(["to", "cc", "bcc", "subject"]) == [
            "to",
            "cc",
            "bcc",
            "subject",
        ]
        assert adapter.validate_python(None) is None
        with pytest.raises(ValidationError):
            adapter.validate_python(["ccc"])
        with pytest.raises(ValidationError):
            adapter.validate_python(["body"])  # body is deliberately not clearable


def _with_transport_headers(raw_b64: str) -> bytes:
    """Prepend what Gmail's store adds: a Received stamp per insert, etc."""
    stamped = (
        b"Received: by 2002:a05:1234 with HTTP; Sat, 20 Sep 2026 10:00:00 -0700 (PDT)\r\n"
        b"Received: by 2002:a05:5678 with HTTP; Sat, 20 Sep 2026 10:05:00 -0700 (PDT)\r\n"
        b"X-Received: by 2002:a17:90a with SMTP id abc; Sat, 20 Sep 2026 10:05:01 -0700\r\n"
        b"Return-Path: <user@example.com>\r\n"
        b"Delivered-To: user@example.com\r\n"
        b"Message-ID: <draft-1@mail.example.com>\r\n"
        b"Date: Sat, 20 Sep 2026 10:00:00 -0700\r\n"
    )
    return stamped + base64.urlsafe_b64decode(raw_b64)


class TestPatchStripsTransportHeaders:
    """Gmail stamps Received: on every draft insert and the patch path re-uploads
    the stored message verbatim, so without stripping they accumulate: N patches
    leave N+1 Received lines (observed live)."""

    @pytest.mark.asyncio
    async def test_transport_headers_go_and_everything_composed_stays(self):
        from email import message_from_bytes as _mfb
        from email.policy import SMTP as _SMTP
        from gmail.gmail_tools import _patch_draft_addressing

        stored = _with_transport_headers(_stored_draft_raw(threaded=True))
        before = _semantic_parts(stored)

        patched, is_threaded = _patch_draft_addressing(
            stored, to=None, cc="new@example.com", bcc=None, subject=None
        )
        msg = _mfb(patched, policy=_SMTP)

        for gone in ("Received", "X-Received", "Return-Path", "Delivered-To"):
            assert msg.get_all(gone) is None, gone
        # The addressing change was applied...
        assert msg["Cc"] == "new@example.com"
        # ...and what the user (or Gmail, on their behalf) composed is untouched.
        assert msg["From"] == "user@example.com"
        assert msg["Message-ID"] == "<draft-1@mail.example.com>"
        assert msg["Date"] is not None
        assert msg["In-Reply-To"] == "<parent@mail.example.com>"
        assert "<root@mail.example.com>" in msg["References"]
        assert is_threaded is True
        after = _semantic_parts(patched)
        assert after["body"] == before["body"]
        assert after["attachments"] == before["attachments"]

    @pytest.mark.asyncio
    async def test_repeated_patches_do_not_accumulate(self):
        """Feed each patch's output back in with a fresh stamp, as Gmail would."""
        from email import message_from_bytes as _mfb
        from email.policy import SMTP as _SMTP
        from gmail.gmail_tools import _patch_draft_addressing

        current = base64.urlsafe_b64decode(_stored_draft_raw())
        for i in range(3):
            restamped = (
                f"Received: by stamp-{i}; Sat, 20 Sep 2026 10:0{i}:00 -0700\r\n".encode()
                + current
            )
            current, _ = _patch_draft_addressing(
                restamped, to=None, cc=f"round{i}@example.com", bcc=None, subject=None
            )
        assert _mfb(current, policy=_SMTP).get_all("Received") is None

    @pytest.mark.asyncio
    async def test_end_to_end_through_the_tool(self):
        raw = base64.urlsafe_b64encode(
            _with_transport_headers(_stored_draft_raw())
        ).decode("ascii")
        service = _patch_service(raw=raw)
        await _call(service, action="update", draft_id="r-1", cc="new@example.com")
        text = _sent_message_bytes(service).decode("utf-8", "replace")
        assert "Received:" not in text
        assert "Cc: new@example.com" in text


# ---------------------------------------------------------------------------
# Review round: long source headers, body-required rebuilds, and edge handling.
# ---------------------------------------------------------------------------

_LONG_MSG_ID = (
    "<BN8PR12MB30115F0A9C7E4B2D8A1F3C6E9D7B0A2C4E6F8091A3B5"
    "@BN8PR12MB3011.namprd12.prod.outlook.com>"
)


class TestPatchDoesNotRefoldSourceHeaders:
    """Plain email.policy.SMTP refolds any SOURCE header over 78 chars when it
    serialises. In-Reply-To/References are unstructured to the email package, so
    a Message-ID too long for one line came out as an RFC 2047 encoded-word —
    illegal inside a msg-id, and fatal to threading. Exchange/Outlook IDs are
    routinely this long; Gmail's are not, which is why live testing missed it."""

    @staticmethod
    def _stored() -> bytes:
        from email.message import EmailMessage as _EM
        from email.policy import SMTP as _SMTP

        # Assemble by hand: the source must contain the long headers VERBATIM,
        # as Gmail would return them, not as the email package would fold them.
        inner = _EM(policy=_SMTP)
        inner["Subject"] = "Re: Contract"
        inner["From"] = "user@example.com"
        inner["To"] = "rcpt@example.com"
        inner.set_content("Body text.\n")
        body_bytes = inner.as_bytes()
        head, _, rest = body_bytes.partition(b"\r\n")
        return (
            head
            + b"\r\n"
            + f"In-Reply-To: {_LONG_MSG_ID}\r\n".encode()
            + f"References: {_LONG_MSG_ID}\r\n".encode()
            + rest
        )

    @pytest.mark.asyncio
    async def test_long_message_ids_survive_verbatim(self):
        from gmail.gmail_tools import _patch_draft_addressing

        assert len(_LONG_MSG_ID) > 80
        patched, is_threaded = _patch_draft_addressing(
            self._stored(), to=None, cc="new@example.com", bcc=None, subject=None
        )
        text = patched.decode("ascii")
        assert f"In-Reply-To: {_LONG_MSG_ID}\r\n" in text
        assert f"References: {_LONG_MSG_ID}\r\n" in text
        assert "=?utf-8?" not in text  # no encoded-word anywhere
        assert is_threaded is True

    @pytest.mark.asyncio
    async def test_long_and_encoded_attachment_filenames_are_unchanged(self):
        from gmail.gmail_tools import _patch_draft_addressing

        long_name = (
            "Quarterly-financial-summary-and-forward-projections-FY2026-"
            + "x" * 40
            + ".pdf"
        )
        encoded_name = (
            "=?utf-8?B?0J7RgtGH0ZHRgi5wZGY=?="  # RFC 2047, as some senders emit
        )
        stored = (
            b"Subject: Files\r\nFrom: user@example.com\r\nTo: rcpt@example.com\r\n"
            b"MIME-Version: 1.0\r\n"
            b'Content-Type: multipart/mixed; boundary="BOUND"\r\n\r\n'
            b"--BOUND\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nBody.\r\n"
            b"--BOUND\r\nContent-Type: application/pdf\r\n"
            + f'Content-Disposition: attachment; filename="{long_name}"\r\n'.encode()
            + b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n"
            b"--BOUND\r\nContent-Type: application/pdf\r\n"
            + f'Content-Disposition: attachment; filename="{encoded_name}"\r\n'.encode()
            + b"Content-Transfer-Encoding: base64\r\n\r\nJVBERi0=\r\n"
            b"--BOUND--\r\n"
        )
        patched, _ = _patch_draft_addressing(
            stored, to=None, cc="new@example.com", bcc=None, subject=None
        )
        text = patched.decode("ascii")
        assert f'filename="{long_name}"' in text
        assert f'filename="{encoded_name}"' in text
        assert "filename*0" not in text  # no RFC 2231 continuation rewriting

    @pytest.mark.asyncio
    async def test_a_newly_set_non_ascii_header_is_still_encoded(self):
        """refold_source="none" must only spare SOURCE headers. One set here
        still has to be encoded, or the output is not valid 7-bit mail."""
        from gmail.gmail_tools import _patch_draft_addressing

        patched, _ = _patch_draft_addressing(
            self._stored(), to=None, cc=None, bcc=None, subject="Отчёт за квартал"
        )
        text = patched.decode("ascii")  # would raise if raw UTF-8 leaked through
        assert "=?utf-8?" in text.split("Subject:")[1].split("\r\n")[0]


class TestRebuildRequiresABody:
    """The rebuild path composes a NEW message, so a missing body is an empty
    body. Every call here used to clear the no-op guard, miss the
    addressing-only gate, wipe the text, and report success."""

    @pytest.mark.parametrize(
        "kwargs,named",
        [
            (
                {"attachments": [{"filename": "a.txt", "content": "aGk="}]},
                "attachments",
            ),
            ({"attachments": []}, "attachments"),
            ({"from_name": "Andy"}, "from_name"),
            ({"thread_id": "t-NEW"}, "thread_id"),
            ({"body_format": "html", "cc": "x@example.com"}, "body_format"),
            ({"quote_original": True, "cc": "x@example.com"}, "quote_original"),
            ({"thread_id": "t-NEW", "cc": "x@example.com"}, "thread_id"),
            ({"in_reply_to": "<p@example.com>", "cc": "x@example.com"}, "in_reply_to"),
            ({"references": "<p@example.com>", "cc": "x@example.com"}, "references"),
            ({"from_email": "alias@example.com", "cc": "x@example.com"}, "from_email"),
        ],
    )
    @pytest.mark.asyncio
    async def test_rejected_with_nothing_written(self, kwargs, named):
        service = _patch_service()
        with pytest.raises(
            UserInputError, match="does not keep the existing body"
        ) as exc:
            await _call(service, action="update", draft_id="r-1", **kwargs)
        assert named in str(exc.value)
        service.users().drafts().update.assert_not_called()
        service.users().drafts().get.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_rethread_is_never_reported_as_addressing_only(self):
        """thread_id counted as "supplied" for the no-op guard but was invisible
        to the addressing-only gate, so this call said "addressing only" while
        silently dropping the re-thread. With a body it now rebuilds."""
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-NEW"},
        }
        service.users().drafts().update.reset_mock()

        result = await _call(
            service,
            action="update",
            draft_id="r-1",
            body="B",
            cc="x@example.com",
            thread_id="t-NEW",
            in_reply_to="<p@example.com>",
            references="<p@example.com>",
        )
        assert "message rebuilt" in result and "addressing only" not in result
        body = service.users().drafts().update.call_args.kwargs["body"]
        assert body["message"]["threadId"] == "t-NEW"

    @pytest.mark.asyncio
    async def test_an_explicit_empty_body_is_a_real_request(self):
        """body="" is supplied, not absent — the caller asked for an empty body."""
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()
        result = await _call(service, action="update", draft_id="r-1", body="")
        assert "message rebuilt" in result

    @pytest.mark.asyncio
    async def test_an_explicit_empty_body_satisfies_the_body_required_guard(self):
        """The guard is `body is None`, not `not body`. With a rebuild reason
        present, body="" must still rebuild — a truthiness "cleanup" here would
        reject a caller who deliberately asked for an empty body."""
        service = _mock_service(draft_headers=_ADDRESSED_DRAFT_HEADERS)
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()
        result = await _call(
            service, action="update", draft_id="r-1", body="", from_name="X"
        )
        assert "message rebuilt" in result
        service.users().drafts().update.assert_called_once()


class TestPatchPathEdges:
    @pytest.mark.asyncio
    async def test_missing_draft_gets_the_list_recovery_guidance(self):
        service = _patch_service()
        service.users().drafts().get().execute.side_effect = _http_error(404)
        with pytest.raises(UserInputError, match=r"action='list'"):
            await _call(service, action="update", draft_id="r-gone", cc="x@example.com")
        service.users().drafts().update.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_http_errors_are_not_retyped(self):
        service = _patch_service()
        service.users().drafts().get().execute.side_effect = _http_error(500)
        with pytest.raises(HttpError):
            await _call(service, action="update", draft_id="r-1", cc="x@example.com")

    @pytest.mark.asyncio
    async def test_unknown_size_is_refused_when_a_cap_is_set(self, monkeypatch):
        """Fail CLOSED. The shared helper treats an unknown size as fine; an
        operator who configured a cap did not ask for that."""
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1048576")
        service = _patch_service()
        # Two responses, so that a fail-OPEN regression reaches the raw fetch and
        # fails this test cleanly. (With one, the exhausted side_effect raises
        # StopIteration inside asyncio.to_thread, which hangs instead of failing.)
        service.users().drafts().get().execute.side_effect = [
            {"id": "r-1", "message": {"id": "m-1"}},  # probe: no sizeEstimate
            {"id": "r-1", "message": {"id": "m-1", "raw": _stored_draft_raw()}},
        ]
        service.users().drafts().get.reset_mock()

        with pytest.raises(UserInputError, match="did not report a size"):
            await _call(service, action="update", draft_id="r-1", cc="x@example.com")

        gets = [c for c in service.users().drafts().get.call_args_list if c.kwargs]
        assert gets == [call(userId="me", id="r-1", format="metadata")]
        service.users().drafts().update.assert_not_called()

    @pytest.mark.parametrize("path_kwargs", [{}, {"body": "B"}])
    @pytest.mark.asyncio
    async def test_a_line_break_in_an_address_is_a_clean_error(self, path_kwargs):
        service = _patch_service()
        with pytest.raises(UserInputError, match="line break"):
            await _call(
                service,
                action="update",
                draft_id="r-1",
                cc="a@example.com\nBcc: evil@example.com",
                **path_kwargs,
            )
        service.users().drafts().update.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_folded_stored_header_does_not_crash_a_body_update(self):
        """A long To can come back folded; assigning a value with a line break
        to a header raises ValueError. Unfolded on the way in instead."""
        folded = "a@example.com,\r\n b@example.com,\r\n\tc@example.com"
        service = _mock_service(
            draft_headers=[
                {"name": "To", "value": folded},
                {"name": "Subject", "value": "S"},
            ]
        )
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2"},
        }
        service.users().drafts().update.reset_mock()

        await _call(service, action="update", draft_id="r-1", body="New wording.")

        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        for addr in ("a@example.com", "b@example.com", "c@example.com"):
            assert addr in raw

    @pytest.mark.asyncio
    async def test_the_stale_tab_notice_claims_only_what_was_observed(self):
        service = _patch_service()
        result = await _call(
            service, action="update", draft_id="r-1", cc="x@example.com"
        )
        assert "refresh before touching it" in result
        assert "can silently overwrite" in result
        assert "will silently overwrite" not in result


class TestPolishPins:
    @pytest.mark.asyncio
    async def test_a_folded_references_header_is_unfolded_with_a_space(self):
        """Unfolding must replace the fold with a SPACE. Replacing it with
        nothing glues <a@x>\r\n <b@y> into <a@x><b@y>, which is no longer a
        list of message-ids."""
        service = _mock_service(
            draft_headers=[
                {"name": "In-Reply-To", "value": "<b@example.com>"},
                {
                    "name": "References",
                    "value": "<a@example.com>\r\n <b@example.com>",
                },
                {"name": "To", "value": "rcpt@example.com"},
                {"name": "Subject", "value": "Re: S"},
            ]
        )
        service.users().drafts().update().execute.return_value = {
            "id": "r-1",
            "message": {"id": "m-2", "threadId": "t-1"},
        }
        service.users().drafts().update.reset_mock()

        await _call(service, action="update", draft_id="r-1", body="New wording.")

        raw = _decoded_raw(service.users().drafts().update.call_args.kwargs["body"])
        assert "References: <a@example.com> <b@example.com>" in raw
        assert "<a@example.com><b@example.com>" not in raw

    @pytest.mark.asyncio
    async def test_an_empty_page_token_placeholder_is_not_sent_as_a_cursor(self):
        service = _list_service(_TWO_DRAFTS)
        await _call(service, action="list", page_token="")
        assert service.users().drafts().list.call_args == call(
            userId="me", maxResults=25, pageToken=None
        )

    @pytest.mark.asyncio
    async def test_the_line_break_hint_fits_the_field(self):
        service = _patch_service()
        with pytest.raises(UserInputError) as subject_exc:
            await _call(service, action="update", draft_id="r-1", subject="a\nb")
        assert "one line" in str(subject_exc.value)
        assert "addresses" not in str(subject_exc.value)

        with pytest.raises(UserInputError) as cc_exc:
            await _call(service, action="update", draft_id="r-1", cc="a@b.c\nd@e.f")
        assert "commas" in str(cc_exc.value)

    @pytest.mark.asyncio
    async def test_the_unknown_size_error_mentions_attachments(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1048576")
        service = _patch_service()
        service.users().drafts().get().execute.side_effect = [
            {"id": "r-1", "message": {"id": "m-1"}},
            {"id": "r-1", "message": {"id": "m-1", "raw": _stored_draft_raw()}},
        ]
        with pytest.raises(UserInputError, match="attachments"):
            await _call(service, action="update", draft_id="r-1", cc="x@example.com")
