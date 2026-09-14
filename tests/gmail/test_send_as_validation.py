"""
Tests for send-as identity validation.

users.messages.send does not reject a From header naming an address that is not a
configured send-as identity -- Gmail silently substitutes the default sender and
reports success. These cover the resulting contract: an unusable from_email raises
instead, an alias Gmail has not explicitly rejected is honoured, and the alias's
own display name fills in when the caller did not supply one.
"""

import base64
from email import policy
from email.parser import BytesParser
import os
import sys
from unittest.mock import Mock

from fastmcp.exceptions import ToolError
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

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
    """An unconfigured From would be silently rewritten by Gmail, so refuse it."""
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    with pytest.raises(ToolError) as exc:
        await _send(service, from_email="nobody@example.com")

    message = str(exc.value)
    assert "nobody@example.com" in message
    # The error must name what would have worked, or the caller is left guessing.
    assert "primary@example.com" in message
    assert "brand@example.com" in message
    # Nothing may be sent once the identity is known to be wrong.
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
async def test_send_allows_an_alias_with_no_verification_status():
    """Absence of the field means 'not reported', not 'rejected'."""
    unreported = {k: v for k, v in ACCEPTED_ALIAS.items() if k != "verificationStatus"}
    service = _service_with_send_as([PRIMARY, unreported])

    await _send(service, from_email="brand@example.com")

    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    parsed = _parse_raw_message(kwargs["body"]["raw"])
    assert "brand@example.com" in parsed["From"]


@pytest.mark.asyncio
async def test_send_skips_validation_when_send_as_settings_are_unreadable():
    """Without gmail.settings.basic the list comes back empty; do not block the send.

    An unverifiable alias is not the same as a known-bad one, and refusing here
    would make the tool unusable for callers holding only gmail.send.
    """
    service = _service_with_send_as([])

    await _send(service, from_email="whatever@example.com")

    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    parsed = _parse_raw_message(kwargs["body"]["raw"])
    assert "whatever@example.com" in parsed["From"]


@pytest.mark.asyncio
async def test_send_fills_the_display_name_from_the_chosen_alias():
    """Picking an alias should not produce a bare address in the From header."""
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    await _send(service, from_email="brand@example.com")

    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    parsed = _parse_raw_message(kwargs["body"]["raw"])
    assert parsed["From"] == "Brand Team <brand@example.com>"


@pytest.mark.asyncio
async def test_send_prefers_an_explicit_from_name_over_the_alias_display_name():
    service = _service_with_send_as([PRIMARY, ACCEPTED_ALIAS])

    await _send(service, from_email="brand@example.com", from_name="Someone Else")

    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    parsed = _parse_raw_message(kwargs["body"]["raw"])
    assert parsed["From"] == "Someone Else <brand@example.com>"


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
