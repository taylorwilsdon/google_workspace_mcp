"""Mailbox administration reads stay behind service-account DWD and redact content."""

import json

import pytest

from tests.gadmin.test_admin_operation import confirm, operation, propose, ws  # noqa: F401 - fixture
from tests.gadmin.test_delegation_operation import (  # noqa: F401 - fixture
    ADMIN,
    SETTINGS_BASIC,
    STAFF,
    mailbox,
)

BASE = f"/gmail/v1/users/{STAFF}/settings"


@pytest.mark.asyncio
async def test_mailbox_settings_are_bounded_and_minted_for_verified_owner(mailbox):  # noqa: F811 - imported fixture
    cases = [
        (
            "getAutoForwarding",
            "autoForwarding",
            {
                "enabled": True,
                "emailAddress": "outside@example.net",
                "disposition": "leaveInInbox",
                "secret": "hidden",
            },
            {
                "enabled": True,
                "emailAddress": "outside@example.net",
                "disposition": "leaveInInbox",
            },
        ),
        (
            "getImap",
            "imap",
            {
                "enabled": True,
                "autoExpunge": False,
                "expungeBehavior": "archive",
                "maxFolderSize": 100,
                "secret": "hidden",
            },
            {
                "enabled": True,
                "autoExpunge": False,
                "expungeBehavior": "archive",
                "maxFolderSize": 100,
            },
        ),
        (
            "getPop",
            "pop",
            {
                "accessWindow": "allMail",
                "disposition": "leaveInInbox",
                "secret": "hidden",
            },
            {"accessWindow": "allMail", "disposition": "leaveInInbox"},
        ),
        (
            "getLanguage",
            "language",
            {"displayLanguage": "en", "secret": "hidden"},
            {"displayLanguage": "en"},
        ),
        (
            "getVacation",
            "vacation",
            {
                "enableAutoReply": True,
                "responseSubject": "private-subject",
                "responseBodyHtml": "private-html",
                "responseBodyPlainText": "private-text",
                "restrictToContacts": True,
            },
            {"enableAutoReply": True, "restrictToContacts": True},
        ),
    ]
    for method, path, response, expected in cases:
        mailbox.gmail_http.handlers[("GET", f"{BASE}/{path}")] = response
        result, text = await operation(
            f"gmail.users.settings.{method}", {"userId": STAFF}
        )
        assert not result.is_error, text
        assert json.loads(text)["result"] == expected
        assert "private-" not in text
    assert mailbox.minted == [([SETTINGS_BASIC], STAFF)] * len(cases)
    assert all(r[0] == "GET" for r in mailbox.gmail_http.requests)


@pytest.mark.asyncio
async def test_mailbox_settings_cannot_read_another_customer(mailbox):  # noqa: F811 - imported fixture
    result, text = await operation(
        "gmail.users.settings.getAutoForwarding", {"userId": "u@other.example"}
    )
    assert result.is_error and "another customer" in text
    assert mailbox.minted == [] and mailbox.gmail_http.requests == []


@pytest.mark.asyncio
async def test_send_as_and_forwarding_reads_never_return_credentials_or_signature(
    mailbox,  # noqa: F811 - imported fixture
):
    mailbox.gmail_http.handlers.update(
        {
            ("GET", f"{BASE}/sendAs"): {
                "sendAs": [
                    {
                        "sendAsEmail": "alias@example.net",
                        "displayName": "Support",
                        "isDefault": False,
                        "signature": "private-signature",
                        "smtpMsa": {"password": "private-password"},
                    }
                ]
            },
            ("GET", f"{BASE}/sendAs/alias@example.net"): {
                "sendAsEmail": "alias@example.net",
                "displayName": "Support",
                "isDefault": False,
                "signature": "private-signature",
                "smtpMsa": {"password": "private-password"},
            },
            ("GET", f"{BASE}/forwardingAddresses"): {
                "forwardingAddresses": [
                    {
                        "forwardingEmail": "forward@example.net",
                        "verificationStatus": "accepted",
                        "secret": "hidden",
                    }
                ]
            },
        }
    )
    for name, params, expected in [
        (
            "sendAs.list",
            {"userId": STAFF},
            {
                "sendAs": [
                    {
                        "sendAsEmail": "alias@example.net",
                        "displayName": "Support",
                        "isDefault": False,
                    }
                ]
            },
        ),
        (
            "sendAs.get",
            {"userId": STAFF, "sendAsEmail": "alias@example.net"},
            {
                "sendAsEmail": "alias@example.net",
                "displayName": "Support",
                "isDefault": False,
            },
        ),
        (
            "forwardingAddresses.list",
            {"userId": STAFF},
            {
                "forwardingAddresses": [
                    {
                        "forwardingEmail": "forward@example.net",
                        "verificationStatus": "accepted",
                    }
                ]
            },
        ),
    ]:
        result, text = await operation("gmail.users.settings." + name, params)
        assert not result.is_error, text
        assert json.loads(text)["result"] == expected
        assert "private" not in text and "password" not in text
    assert mailbox.minted == [([SETTINGS_BASIC], STAFF)] * 3


@pytest.mark.asyncio
async def test_forwarding_address_get_is_mailbox_scoped_and_redacted(mailbox):  # noqa: F811 - imported fixture
    address = "forward@example.net"
    mailbox.gmail_http.handlers[("GET", f"{BASE}/forwardingAddresses/{address}")] = {
        "forwardingEmail": address,
        "verificationStatus": "accepted",
        "secret": "private-text",
    }
    result, text = await operation(
        "gmail.users.settings.forwardingAddresses.get",
        {"userId": STAFF, "forwardingEmail": address},
    )
    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "forwardingEmail": address,
        "verificationStatus": "accepted",
    }
    assert "private-text" not in text
    assert mailbox.minted == [([SETTINGS_BASIC], STAFF)]


@pytest.mark.asyncio
async def test_language_update_is_confirmed_once_and_restricted(mailbox):  # noqa: F811 - imported fixture
    mailbox.gmail_http.handlers[("PUT", f"{BASE}/language")] = {
        "displayLanguage": "fr",
        "hidden": "private-text",
    }
    proposal = await propose(
        "gmail.users.settings.updateLanguage",
        {"userId": STAFF},
        {"displayLanguage": "fr"},
    )
    assert proposal["risk"] == "manage"
    assert not [r for r in mailbox.gmail_http.requests if r[0] == "PUT"]
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"] == {"displayLanguage": "fr"}
    assert "private-text" not in text
    assert [r for r in mailbox.gmail_http.requests if r[0] == "PUT"] == [
        ("PUT", f"{BASE}/language", {}, {"displayLanguage": "fr"})
    ]
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    result, text = await operation(
        "gmail.users.settings.updateLanguage",
        {"userId": "u@other.example"},
        {"displayLanguage": "fr"},
    )
    assert result.is_error and "another customer" in text
    for invalid in ("https://evil.test", "fr<script>", "x" * 80):
        result, text = await operation(
            "gmail.users.settings.updateLanguage",
            {"userId": STAFF},
            {"displayLanguage": invalid},
        )
        assert result.is_error
    assert len([r for r in mailbox.gmail_http.requests if r[0] == "PUT"]) == 1
