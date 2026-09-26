"""User offboarding plans with reads only, then advances one Google write at a
time from persisted step states. Transfer finishes before licenses or the account
go, every destructive step needs its own fresh confirmation, and a retry reads
Google's state instead of repeating a write it cannot see."""

import os
import stat

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
import gadmin.offboarding as workflow_module
import gadmin.offboarding_store as workflow_storage
from gadmin.audit import AuditSink
from gadmin.confirm import ConfirmationError, ConfirmationStore
from gadmin.guard import AdminBoundaryError, AdminContext
from gadmin.offboarding import (
    STEPS,
    AdminClients,
    OffboardingError,
    advance_offboarding,
    get_offboarding_status,
    plan_user_offboarding,
)
from gadmin.offboarding_store import WorkflowStore
from gadmin.registry import InvalidOperationInput, UnknownOperation
from tests.gadmin.fake_directory import FakeDirectory, http_error
from tests.gadmin.fake_google import FakeGoogleApi

ADMIN = "admin@op.example"
LEAVER = "leaver@op.example"
MANAGER = "manager@op.example"
CONTEXT = AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN")
DRIVE = {
    "id": "55656082996",
    "name": "Drive and Docs",
    "transferParams": [{"key": "PRIVACY_LEVEL", "value": ["PRIVATE", "SHARED"]}],
}
CALENDAR = {
    "id": "435070579839",
    "name": "Calendar",
    "transferParams": [{"key": "RELEASE_RESOURCES", "value": ["TRUE"]}],
}


class Workspace:
    """Directory, Data Transfer, and Licensing fakes sharing one customer."""

    def __init__(self):
        self.directory = FakeDirectory(customer_id="C01", page_size=10)
        self.directory.add_user(ADMIN, "U_ADMIN", super_admin=True)
        self.directory.add_user(LEAVER, "U_LEAVER")
        self.directory.add_user(MANAGER, "U_MANAGER")
        self.directory.add_group("G1", "sales@op.example", ["U_LEAVER", "U_MANAGER"])
        self.directory.add_group("G2", "all@op.example", ["U_LEAVER"])
        self.directory.user_tokens["U_LEAVER"] = [
            {"clientId": "client-1", "displayText": "Zoom"}
        ]
        self.transfers: dict[str, dict] = {}
        self.transfer_status = "inProgress"
        self.licenses = [
            _license("Google-Apps", "1010020027", LEAVER, "Business Starter"),
            _license("Google-Vault", "Google-Vault", LEAVER, "Vault"),
            _license("Google-Apps", "1010020027", MANAGER, "Business Starter"),
        ]
        self.license_failures: dict[str, Exception] = {}
        self.transfer_api = FakeGoogleApi(
            "datatransfer",
            {
                "datatransfer.applications.list": lambda **_: {
                    "applications": [DRIVE, CALENDAR]
                },
                "datatransfer.transfers.list": self._list_transfers,
                "datatransfer.transfers.insert": self._insert_transfer,
                "datatransfer.transfers.get": self._get_transfer,
            },
        )
        self.licensing_api = FakeGoogleApi(
            "licensing",
            {
                "licensing.licenseAssignments.listForProduct": self._list_licenses,
                "licensing.licenseAssignments.delete": self._delete_license,
            },
        )
        self.clients = AdminClients(
            directory=self.directory,
            transfer=self.transfer_api,
            licensing=self.licensing_api,
        )

    # --- Data Transfer -----------------------------------------------------------

    def _transfer(self, transfer: dict) -> dict:
        status = self.transfer_status
        return {
            **transfer,
            "overallTransferStatusCode": status,
            "applicationDataTransfers": [
                {**app, "applicationTransferStatus": status}
                for app in transfer["applicationDataTransfers"]
            ],
        }

    def _list_transfers(self, oldOwnerUserId, newOwnerUserId, **_):
        return {
            "dataTransfers": [
                self._transfer(t)
                for t in self.transfers.values()
                if (t["oldOwnerUserId"], t["newOwnerUserId"])
                == (oldOwnerUserId, newOwnerUserId)
            ]
        }

    def _insert_transfer(self, body):
        transfer_id = f"T{len(self.transfers) + 1}"
        self.transfers[transfer_id] = {"id": transfer_id, **body}
        return self._transfer(self.transfers[transfer_id])

    def _get_transfer(self, dataTransferId):
        return self._transfer(self.transfers[dataTransferId])

    # --- Licensing ---------------------------------------------------------------

    def _list_licenses(self, productId, customerId, **_):
        if productId in self.license_failures:
            raise self.license_failures[productId]
        return {
            "items": [lic for lic in self.licenses if lic["productId"] == productId]
        }

    def _delete_license(self, productId, skuId, userId):
        before = len(self.licenses)
        self.licenses = [
            lic
            for lic in self.licenses
            if (lic["productId"], lic["skuId"], lic["userId"])
            != (productId, skuId, userId)
        ]
        if len(self.licenses) == before:
            raise http_error(404, "Resource Not Found")
        return ""

    # --- What happened -----------------------------------------------------------

    def writes(self) -> list[str]:
        """Every Google write the fakes received, across all three APIs."""
        calls = (
            *self.directory.calls,
            *self.transfer_api.calls,
            *self.licensing_api.calls,
        )
        return [
            op
            for op, _ in calls
            if op.rsplit(".", 1)[1] in {"update", "delete", "signOut", "insert"}
        ]

    def leaver_license_count(self) -> int:
        return sum(lic["userId"] == LEAVER for lic in self.licenses)


def _license(product: str, sku: str, user: str, name: str) -> dict:
    return {"productId": product, "skuId": sku, "userId": user, "skuName": name}


@pytest.fixture(autouse=True)
def _all_admin_services(monkeypatch):
    monkeypatch.setattr(
        scopes,
        "_ENABLED_TOOLS",
        ["admin-directory", "admin-datatransfer", "admin-licensing"],
    )
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


@pytest.fixture
def workspace():
    return Workspace()


@pytest.fixture
def stores(tmp_path):
    return {
        "store": WorkflowStore(tmp_path / "workflows"),
        "confirmations": ConfirmationStore(tmp_path / "confirmations"),
        "audit": AuditSink(tmp_path / "audit.jsonl"),
    }


@pytest.fixture
def plan(workspace, stores):
    return plan_user_offboarding(
        CONTEXT, workspace.clients, LEAVER, MANAGER, store=stores["store"]
    )


def advance(workspace, stores, workflow_id, confirmation=None, context=CONTEXT):
    return advance_offboarding(
        context, workspace.clients, workflow_id, confirmation, **stores
    )


def advance_until(workspace, stores, workflow_id, step_id):
    """Advance, confirming each destructive step, until ``step_id`` is current."""
    state = advance(workspace, stores, workflow_id)
    for _ in range(30):
        if state.current_step == step_id:
            return state
        state = advance(workspace, stores, workflow_id, state.confirmation)
    raise AssertionError(f"never reached {step_id}: {state}")


def step(state, step_id):
    return next(s for s in state.steps if s.id == step_id)


# --- Planning ----------------------------------------------------------------------


def test_plan_reads_everything_and_writes_nothing(workspace, plan, stores):
    assert plan.target_email == LEAVER and plan.target_id == "U_LEAVER"
    assert plan.recipient_email == MANAGER and plan.customer_id == "C01"
    assert (
        plan.steps
        == STEPS
        == (
            "suspend_user",
            "sign_out",
            "revoke_tokens",
            "transfer_data",
            "wait_for_transfer",
            "remove_groups",
            "remove_licenses",
            "delete_user",
        )
    )
    assert plan.confirmed_steps == ("suspend_user", "remove_licenses", "delete_user")
    assert plan.transfer_applications == ("Drive and Docs", "Calendar")
    assert plan.unsupported_applications == ()
    assert plan.groups == ("sales@op.example", "all@op.example")
    assert plan.token_count == 1
    assert [a.sku_name for a in plan.licenses.assignments] == [
        "Business Starter",
        "Vault",
    ]
    assert plan.licenses.complete
    assert any("Vault" in gap for gap in plan.not_checked)
    assert workspace.writes() == []
    assert workspace.directory.write_calls == []

    status = get_offboarding_status(CONTEXT, plan.workflow_id, store=stores["store"])
    assert status.status == "in_progress"
    assert status.current_step == "suspend_user"
    assert any("Vault" in warning for warning in status.not_checked)
    assert {s.status for s in status.steps} == {"pending"}


def test_plan_refuses_recipient_outside_the_customer(workspace, stores):
    workspace.directory.add_user("x@other.example", "U_X", customer_id="C_OTHER")

    with pytest.raises(AdminBoundaryError, match="another customer"):
        plan_user_offboarding(
            CONTEXT, workspace.clients, LEAVER, "x@other.example", store=stores["store"]
        )

    assert list((stores["store"].directory).glob("*.json")) == []


@pytest.mark.parametrize(
    "recipient, message",
    [(LEAVER, "different user"), ("gone@op.example", "active user")],
)
def test_plan_refuses_unusable_recipient(workspace, stores, recipient, message):
    workspace.directory.add_user("gone@op.example", "U_GONE", suspended=True)

    with pytest.raises(AdminBoundaryError, match=message):
        plan_user_offboarding(
            CONTEXT, workspace.clients, LEAVER, recipient, store=stores["store"]
        )


@pytest.mark.parametrize(
    ("target", "recipient"),
    [("https://evil.example/x", MANAGER), (LEAVER, "../other-tenant")],
)
def test_plan_rejects_url_and_path_inputs_before_google_lookup(
    workspace, stores, target, recipient
):
    with pytest.raises(InvalidOperationInput):
        plan_user_offboarding(
            CONTEXT, workspace.clients, target, recipient, store=stores["store"]
        )
    assert workspace.directory.calls == []


def test_plan_refuses_the_admins_own_account(workspace, stores):
    with pytest.raises(AdminBoundaryError, match="your own account"):
        plan_user_offboarding(
            CONTEXT, workspace.clients, ADMIN, MANAGER, store=stores["store"]
        )


# --- The full sequence --------------------------------------------------------------


def test_full_offboarding_order_with_a_confirmation_per_destructive_step(
    workspace, plan, stores
):
    wid = plan.workflow_id

    state = advance(workspace, stores, wid)
    assert state.status == "awaiting_confirmation"
    assert state.current_step == "suspend_user"
    assert state.confirmation and LEAVER in step(state, "suspend_user").message
    assert workspace.directory.write_calls == []

    state = advance(workspace, stores, wid, state.confirmation)
    assert workspace.directory.users_by_key[LEAVER]["suspended"] is True
    assert state.current_step == "sign_out"

    state = advance(workspace, stores, wid)
    assert state.current_step == "revoke_tokens"
    state = advance(workspace, stores, wid)
    assert workspace.directory.user_tokens["U_LEAVER"] == []
    assert state.current_step == "revoke_tokens"

    state = advance(workspace, stores, wid)
    assert list(workspace.transfers) == ["T1"]
    inserted = workspace.transfers["T1"]
    assert (inserted["oldOwnerUserId"], inserted["newOwnerUserId"]) == (
        "U_LEAVER",
        "U_MANAGER",
    )
    assert [a["applicationId"] for a in inserted["applicationDataTransfers"]] == [
        DRIVE["id"],
        CALENDAR["id"],
    ]
    assert state.current_step == "wait_for_transfer"

    state = advance(workspace, stores, wid)
    assert state.status == "waiting"

    workspace.transfer_status = "completed"
    state = advance(workspace, stores, wid)
    assert step(state, "wait_for_transfer").status == "done"
    assert workspace.directory.group_items["G1"]["members"] == ["U_MANAGER"]
    assert workspace.directory.group_items["G2"]["members"] == ["U_LEAVER"]
    assert state.current_step == "remove_groups"
    state = advance(workspace, stores, wid)
    assert workspace.directory.group_items["G2"]["members"] == []
    state = advance(workspace, stores, wid)
    assert state.current_step == "remove_licenses"

    # One confirmation per license, never reused.
    seen = set()
    while state.current_step == "remove_licenses":
        state = advance(workspace, stores, wid)
        if state.current_step != "remove_licenses":
            break
        assert state.status == "awaiting_confirmation"
        assert state.confirmation not in seen
        seen.add(state.confirmation)
        state = advance(workspace, stores, wid, state.confirmation)
    assert len(seen) == 2
    assert workspace.leaver_license_count() == 0
    assert len(workspace.licenses) == 1  # the manager keeps their license

    assert state.current_step == "delete_user"
    assert state.status == "awaiting_confirmation"
    assert LEAVER in workspace.directory.users_by_key
    state = advance(workspace, stores, wid, state.confirmation)

    assert LEAVER not in workspace.directory.users_by_key
    assert state.status == "completed" and state.current_step is None
    assert any("Vault" in warning for warning in state.not_checked)
    assert {s.status for s in state.steps} == {"done"}
    assert [op for op, _ in workspace.directory.write_calls] == [
        "directory.users.update",
        "directory.users.signOut",
        "directory.tokens.delete",
        "directory.members.delete",
        "directory.members.delete",
        "directory.users.delete",
    ]


def test_each_call_makes_at_most_one_write_step(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    advance(workspace, stores, plan.workflow_id, state.confirmation)

    before = len(workspace.directory.write_calls)
    advance(workspace, stores, plan.workflow_id)

    assert [op for op, _ in workspace.directory.write_calls[before:]] == [
        "directory.users.signOut"
    ]


def test_audit_records_each_write_with_the_workflow(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    advance(workspace, stores, plan.workflow_id, state.confirmation)

    text = stores["audit"].path.read_text()
    assert plan.workflow_id in text
    assert "directory.users.update" in text
    assert state.confirmation.split(":", 1)[1] not in text


# --- Transfer gates deletion -----------------------------------------------------------


def test_deletion_waits_for_transfer(workspace, plan, stores):
    state = advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")
    for _ in range(3):
        state = advance(workspace, stores, plan.workflow_id)

    assert state.current_step == "wait_for_transfer"
    assert state.status == "waiting"
    assert "inProgress" in step(state, "wait_for_transfer").message
    assert LEAVER in workspace.directory.users_by_key
    assert workspace.leaver_license_count() == 2
    assert not any(
        op == "licensing.licenseAssignments.delete"
        for op, _ in workspace.licensing_api.calls
    )


def test_failed_transfer_stops_the_workflow(workspace, plan, stores):
    advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")
    workspace.transfer_status = "failed"

    state = advance(workspace, stores, plan.workflow_id)
    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "failed"
    assert state.current_step == "wait_for_transfer"
    assert "failed" in step(state, "wait_for_transfer").message
    assert workspace.leaver_license_count() == 2
    assert LEAVER in workspace.directory.users_by_key


# --- Recovery ----------------------------------------------------------------------


class _FailingSave(WorkflowStore):
    """Loses the save that would record a finished Google write."""

    def __init__(self, directory, fail_when):
        super().__init__(directory)
        self.fail_when = fail_when

    def save(self, workflow):
        if self.fail_when(workflow):
            self.fail_when = lambda _: False
            raise OSError("disk full")
        super().save(workflow)


def test_retry_adopts_a_transfer_google_accepted_before_the_save_failed(
    workspace, plan, stores
):
    advance_until(workspace, stores, plan.workflow_id, "revoke_tokens")
    advance(workspace, stores, plan.workflow_id)  # remove the last token
    failing = dict(
        stores,
        store=_FailingSave(
            stores["store"].directory,
            lambda wf: (
                next(s for s in wf["steps"] if s["id"] == "transfer_data")["status"]
                == "done"
            ),
        ),
    )

    with pytest.raises(OSError):
        advance(workspace, failing, plan.workflow_id)
    assert list(workspace.transfers) == ["T1"]
    lost = get_offboarding_status(CONTEXT, plan.workflow_id, store=stores["store"])
    assert step(lost, "transfer_data").status == "in_progress"

    state = advance(workspace, stores, plan.workflow_id)

    assert list(workspace.transfers) == ["T1"]  # not submitted twice
    assert step(state, "transfer_data").status == "done"
    assert state.current_step == "wait_for_transfer"
    inserts = [
        op for op, _ in workspace.transfer_api.calls if op.endswith("transfers.insert")
    ]
    assert len(inserts) == 1


def test_retry_does_not_adopt_an_unrelated_earlier_transfer(workspace, stores):
    workspace._insert_transfer(
        {
            "oldOwnerUserId": "U_LEAVER",
            "newOwnerUserId": "U_MANAGER",
            "applicationDataTransfers": [{"applicationId": CALENDAR["id"]}],
        }
    )
    plan = plan_user_offboarding(
        CONTEXT, workspace.clients, LEAVER, MANAGER, store=stores["store"]
    )

    advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")

    assert list(workspace.transfers) == ["T1", "T2"]


def test_retry_after_lost_suspension_save_needs_no_new_confirmation(
    workspace, plan, stores
):
    state = advance(workspace, stores, plan.workflow_id)
    failing = dict(
        stores,
        store=_FailingSave(
            stores["store"].directory,
            lambda wf: wf["steps"][0]["status"] == "done",
        ),
    )
    with pytest.raises(OSError):
        advance(workspace, failing, plan.workflow_id, state.confirmation)

    state = advance(workspace, stores, plan.workflow_id)

    assert step(state, "suspend_user").status == "done"
    assert "already suspended" in step(state, "suspend_user").message.lower()
    updates = [op for op, _ in workspace.directory.write_calls if op.endswith("update")]
    assert updates == ["directory.users.update"]


def test_steps_google_already_finished_are_not_repeated(workspace, plan, stores):
    workspace.directory.users_by_key[LEAVER]["suspended"] = True

    state = advance(workspace, stores, plan.workflow_id)

    assert step(state, "suspend_user").status == "done"
    assert state.current_step == "revoke_tokens"  # sign_out was this call's write
    assert [op for op, _ in workspace.directory.write_calls] == [
        "directory.users.signOut"
    ]


# --- Manual action, never false success ------------------------------------------------


def test_unsupported_application_requires_manual_action(workspace, stores):
    plan = plan_user_offboarding(
        CONTEXT,
        workspace.clients,
        LEAVER,
        MANAGER,
        applications=["Drive and Docs", "Chat"],
        store=stores["store"],
    )
    assert plan.unsupported_applications == ("Chat",)
    assert plan.transfer_applications == ("Drive and Docs",)

    state = advance_until(workspace, stores, plan.workflow_id, "transfer_data")
    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert state.current_step == "transfer_data"
    assert "Chat" in step(state, "transfer_data").message
    assert workspace.transfers == {}
    assert LEAVER in workspace.directory.users_by_key


def test_group_in_another_customer_cannot_be_modified(workspace, plan, stores):
    workspace.directory.add_group(
        "G_OUT", "external@other.example", ["U_LEAVER"], customer_id="C_OTHER"
    )
    advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")
    workspace.transfer_status = "completed"

    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert "group" in step(state, "remove_groups").message.lower()
    assert workspace.directory.group_items["G_OUT"]["members"] == ["U_LEAVER"]
    assert not any(
        op == "directory.members.delete" for op, _ in workspace.directory.write_calls
    )


def test_registry_method_removed_after_planning_needs_manual_action(
    workspace, plan, stores, monkeypatch
):
    state = advance(workspace, stores, plan.workflow_id)
    advance(workspace, stores, plan.workflow_id, state.confirmation)
    original = workflow_module.get_operation

    def without_signout(operation_id):
        if operation_id == "directory.users.signOut":
            raise UnknownOperation(operation_id)
        return original(operation_id)

    monkeypatch.setattr(workflow_module, "get_operation", without_signout)
    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert "pinned registry" in step(state, "sign_out").message
    assert not any(op.endswith("signOut") for op, _ in workspace.directory.write_calls)


def test_no_transferable_application_requires_manual_action(workspace, stores):
    workspace.transfer_api.handlers["datatransfer.applications.list"] = lambda **_: {
        "applications": []
    }
    plan = plan_user_offboarding(
        CONTEXT, workspace.clients, LEAVER, MANAGER, store=stores["store"]
    )

    advance_until(workspace, stores, plan.workflow_id, "transfer_data")
    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert workspace.transfers == {}


def test_unchecked_license_product_requires_manual_action(workspace, plan, stores):
    advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")
    workspace.transfer_status = "completed"
    workspace.license_failures["101001"] = http_error(503, "backend")

    state = advance_until(workspace, stores, plan.workflow_id, "remove_licenses")
    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert "101001" in step(state, "remove_licenses").message
    assert workspace.leaver_license_count() == 2
    assert LEAVER in workspace.directory.users_by_key


def test_missing_scope_on_a_write_requires_manual_action(workspace, plan, stores):
    workspace.directory.failures["directory.users.signOut"] = http_error(
        403,
        "Request had insufficient authentication scopes.",
        code="insufficientPermissions",
    )
    state = advance(workspace, stores, plan.workflow_id)
    advance(workspace, stores, plan.workflow_id, state.confirmation)

    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert state.current_step == "sign_out"
    assert "missing_scope" in step(state, "sign_out").message


def test_permission_level_without_destructive_requires_manual_action(
    workspace, plan, stores, monkeypatch
):
    monkeypatch.setattr(
        permissions,
        "_PERMISSIONS",
        {
            "admin-directory": "manage",
            "admin-datatransfer": "manage",
            "admin-licensing": "manage",
        },
    )

    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert state.confirmation is None
    assert "destructive" in step(state, "suspend_user").message
    assert workspace.directory.write_calls == []


def test_uncertain_signout_is_not_blindly_retried(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    advance(workspace, stores, plan.workflow_id, state.confirmation)
    workspace.directory.failures["directory.users.signOut"] = http_error(500, "boom")

    state = advance(workspace, stores, plan.workflow_id)
    assert state.status == "failed"

    del workspace.directory.failures["directory.users.signOut"]
    state = advance(workspace, stores, plan.workflow_id)
    assert state.status == "manual_action_required"
    assert "cannot be reconciled" in step(state, "sign_out").message
    assert [
        op for op, _ in workspace.directory.write_calls if op.endswith("signOut")
    ] == ["directory.users.signOut"]


def test_completed_transfer_for_a_different_owner_cannot_unlock_deletion(
    workspace, plan, stores
):
    advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")
    workspace.transfer_status = "completed"
    original = workspace.transfer_api.handlers["datatransfer.transfers.get"]
    workspace.transfer_api.handlers["datatransfer.transfers.get"] = lambda **params: {
        **original(**params),
        "newOwnerUserId": "U_OTHER",
    }

    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert workspace.leaver_license_count() == 2
    assert LEAVER in workspace.directory.users_by_key


def make_leaver_last_super_admin(workspace):
    workspace.directory.users_by_key[ADMIN].update(isAdmin=False, isDelegatedAdmin=True)
    workspace.directory.assignments.clear()
    workspace.directory.users_by_key[LEAVER]["isAdmin"] = True
    workspace.directory.assign("U_LEAVER", "R_SUPER")


@pytest.mark.parametrize("change_after_proposal", [False, True])
def test_new_last_super_admin_cannot_be_suspended(
    workspace, plan, stores, change_after_proposal
):
    confirmation = (
        advance(workspace, stores, plan.workflow_id).confirmation
        if change_after_proposal
        else None
    )
    make_leaver_last_super_admin(workspace)

    state = advance(workspace, stores, plan.workflow_id, confirmation)

    assert state.status == "manual_action_required"
    assert "last active super admin" in step(state, "suspend_user").message
    assert workspace.directory.users_by_key[LEAVER]["suspended"] is False
    assert workspace.directory.write_calls == []


def test_suspension_rechecks_super_admin_just_before_write(
    workspace, plan, stores, monkeypatch
):
    state = advance(workspace, stores, plan.workflow_id)
    original = workflow_module._confirmation

    def promote_target(*args, **kwargs):
        confirmed = original(*args, **kwargs)
        if confirmed:
            make_leaver_last_super_admin(workspace)
        return confirmed

    monkeypatch.setattr(workflow_module, "_confirmation", promote_target)
    state = advance(workspace, stores, plan.workflow_id, state.confirmation)

    assert state.status == "manual_action_required"
    assert "last active super admin" in step(state, "suspend_user").message
    assert workspace.directory.users_by_key[LEAVER]["suspended"] is False
    assert workspace.directory.write_calls == []


def test_demoted_admin_cannot_confirm_existing_proposal(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    workspace.directory.users_by_key[ADMIN]["isAdmin"] = False

    with pytest.raises(AdminBoundaryError, match="not a Workspace admin"):
        advance(workspace, stores, plan.workflow_id, state.confirmation)
    assert workspace.directory.write_calls == []


# --- Confirmations ---------------------------------------------------------------------


def test_replayed_confirmation_fails(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    used = state.confirmation
    advance(workspace, stores, plan.workflow_id, used)
    workspace.transfer_status = "completed"
    state = advance_until(workspace, stores, plan.workflow_id, "delete_user")
    assert state.status == "awaiting_confirmation"

    with pytest.raises(ConfirmationError):
        advance(workspace, stores, plan.workflow_id, used)

    assert LEAVER in workspace.directory.users_by_key


def test_changed_confirmation_fails_and_writes_nothing(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    proposal_id, token = state.confirmation.split(":", 1)

    with pytest.raises(ConfirmationError):
        advance(workspace, stores, plan.workflow_id, f"{proposal_id}:{token}x")

    assert workspace.directory.write_calls == []
    # The failed attempt used up that proposal; a new one is needed.
    with pytest.raises(ConfirmationError):
        advance(workspace, stores, plan.workflow_id, state.confirmation)
    assert workspace.directory.write_calls == []


def test_confirmation_for_another_workflow_is_refused_unused(workspace, stores):
    first = plan_user_offboarding(
        CONTEXT, workspace.clients, LEAVER, MANAGER, store=stores["store"]
    )
    workspace.directory.add_user("other@op.example", "U_OTHER2")
    second = plan_user_offboarding(
        CONTEXT, workspace.clients, "other@op.example", MANAGER, store=stores["store"]
    )
    first_state = advance(workspace, stores, first.workflow_id)
    advance(workspace, stores, second.workflow_id)

    with pytest.raises(ConfirmationError, match="current step"):
        advance(workspace, stores, second.workflow_id, first_state.confirmation)
    assert workspace.directory.write_calls == []

    # Refused without being consumed: it still confirms its own workflow.
    advance(workspace, stores, first.workflow_id, first_state.confirmation)
    assert workspace.directory.users_by_key[LEAVER]["suspended"] is True


@pytest.mark.parametrize("confirmation", ["", "no-separator", ":", "a:b:c"])
def test_malformed_confirmation_is_refused(workspace, plan, stores, confirmation):
    advance(workspace, stores, plan.workflow_id)

    with pytest.raises(ConfirmationError):
        advance(workspace, stores, plan.workflow_id, confirmation)
    assert workspace.directory.write_calls == []


def test_proposal_is_refreshed_when_the_step_changes(workspace, plan, stores):
    """A license confirmation approves one exact assignment."""
    advance_until(workspace, stores, plan.workflow_id, "wait_for_transfer")
    workspace.transfer_status = "completed"
    state = advance_until(workspace, stores, plan.workflow_id, "remove_licenses")
    state = advance(workspace, stores, plan.workflow_id)
    first = state.confirmation
    # Someone removes that license in the console before it is confirmed.
    workspace.licenses = [
        lic
        for lic in workspace.licenses
        if not (lic["userId"] == LEAVER and lic["productId"] == "Google-Apps")
    ]

    with pytest.raises(ConfirmationError, match="no longer matches"):
        advance(workspace, stores, plan.workflow_id, first)

    assert workspace.leaver_license_count() == 1


# --- Every write re-checks the admin and customer ---------------------------------------


def test_write_is_refused_when_the_target_left_the_customer(workspace, plan, stores):
    workspace.directory.users_by_key[LEAVER]["customerId"] = "C_OTHER"

    state = advance(workspace, stores, plan.workflow_id)

    assert state.status == "manual_action_required"
    assert "another customer" in step(state, "suspend_user").message
    assert state.confirmation is None
    assert workspace.directory.write_calls == []


def test_workflow_belongs_to_the_admin_who_planned_it(workspace, plan, stores):
    other = AdminContext(actor_email="other-admin@op.example", customer_id="C01")
    other_customer = AdminContext(actor_email=ADMIN, customer_id="C_OTHER")

    for context in (other, other_customer):
        with pytest.raises(OffboardingError, match="another admin"):
            advance(workspace, stores, plan.workflow_id, context=context)
        with pytest.raises(OffboardingError, match="another admin"):
            get_offboarding_status(context, plan.workflow_id, store=stores["store"])
    assert workspace.directory.write_calls == []


def test_unknown_workflow_is_refused(workspace, stores):
    with pytest.raises(OffboardingError, match="Unknown"):
        get_offboarding_status(CONTEXT, "A" * 24, store=stores["store"])
    with pytest.raises(OffboardingError, match="Unknown"):
        get_offboarding_status(CONTEXT, "../../etc/passwd", store=stores["store"])


def test_concurrent_advance_is_refused(workspace, plan, stores):
    with stores["store"].locked(plan.workflow_id):
        with pytest.raises(OffboardingError, match="another call"):
            advance(workspace, stores, plan.workflow_id)
    assert workspace.directory.write_calls == []


# --- Persistence ---------------------------------------------------------------------


def test_default_workflow_storage_refuses_stateless_mode(monkeypatch):
    monkeypatch.setattr(
        workflow_storage, "is_stateless_mode", lambda: True, raising=False
    )
    with pytest.raises(workflow_storage.WorkflowStoreError, match="stateless"):
        workflow_storage.default_store()


def test_workflow_state_is_private_and_holds_no_token(workspace, plan, stores):
    state = advance(workspace, stores, plan.workflow_id)
    token = state.confirmation.split(":", 1)[1]
    directory = stores["store"].directory

    assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
    files = list(directory.glob("*.json"))
    assert [f.stem for f in files] == [plan.workflow_id]
    assert stat.S_IMODE(os.stat(files[0]).st_mode) == 0o600
    assert token not in files[0].read_text()


def test_status_reports_persisted_state_without_a_confirmation(workspace, plan, stores):
    advance(workspace, stores, plan.workflow_id)

    status = get_offboarding_status(CONTEXT, plan.workflow_id, store=stores["store"])

    assert status.status == "awaiting_confirmation"
    assert status.confirmation is None
    assert step(status, "suspend_user").status == "awaiting_confirmation"
