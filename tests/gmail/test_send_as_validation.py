"""Tests for send-as identity validation in send_gmail_message and draft_gmail_message."""

import base64
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock

from fastmcp.exceptions import ToolError
import pytest

from gmail.gmail_tools import draft_gmail_message, send_gmail_message


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _parse_raw_message(raw_message: str):
    return BytesParser(policy=policy.default).parsebytes(
        base64.urlsafe_b64decode(raw_message.encode())
    )


def _service_with_send_as(entries):
    service = Mock()
    service.users.return_value.settings.return_value.sendAs.return_value.list.return_value.execute.return_value = {
        "sendAs": entries
    }
    service.users.return_value.messages.return_value.send.return_value.execute.return_value = {
        "id": "msg_1"
    }
    service.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": "draft_1"
    }
    return service


def _sent_message(service):
    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    return _parse_raw_message(kwargs["body"]["raw"])


PRIMARY = {
    "sendAsEmail": "primary@example.com",
    "displayName": "Primary Person",
    "isPrimary": True,
    "isDefault": True,
    "signature": "",
    "verificationStatus": "accepted",
}
ACCEPTED_ALIAS = {
    "sendAsEmail": "brand@example.com",
    "displayName": "Brand Team",
    "isPrimary": False,
    "isDefault": False,
    "signature": "",
    "verificationStatus": "accepted",
}


async def _send(service, **kwargs):
    return await _unwrap(send_gmail_message)(
        service=service,
        user_google_email="primary@example.com",
        to="recipient@example.com",
        subject="Subject",
        body="Body",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_send_rejects_an_alias_that_is_not_configured():
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    with pytest.raises(ToolError) as exc:
        await _send(service, from_email="nobody@example.com")

    message = str(exc.value)
    assert "nobody@example.com" in message
    assert "primary@example.com" in message
    assert "brand@example.com" in message
    service.users.return_value.messages.return_value.send.assert_not_called()


@pytest.mark.asyncio
async def test_send_rejects_an_alias_still_pending_verification():
    pending = dict(ACCEPTED_ALIAS, verificationStatus="pending")
    service = _service_with_send_as([PRIMARY, pending])

    with pytest.raises(ToolError) as exc:
        await _send(service, from_email="brand@example.com")

    assert "brand@example.com" in str(exc.value)
    service.users.return_value.messages.return_value.send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "verificationStatusUnspecified"])
async def test_send_allows_an_alias_without_a_pending_status(status):
    unreported = {k: v for k, v in ACCEPTED_ALIAS.items() if k != "verificationStatus"}
    if status:
        unreported["verificationStatus"] = status
    service = _service_with_send_as([PRIMARY, unreported])

    await _send(service, from_email="brand@example.com")

    assert "brand@example.com" in _sent_message(service)["From"]


@pytest.mark.asyncio
async def test_send_skips_validation_when_send_as_settings_are_unreadable():
    service = _service_with_send_as([])

    await _send(service, from_email="whatever@example.com")

    assert "whatever@example.com" in _sent_message(service)["From"]


@pytest.mark.asyncio
async def test_send_without_from_email_keeps_the_authenticated_sender():
    default_alias = dict(ACCEPTED_ALIAS, isDefault=True, signature="Brand sig")
    primary = dict(PRIMARY, isDefault=False, signature="Primary sig")
    service = _service_with_send_as([primary, default_alias])

    await _send(service)

    sent = _sent_message(service)
    assert sent["From"] == "Primary Person <primary@example.com>"
    assert "Primary sig" in sent.get_body().get_content()


@pytest.mark.asyncio
async def test_send_fills_the_display_name_from_the_chosen_alias():
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    await _send(service, from_email="brand@example.com")

    assert _sent_message(service)["From"] == "Brand Team <brand@example.com>"


@pytest.mark.asyncio
async def test_send_prefers_an_explicit_from_name_over_the_alias_display_name():
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    await _send(service, from_email="brand@example.com", from_name="Someone Else")

    assert _sent_message(service)["From"] == "Someone Else <brand@example.com>"


@pytest.mark.asyncio
async def test_draft_rejects_an_alias_that_is_not_configured():
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    with pytest.raises(ToolError) as exc:
        await _unwrap(draft_gmail_message)(
            service=service,
            user_google_email="primary@example.com",
            to="recipient@example.com",
            subject="Subject",
            body="Body",
            from_email="nobody@example.com",
        )

    assert "brand@example.com" in str(exc.value)
    service.users.return_value.drafts.return_value.create.assert_not_called()


@pytest.mark.asyncio
async def test_send_without_from_email_tolerates_an_unlisted_login_address():
    login = dict(PRIMARY, sendAsEmail="login@example.com", signature="Login sig")
    service = _service_with_send_as([login, ACCEPTED_ALIAS])

    await _send(service)

    sent = _sent_message(service)
    assert sent["From"] == "Primary Person <primary@example.com>"
    assert "Login sig" in sent.get_body().get_content()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [send_gmail_message, draft_gmail_message])
async def test_forward_rejects_an_alias_that_is_not_configured(tool):
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    with pytest.raises(ToolError) as exc:
        await _unwrap(tool)(
            service=service,
            user_google_email="primary@example.com",
            to="recipient@example.com",
            forward_message_id="orig_1",
            from_email="nobody@example.com",
        )

    assert "nobody@example.com" in str(exc.value)
    service.users.return_value.messages.return_value.get.assert_not_called()
