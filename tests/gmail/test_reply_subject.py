"""Tests for reply-subject normalization and subject inheritance on replies."""

import base64
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gmail.gmail_helpers import normalize_reply_subject
from gmail.gmail_tools import (
    _prepare_gmail_message,
    draft_gmail_message,
    send_gmail_message,
)


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _parse(raw_message: str):
    return BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw_message)
    )


def _thread_with_parent_subject(subject: str) -> dict:
    return {
        "messages": [
            {
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Message-ID", "value": "<parent@example.com>"},
                        {"name": "From", "value": "Ada <ada@example.com>"},
                        {"name": "Subject", "value": subject},
                    ]
                },
            }
        ]
    }


def _service_with_parent(subject: str) -> Mock:
    service = Mock()
    service.users().threads().get().execute.return_value = _thread_with_parent_subject(
        subject
    )
    service.users().messages().send().execute.return_value = {"id": "sent"}
    service.users().drafts().create().execute.return_value = {"id": "draft"}
    service.users().settings().sendAs().list().execute.return_value = {
        "sendAs": [
            {"sendAsEmail": "you@example.org", "isPrimary": True, "isDefault": True}
        ]
    }
    service.users().messages().send.reset_mock()
    service.users().drafts().create.reset_mock()
    return service


def _sent_subject(service: Mock) -> str:
    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    return _parse(kwargs["body"]["raw"])["Subject"]


def _drafted_subject(service: Mock) -> str:
    kwargs = service.users.return_value.drafts.return_value.create.call_args.kwargs
    return _parse(kwargs["body"]["message"]["raw"])["Subject"]


class TestNormalizeReplySubject:
    def test_plain_subject_gets_single_re_prefix(self):
        assert normalize_reply_subject("Project update") == "Re: Project update"

    def test_existing_re_is_not_doubled(self):
        assert normalize_reply_subject("Re: Project update") == "Re: Project update"

    def test_uppercase_re_is_treated_as_reply(self):
        assert normalize_reply_subject("RE: Project update") == "RE: Project update"

    def test_list_tag_before_existing_re_is_left_verbatim(self):
        subject = "[list] Re: RE: Project status [#123]"
        assert normalize_reply_subject(subject) == subject

    def test_list_tag_without_re_gets_single_re_and_keeps_tag(self):
        assert (
            normalize_reply_subject("[list] Project status [#123]")
            == "Re: [list] Project status [#123]"
        )

    def test_word_starting_with_re_is_not_a_reply_prefix(self):
        assert normalize_reply_subject("Report: Q3") == "Re: Report: Q3"

    def test_is_idempotent(self):
        for s in ["Project update", "Re: X", "[list] Re: RE: X [#1]", "[list] X"]:
            once = normalize_reply_subject(s)
            assert normalize_reply_subject(once) == once


def test_prepare_gmail_message_does_not_double_re_behind_list_tag():
    raw, *_ = _prepare_gmail_message(
        subject="[list] Re: Project status",
        body="Thanks",
        to="ada@example.com",
        in_reply_to="<parent@example.com>",
    )
    assert _parse(raw)["Subject"] == "[list] Re: Project status"


@pytest.mark.asyncio
async def test_send_reply_inherits_parent_subject_when_omitted():
    service = _service_with_parent("Project sync")

    await _unwrap(send_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
    )

    assert _sent_subject(service) == "Re: Project sync"


@pytest.mark.asyncio
async def test_send_reply_inherited_subject_keeps_tags_and_single_re():
    parent = "[list] Re: RE: Project status [#123]"
    service = _service_with_parent(parent)

    await _unwrap(send_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
    )

    assert _sent_subject(service) == parent


@pytest.mark.asyncio
async def test_send_reply_inherits_subject_even_with_explicit_reply_headers():
    """Both reply headers supplied: the thread must still be fetched for the subject."""
    service = _service_with_parent("Project sync")

    await _unwrap(send_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        body="Thanks!",
        thread_id="thread123",
        in_reply_to="<parent@example.com>",
        references="<parent@example.com>",
        include_signature=False,
    )

    assert _sent_subject(service) == "Re: Project sync"


@pytest.mark.asyncio
async def test_send_explicit_reply_subject_wins_over_parent():
    service = _service_with_parent("Project sync")

    await _unwrap(send_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        subject="Different topic",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
    )

    assert _sent_subject(service) == "Re: Different topic"


@pytest.mark.asyncio
async def test_send_new_message_still_requires_subject():
    service = _service_with_parent("unused")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(send_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            body="Hello",
            include_signature=False,
        )
    service.users().messages().send.assert_not_called()


@pytest.mark.asyncio
async def test_send_reply_without_inheritable_subject_raises():
    service = _service_with_parent("")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(send_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            body="Thanks!",
            thread_id="thread123",
            include_signature=False,
        )
    service.users().messages().send.assert_not_called()


@pytest.mark.asyncio
async def test_draft_reply_inherits_subject_even_with_explicit_reply_headers():
    service = _service_with_parent("[list] Re: Project status")

    await _unwrap(draft_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        subject="",
        body="Thanks!",
        thread_id="thread123",
        in_reply_to="<parent@example.com>",
        references="<parent@example.com>",
        include_signature=False,
    )

    assert _drafted_subject(service) == "[list] Re: Project status"


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["", "   "])
async def test_send_reply_blank_subject_without_inheritable_subject_raises(subject):
    service = _service_with_parent("")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(send_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            subject=subject,
            body="Thanks!",
            thread_id="thread123",
            include_signature=False,
        )
    service.users().messages().send.assert_not_called()


@pytest.mark.asyncio
async def test_send_reply_whitespace_subject_inherits_parent_subject():
    service = _service_with_parent("Project sync")

    await _unwrap(send_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        subject="   ",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
    )

    assert _sent_subject(service) == "Re: Project sync"


@pytest.mark.asyncio
async def test_draft_reply_inherits_subject_when_subject_omitted():
    service = _service_with_parent("Project sync")

    await _unwrap(draft_gmail_message)(
        service=service,
        user_google_email="you@example.org",
        to="ada@example.com",
        body="Thanks!",
        thread_id="thread123",
        include_signature=False,
    )

    assert _drafted_subject(service) == "Re: Project sync"


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", [None, "", "   "])
async def test_draft_reply_without_inheritable_subject_raises(subject):
    service = _service_with_parent("")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(draft_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            subject=subject,
            body="Thanks!",
            thread_id="thread123",
            include_signature=False,
        )
    service.users().drafts().create.assert_not_called()


@pytest.mark.asyncio
async def test_draft_new_message_still_requires_subject():
    service = _service_with_parent("unused")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(draft_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            body="Hello",
            include_signature=False,
        )
    service.users().drafts().create.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["", "   "])
async def test_send_new_message_rejects_blank_subject(subject):
    service = _service_with_parent("unused")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(send_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            subject=subject,
            body="Hello",
            include_signature=False,
        )
    service.users().messages().send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("subject", ["", "   "])
async def test_draft_new_message_rejects_blank_subject(subject):
    service = _service_with_parent("unused")

    with pytest.raises(UserInputError, match="subject"):
        await _unwrap(draft_gmail_message)(
            service=service,
            user_google_email="you@example.org",
            to="ada@example.com",
            subject=subject,
            body="Hello",
            include_signature=False,
        )
    service.users().drafts().create.assert_not_called()


def test_draft_gmail_message_keeps_subject_before_body():
    # Positional callers rely on upstream's (subject, body) order.
    import inspect

    params = list(inspect.signature(_unwrap(draft_gmail_message)).parameters)
    assert params.index("subject") < params.index("body")
