"""Contact Delegation and Gmail mail delegates are separate opt-in services.

Contact Delegation uses the admin's OAuth grant with its two documented scopes.
Gmail delegate settings add no scope to any OAuth consent: their two settings
scopes are requested only from a service-account token minted for a verified
mailbox owner, and no Gmail message scope is requested anywhere."""

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
from auth.scopes import get_scopes_for_tools
from auth.service_decorator import SERVICE_CONFIGS
from gadmin.auth import admin_service_for, assert_admin_permission, client_scopes
from gadmin.registry import EXCLUSION_CATEGORIES, excluded_operations, iter_operations

CONTACTS = "admin-contact-delegation"
GMAIL = "admin-gmail-delegates"
DELEGATION = "https://www.googleapis.com/auth/admin.contact.delegation"
DELEGATION_READ = DELEGATION + ".readonly"
SETTINGS_BASIC = "https://www.googleapis.com/auth/gmail.settings.basic"
SETTINGS_SHARING = "https://www.googleapis.com/auth/gmail.settings.sharing"
# Scopes that reach Gmail message content, labels, or the whole mailbox.
MESSAGE_SCOPES = {
    "https://mail.google.com/",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.metadata",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.insert",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.send",
}


def _operations(service, version):
    return {
        spec.id: spec
        for spec in iter_operations()
        if (spec.service, spec.version) == (service, version)
    }


CONTACT_OPS = _operations("admin", "contacts_v1")
GMAIL_OPS = _operations("gmail", "v1")


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


def test_scopes_are_the_documented_ones():
    assert scopes.CONTACT_DELEGATION_SCOPE == DELEGATION
    assert scopes.CONTACT_DELEGATION_READONLY_SCOPE == DELEGATION_READ
    assert scopes.GMAIL_SETTINGS_SHARING_SCOPE == SETTINGS_SHARING
    assert scopes.CONTACT_DELEGATION_SERVICES == {
        CONTACTS: ([DELEGATION_READ], [DELEGATION])
    }
    assert scopes.DELEGATED_SERVICES == {GMAIL: ([SETTINGS_BASIC], [SETTINGS_SHARING])}


def test_all_three_contact_delegation_methods_are_registered():
    assert {spec_id: spec.risk for spec_id, spec in CONTACT_OPS.items()} == {
        "admin.contacts.v1.users.delegates.list": "read",
        "admin.contacts.v1.users.delegates.create": "manage",
        "admin.contacts.v1.users.delegates.delete": "destructive",
    }
    assert {admin_service_for(spec) for spec in CONTACT_OPS.values()} == {CONTACTS}
    # Responses carry delegate addresses only, never contact data.
    assert {
        field for spec in CONTACT_OPS.values() for field in spec.response_fields
    } <= {"email"}
    assert client_scopes(CONTACT_OPS["admin.contacts.v1.users.delegates.list"]) == [
        DELEGATION_READ
    ]
    for spec_id in ("create", "delete"):
        spec = CONTACT_OPS[f"admin.contacts.v1.users.delegates.{spec_id}"]
        assert set(client_scopes(spec)) <= {DELEGATION, DELEGATION_READ}
    assert not [
        e
        for e in excluded_operations()
        if e.service == "admin" and e.version == "contacts_v1"
    ]


def test_only_delegate_operations_and_bounded_settings_are_registered():
    assert {spec_id: spec.risk for spec_id, spec in GMAIL_OPS.items()} == {
        "gmail.users.settings.delegates.list": "read",
        "gmail.users.settings.delegates.get": "read",
        "gmail.users.settings.delegates.create": "manage",
        "gmail.users.settings.delegates.delete": "destructive",
        **dict.fromkeys(
            (
                "gmail.users.settings.getAutoForwarding",
                "gmail.users.settings.getImap",
                "gmail.users.settings.getPop",
                "gmail.users.settings.getVacation",
                "gmail.users.settings.getLanguage",
                "gmail.users.settings.sendAs.list",
                "gmail.users.settings.sendAs.get",
                "gmail.users.settings.forwardingAddresses.list",
                "gmail.users.settings.forwardingAddresses.get",
            ),
            "read",
        ),
        "gmail.users.settings.updateLanguage": "manage",
    }
    assert {admin_service_for(spec) for spec in GMAIL_OPS.values()} == {GMAIL}
    assert {
        field
        for spec_id, spec in GMAIL_OPS.items()
        if ".delegates." in spec_id
        for field in spec.response_fields
    } <= {"delegateEmail", "verificationStatus"}
    requested = {s for spec in GMAIL_OPS.values() for s in client_scopes(spec)}
    assert requested == {SETTINGS_BASIC, SETTINGS_SHARING}
    for spec_id in ("list", "get"):
        spec = GMAIL_OPS[f"gmail.users.settings.delegates.{spec_id}"]
        assert client_scopes(spec) == [SETTINGS_BASIC]


def test_every_other_gmail_method_is_excluded_by_category():
    excluded = [e for e in excluded_operations() if e.service == "gmail"]
    counts: dict[str, int] = {}
    for exclusion in excluded:
        counts[exclusion.category] = counts.get(exclusion.category, 0) + 1
    assert counts == {
        "message-scope": 32,
        "push-channel": 2,
        "secret-in-payload": 5,
        "mailbox-setting": 12,
        "encryption-key": 14,
    }
    categories = {e.id: e.category for e in excluded}
    for message_method in (
        "gmail.users.messages.get",
        "gmail.users.messages.attachments.get",
        "gmail.users.threads.get",
        "gmail.users.drafts.get",
        "gmail.users.history.list",
        "gmail.users.labels.list",
        "gmail.users.getProfile",
    ):
        assert categories[message_method] == "message-scope", message_method
    assert categories["gmail.users.settings.forwardingAddresses.create"] == (
        "mailbox-setting"
    )
    assert categories["gmail.users.settings.cse.keypairs.obliterate"] == (
        "encryption-key"
    )
    assert {"encryption-key", "mailbox-setting", "message-scope"} <= set(
        EXCLUSION_CATEGORIES
    )
    assert list(EXCLUSION_CATEGORIES) == sorted(EXCLUSION_CATEGORIES)


def test_no_oauth_consent_ever_requests_a_gmail_settings_or_message_scope(monkeypatch):
    import main

    everything = list(main.SERVICE_MODULES)
    for selection in (None, everything, [GMAIL], [CONTACTS, GMAIL]):
        requested = set(get_scopes_for_tools(selection))
        assert SETTINGS_SHARING not in requested, selection
        if selection and "gmail" not in selection:
            assert not requested & ({SETTINGS_BASIC} | MESSAGE_SCOPES), selection
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    assert not set(get_scopes_for_tools([GMAIL])) & (
        {SETTINGS_BASIC, SETTINGS_SHARING} | MESSAGE_SCOPES
    )
    assert scopes.TOOL_SCOPES_MAP[GMAIL] == []
    assert scopes.TOOL_READONLY_SCOPES_MAP[GMAIL] == []
    for level in ("readonly", "manage", "destructive"):
        assert permissions.get_scopes_for_permission(GMAIL, level) == []


def test_contact_delegation_scopes_only_come_with_that_service(monkeypatch):
    import main

    assert DELEGATION not in get_scopes_for_tools(None)
    others = [s for s in main.SERVICE_MODULES if s != CONTACTS]
    assert not {DELEGATION, DELEGATION_READ} & set(get_scopes_for_tools(others))
    assert {DELEGATION, DELEGATION_READ} <= set(get_scopes_for_tools([CONTACTS]))
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    requested = set(get_scopes_for_tools([CONTACTS]))
    assert DELEGATION_READ in requested and DELEGATION not in requested


@pytest.mark.parametrize("service", [CONTACTS, GMAIL])
def test_services_are_opt_in_and_never_built_by_the_generic_decorator(service):
    import main

    assert service in scopes.OPT_IN_SERVICES
    assert service not in main.DEFAULT_SERVICES
    assert main.SERVICE_MODULES[service] == "gadmin.admin_tools"
    assert len(main.SERVICE_ICONS[service]) == 1
    # Neither is a require_google_service type, so no OAuth path can build one.
    assert service not in SERVICE_CONFIGS


def test_contact_delegation_permission_levels():
    level = permissions.get_scopes_for_permission
    assert level(CONTACTS, "readonly") == [DELEGATION_READ]
    assert set(level(CONTACTS, "manage")) == {DELEGATION_READ, DELEGATION}


@pytest.mark.parametrize(
    ("level", "allowed"),
    [
        ("readonly", {"list", "get"}),
        ("manage", {"list", "get", "create"}),
        ("destructive", {"list", "get", "create", "delete"}),
    ],
)
def test_gmail_permission_levels_gate_by_risk(monkeypatch, level, allowed):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory", GMAIL])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {GMAIL: level})
    permitted = set()
    for spec_id, spec in GMAIL_OPS.items():
        try:
            assert_admin_permission(spec)
        except PermissionError:
            continue
        if ".delegates." in spec_id:
            permitted.add(spec_id.rsplit(".", 1)[1])
        elif spec_id.endswith("updateLanguage"):
            assert level != "readonly"
        else:
            assert spec.risk == "read"
    assert permitted == allowed


def test_gmail_reads_run_in_read_only_mode(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory", GMAIL])
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    assert_admin_permission(GMAIL_OPS["gmail.users.settings.delegates.list"])
    with pytest.raises(PermissionError):
        assert_admin_permission(GMAIL_OPS["gmail.users.settings.delegates.delete"])
