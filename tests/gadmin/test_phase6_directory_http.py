"""Directory method paths, scopes and request bodies through the installed client."""

import pytest

import auth.scopes as scopes
from tests.gadmin.fake_http import directory_client
from tests.gadmin.test_admin_operation import (  # noqa: F401 - fixture
    confirm,
    propose,
    ws,
)

BASE = "/admin/directory/v1"


@pytest.fixture
def real_directory(ws, monkeypatch):  # noqa: F811 - imported fixture
    client, http = directory_client(
        {
            ("GET", f"{BASE}/customers/C01"): {"id": "C01"},
            ("PATCH", f"{BASE}/customers/C01"): lambda _, body: {
                "id": "C01",
                **body,
                "alternateEmail": "private@example.net",
            },
            ("GET", f"{BASE}/customer/C01/roles/ALL/privileges"): {
                "items": [{"serviceId": "S1", "privilegeName": "READ"}]
            },
            ("POST", f"{BASE}/customer/C01/roles"): {
                "roleId": "R3",
                "roleName": "Help",
                "rolePrivileges": [{"serviceId": "S1", "privilegeName": "READ"}],
            },
            ("POST", f"{BASE}/customer/C01/schemas"): {
                "schemaId": "S3",
                "schemaName": "badge",
            },
        }
    )
    apis = ws.apis
    monkeypatch.setattr(
        ws, "apis", lambda: {**apis(), ("admin", "directory_v1"): client}
    )
    return ws, http


@pytest.mark.asyncio
async def test_real_directory_client_sends_tenant_role_and_schema_writes(
    real_directory,
):
    ws, http = real_directory  # noqa: F811 - imported fixture
    role = {
        "roleName": "Help",
        "rolePrivileges": [{"serviceId": "S1", "privilegeName": "READ"}],
    }
    schema = {
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
    for operation_id, body, method, path in (
        (
            "directory.customers.patch",
            {"phoneNumber": "+12025550123"},
            "PATCH",
            f"{BASE}/customers/C01",
        ),
        ("directory.roles.insert", role, "POST", f"{BASE}/customer/C01/roles"),
        ("directory.schemas.insert", schema, "POST", f"{BASE}/customer/C01/schemas"),
    ):
        proposal = await propose(operation_id, body=body)
        result, text = await confirm(proposal)
        assert not result.is_error, text
        assert (method, path, {}, body) in http.requests
        assert ws.requested_scopes[-1]
    assert len([r for r in http.requests if r[0] in ("POST", "PATCH")]) == 3
    assert "private@example.net" not in ws.audit_path.read_text()
    assert (
        scopes.ADMIN_DIRECTORY_CUSTOMER_SCOPE
        not in scopes.TOOL_READONLY_SCOPES_MAP["admin-directory"]
    )
    assert (
        scopes.ADMIN_DIRECTORY_USERSCHEMA_SCOPE
        not in scopes.TOOL_READONLY_SCOPES_MAP["admin-directory"]
    )
