"""Gmail web links address the owning account by email, so multi-account browsers open the right mailbox."""

import base64
from unittest.mock import Mock

import pytest

import gmail.gmail_tools as gmail_tools
from gmail.gmail_tools import (
    _generate_gmail_web_url,
    get_gmail_messages_content_batch,
    search_gmail_messages,
)


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def test_web_url_uses_the_account_email():
    url = _generate_gmail_web_url("msg-1", user_google_email="second@example.com")

    assert url == "https://mail.google.com/mail/u/second@example.com/#all/msg-1"


def test_web_url_encodes_special_characters_in_the_email():
    url = _generate_gmail_web_url("msg-1", user_google_email="jorge+work@example.com")

    assert url == "https://mail.google.com/mail/u/jorge%2Bwork@example.com/#all/msg-1"


@pytest.mark.parametrize("email", [None, ""])
def test_web_url_falls_back_to_account_index_when_email_is_unknown(email):
    url = _generate_gmail_web_url("msg-1", user_google_email=email)

    assert url == "https://mail.google.com/mail/u/0/#all/msg-1"


def test_web_url_keeps_index_only_callers_working():
    assert _generate_gmail_web_url("msg-1") == (
        "https://mail.google.com/mail/u/0/#all/msg-1"
    )
    assert _generate_gmail_web_url("msg-1", account_index=2) == (
        "https://mail.google.com/mail/u/2/#all/msg-1"
    )


@pytest.mark.asyncio
async def test_search_results_link_to_the_searched_account():
    service = Mock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": "msg-1", "threadId": "thread-1"}]
    }

    result = await _unwrap(search_gmail_messages)(
        service=service, query="invoice", user_google_email="second@example.com"
    )

    assert (
        "     Web Link: https://mail.google.com/mail/u/second@example.com/#all/msg-1"
        in result
    )
    assert (
        "     Thread Link: https://mail.google.com/mail/u/second@example.com/#all/thread-1"
        in result
    )


@pytest.mark.asyncio
async def test_message_content_links_to_the_account_that_was_read(monkeypatch):
    monkeypatch.setattr(gmail_tools, "GMAIL_REQUEST_DELAY", 0)
    body = base64.urlsafe_b64encode(b"Body text").decode()
    service = Mock()
    service.new_batch_http_request.side_effect = RuntimeError("no batch")
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "id": "msg-1",
        "threadId": "thread-1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": "Hello"}],
            "body": {"data": body},
        },
    }

    result = await _unwrap(get_gmail_messages_content_batch)(
        service=service,
        message_ids=["msg-1"],
        user_google_email="second@example.com",
    )

    assert (
        "Web Link: https://mail.google.com/mail/u/second@example.com/#all/msg-1"
        in result
    )
    assert "Body text" in result
