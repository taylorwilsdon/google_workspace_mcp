"""Offboarding with the optional Contact Delegation, Gmail delegate, and Vault
clients. Contact delegates are removed one confirmed write at a time, Gmail
delegates are reported for manual review, and the final deletion needs a fresh
Vault hold check that finds no covering hold. A plan made without these clients
keeps the earlier steps."""

import json
from dataclasses import asdict, replace

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
from gadmin.audit import AuditSink
from gadmin.confirm import ConfirmationError, ConfirmationStore
from gadmin.execute import AdminApiError
from gadmin.guard import AdminBoundaryError
from gadmin.offboarding import STEPS, VaultHold, plan_user_offboarding
from gadmin.offboarding_store import WorkflowStore
from tests.gadmin.fake_directory import http_error
from tests.gadmin.fake_google import FakeGoogleApi
from tests.gadmin.test_offboarding import (
    CONTEXT,
    LEAVER,
    MANAGER,
    Workspace,
    advance,
    advance_until,
    step,
)


@pytest.fixture
def stores(tmp_path):
    return {
        "store": WorkflowStore(tmp_path / "workflows"),
        "confirmations": ConfirmationStore(tmp_path / "confirmations"),
        "audit": AuditSink(tmp_path / "audit.jsonl"),
    }


ASSISTANT = "assistant@op.example"
FORMER = "former@op.example"
CONTACT_STEPS = (
    "suspend_user",
    "sign_out",
    "revoke_tokens",
    "remove_contact_delegates",
    "transfer_data",
    "wait_for_transfer",
    "remove_groups",
    "remove_licenses",
    "delete_user",
)
# Private values Google may return next to the fields offboarding needs.
PRIVATE = ("Private Name", "Quarterly numbers attached", "Litigation X", "Lee")
VAULT_UNCHECKED_GAP = (
    "Vault holds were not checked: final deletion is blocked until a fresh check "
    "with the admin-vault service finds no covering hold."
)


@pytest.fixture(autouse=True)
def _services(monkeypatch):
    monkeypatch.setattr(
        scopes,
        "_ENABLED_TOOLS",
        [
            "admin-directory",
            "admin-datatransfer",
            "admin-licensing",
            "admin-contact-delegation",
            "admin-gmail-delegates",
            "admin-vault",
        ],
    )
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


class Delegation(Workspace):
    """The offboarding fakes plus Contact Delegation, Gmail, and Vault."""

    def __init__(self):
        super().__init__()
        self.directory.add_org_unit("OU_ROOT", path="/")
        self.directory.add_org_unit("OU_SALES", path="/Sales")
        self.directory.add_org_unit("OU_EAST", path="/Sales/East")
        self.directory.add_org_unit("OU_SALESFORCE", path="/SalesForce")
        self.directory.users_by_key[LEAVER]["orgUnitPath"] = "/Sales/East"
        self.contact_delegates = [ASSISTANT, FORMER]
        self.contact_failure: Exception | None = None
        self.contacts_api = FakeGoogleApi(
            "admin.contacts.v1",
            {
                "admin.contacts.v1.users.delegates.list": self._list_contacts,
                "admin.contacts.v1.users.delegates.delete": self._delete_contact,
            },
        )
        self.matters = [{"matterId": "M1", "name": "Litigation X", "state": "OPEN"}]
        self.holds: dict[str, list[dict]] = {"M1": []}
        self.vault_api = FakeGoogleApi(
            "vault",
            {
                "vault.matters.list": self._list_matters,
                "vault.matters.holds.list": self._list_holds,
            },
        )
        self.gmail_api = FakeGoogleApi(
            "gmail",
            {
                "gmail.users.settings.delegates.list": lambda userId: {
                    "delegates": [
                        {
                            "delegateEmail": ASSISTANT,
                            "verificationStatus": "accepted",
                            "displayName": "Private Name",
                            "snippet": "Quarterly numbers attached",
                        }
                    ]
                }
            },
        )
        self.clients = replace(
            self.clients, contacts=self.contacts_api, vault=self.vault_api
        )

    def _list_contacts(self, userId, **_):
        assert userId == LEAVER
        return {
            "delegates": [{"email": e} for e in self.contact_delegates],
            "contacts": [{"displayName": "Private Name"}],
        }

    def _delete_contact(self, userId, delegate):
        assert userId == LEAVER
        if self.contact_failure:
            raise self.contact_failure
        self.contact_delegates.remove(delegate)
        return {}

    def _list_matters(self, state, **_):
        assert state == "OPEN"
        return {"matters": list(self.matters)}

    def _list_holds(self, matterId, view, **_):
        assert view == "FULL_HOLD"
        return {"holds": list(self.holds[matterId])}

    def contact_deletes(self) -> list[dict]:
        return [
            kwargs for op, kwargs in self.contacts_api.calls if op.endswith(".delete")
        ]

    def all_writes(self) -> int:
        return len(self.writes()) + len(self.contact_deletes())


def _hold(hold_id="H1", corpus="MAIL", **shape) -> dict:
    return {"holdId": hold_id, "name": "Case hold", "corpus": corpus, **shape}


def _account_hold(account_id="U_LEAVER", email=LEAVER, **extra) -> dict:
    return _hold(
        accounts=[{"accountId": account_id, "email": email, "firstName": "Lee"}],
        **extra,
    )


def _unit_hold(unit: str, **extra) -> dict:
    return _hold(orgUnit={"orgUnitId": f"id:{unit}"}, **extra)


@pytest.fixture
def ws():
    return Delegation()


def _plan(ws, stores):
    return plan_user_offboarding(
        CONTEXT, ws.clients, LEAVER, MANAGER, store=stores["store"]
    )


def _to_deletion(ws, stores, workflow_id):
    advance_until(ws, stores, workflow_id, "wait_for_transfer")
    ws.transfer_status = "completed"
    return advance_until(ws, stores, workflow_id, "delete_user")


# --- A plan without the new clients ------------------------------------------------


def test_plan_without_new_clients_keeps_the_earlier_steps(ws, stores):
    ws.clients = replace(ws.clients, contacts=None, vault=None)

    plan = _plan(ws, stores)

    assert plan.steps == STEPS
    assert plan.confirmed_steps == ("suspend_user", "remove_licenses", "delete_user")
    assert VAULT_UNCHECKED_GAP in plan.not_checked
    assert any("retention" in gap.casefold() for gap in plan.not_checked)
    assert any("cannot see" in gap for gap in plan.not_checked)
    assert plan.contact_delegates == () and plan.gmail_delegates == ()
    assert plan.vault_status == "" and plan.vault_holds == ()
    assert any(
        "Gmail mail delegates were not checked" in gap
        and "admin-gmail-delegates" in gap
        and "domain-wide delegation" in gap
        for gap in plan.not_checked
    )
    assert any("Contact delegates were not checked" in gap for gap in plan.not_checked)
    assert ws.contacts_api.calls == [] and ws.vault_api.calls == []


# --- Contact delegates ---------------------------------------------------------------


def test_plan_lists_contact_delegates_and_adds_a_confirmed_step(ws, stores):
    plan = _plan(ws, stores)

    assert plan.steps == CONTACT_STEPS
    assert plan.confirmed_steps == (
        "suspend_user",
        "remove_contact_delegates",
        "remove_licenses",
        "delete_user",
    )
    assert plan.contact_delegates == (ASSISTANT, FORMER)
    assert any("delegate of another user" in gap for gap in plan.not_checked)
    assert not any("Contact delegates were not checked" in g for g in plan.not_checked)
    assert ws.contact_deletes() == [] and ws.writes() == []
    rendered = json.dumps(asdict(plan))
    assert not [value for value in PRIVATE if value in rendered]


def test_each_contact_delegate_needs_its_own_confirmation(ws, stores):
    wid = _plan(ws, stores).workflow_id

    state = advance_until(ws, stores, wid, "remove_contact_delegates")
    assert state.status == "awaiting_confirmation"
    assert ASSISTANT in step(state, "remove_contact_delegates").message
    first = state.confirmation

    state = advance(ws, stores, wid, first)
    assert ws.contact_deletes() == [{"userId": LEAVER, "delegate": ASSISTANT}]
    assert state.current_step == "remove_contact_delegates"

    state = advance(ws, stores, wid)
    assert state.status == "awaiting_confirmation"
    assert FORMER in step(state, "remove_contact_delegates").message
    with pytest.raises(ConfirmationError):
        advance(ws, stores, wid, first)
    assert len(ws.contact_deletes()) == 1

    state = advance(ws, stores, wid)
    before = ws.all_writes()
    state = advance(ws, stores, wid, state.confirmation)
    assert ws.contact_deletes()[-1] == {"userId": LEAVER, "delegate": FORMER}
    assert ws.all_writes() == before + 1

    state = advance(ws, stores, wid)
    assert step(state, "remove_contact_delegates").status == "done"
    assert ws.contact_delegates == []


def test_every_advance_makes_at_most_one_write(ws, stores):
    wid = _plan(ws, stores).workflow_id
    state = advance(ws, stores, wid)
    for _ in range(30):
        before = ws.all_writes()
        if state.current_step == "wait_for_transfer":
            ws.transfer_status = "completed"
        state = advance(ws, stores, wid, state.confirmation)
        assert ws.all_writes() - before <= 1
        if state.status == "completed":
            break
    assert state.status == "completed"
    assert len(ws.contact_deletes()) == 2


def test_changed_delegate_list_refuses_the_old_confirmation(ws, stores):
    wid = _plan(ws, stores).workflow_id
    state = advance_until(ws, stores, wid, "remove_contact_delegates")
    ws.contact_delegates.remove(ASSISTANT)  # removed in the console meanwhile

    with pytest.raises(ConfirmationError, match="no longer matches"):
        advance(ws, stores, wid, state.confirmation)
    assert ws.contact_deletes() == []


def test_delegate_still_listed_after_an_attempt_stops_for_review(ws, stores):
    wid = _plan(ws, stores).workflow_id
    state = advance_until(ws, stores, wid, "remove_contact_delegates")
    ws.contact_failure = http_error(503, "Backend Error")

    state = advance(ws, stores, wid, state.confirmation)
    assert state.status == "failed"
    ws.contact_failure = None
    state = advance(ws, stores, wid)

    assert state.status == "manual_action_required"
    assert "uncertain" in step(state, "remove_contact_delegates").message
    assert len(ws.contact_deletes()) == 1


def test_verified_earlier_attempt_moves_on(ws, stores):
    wid = _plan(ws, stores).workflow_id
    state = advance_until(ws, stores, wid, "remove_contact_delegates")
    ws.contact_failure = http_error(503, "Backend Error")
    advance(ws, stores, wid, state.confirmation)
    ws.contact_failure = None
    ws.contact_delegates.remove(ASSISTANT)  # Google applied it after all

    state = advance(ws, stores, wid)

    assert state.status == "awaiting_confirmation"
    assert FORMER in step(state, "remove_contact_delegates").message


def test_contact_listing_that_never_ends_refuses_the_plan(ws, stores):
    ws.contacts_api.handlers["admin.contacts.v1.users.delegates.list"] = lambda **_: {
        "delegates": [{"email": ASSISTANT}],
        "nextPageToken": "more",
    }

    with pytest.raises(AdminBoundaryError, match="contact delegate"):
        _plan(ws, stores)
    assert len(ws.contacts_api.calls) <= 10
    assert list(stores["store"].directory.glob("*.json")) == []


def test_contact_permission_error_refuses_the_plan(ws, stores):
    ws.contacts_api.handlers["admin.contacts.v1.users.delegates.list"] = http_error(
        403, "Not Authorized to access this resource/api"
    )

    with pytest.raises(AdminApiError, match="HTTP 403"):
        _plan(ws, stores)
    assert list(stores["store"].directory.glob("*.json")) == []


def test_missing_contact_client_stops_the_step(ws, stores):
    wid = _plan(ws, stores).workflow_id
    advance_until(ws, stores, wid, "revoke_tokens")
    ws.clients = replace(ws.clients, contacts=None)

    state = advance_until(ws, stores, wid, "remove_contact_delegates")
    state = advance(ws, stores, wid)

    assert state.status == "manual_action_required"
    assert "admin-contact-delegation" in step(state, "remove_contact_delegates").message
    assert ws.contact_deletes() == []


def test_re_added_contact_delegate_blocks_deletion(ws, stores):
    wid = _plan(ws, stores).workflow_id
    advance_until(ws, stores, wid, "wait_for_transfer")
    ws.contact_delegates.append("late@op.example")

    state = _to_deletion(ws, stores, wid)

    assert state.status == "manual_action_required"
    assert "contact delegate" in step(state, "delete_user").message
    assert LEAVER in ws.directory.users_by_key


# --- Gmail delegates -----------------------------------------------------------------


def test_gmail_delegates_are_reported_for_manual_review(ws, stores):
    ws.clients = replace(ws.clients, mailbox=ws.gmail_api, mailbox_owner=LEAVER)

    plan = _plan(ws, stores)

    assert plan.gmail_delegates == (f"{ASSISTANT} (accepted)",)
    assert any(
        "Gmail mail delegates" in gap and ASSISTANT in gap for gap in plan.not_checked
    )
    assert ws.gmail_api.calls == [
        ("gmail.users.settings.delegates.list", {"userId": LEAVER})
    ]
    rendered = json.dumps(asdict(plan))
    assert not [value for value in PRIVATE if value in rendered]
    assert plan.steps == CONTACT_STEPS  # Gmail delegates are never removed


def test_gmail_client_for_another_mailbox_is_not_used(ws, stores):
    ws.clients = replace(ws.clients, mailbox=ws.gmail_api, mailbox_owner=MANAGER)

    plan = _plan(ws, stores)

    assert plan.gmail_delegates == ()
    assert ws.gmail_api.calls == []
    assert any("Gmail mail delegates were not checked" in g for g in plan.not_checked)


def test_gmail_error_is_reported_as_not_checked(ws, stores):
    ws.gmail_api.handlers["gmail.users.settings.delegates.list"] = http_error(403)
    ws.clients = replace(ws.clients, mailbox=ws.gmail_api, mailbox_owner=LEAVER)

    plan = _plan(ws, stores)

    assert plan.gmail_delegates == ()
    assert any("could not be checked" in g for g in plan.not_checked)


# --- Vault holds ---------------------------------------------------------------------


def test_no_covering_hold_is_none_found_and_never_certified(ws, stores):
    ws.holds["M1"] = [
        _account_hold("U_OTHER", "other@op.example"),
        _unit_hold("OU_SALESFORCE", hold_id="H2"),
    ]

    plan = _plan(ws, stores)

    assert plan.vault_status == "none_found" and plan.vault_holds == ()
    assert VAULT_UNCHECKED_GAP not in plan.not_checked
    assert any("retention" in gap.casefold() for gap in plan.not_checked)
    assert any("cannot see" in gap for gap in plan.not_checked)
    assert (
        "vault.matters.holds.list",
        {"matterId": "M1", "view": "FULL_HOLD", "pageSize": 100},
    ) in ws.vault_api.calls


@pytest.mark.parametrize(
    "hold, corpus",
    [
        (_account_hold(corpus="MAIL"), "MAIL"),
        (_account_hold("U_SOMEONE", LEAVER.upper(), corpus="GROUPS"), "GROUPS"),
        (_unit_hold("OU_EAST", corpus="DRIVE"), "DRIVE"),
        (_unit_hold("OU_SALES", corpus="HANGOUTS_CHAT"), "HANGOUTS_CHAT"),
        (_unit_hold("OU_ROOT", corpus="CALENDAR"), "CALENDAR"),
    ],
    ids=["account-id", "account-email", "own-unit", "ancestor-unit", "root-unit"],
)
def test_covering_hold_is_recorded_with_safe_ids_only(ws, stores, hold, corpus):
    ws.holds["M1"] = [hold]

    plan = _plan(ws, stores)

    assert plan.vault_status == "held"
    assert plan.vault_holds == (VaultHold("M1", "H1", corpus),)
    rendered = json.dumps(asdict(plan))
    assert not [value for value in (*PRIVATE, "Case hold") if value in rendered]


def test_covering_hold_blocks_deletion(ws, stores):
    ws.holds["M1"] = [_unit_hold("OU_SALES")]
    wid = _plan(ws, stores).workflow_id

    state = _to_deletion(ws, stores, wid)

    assert state.status == "manual_action_required"
    assert "Vault" in step(state, "delete_user").message
    assert state.confirmation is None
    assert LEAVER in ws.directory.users_by_key


def test_hold_added_after_the_proposal_blocks_the_confirmed_deletion(ws, stores):
    wid = _plan(ws, stores).workflow_id
    state = _to_deletion(ws, stores, wid)
    assert state.status == "awaiting_confirmation"
    ws.holds["M1"] = [_account_hold()]

    state = advance(ws, stores, wid, state.confirmation)

    assert state.status == "manual_action_required"
    assert LEAVER in ws.directory.users_by_key


def test_none_found_allows_the_confirmed_deletion(ws, stores):
    wid = _plan(ws, stores).workflow_id
    state = _to_deletion(ws, stores, wid)

    state = advance(ws, stores, wid, state.confirmation)

    assert state.status == "completed"
    assert LEAVER not in ws.directory.users_by_key


@pytest.mark.parametrize(
    "hold",
    [
        _hold(),  # neither accounts nor org unit
        _hold(accounts=[], orgUnit={"orgUnitId": "id:OU_SALES"}),
        _hold(accounts="U_LEAVER"),
        _hold(orgUnit={"orgUnitPath": "/Sales"}),
        _unit_hold("OU_MISSING"),  # not a unit of this customer
        _account_hold(corpus="mail"),
        _account_hold(hold_id="../H1"),
        _hold(holdId=None, accounts=[{"accountId": "U_LEAVER"}]),
    ],
    ids=[
        "no-scope",
        "both-scopes",
        "accounts-not-a-list",
        "unit-without-id",
        "unknown-unit",
        "odd-corpus",
        "unsafe-id",
        "missing-id",
    ],
)
def test_unsupported_hold_shape_is_unknown(ws, stores, hold):
    ws.holds["M1"] = [hold]

    plan = _plan(ws, stores)

    assert plan.vault_status == "unknown" and plan.vault_holds == ()
    assert any("could not be fully checked" in g for g in plan.not_checked)


@pytest.mark.parametrize(
    "operation", ["vault.matters.list", "vault.matters.holds.list"]
)
def test_endless_paging_is_unknown_within_the_bound(ws, stores, operation):
    items = "matters" if operation == "vault.matters.list" else "holds"
    ws.vault_api.handlers[operation] = lambda **_: {
        items: [{"matterId": "M1"}] if items == "matters" else [],
        "nextPageToken": "more",
    }

    plan = _plan(ws, stores)

    assert plan.vault_status == "unknown"
    assert sum(op == operation for op, _ in ws.vault_api.calls) <= 5


@pytest.mark.parametrize(
    "operation", ["vault.matters.list", "vault.matters.holds.list"]
)
@pytest.mark.parametrize("status", [403, 500])
def test_google_error_is_unknown(ws, stores, operation, status):
    ws.vault_api.handlers[operation] = http_error(status)

    plan = _plan(ws, stores)

    assert plan.vault_status == "unknown"


def test_unknown_result_blocks_deletion(ws, stores):
    wid = _plan(ws, stores).workflow_id
    ws.vault_api.handlers["vault.matters.list"] = http_error(403)

    state = _to_deletion(ws, stores, wid)

    assert state.status == "manual_action_required"
    assert "Vault" in step(state, "delete_user").message
    assert LEAVER in ws.directory.users_by_key


def test_plan_without_vault_runs_earlier_steps_but_never_proposes_deletion(ws, stores):
    ws.clients = replace(ws.clients, vault=None)
    wid = _plan(ws, stores).workflow_id

    state = _to_deletion(ws, stores, wid)

    assert tuple(s.id for s in state.steps if s.status == "done") == CONTACT_STEPS[:-1]
    assert state.status == "manual_action_required"
    assert state.confirmation is None
    assert "admin-vault" in step(state, "delete_user").message
    assert LEAVER in ws.directory.users_by_key
    assert ws.vault_api.calls == []

    # A fresh check that finds no covering hold is what allows the proposal.
    ws.clients = replace(ws.clients, vault=ws.vault_api)
    state = advance(ws, stores, wid)
    assert state.status == "awaiting_confirmation"
    assert state.current_step == "delete_user"


def _remove_vault(ws):
    ws.clients = replace(ws.clients, vault=None)


def _fail_vault(ws):
    ws.vault_api.handlers["vault.matters.list"] = http_error(503)


def _deselect_vault(ws):
    scopes._ENABLED_TOOLS.remove("admin-vault")


@pytest.mark.parametrize("planned_with_vault", [True, False])
@pytest.mark.parametrize(
    "change", [_remove_vault, _fail_vault, _deselect_vault], ids=lambda f: f.__name__
)
def test_vault_check_that_cannot_run_blocks_the_confirmed_deletion(
    ws, stores, change, planned_with_vault
):
    if not planned_with_vault:
        ws.clients = replace(ws.clients, vault=None)
    wid = _plan(ws, stores).workflow_id
    ws.clients = replace(ws.clients, vault=ws.vault_api)
    state = _to_deletion(ws, stores, wid)
    assert state.status == "awaiting_confirmation"
    change(ws)

    state = advance(ws, stores, wid, state.confirmation)

    assert state.status == "manual_action_required"
    assert "Vault" in step(state, "delete_user").message
    assert LEAVER in ws.directory.users_by_key
    assert not any(op == "directory.users.delete" for op, _ in ws.directory.calls)


# --- Offboarding tools ---------------------------------------------------------------


def _fake_tool_clients(ws, monkeypatch, tmp_path):
    """Serve the tools' Google clients from ``ws``; return (clients, built)."""
    import gadmin.admin_tools as tools
    import gadmin.auth as auth

    clients = {
        "datatransfer": ws.transfer_api,
        "licensing": ws.licensing_api,
        "admin": ws.contacts_api,
        "vault": ws.vault_api,
    }
    built = []

    async def get_service(
        use_oauth21, service, version, tool, selected, scopes, *a, **kw
    ):
        built.append((tool, (service, version), list(scopes)))
        return clients[tool.split(".")[0]], selected

    async def directory(*args, **kwargs):
        return ws.directory, CONTEXT.actor_email

    monkeypatch.setattr("auth.service_decorator._authenticate_service", directory)
    monkeypatch.setattr(auth, "_authenticate_service", get_service)
    monkeypatch.setattr(auth, "pinned_client", lambda client, *_: client)
    # OAuth mode: no Gmail client can be built, so Gmail is reported unchecked.
    monkeypatch.setattr(auth, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(tools, "default_workflows", lambda: stores_at(tmp_path)[0])
    monkeypatch.setattr(tools, "default_confirmations", lambda: stores_at(tmp_path)[1])
    monkeypatch.setattr(
        tools, "_audit_sink", lambda: tools.AuditSink(tmp_path / "audit.jsonl")
    )
    return clients, built


@pytest.mark.asyncio
async def test_tools_build_the_selected_clients_and_close_them(
    ws, monkeypatch, tmp_path
):
    from tests.gadmin.test_offboarding_tools import call

    clients, built = _fake_tool_clients(ws, monkeypatch, tmp_path)

    result, text = await call(
        "plan_user_offboarding",
        user_google_email=CONTEXT.actor_email,
        target_email=LEAVER,
        transfer_recipient=MANAGER,
    )

    assert not result.is_error, text
    plan = json.loads(text)
    assert plan["steps"] == list(CONTACT_STEPS)
    assert plan["contact_delegates"] == [ASSISTANT, FORMER]
    assert plan["vault_status"] == "none_found"
    assert any(
        "Gmail mail delegates were not checked" in g for g in plan["not_checked"]
    )
    assert (
        "admin.contacts.v1.users.delegates.list",
        ("admin", "directory_v1"),
        ["https://www.googleapis.com/auth/admin.contact.delegation.readonly"],
    ) in built
    assert (
        "vault.matters.holds.list",
        ("vault", "v1"),
        ["https://www.googleapis.com/auth/ediscovery.readonly"],
    ) in built
    assert all(api.closed for api in clients.values())

    result, text = await call(
        "advance_user_offboarding",
        user_google_email=CONTEXT.actor_email,
        workflow_id=plan["workflow_id"],
    )
    assert not result.is_error, text
    assert "admin.contacts.v1.users.delegates.delete" in [b[0] for b in built]


@pytest.mark.asyncio
async def test_contact_manage_level_keeps_earlier_steps_and_stops_the_removal(
    ws, monkeypatch, tmp_path
):
    from tests.gadmin.test_offboarding_tools import call

    # manage may list contact delegates but not delete them (a destructive write).
    monkeypatch.setattr(
        permissions, "_PERMISSIONS", {"admin-contact-delegation": "manage"}
    )
    _, built = _fake_tool_clients(ws, monkeypatch, tmp_path)
    result, text = await call(
        "plan_user_offboarding",
        user_google_email=CONTEXT.actor_email,
        target_email=LEAVER,
        transfer_recipient=MANAGER,
    )
    assert not result.is_error, text
    arguments = {
        "user_google_email": CONTEXT.actor_email,
        "workflow_id": json.loads(text)["workflow_id"],
    }

    state = {}
    for _ in range(8):
        result, text = await call(
            "advance_user_offboarding",
            **arguments,
            **(
                {"confirmation": state["confirmation"]}
                if state.get("confirmation")
                else {}
            ),
        )
        assert not result.is_error, text
        state = json.loads(text)
        if state["current_step"] == "remove_contact_delegates":
            break

    assert ws.directory.users_by_key[LEAVER]["suspended"] is True
    assert ws.directory.user_tokens["U_LEAVER"] == []
    assert state["status"] == "manual_action_required"
    contact = next(s for s in state["steps"] if s["id"] == "remove_contact_delegates")
    assert "destructive" in contact["message"]
    assert ws.contact_deletes() == []
    contact_clients = {b[0] for b in built if b[0].startswith("admin.contacts.")}
    assert contact_clients == {"admin.contacts.v1.users.delegates.list"}


def stores_at(path):
    return WorkflowStore(path / "workflows"), ConfirmationStore(path / "confirmations")
