"""Access Context Manager reads cross the real MCP boundary through
admin_operation.

Only the transport is faked: the client is the installed Access Context Manager
client, and the organization lookup runs on its own transport through the
installed Cloud Resource Manager client, so each test shows the requests that
would reach Google. Every call is bound to the one active Google Cloud
organization linked to the admin's verified customer; access levels are read
only from that organization's own, unscoped policy; and no write is callable."""

import json

import pytest
from googleapiclient.discovery import build

import auth.scopes as scopes
from gadmin.boundary import resolve_call
from gadmin.guard import AdminBoundaryError, AdminContext
from tests.gadmin.fake_http import RoutingHttp
from tests.gadmin.test_admin_operation import (  # noqa: F401 - ws is a fixture
    ALL_ADMIN,
    operation,
    ws,
)

CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
SEARCH = ("GET", "/v3/organizations:search")
ORGANIZATIONS = {
    "organizations": [
        {
            "name": "organizations/123",
            "displayName": "op.example",
            "directoryCustomerId": "C01",
            "state": "ACTIVE",
        },
        {
            "name": "organizations/999",
            "displayName": "other.example",
            "directoryCustomerId": "C_OTHER",
            "state": "ACTIVE",
        },
    ]
}
ORG_POLICY = {
    "name": "accessPolicies/111",
    "parent": "organizations/123",
    "title": "default policy",
    "etag": "e1",
}
SCOPED_POLICY = {
    "name": "accessPolicies/222",
    "parent": "organizations/123",
    "title": "Cloud team",
    "scopes": ["projects/42"],
}
OTHER_POLICY = {
    "name": "accessPolicies/333",
    "parent": "organizations/999",
    "title": "Other org policy",
}
LEVEL = {
    "name": "accessPolicies/111/accessLevels/corp",
    "title": "Corp devices",
    "description": "Managed devices on the office network",
    "basic": {
        "combiningFunction": "AND",
        "conditions": [
            {
                "ipSubnetworks": ["203.0.113.0/24"],
                "regions": ["US"],
                "negate": False,
                "requiredAccessLevels": ["accessPolicies/111/accessLevels/base"],
                "devicePolicy": {
                    "requireScreenlock": True,
                    "requireCorpOwned": False,
                    "allowedEncryptionStatuses": ["ENCRYPTED"],
                    "osConstraints": [
                        {"osType": "DESKTOP_CHROME_OS", "minimumVersion": "120.0"}
                    ],
                    "undocumented": "device secret",
                },
            }
        ],
    },
    "undocumented": {"token": "not-a-level-field"},
}
CEL_LEVEL = {
    "name": "accessPolicies/111/accessLevels/cel",
    "title": "CEL level",
    "custom": {
        "expr": {
            "expression": "device.is_corp_owned_device",
            "title": "Corp owned",
            "location": "/home/admin/levels.cel",
        }
    },
}
LEVEL_PATH = "/v1/accessPolicies/111/accessLevels"


@pytest.fixture
def acm(ws, monkeypatch):  # noqa: F811 - the imported fixture
    http = RoutingHttp(
        {
            SEARCH: ORGANIZATIONS,
            ("GET", "/v1/accessPolicies"): {"accessPolicies": [ORG_POLICY]},
            ("GET", "/v1/accessPolicies/111"): ORG_POLICY,
            ("GET", "/v1/accessPolicies/222"): SCOPED_POLICY,
            ("GET", "/v1/accessPolicies/333"): OTHER_POLICY,
            ("GET", LEVEL_PATH): {"accessLevels": [LEVEL, CEL_LEVEL]},
            ("GET", LEVEL_PATH + "/corp"): LEVEL,
        }
    )
    installed = {
        ("accesscontextmanager", "v1"): build("accesscontextmanager", "v1", http=http)
    }
    apis = ws.apis
    monkeypatch.setattr(ws, "apis", lambda: {**apis(), **installed})
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, "admin-access-context"])
    ws.http = http
    return ws


def _acm_requests(acm) -> list:
    return [r for r in acm.http.requests if r[1].startswith("/v1/")]


# --- organization binding -----------------------------------------------------------


@pytest.mark.asyncio
async def test_policies_are_listed_for_the_customer_organization_only(acm):
    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert not result.is_error, text
    assert acm.http.requests == [
        ("GET", "/v3/organizations:search", {"pageSize": "100"}, None),
        ("GET", "/v1/accessPolicies", {"parent": "organizations/123"}, None),
    ]
    assert json.loads(text)["result"] == {
        "accessPolicies": [
            {
                "name": "accessPolicies/111",
                "parent": "organizations/123",
                "title": "default policy",
            }
        ]
    }
    assert acm.requested_scopes[-1] == [CLOUD_PLATFORM]


@pytest.mark.asyncio
async def test_caller_cannot_name_the_organization(acm):
    for parent in ("organizations/123", "organizations/999"):
        result, text = await operation(
            "accesscontextmanager.accessPolicies.list", {"parent": parent}
        )

        assert result.is_error and "set by the server" in text
    assert _acm_requests(acm) == []


@pytest.mark.asyncio
async def test_organization_of_another_customer_is_never_bound(acm):
    acm.http.handlers[SEARCH] = {"organizations": [ORGANIZATIONS["organizations"][1]]}

    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert result.is_error and "No active Google Cloud organization" in text
    assert _acm_requests(acm) == []
    assert "999" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "organizations",
    [
        # Two organizations claim the customer: ambiguous.
        [
            ORGANIZATIONS["organizations"][0],
            {**ORGANIZATIONS["organizations"][0], "name": "organizations/124"},
        ],
        # Pending deletion.
        [{**ORGANIZATIONS["organizations"][0], "state": "DELETE_REQUESTED"}],
        # Not an organization name.
        [{**ORGANIZATIONS["organizations"][0], "name": "folders/123"}],
    ],
    ids=["ambiguous", "inactive", "malformed"],
)
async def test_organization_lookup_fails_closed(acm, organizations):
    acm.http.handlers[SEARCH] = {"organizations": organizations}

    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert result.is_error and "Google Cloud organization" in text
    assert _acm_requests(acm) == []


@pytest.mark.asyncio
async def test_organization_lookup_error_or_endless_paging_fails_closed(acm):
    acm.http.handlers[SEARCH] = 403

    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert result.is_error and "Could not verify the Google Cloud organization" in text

    acm.http.handlers[SEARCH] = {"organizations": [], "nextPageToken": "more"}
    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert result.is_error and "Google Cloud organization" in text
    assert _acm_requests(acm) == []


@pytest.mark.asyncio
async def test_listed_policy_of_another_organization_is_not_returned(acm):
    acm.http.handlers[("GET", "/v1/accessPolicies")] = {
        "accessPolicies": [ORG_POLICY, OTHER_POLICY]
    }

    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert result.is_error and "another organization" in text
    assert "Other org policy" not in text


@pytest.mark.asyncio
async def test_policy_get_is_limited_to_the_unscoped_organization_policy(acm):
    result, text = await operation(
        "accesscontextmanager.accessPolicies.get", {"name": "accessPolicies/111"}
    )
    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "name": "accessPolicies/111",
        "parent": "organizations/123",
        "title": "default policy",
    }

    for name, refusal in (
        ("accessPolicies/333", "another organization"),
        ("accessPolicies/222", "scoped to Google Cloud"),
    ):
        result, text = await operation(
            "accesscontextmanager.accessPolicies.get", {"name": name}
        )

        assert result.is_error and refusal in text, name
        assert "Other org policy" not in text and "Cloud team" not in text


# --- access levels ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_access_levels_are_read_after_a_fresh_policy_check(acm):
    result, text = await operation(
        "accesscontextmanager.accessPolicies.accessLevels.list",
        {"parent": "accessPolicies/111", "accessLevelFormat": "AS_DEFINED"},
    )

    assert not result.is_error, text
    assert _acm_requests(acm) == [
        ("GET", "/v1/accessPolicies/111", {}, None),
        ("GET", LEVEL_PATH, {"accessLevelFormat": "AS_DEFINED"}, None),
    ]
    levels = json.loads(text)["result"]["accessLevels"]
    assert levels[0] == {
        "name": "accessPolicies/111/accessLevels/corp",
        "title": "Corp devices",
        "description": "Managed devices on the office network",
        "basic": {
            "combiningFunction": "AND",
            "conditions": [
                {
                    "ipSubnetworks": ["203.0.113.0/24"],
                    "regions": ["US"],
                    "negate": False,
                    "requiredAccessLevels": ["accessPolicies/111/accessLevels/base"],
                    "devicePolicy": {
                        "requireScreenlock": True,
                        "requireCorpOwned": False,
                        "allowedEncryptionStatuses": ["ENCRYPTED"],
                        "osConstraints": [
                            {"osType": "DESKTOP_CHROME_OS", "minimumVersion": "120.0"}
                        ],
                    },
                }
            ],
        },
    }
    assert levels[1]["custom"] == {
        "expr": {"expression": "device.is_corp_owned_device", "title": "Corp owned"}
    }
    assert "device secret" not in text and "not-a-level-field" not in text
    assert "/home/admin" not in text


@pytest.mark.asyncio
async def test_access_level_get_verifies_its_policy_first(acm):
    result, text = await operation(
        "accesscontextmanager.accessPolicies.accessLevels.get",
        {"name": "accessPolicies/111/accessLevels/corp"},
    )

    assert not result.is_error, text
    assert [r[1] for r in _acm_requests(acm)] == [
        "/v1/accessPolicies/111",
        LEVEL_PATH + "/corp",
    ]
    assert json.loads(text)["result"]["title"] == "Corp devices"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_id", "params", "refusal"),
    [
        (
            "accesscontextmanager.accessPolicies.accessLevels.list",
            {"parent": "accessPolicies/333"},
            "another organization",
        ),
        (
            "accesscontextmanager.accessPolicies.accessLevels.get",
            {"name": "accessPolicies/333/accessLevels/corp"},
            "another organization",
        ),
        (
            "accesscontextmanager.accessPolicies.accessLevels.list",
            {"parent": "accessPolicies/222"},
            "scoped to Google Cloud",
        ),
        (
            "accesscontextmanager.accessPolicies.accessLevels.get",
            {"name": "accessPolicies/222/accessLevels/corp"},
            "scoped to Google Cloud",
        ),
    ],
    ids=["other-org-list", "other-org-get", "scoped-list", "scoped-get"],
)
async def test_access_levels_of_other_or_cloud_scoped_policies_are_refused(
    acm, operation_id, params, refusal
):
    result, text = await operation(operation_id, params)

    assert result.is_error and refusal in text
    assert "Other org policy" not in text and "Cloud team" not in text
    # Only the policy check was sent; no access level was read.
    policy = next(iter(params.values())).split("/accessLevels")[0]
    assert [r[1] for r in _acm_requests(acm)] == ["/v1/" + policy]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "accessPolicies/111/accessLevels/../../organizations/999",
        "accessPolicies/111/servicePerimeters/perimeter",
        "organizations/999/gcpUserAccessBindings/b1",
        "accessPolicies/111/accessLevels/1starts_with_digit",
    ],
)
async def test_malformed_access_level_names_are_refused(acm, name):
    result, text = await operation(
        "accesscontextmanager.accessPolicies.accessLevels.get", {"name": name}
    )

    assert result.is_error and "expected format" in text
    assert _acm_requests(acm) == []


# --- writes, account isolation, and selection --------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_id", "category"),
    [
        ("accesscontextmanager.accessPolicies.accessLevels.create", "console-only"),
        ("accesscontextmanager.accessPolicies.accessLevels.patch", "console-only"),
        ("accesscontextmanager.accessPolicies.accessLevels.delete", "console-only"),
        ("accesscontextmanager.accessPolicies.accessLevels.replaceAll", "console-only"),
        ("accesscontextmanager.accessPolicies.delete", "console-only"),
        ("accesscontextmanager.accessPolicies.setIamPolicy", "cloud-resource"),
        (
            "accesscontextmanager.accessPolicies.servicePerimeters.create",
            "cloud-resource",
        ),
        (
            "accesscontextmanager.organizations.gcpUserAccessBindings.create",
            "cloud-resource",
        ),
        (
            "accesscontextmanager.accessPolicies.authorizedOrgsDescs.create",
            "unguarded-target",
        ),
        ("accesscontextmanager.operations.cancel", "unguarded-target"),
    ],
)
async def test_access_context_writes_are_excluded_before_any_request(
    acm, operation_id, category
):
    result, text = await operation(
        operation_id,
        {"parent": "accessPolicies/111"},
        {"name": "accessPolicies/111/accessLevels/corp", "title": "x"},
    )

    assert result.is_error and f"excluded ({category})" in text
    assert "confirmation_token" not in text
    assert acm.http.requests == [] and acm.requested_scopes == []
    assert not list((acm.confirmations.directory).glob("*"))
    assert acm.audit() == []


@pytest.mark.asyncio
async def test_non_admin_account_is_refused_before_any_google_cloud_request(acm):
    result, text = await operation(
        "accesscontextmanager.accessPolicies.list", admin="staff@op.example"
    )

    assert result.is_error and "not a Workspace admin" in text
    assert acm.http.requests == [] and acm.requested_scopes == []


@pytest.mark.asyncio
async def test_unselected_service_requests_no_cloud_platform_token(acm, monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", list(ALL_ADMIN))

    result, text = await operation("accesscontextmanager.accessPolicies.list")

    assert result.is_error and "admin-access-context" in text
    assert acm.http.requests == [] and acm.requested_scopes == []


def test_unbound_context_is_refused_without_a_request():
    context = AdminContext(actor_email="admin@op.example", customer_id="C01")

    with pytest.raises(AdminBoundaryError, match="Google Cloud organization"):
        resolve_call(
            context, "accesscontextmanager.accessPolicies.list", {}, None, None
        )
