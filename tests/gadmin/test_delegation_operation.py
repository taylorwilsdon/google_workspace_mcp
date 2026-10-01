"""Contact Delegation and Gmail mail delegate operations cross the real MCP
boundary through admin_operation.

Only transports are faked. Contact Delegation runs on a real client built from
the pinned hand-written document over the Directory client's transport, so each
test shows the request that would reach Google. Gmail runs on the installed
Gmail client, and only through a service-account token minted for a mailbox
owner freshly verified inside the admin's customer; the admin's OAuth grant never
builds a Gmail client. Neither returns contact data or message content."""

import json
from types import SimpleNamespace

import pytest
from googleapiclient.discovery import build

import auth.scopes as scopes
import gadmin.auth as auth
from gadmin.auth import AdminPermissionError, get_admin_service
from gadmin.registry import get_operation
from tests.gadmin.fake_http import RoutingHttp
from tests.gadmin.test_admin_operation import (  # noqa: F401 - ws is a fixture
    ADMIN,
    ALL_ADMIN,
    confirm,
    operation,
    propose,
    ws,
)

STAFF = "staff@op.example"
NEW = "new@op.example"
HELPDESK = "helpdesk@op.example"
DELEGATION = "https://www.googleapis.com/auth/admin.contact.delegation"
DELEGATION_READ = DELEGATION + ".readonly"
SETTINGS_BASIC = "https://www.googleapis.com/auth/gmail.settings.basic"
SETTINGS_SHARING = "https://www.googleapis.com/auth/gmail.settings.sharing"
CONTACTS_PATH = f"/admin/contacts/v1/users/{STAFF}/delegates"
GMAIL_PATH = f"/gmail/v1/users/{STAFF}/settings/delegates"
# Fields Google does not document for a delegate, standing in for contact data
# or message content a response must never carry.
LEAKS = {"displayName": "Private Name", "snippet": "Quarterly numbers attached"}


def _contacts_list(workspace):
    def respond(query, body):
        return {
            "delegates": [{"email": e, **LEAKS} for e in workspace.contact_delegates],
            "contacts": [{"name": "Private contact"}],
        }

    return respond


@pytest.fixture
def contacts(ws, monkeypatch):  # noqa: F811 - the imported fixture
    ws.contact_delegates = [NEW, "former@op.example"]
    ws.contacts_http = RoutingHttp(
        {
            ("GET", CONTACTS_PATH): _contacts_list(ws),
            ("POST", CONTACTS_PATH): lambda query, body: {**body, **LEAKS},
            ("DELETE", f"{CONTACTS_PATH}/{NEW}"): {},
        }
    )
    ws.contact_auth = []
    admin_service = auth._authenticate_service

    async def authenticate(use_oauth21, service, version, tool, selected, *args, **kw):
        if not tool.startswith("admin.contacts."):
            return await admin_service(
                use_oauth21, service, version, tool, selected, *args, **kw
            )
        assert kw.get("verify_account") is True
        ws.contact_auth.append((service, version, list(args[0])))
        return build("admin", "directory_v1", http=ws.contacts_http), selected

    monkeypatch.setattr(auth, "_authenticate_service", authenticate)
    monkeypatch.setattr(
        scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, "admin-contact-delegation"]
    )
    return ws


def _gmail_list(workspace):
    def respond(query, body):
        return {
            "delegates": [
                {"delegateEmail": e, "verificationStatus": "accepted", **LEAKS}
                for e in workspace.mail_delegates
            ]
        }

    return respond


@pytest.fixture
def mailbox(ws, monkeypatch):  # noqa: F811 - the imported fixture
    ws.directory.add_user(HELPDESK, "U_HELP", delegated_admin=True)
    ws.directory.add_user("gone@op.example", "U_GONE", suspended=True)
    ws.mail_delegates = [NEW]
    ws.gmail_http = RoutingHttp(
        {
            ("GET", GMAIL_PATH): _gmail_list(ws),
            ("GET", f"{GMAIL_PATH}/{NEW}"): {
                "delegateEmail": NEW,
                "verificationStatus": "accepted",
                **LEAKS,
            },
            ("POST", GMAIL_PATH): lambda query, body: {
                **body,
                "verificationStatus": "accepted",
            },
            ("DELETE", f"{GMAIL_PATH}/{NEW}"): {},
        }
    )
    ws.minted = []
    ws.dwd_domains = []

    def credentials(scopes, subject):
        ws.minted.append((list(scopes), subject))
        return object()

    def gmail_build(service, version, credentials=None, **kwargs):
        assert (service, version) == ("gmail", "v1") and credentials is not None
        return build("gmail", "v1", http=ws.gmail_http)

    monkeypatch.setattr(auth, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(
        auth,
        "get_oauth_config",
        lambda: SimpleNamespace(dwd_allowed_domains=ws.dwd_domains),
    )
    monkeypatch.setattr(auth, "_get_service_account_credentials", credentials)
    monkeypatch.setattr(auth, "build", gmail_build)
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, "admin-gmail-delegates"])
    return ws


def _no_leaks(text: str) -> None:
    for value in (*LEAKS.values(), "Private contact"):
        assert value not in text


# --- Contact Delegation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_contact_delegates_list_returns_addresses_only(contacts):
    result, text = await operation(
        "admin.contacts.v1.users.delegates.list", {"userId": "U_STAFF"}
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "delegates": [{"email": NEW}, {"email": "former@op.example"}]
    }
    _no_leaks(text)
    # The verified delegator's address is sent, on the Directory client's
    # transport, with only the read-only delegation scope.
    assert [r[:2] for r in contacts.contacts_http.requests] == [("GET", CONTACTS_PATH)]
    assert contacts.contact_auth == [("admin", "directory_v1", [DELEGATION_READ])]


@pytest.mark.asyncio
async def test_contact_delegates_of_another_customers_user_are_not_read(contacts):
    result, text = await operation(
        "admin.contacts.v1.users.delegates.list", {"userId": "u@other.example"}
    )

    assert result.is_error and "another customer" in text
    assert contacts.contacts_http.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("userId", "delegate"),
    [
        (STAFF, "u@other.example"),
        ("u@other.example", NEW),
        (STAFF, "nobody@op.example"),
    ],
)
async def test_contact_delegate_is_created_only_between_verified_users(
    contacts, userId, delegate
):
    result, text = await operation(
        "admin.contacts.v1.users.delegates.create",
        {"userId": userId},
        {"email": delegate},
    )

    assert result.is_error, text
    assert contacts.contacts_http.requests == []
    assert contacts.audit() == []


@pytest.mark.asyncio
async def test_contact_delegate_create_is_proposed_then_confirmed_once(contacts):
    proposal = await propose(
        "admin.contacts.v1.users.delegates.create",
        {"userId": "U_STAFF"},
        {"email": "U_NEW"},
    )
    assert proposal["params"] == {"userId": STAFF}
    assert proposal["body"] == {"email": NEW}
    assert contacts.contacts_http.requests == []

    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"] == {"email": NEW}
    _no_leaks(text)
    assert contacts.contacts_http.requests[-1] == (
        "POST",
        CONTACTS_PATH,
        {},
        {"email": NEW},
    )
    assert DELEGATION in contacts.contact_auth[-1][2]

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    posts = [r for r in contacts.contacts_http.requests if r[0] == "POST"]
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_contact_delegate_is_deleted_only_when_freshly_listed(contacts):
    proposal = await propose(
        "admin.contacts.v1.users.delegates.delete",
        {"userId": STAFF, "delegate": "NEW@op.example"},
    )
    # The listed address is proposed, after a fresh list on the operation client.
    assert proposal["params"] == {"userId": STAFF, "delegate": NEW}
    assert proposal["risk"] == "destructive"

    result, text = await confirm(proposal)
    assert not result.is_error, text
    deletes = [r for r in contacts.contacts_http.requests if r[0] == "DELETE"]
    assert deletes == [("DELETE", f"{CONTACTS_PATH}/{NEW}", {}, None)]

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len([r for r in contacts.contacts_http.requests if r[0] == "DELETE"]) == 1
    audit = json.dumps(contacts.audit())
    assert "succeeded" in audit
    _no_leaks(audit)


@pytest.mark.asyncio
async def test_unlisted_contact_delegate_is_not_deleted(contacts):
    result, text = await operation(
        "admin.contacts.v1.users.delegates.delete",
        {"userId": STAFF, "delegate": "stranger@op.example"},
    )

    assert result.is_error and "not listed" in text
    assert [r[0] for r in contacts.contacts_http.requests] == ["GET"]
    assert contacts.audit() == []


@pytest.mark.asyncio
async def test_delegate_removed_after_proposal_is_not_deleted(contacts):
    proposal = await propose(
        "admin.contacts.v1.users.delegates.delete",
        {"userId": STAFF, "delegate": NEW},
    )
    contacts.contact_delegates.remove(NEW)

    result, text = await confirm(proposal)

    assert result.is_error and "not listed" in text
    assert not [r for r in contacts.contacts_http.requests if r[0] == "DELETE"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "endless"])
async def test_delegate_list_failure_or_overrun_refuses_deletion(contacts, failure):
    if failure == "error":
        contacts.contacts_http.handlers[("GET", CONTACTS_PATH)] = 403
    else:
        contacts.contacts_http.handlers[("GET", CONTACTS_PATH)] = lambda q, b: {
            "delegates": [{"email": "other@op.example"}],
            "nextPageToken": f"p{q.get('pageToken', '')}x",
        }

    result, text = await operation(
        "admin.contacts.v1.users.delegates.delete",
        {"userId": STAFF, "delegate": NEW},
    )

    assert result.is_error and "Could not verify the delegate" in text
    assert not [r for r in contacts.contacts_http.requests if r[0] == "DELETE"]
    gets = [r for r in contacts.contacts_http.requests if r[0] == "GET"]
    assert len(gets) <= 10


@pytest.mark.asyncio
async def test_contact_delegation_needs_its_own_service(contacts):
    scopes._ENABLED_TOOLS = list(ALL_ADMIN)

    result, text = await operation(
        "admin.contacts.v1.users.delegates.list", {"userId": STAFF}
    )

    assert result.is_error and "admin-contact-delegation" in text
    assert contacts.contact_auth == []


# --- Gmail mail delegates -----------------------------------------------------------


@pytest.mark.asyncio
async def test_gmail_delegates_fail_closed_under_an_oauth_grant(mailbox, monkeypatch):
    monkeypatch.setattr(auth, "is_service_account_enabled", lambda: False)
    before = len(mailbox.requested_scopes)

    result, text = await operation(
        "gmail.users.settings.delegates.list", {"userId": STAFF}
    )

    assert result.is_error and "service account" in text
    assert mailbox.minted == [] and mailbox.gmail_http.requests == []
    # No OAuth token was requested for the Gmail operation either.
    assert len(mailbox.requested_scopes) == before


@pytest.mark.asyncio
async def test_admin_oauth_client_is_never_built_for_gmail(mailbox):
    with pytest.raises(AdminPermissionError, match="service account"):
        await get_admin_service(
            ADMIN, get_operation("gmail.users.settings.delegates.list"), None
        )
    assert mailbox.requested_scopes == []


@pytest.mark.asyncio
async def test_gmail_delegates_list_impersonates_only_the_verified_owner(mailbox):
    result, text = await operation(
        "gmail.users.settings.delegates.list", {"userId": "U_STAFF"}
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "delegates": [{"delegateEmail": NEW, "verificationStatus": "accepted"}]
    }
    _no_leaks(text)
    assert mailbox.minted == [([SETTINGS_BASIC], STAFF)]
    assert [r[:2] for r in mailbox.gmail_http.requests] == [("GET", GMAIL_PATH)]


@pytest.mark.asyncio
async def test_gmail_delegate_get_returns_only_delegate_fields(mailbox):
    result, text = await operation(
        "gmail.users.settings.delegates.get",
        {"userId": STAFF, "delegateEmail": NEW},
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "delegateEmail": NEW,
        "verificationStatus": "accepted",
    }
    _no_leaks(text)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("owner", "admin", "message"),
    [
        ("u@other.example", ADMIN, "another customer"),
        ("gone@op.example", ADMIN, "active"),
        ("nobody@op.example", ADMIN, "Could not verify"),
        (STAFF, HELPDESK, "super admin"),
        ("me", ADMIN, "Could not verify"),
    ],
)
async def test_unverified_mailbox_owner_or_actor_is_never_impersonated(
    mailbox, owner, admin, message
):
    result, text = await operation(
        "gmail.users.settings.delegates.list", {"userId": owner}, admin=admin
    )

    assert result.is_error and message in text, text
    assert mailbox.minted == [] and mailbox.gmail_http.requests == []


@pytest.mark.asyncio
async def test_owner_outside_the_domain_wide_delegation_allowlist_is_refused(mailbox):
    mailbox.dwd_domains.append("elsewhere.example")

    result, text = await operation(
        "gmail.users.settings.delegates.list", {"userId": STAFF}
    )

    assert result.is_error and "DWD_ALLOWED_DOMAINS" in text
    assert mailbox.minted == []


@pytest.mark.asyncio
async def test_gmail_message_methods_are_excluded_and_mint_nothing(mailbox):
    for operation_id in (
        "gmail.users.messages.list",
        "gmail.users.threads.get",
        "gmail.users.settings.forwardingAddresses.create",
    ):
        result, text = await operation(operation_id, {"userId": STAFF})
        assert result.is_error and "is excluded" in text, operation_id
    assert mailbox.minted == [] and mailbox.gmail_http.requests == []


@pytest.mark.asyncio
async def test_gmail_delegate_create_needs_a_delegate_in_the_customer(mailbox):
    result, text = await operation(
        "gmail.users.settings.delegates.create",
        {"userId": STAFF},
        {"delegateEmail": "u@other.example"},
    )

    assert result.is_error and "another customer" in text
    assert not [r for r in mailbox.gmail_http.requests if r[0] == "POST"]

    proposal = await propose(
        "gmail.users.settings.delegates.create",
        {"userId": STAFF},
        {"delegateEmail": "U_NEW"},
    )
    assert proposal["body"] == {"delegateEmail": NEW}
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert mailbox.gmail_http.requests[-1] == (
        "POST",
        GMAIL_PATH,
        {},
        {"delegateEmail": NEW},
    )
    assert mailbox.minted[-1] == ([SETTINGS_SHARING], STAFF)


@pytest.mark.asyncio
async def test_gmail_delegate_delete_is_listed_confirmed_and_not_replayed(mailbox):
    result, text = await operation(
        "gmail.users.settings.delegates.delete",
        {"userId": STAFF, "delegateEmail": "stranger@op.example"},
    )
    assert result.is_error and "not listed" in text

    proposal = await propose(
        "gmail.users.settings.delegates.delete",
        {"userId": STAFF, "delegateEmail": NEW},
    )
    result, text = await confirm(proposal)
    assert not result.is_error, text
    deletes = [r for r in mailbox.gmail_http.requests if r[0] == "DELETE"]
    assert deletes == [("DELETE", f"{GMAIL_PATH}/{NEW}", {}, None)]
    assert mailbox.minted[-1] == ([SETTINGS_SHARING, SETTINGS_BASIC], STAFF)

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len([r for r in mailbox.gmail_http.requests if r[0] == "DELETE"]) == 1
    audit = json.dumps(mailbox.audit())
    _no_leaks(audit)
    # No Gmail request ever touched messages, threads, drafts, or labels.
    assert all(
        "/settings/delegates" in path for _, path, _, _ in mailbox.gmail_http.requests
    )


@pytest.mark.asyncio
async def test_confirmation_is_refused_when_the_owner_was_suspended(mailbox):
    proposal = await propose(
        "gmail.users.settings.delegates.delete",
        {"userId": STAFF, "delegateEmail": NEW},
    )
    minted = len(mailbox.minted)
    mailbox.directory.users_by_key[STAFF]["suspended"] = True

    result, text = await confirm(proposal)

    assert result.is_error and "active" in text
    assert len(mailbox.minted) == minted
    assert not [r for r in mailbox.gmail_http.requests if r[0] == "DELETE"]
