"""The general admin_operation tools cross the real MCP boundary with only Google
clients faked: reads run inside the verified customer, and every write is first
proposed, then executed only by a matching one-use confirmation after a fresh
recheck of the admin, permission, and target."""

import json

import pytest
from fastmcp import Client

import auth.permissions as permissions
import auth.scopes as scopes
import auth.service_decorator as decorator
import gadmin.admin_tools as tools
import gadmin.auth as auth
from core.server import server
from gadmin.confirm import ConfirmationStore, propose_operation
from gadmin.guard import AdminContext
from gadmin.registry import get_operation
from tests.gadmin.fake_directory import FakeDirectory, http_error
from tests.gadmin.fake_google import FakeGoogleApi

ADMIN = "admin@op.example"
OTHER_ADMIN = "admin2@op.example"
ALL_ADMIN = [
    "admin-directory",
    "admin-datatransfer",
    "admin-licensing",
    "admin-reports",
    "admin-vault",
    "admin-alertcenter",
    "admin-groupssettings",
]
TRANSFER = {
    "id": "T1",
    "oldOwnerUserId": "U_STAFF",
    "newOwnerUserId": "U_NEW",
    "overallTransferStatusCode": "new",
    "requestTime": "2026-09-25T00:00:00Z",
}
LICENSE = {"productId": "Google-Apps", "skuId": "1010020027", "userId": "x@op.example"}
ACTIVITY = {"id": {"time": "2026-09-25T00:00:00Z"}, "actor": {"profileId": "U_STAFF"}}
# Export download locations must never leave the server.
EXPORT = {
    "id": "E1",
    "matterId": "M1",
    "name": "Staff mail",
    "status": "COMPLETED",
    "cloudStorageSink": {"files": [{"bucketName": "sink-bucket", "md5Hash": "h"}]},
}
ALERT = {"alertId": "A1", "customerId": "01", "type": "Suspicious login"}
GROUP_SETTINGS = {"email": "team@op.example", "whoCanJoin": "INVITED_CAN_JOIN"}


class Workspace:
    def __init__(self, tmp_path):
        self.directory = FakeDirectory(customer_id="C01")
        self.directory.add_user(ADMIN, "U_ADMIN", super_admin=True)
        self.directory.add_user(OTHER_ADMIN, "U_ADMIN2", super_admin=True)
        self.directory.add_user("staff@op.example", "U_STAFF")
        self.directory.add_user("new@op.example", "U_NEW")
        self.directory.add_user("u@other.example", "U_OTHER", customer_id="C_OTHER")
        self.directory.add_group("G1", "team@op.example", ["U_STAFF"])
        self.directory.add_group("G_OTHER", "team@other.example", ["U_OTHER"])
        self.transfer_api = FakeGoogleApi(
            "datatransfer",
            {
                "datatransfer.transfers.list": {
                    "dataTransfers": [{**TRANSFER, "secretField": "hidden"}]
                },
                "datatransfer.transfers.insert": lambda body: {**TRANSFER, **body},
            },
        )
        self.licensing_api = FakeGoogleApi(
            "licensing",
            {
                "licensing.licenseAssignments.listForProductAndSku": {
                    "items": [LICENSE],
                    "etag": "e",
                },
                "licensing.licenseAssignments.insert": lambda body, **_: {
                    **LICENSE,
                    **body,
                },
            },
        )
        self.reports_api = FakeGoogleApi(
            "reports",
            {"reports.activities.list": {"items": [{**ACTIVITY, "etag": "e"}]}},
        )
        self.vault_api = FakeGoogleApi(
            "vault",
            {
                "vault.matters.exports.get": EXPORT,
                "vault.matters.exports.create": lambda body, **_: EXPORT,
                "vault.matters.holds.addHeldAccounts": {"responses": []},
            },
        )
        self.alert_api = FakeGoogleApi(
            "alertcenter",
            {
                "alertcenter.alerts.list": {
                    "alerts": [{**ALERT, "data": {"email": "leaked@op.example"}}]
                },
                "alertcenter.getSettings": {"notifications": []},
                "alertcenter.alerts.batchDelete": {"successAlertIds": ["A1"]},
            },
            root="v1beta1",
        )
        self.groups_settings_api = FakeGoogleApi(
            "groupsSettings",
            {
                "groupsSettings.groups.get": GROUP_SETTINGS,
                "groupsSettings.groups.patch": lambda body, **_: {
                    **GROUP_SETTINGS,
                    **body,
                },
            },
        )
        self.clock = [1000.0]
        self.confirmations = ConfirmationStore(
            tmp_path / "confirmations", clock=lambda: self.clock[0]
        )
        self.audit_path = tmp_path / "audit.jsonl"
        self.requested_scopes: list[list[str]] = []

    def apis(self):
        return {
            ("admin", "directory_v1"): self.directory,
            ("admin", "datatransfer_v1"): self.transfer_api,
            ("licensing", "v1"): self.licensing_api,
            ("admin", "reports_v1"): self.reports_api,
            ("vault", "v1"): self.vault_api,
            ("alertcenter", "v1beta1"): self.alert_api,
            ("groupssettings", "v1"): self.groups_settings_api,
        }

    def writes(self):
        fakes = [api for api in self.apis().values() if api is not self.directory]
        return [
            *self.directory.write_calls,
            *(
                c
                for api in fakes
                for c in api.calls
                if get_operation(c[0]).risk != "read"
            ),
        ]

    def audit(self):
        if not self.audit_path.exists():
            return []
        return [json.loads(line) for line in self.audit_path.read_text().splitlines()]


@pytest.fixture
def ws(monkeypatch, tmp_path):
    workspace = Workspace(tmp_path)
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", list(ALL_ADMIN))
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)

    async def decorator_service(*args, **kwargs):
        return workspace.directory, args[4]

    async def admin_service(
        use_oauth21,
        service,
        version,
        tool,
        selected,
        requested,
        session,
        identity,
        **kw,
    ):
        assert kw.get("verify_account") is True
        workspace.requested_scopes.append(list(requested))
        return workspace.apis()[(service, version)], selected

    monkeypatch.setattr(decorator, "_authenticate_service", decorator_service)
    monkeypatch.setattr(auth, "_authenticate_service", admin_service)
    monkeypatch.setattr(tools, "default_confirmations", lambda: workspace.confirmations)
    monkeypatch.setattr(
        tools, "_audit_sink", lambda: tools.AuditSink(workspace.audit_path)
    )
    return workspace


async def call(tool, **arguments):
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return result, result.content[0].text


async def operation(operation_id, params=None, body=None, admin=ADMIN):
    arguments = {"user_google_email": admin, "operation_id": operation_id}
    if params is not None:
        arguments["params"] = params
    if body is not None:
        arguments["body"] = body
    return await call("admin_operation", **arguments)


async def propose(operation_id, params=None, body=None):
    result, text = await operation(operation_id, params, body)
    assert not result.is_error, text
    proposal = json.loads(text)
    assert proposal["confirmation_token"]
    return proposal


async def confirm(proposal, admin=ADMIN, token=None):
    return await call(
        "confirm_admin_operation",
        user_google_email=admin,
        proposal_id=proposal["proposal_id"],
        confirmation_token=token or proposal["confirmation_token"],
    )


# --- reads ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_directory_read_returns_bounded_result_inside_the_customer(ws):
    result, text = await operation(
        "directory.groups.get", {"groupKey": "team@op.example"}
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {"id": "G1", "email": "team@op.example"}
    # The group and domain guards need their read scopes on the same client.
    assert {
        scopes.ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
        scopes.ADMIN_DIRECTORY_DOMAIN_READONLY_SCOPE,
    } <= set(ws.requested_scopes[-1])
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_read_of_another_customers_group_is_refused(ws):
    result, text = await operation(
        "directory.groups.get", {"groupKey": "team@other.example"}
    )

    assert result.is_error and "customer's domains" in text
    assert not any(
        c[0] == "directory.groups.get" and c[1]["groupKey"] == "G_OTHER"
        for c in ws.directory.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        ("directory.users.list", {"customer": "C_OTHER"}),
        ("directory.users.list", {"domain": "other.example"}),
        (
            "licensing.licenseAssignments.listForProductAndSku",
            {"productId": "Google-Apps", "skuId": "1", "customerId": "other.example"},
        ),
    ],
)
async def test_caller_customer_parameters_are_refused(ws, operation_id, params):
    result, text = await operation(operation_id, params)

    assert result.is_error and "set by the server or not accepted" in text
    assert ws.licensing_api.calls == []


@pytest.mark.asyncio
async def test_transfer_read_is_bound_to_the_customer_and_verified_users(ws):
    result, text = await operation(
        "datatransfer.transfers.list", {"oldOwnerUserId": "staff@op.example"}
    )

    assert not result.is_error, text
    assert ws.transfer_api.calls == [
        (
            "datatransfer.transfers.list",
            {"customerId": "C01", "oldOwnerUserId": "U_STAFF"},
        )
    ]
    assert json.loads(text)["result"] == {"dataTransfers": [TRANSFER]}


@pytest.mark.asyncio
async def test_transfer_read_about_another_customers_user_is_refused(ws):
    result, text = await operation(
        "datatransfer.transfers.list", {"oldOwnerUserId": "u@other.example"}
    )

    assert result.is_error and "another customer" in text
    assert ws.transfer_api.calls == []


@pytest.mark.asyncio
async def test_transfer_from_another_customers_account_is_not_returned(ws):
    ws.transfer_api.handlers["datatransfer.transfers.get"] = {
        **TRANSFER,
        "oldOwnerUserId": "U_OTHER",
    }

    result, text = await operation(
        "datatransfer.transfers.get", {"dataTransferId": "T1"}
    )

    assert result.is_error
    assert "U_OTHER" not in text and "C_OTHER" not in text
    assert ws.transfer_api.calls == [
        ("datatransfer.transfers.get", {"dataTransferId": "T1"})
    ]


@pytest.mark.asyncio
async def test_licensing_read_is_bound_to_the_customer(ws):
    result, text = await operation(
        "licensing.licenseAssignments.listForProductAndSku",
        {"productId": "Google-Apps", "skuId": "1010020027"},
    )

    assert not result.is_error, text
    assert ws.licensing_api.calls[0][1]["customerId"] == "C01"
    assert json.loads(text)["result"] == {"items": [LICENSE]}


@pytest.mark.asyncio
async def test_excluded_and_unknown_operations_are_refused_with_the_reason(ws):
    result, text = await operation(
        "directory.users.insert", body={"primaryEmail": "x@op.example"}
    )
    assert result.is_error and "secret-in-payload" in text

    result, text = await operation("directory.users.anything")
    assert result.is_error and "directory.users.anything" in text
    assert ws.directory.calls == []


@pytest.mark.asyncio
async def test_invalid_input_is_refused_before_google_is_called(ws):
    result, text = await operation(
        "directory.groups.get", {"groupKey": "team@op.example", "fields": "*"}
    )

    assert result.is_error
    assert not any(c[0] == "directory.groups.get" for c in ws.directory.calls)


# --- proposals and confirmation ---------------------------------------------------


@pytest.mark.asyncio
async def test_directory_write_is_proposed_then_confirmed_once(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example", "role": "MEMBER"},
    )

    assert proposal["operation"] == "directory.members.insert"
    assert proposal["risk"] == "manage"
    assert proposal["target"] == "G1"
    assert proposal["params"] == {"groupKey": "G1"}
    assert ws.writes() == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert ws.directory.write_calls == [
        (
            "directory.members.insert",
            {"groupKey": "G1", "body": {"email": "new@op.example", "role": "MEMBER"}},
        )
    ]
    assert json.loads(text)["result"]["email"] == "new@op.example"

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(ws.writes()) == 1


@pytest.mark.asyncio
async def test_audit_records_proposal_and_outcome_without_payload_values(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example", "role": "MEMBER"},
    )
    await confirm(proposal)

    records = ws.audit()
    assert [r["outcome"] for r in records] == ["proposed", "succeeded"]
    assert {r["proposal_id"] for r in records} == {proposal["proposal_id"]}
    assert records[1]["body_fields"] == ["email", "role"]
    text = ws.audit_path.read_text()
    assert "new@op.example" not in text
    assert proposal["confirmation_token"] not in text


@pytest.mark.asyncio
async def test_transfer_write_is_proposed_with_verified_ids_then_confirmed(ws):
    proposal = await propose(
        "datatransfer.transfers.insert",
        body={
            "oldOwnerUserId": "staff@op.example",
            "newOwnerUserId": "new@op.example",
            "applicationDataTransfers": [{"applicationId": "55"}],
        },
    )
    assert proposal["body"]["oldOwnerUserId"] == "U_STAFF"
    assert ws.writes() == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert ws.writes() == [
        (
            "datatransfer.transfers.insert",
            {
                "body": {
                    "oldOwnerUserId": "U_STAFF",
                    "newOwnerUserId": "U_NEW",
                    "applicationDataTransfers": [{"applicationId": "55"}],
                }
            },
        )
    ]


@pytest.mark.asyncio
async def test_licensing_write_is_proposed_then_confirmed(ws):
    proposal = await propose(
        "licensing.licenseAssignments.insert",
        {"productId": "Google-Apps", "skuId": "1010020027"},
        {"userId": "STAFF@op.example"},
    )
    assert proposal["body"] == {"userId": "staff@op.example"}

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert ws.writes()[0][1]["body"] == {"userId": "staff@op.example"}


@pytest.mark.asyncio
async def test_licensing_write_for_another_customers_user_is_refused(ws):
    result, text = await operation(
        "licensing.licenseAssignments.insert",
        {"productId": "Google-Apps", "skuId": "1010020027"},
        {"userId": "u@other.example"},
    )

    assert result.is_error and "another customer" in text
    assert ws.audit() == [] and ws.writes() == []


@pytest.mark.asyncio
async def test_tampered_token_and_unknown_proposal_are_refused(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )

    result, text = await confirm(proposal, token="x" * 43)
    assert result.is_error and "does not match" in text
    # A failed attempt consumes the proposal, so the right token no longer works.
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text

    result, text = await confirm({**proposal, "proposal_id": "A" * 24})
    assert result.is_error and "already used" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_another_admin_cannot_confirm(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )

    result, text = await confirm(proposal, admin=OTHER_ADMIN)

    assert result.is_error and "another admin" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_a_recreated_admin_account_with_the_same_email_cannot_confirm(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )
    ws.directory.users_by_key.pop("U_ADMIN")
    ws.directory.add_user(ADMIN, "U_ADMIN_NEW", super_admin=True)

    result, text = await confirm(proposal)

    assert result.is_error and "another admin" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_expired_confirmation_is_refused(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )
    ws.clock[0] += 301

    result, text = await confirm(proposal)

    assert result.is_error and "expired" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_an_offboarding_confirmation_cannot_be_used_here(ws):
    context = AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN")
    proposal, token = propose_operation(
        context,
        "directory.users.update",
        {"userKey": "U_STAFF"},
        {"suspended": True},
        store=ws.confirmations,
    )

    result, text = await confirm(
        {"proposal_id": proposal.id, "confirmation_token": token}
    )

    assert result.is_error and "another tool" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_target_that_changed_after_proposal_is_refused(ws):
    proposal = await propose(
        "licensing.licenseAssignments.insert",
        {"productId": "Google-Apps", "skuId": "1010020027"},
        {"userId": "staff@op.example"},
    )
    ws.directory.users_by_key["U_STAFF"]["primaryEmail"] = "renamed@op.example"

    result, text = await confirm(proposal)

    assert result.is_error and "changed after proposal" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_target_moved_to_another_customer_after_proposal_is_refused(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )
    ws.directory.users_by_key["U_NEW"]["customerId"] = "C_OTHER"

    result, text = await confirm(proposal)

    assert result.is_error and "another customer" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_destructive_write_on_the_acting_admin_is_refused(ws):
    result, text = await operation("directory.users.delete", {"userKey": ADMIN})

    assert result.is_error
    assert ws.audit() == [] and ws.writes() == []


@pytest.mark.asyncio
async def test_google_error_on_confirm_is_categorised_and_audited(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )
    ws.directory.failures["directory.members.insert"] = http_error(
        403, "Not Authorized to access this resource/api"
    )

    result, text = await confirm(proposal)

    assert result.is_error and "missing_admin_privilege" in text
    assert "secret-uri" not in text
    last = ws.audit()[-1]
    assert (last["outcome"], last["status"], last["category"]) == (
        "failed",
        403,
        "missing_admin_privilege",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [408, 429, 503])
async def test_uncertain_google_error_on_confirm_requires_state_check(ws, status):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )
    ws.directory.failures["directory.members.insert"] = http_error(status, "backend")

    result, text = await confirm(proposal)

    assert result.is_error
    assert "check Google before proposing again" in text
    assert "secret-uri" not in text
    last = ws.audit()[-1]
    assert (last["outcome"], last["status"], last["category"]) == (
        "unknown",
        status,
        "rate_limited" if status == 429 else "api_error",
    )
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(ws.writes()) == 1


@pytest.mark.asyncio
async def test_unexpected_error_on_confirm_is_reported_as_unknown(ws):
    proposal = await propose(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )
    ws.directory.failures["directory.members.insert"] = RuntimeError("private detail")

    result, text = await confirm(proposal)

    assert result.is_error and "private detail" not in text
    assert "unknown" in text
    assert ws.audit()[-1]["outcome"] == "unknown"


# --- permission modes -------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_only_mode_refuses_to_propose_writes(ws, monkeypatch):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    result, text = await operation(
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "new@op.example"},
    )

    assert result.is_error and "allows up to read" in text
    assert not ws.confirmations.directory.exists()
    result, text = await operation(
        "directory.groups.get", {"groupKey": "team@op.example"}
    )
    assert not result.is_error, text


@pytest.mark.asyncio
async def test_manage_level_refuses_destructive_operations(ws, monkeypatch):
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-directory": "manage"})

    result, text = await operation(
        "directory.users.delete", {"userKey": "staff@op.example"}
    )

    assert result.is_error and "destructive" in text


@pytest.mark.asyncio
async def test_permission_lowered_after_proposal_blocks_confirmation(ws, monkeypatch):
    proposal = await propose("directory.users.delete", {"userKey": "staff@op.example"})
    assert proposal["risk"] == "destructive"
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-directory": "manage"})

    result, text = await confirm(proposal)

    assert result.is_error and "destructive" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_operation_of_an_unselected_service_is_refused(ws, monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory"])

    result, text = await operation(
        "licensing.licenseAssignments.listForProductAndSku",
        {"productId": "Google-Apps", "skuId": "1010020027"},
    )

    assert result.is_error and "admin-licensing" in text
    assert ws.licensing_api.calls == []


# --- Reports, Vault, Alert Center, and Groups Settings ----------------------------


@pytest.mark.asyncio
async def test_reports_read_is_bound_to_the_customer_and_a_verified_user(ws):
    result, text = await operation(
        "reports.activities.list",
        {"userKey": "staff@op.example", "applicationName": "login"},
    )

    assert not result.is_error, text
    assert ws.reports_api.calls == [
        (
            "reports.activities.list",
            {"userKey": "U_STAFF", "applicationName": "login", "customerId": "C01"},
        )
    ]
    assert json.loads(text)["result"] == {"items": [ACTIVITY]}
    assert scopes.REPORTS_AUDIT_READONLY_SCOPE in ws.requested_scopes[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        (
            "reports.activities.list",
            {"userKey": "u@other.example", "applicationName": "login"},
        ),
        (
            "reports.activities.list",
            {"userKey": "all", "applicationName": "login", "customerId": "C_OTHER"},
        ),
        ("vault.matters.get", {"matterId": "M1", "customerId": "C_OTHER"}),
        ("alertcenter.alerts.list", {"customerId": "C_OTHER"}),
        ("groupsSettings.groups.get", {"groupUniqueId": "team@other.example"}),
    ],
)
async def test_phase2_reads_outside_the_customer_are_refused(ws, operation_id, params):
    result, text = await operation(operation_id, params)

    assert result.is_error
    assert "U_OTHER" not in text and "G_OTHER" not in text
    for api in (ws.reports_api, ws.vault_api, ws.alert_api, ws.groups_settings_api):
        assert api.calls == []


@pytest.mark.asyncio
async def test_vault_hold_is_proposed_with_verified_accounts_then_confirmed_once(ws):
    proposal = await propose(
        "vault.matters.holds.addHeldAccounts",
        {"matterId": "M1", "holdId": "H1"},
        {"emails": ["STAFF@op.example"], "accountIds": ["new@op.example"]},
    )

    assert (proposal["risk"], proposal["target"]) == ("manage", "M1")
    assert proposal["body"] == {
        "emails": ["staff@op.example"],
        "accountIds": ["U_NEW"],
    }
    assert ws.writes() == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert ws.writes() == [
        (
            "vault.matters.holds.addHeldAccounts",
            {"matterId": "M1", "holdId": "H1", "body": proposal["body"]},
        )
    ]
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(ws.writes()) == 1


@pytest.mark.asyncio
async def test_vault_custodian_in_another_customer_is_refused(ws):
    result, text = await operation(
        "vault.matters.holds.addHeldAccounts",
        {"matterId": "M1", "holdId": "H1"},
        {"emails": ["staff@op.example", "u@other.example"]},
    )

    assert result.is_error and "another customer" in text
    assert ws.vault_api.calls == [] and not ws.confirmations.directory.exists()


@pytest.mark.asyncio
async def test_vault_search_scope_that_cannot_be_bound_is_refused(ws):
    result, text = await operation(
        "vault.matters.count",
        {"matterId": "M1"},
        {"query": {"corpus": "MAIL", "method": "ORG_UNIT"}},
    )

    assert result.is_error
    assert ws.vault_api.calls == []


@pytest.mark.asyncio
async def test_vault_export_needs_destructive_and_never_returns_its_download_location(
    ws, monkeypatch
):
    body = {
        "name": "Staff mail",
        "query": {
            "corpus": "MAIL",
            "method": "ACCOUNT",
            "dataScope": "ALL_DATA",
            "accountInfo": {"emails": ["staff@op.example"]},
            "terms": "subject:contract",
        },
        "exportOptions": {"mailOptions": {"exportFormat": "MBOX"}},
    }
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-vault": "manage"})
    result, text = await operation(
        "vault.matters.exports.create", {"matterId": "M1"}, body
    )
    assert result.is_error and "destructive" in text

    monkeypatch.setattr(permissions, "_PERMISSIONS", None)
    proposal = await propose("vault.matters.exports.create", {"matterId": "M1"}, body)
    assert proposal["risk"] == "destructive"
    result, text = await confirm(proposal)
    assert not result.is_error, text
    result, read = await operation(
        "vault.matters.exports.get", {"matterId": "M1", "exportId": "E1"}
    )
    assert not result.is_error, read

    for output in (text, read):
        assert json.loads(output)["result"]["status"] == "COMPLETED"
        assert "cloudStorageSink" not in output and "sink-bucket" not in output
    audit = ws.audit_path.read_text()
    for value in ("sink-bucket", "staff@op.example", "subject:contract"):
        assert value not in audit
    assert ws.audit()[-1]["body_fields"] == ["exportOptions", "name", "query"]

    ws.vault_api.handlers["vault.operations.get"] = {
        "name": "operations/E1",
        "done": True,
        "metadata": {"exportId": "E1"},
        "response": EXPORT,
    }
    result, text = await operation("vault.operations.get", {"name": "operations/E1"})
    assert not result.is_error, text
    assert json.loads(text)["result"] == {"name": "operations/E1", "done": True}


@pytest.mark.asyncio
async def test_alert_list_omits_alert_data_and_refuses_another_customers_alert(ws):
    result, text = await operation("alertcenter.alerts.list")

    assert not result.is_error, text
    assert json.loads(text)["result"] == {"alerts": [ALERT]}
    assert "leaked@op.example" not in text
    assert ws.alert_api.calls == [("alertcenter.alerts.list", {})]

    ws.alert_api.handlers["alertcenter.alerts.list"] = {
        "alerts": [ALERT, {**ALERT, "alertId": "A2", "customerId": "C_OTHER"}]
    }
    result, text = await operation("alertcenter.alerts.list")
    assert result.is_error and "another customer" in text
    assert "A2" not in text


@pytest.mark.asyncio
async def test_alert_center_reads_need_manage_because_it_has_no_read_scope(
    ws, monkeypatch
):
    result, text = await operation("alertcenter.getSettings")
    assert not result.is_error, text
    assert ws.alert_api.calls == [("alertcenter.getSettings", {})]

    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-alertcenter": "readonly"})
    result, text = await operation("alertcenter.getSettings")
    assert result.is_error and "not grant" in text
    assert len(ws.alert_api.calls) == 1


@pytest.mark.asyncio
async def test_alert_batch_delete_is_proposed_then_confirmed(ws):
    proposal = await propose(
        "alertcenter.alerts.batchDelete", body={"alertId": ["A1", "A2"]}
    )
    assert proposal["risk"] == "destructive"

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert ws.writes() == [
        ("alertcenter.alerts.batchDelete", {"body": {"alertId": ["A1", "A2"]}})
    ]


@pytest.mark.asyncio
async def test_group_settings_are_read_and_patched_by_verified_group_email(
    ws, monkeypatch
):
    result, text = await operation("groupsSettings.groups.get", {"groupUniqueId": "G1"})
    assert not result.is_error, text
    assert ws.groups_settings_api.calls == [
        ("groupsSettings.groups.get", {"groupUniqueId": "team@op.example"})
    ]
    assert scopes.GROUPS_SETTINGS_SCOPE in ws.requested_scopes[-1]

    proposal = await propose(
        "groupsSettings.groups.patch",
        {"groupUniqueId": "team@op.example"},
        {"whoCanPostMessage": "ALL_MEMBERS_CAN_POST"},
    )
    assert (proposal["risk"], proposal["target"]) == ("manage", "team@op.example")
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"]["whoCanPostMessage"] == "ALL_MEMBERS_CAN_POST"

    # Opening the group to anyone needs the destructive level.
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-groupssettings": "manage"})
    result, text = await operation(
        "groupsSettings.groups.patch",
        {"groupUniqueId": "team@op.example"},
        {"whoCanJoin": "ANYONE_CAN_JOIN"},
    )
    assert result.is_error and "destructive" in text
    assert len(ws.writes()) == 1


@pytest.mark.asyncio
async def test_google_error_on_a_vault_read_is_categorised_without_details(ws):
    ws.vault_api.handlers["vault.matters.get"] = http_error(
        403, "Matter M1 belongs to someone", code="insufficientPermissions"
    )

    result, text = await operation("vault.matters.get", {"matterId": "M1"})

    assert result.is_error and "missing_scope" in text
    assert "secret-uri" not in text and "belongs to someone" not in text
