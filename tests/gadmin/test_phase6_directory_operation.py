"""Phase 6 Directory writes keep the customer boundary through the MCP tools."""

import json

import pytest

from tests.gadmin.test_admin_operation import confirm, operation, propose, ws  # noqa: F401


@pytest.mark.asyncio
async def test_tenant_phone_patch_rechecks_customer_and_replay(ws):  # noqa: F811 - imported fixture
    proposal = await propose(
        "directory.customers.patch", body={"phoneNumber": "+12025550123"}
    )
    assert proposal["risk"] == "destructive" and proposal["params"] == {
        "customerKey": "C01"
    }
    assert ws.writes() == []
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"] == {"id": "C01", "phoneNumber": "+12025550123"}
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(ws.writes()) == 1
    result, text = await operation(
        "directory.customers.patch",
        {"customerKey": "C_OTHER"},
        {"phoneNumber": "+12025550123"},
    )
    assert result.is_error and "set by the server" in text
    assert len(ws.writes()) == 1
    assert "+12025550123" not in ws.audit_path.read_text()


@pytest.mark.asyncio
async def test_tenant_patch_fails_when_customer_read_changes(ws):  # noqa: F811 - imported fixture
    proposal = await propose(
        "directory.customers.patch", body={"phoneNumber": "+12025550123"}
    )
    ws.directory.customer_record["id"] = "C_OTHER"
    result, text = await confirm(proposal)
    assert result.is_error and "customer" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_custom_role_privileges_are_fresh_and_custom_role_is_required(ws):  # noqa: F811 - imported fixture
    body = {
        "roleName": "Desk help",
        "rolePrivileges": [{"serviceId": "S1", "privilegeName": "READ"}],
    }
    proposal = await propose("directory.roles.insert", body=body)
    assert ws.writes() == [] and proposal["risk"] == "destructive"
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"]["roleName"] == "Desk help"
    assert len(ws.writes()) == 1
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    for privilege in ("OWNER", "ADMIN"):
        result, text = await operation(
            "directory.roles.insert",
            body={
                "roleName": "x",
                "rolePrivileges": [{"serviceId": "S1", "privilegeName": privilege}],
            },
        )
        assert result.is_error
    result, text = await operation("directory.roles.delete", {"roleId": "R_SUPER"})
    assert result.is_error and len(ws.directory.write_calls) == 1
    assert "rolePrivileges" in ws.audit()[0]["body_fields"]
    assert "READ" not in ws.audit_path.read_text()


@pytest.mark.asyncio
async def test_custom_role_delete_denies_assignments_and_rechecks(ws):  # noqa: F811 - imported fixture
    ws.directory.role_items.append(
        {
            "roleId": "R_CUSTOM",
            "roleName": "Desk",
            "isSystemRole": False,
            "isSuperAdminRole": False,
        }
    )
    ws.directory.assign("U_STAFF", "R_CUSTOM")
    result, text = await operation("directory.roles.delete", {"roleId": "R_CUSTOM"})
    assert result.is_error and ws.writes() == []
    ws.directory.assignments = [
        a for a in ws.directory.assignments if a["roleId"] != "R_CUSTOM"
    ]
    proposal = await propose("directory.roles.delete", {"roleId": "R_CUSTOM"})
    ws.directory.assign("U_STAFF", "R_CUSTOM")
    result, text = await confirm(proposal)
    assert result.is_error and ws.writes() == []


@pytest.mark.asyncio
async def test_custom_schema_restricts_fields_and_rechecks_on_delete(ws):  # noqa: F811 - imported fixture
    body = {
        "schemaName": "badge",
        "fields": [
            {
                "fieldName": "level",
                "displayName": "Level",
                "fieldType": "STRING",
                "readAccessType": "ADMINS_AND_SELF",
            }
        ],
    }
    proposal = await propose("directory.schemas.insert", body=body)
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "schemaId": "S_NEW",
        "schemaName": "badge",
        "displayName": "badge",
    }
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    result, text = await operation(
        "directory.schemas.insert",
        body={
            **body,
            "fields": [{**body["fields"][0], "numericIndexingSpec": {"minValue": 0}}],
        },
    )
    assert result.is_error
    result, text = await operation("directory.schemas.delete", {"schemaKey": "S_OTHER"})
    assert result.is_error and len(ws.writes()) == 1
    ws.directory.schema_items.append(
        {"schemaId": "S1", "schemaName": "badge", "fields": []}
    )
    proposal = await propose("directory.schemas.delete", {"schemaKey": "S1"})
    ws.directory.schema_items.clear()
    result, text = await confirm(proposal)
    assert result.is_error and len(ws.writes()) == 1
    assert "level" not in ws.audit_path.read_text()


@pytest.mark.asyncio
async def test_custom_role_and_schema_metadata_patch_then_safe_delete(ws):  # noqa: F811 - imported fixture
    ws.directory.role_items.append(
        {
            "roleId": "R_CUSTOM",
            "roleName": "Old",
            "isSystemRole": False,
            "isSuperAdminRole": False,
        }
    )
    ws.directory.schema_items.append(
        {"schemaId": "S_EMPTY", "schemaName": "badge", "fields": []}
    )
    for method, params, body in (
        ("directory.roles.patch", {"roleId": "R_CUSTOM"}, {"roleName": "New"}),
        ("directory.schemas.patch", {"schemaKey": "S_EMPTY"}, {"displayName": "Badge"}),
        ("directory.roles.delete", {"roleId": "R_CUSTOM"}, None),
        ("directory.schemas.delete", {"schemaKey": "S_EMPTY"}, None),
    ):
        proposal = await propose(method, params, body)
        assert proposal["risk"] == "destructive"
        result, text = await confirm(proposal)
        assert not result.is_error, text
        result, text = await confirm(proposal)
        assert result.is_error and "already used" in text
    assert len(ws.writes()) == 4
    assert "R_CUSTOM" not in [role["roleId"] for role in ws.directory.role_items]
    assert ws.directory.schema_items == []


@pytest.mark.asyncio
async def test_schema_with_fields_cannot_be_deleted(ws):  # noqa: F811 - imported fixture
    ws.directory.schema_items.append(
        {"schemaId": "S1", "schemaName": "badge", "fields": [{"fieldName": "level"}]}
    )
    result, text = await operation("directory.schemas.delete", {"schemaKey": "S1"})
    assert result.is_error and "empty custom schema" in text
    assert ws.writes() == []


@pytest.mark.asyncio
async def test_foreign_customer_and_untrusted_role_values_never_write(ws):  # noqa: F811 - imported fixture
    for name, params, body in (
        (
            "directory.roles.insert",
            {"customer": "C_OTHER"},
            {
                "roleName": "x",
                "rolePrivileges": [{"serviceId": "S1", "privilegeName": "READ"}],
            },
        ),
        (
            "directory.schemas.insert",
            {"customerId": "C_OTHER"},
            {"schemaName": "badge", "fields": []},
        ),
        ("directory.roles.patch", {"roleId": "R_SUPER"}, {"roleName": "Root"}),
        (
            "directory.roles.insert",
            {},
            {
                "roleName": "x",
                "rolePrivileges": [
                    {"serviceId": "S1", "privilegeName": "READ"},
                    {"serviceId": "S1", "privilegeName": "READ"},
                ],
            },
        ),
    ):
        result, text = await operation(name, params, body)
        assert result.is_error, text
    assert ws.writes() == [] and ws.audit() == []


@pytest.mark.asyncio
async def test_role_privilege_changed_before_confirmation_refuses_write(ws):  # noqa: F811 - imported fixture
    proposal = await propose(
        "directory.roles.insert",
        body={
            "roleName": "Desk help",
            "rolePrivileges": [{"serviceId": "S1", "privilegeName": "READ"}],
        },
    )
    ws.directory.privilege_items.clear()
    result, text = await confirm(proposal)
    assert result.is_error and "unverified" in text
    assert ws.writes() == []
