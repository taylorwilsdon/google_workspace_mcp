"""Cloud Identity operations cross the real MCP boundary through admin_operation.

Only the transport is faked: the Cloud Identity client is the installed client
rebuilt from the pinned discovery document, exactly as get_admin_service does, so
each test shows the request that would reach Google. Opaque names are verified
by a fresh read on that client, and every write is proposed, then sent once by a
matching confirmation."""

import json

import pytest
from googleapiclient.discovery import build

import auth.permissions as permissions
import auth.scopes as scopes
from tests.gadmin.fake_http import RoutingHttp
from tests.gadmin.test_admin_operation import (  # noqa: F401 - ws is a fixture
    ALL_ADMIN,
    call,
    confirm,
    operation,
    propose,
    ws,
)

CLOUD_IDENTITY = [
    "admin-cloudidentity-groups",
    "admin-cloudidentity-devices",
    "admin-cloudidentity-sso",
    "admin-cloudidentity-policies",
    "admin-cloudidentity-invitations",
    "admin-cloudidentity-domains",
    "admin-cloudidentity-orgunits",
]
GROUP = {
    "name": "groups/G1",
    "parent": "customers/C01",
    "groupKey": {"id": "team@op.example"},
    "displayName": "Team",
}
OTHER_GROUP = {"name": "groups/GX", "parent": "customers/C_OTHER"}
DEVICE = {
    "name": "devices/D1",
    "model": "Pixel",
    "imei": "356938035643809",
    "wifiMacAddresses": ["00:11:22:33:44:55"],
}
# Google echoes the changed device in the operation response; it is not returned.
WIPE_OPERATION = {
    "name": "operations/w1",
    "done": True,
    "response": {"device": DEVICE},
    "metadata": {"note": "free text"},
}
OIDC_SECRET = "oidc-client-secret-value"
OIDC_PROFILE = {
    "name": "inboundOidcSsoProfiles/O1",
    "customer": "customers/C01",
    "displayName": "IdP",
    "rpConfig": {"clientId": "client-1", "clientSecret": OIDC_SECRET},
}
POLICY = {
    "name": "policies/P1",
    "customer": "customers/C01",
    "policyQuery": {"orgUnit": "orgUnits/OU1"},
    "setting": {"type": "settings/security.two_step_verification", "value": {}},
}
OTHER_POLICY = {**POLICY, "name": "policies/PX", "customer": "customers/C_OTHER"}


@pytest.fixture
def ci(ws, monkeypatch):  # noqa: F811 - the imported fixture
    http = RoutingHttp(
        {
            ("GET", "/v1/groups/G1"): GROUP,
            ("GET", "/v1/groups/GX"): OTHER_GROUP,
            ("GET", "/v1/devices/D1"): DEVICE,
            ("POST", "/v1/devices/D1:wipe"): WIPE_OPERATION,
            ("GET", "/v1/inboundOidcSsoProfiles/O1"): OIDC_PROFILE,
            ("GET", "/v1/policies/P1"): POLICY,
            ("GET", "/v1/policies/PX"): OTHER_POLICY,
            ("GET", "/v1/policies"): {"policies": [POLICY]},
            ("PATCH", "/v1/policies/P1"): {"name": "operations/p1", "done": False},
            (
                "POST",
                "/v1beta1/orgUnits/OU1/memberships/shared_drive;0AB:move",
            ): {"name": "operations/m1", "done": True},
        }
    )
    installed = {
        ("cloudidentity", version): build("cloudidentity", version, http=http)
        for version in ("v1", "v1beta1")
    }
    apis = ws.apis
    monkeypatch.setattr(ws, "apis", lambda: {**apis(), **installed})
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, *CLOUD_IDENTITY])
    ws.directory.add_org_unit("OU1")
    ws.directory.add_org_unit("OU2")
    ws.directory.add_org_unit("OU_OTHER", customer_id="C_OTHER")
    ws.http = http
    return ws


def _writes(ci) -> list:
    return [r for r in ci.http.requests if r[0] != "GET"]


# --- groups -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_read_returns_bounded_result_inside_the_customer(ci):
    result, text = await operation("cloudidentity.groups.get", {"name": "groups/G1"})

    assert not result.is_error, text
    assert json.loads(text)["result"] == GROUP
    assert ci.requested_scopes[-1] == [scopes.CLOUD_IDENTITY_GROUPS_READONLY_SCOPE]


@pytest.mark.asyncio
async def test_another_customers_group_is_neither_returned_nor_changed(ci):
    result, text = await operation("cloudidentity.groups.get", {"name": "groups/GX"})
    assert result.is_error and "another customer" in text
    assert "C_OTHER" not in text

    result, text = await operation("cloudidentity.groups.delete", {"name": "groups/GX"})
    assert result.is_error and "another customer" in text
    assert ci.audit() == [] and _writes(ci) == []


@pytest.mark.asyncio
async def test_group_list_is_bound_to_the_customer(ci):
    ci.http.handlers[("GET", "/v1/groups")] = {"groups": [GROUP]}

    result, text = await operation("cloudidentity.groups.list", {"view": "BASIC"})
    assert not result.is_error, text
    assert ci.http.requests[-1][2] == {"parent": "customers/C01", "view": "BASIC"}

    result, text = await operation(
        "cloudidentity.groups.list", {"parent": "customers/C_OTHER"}
    )
    assert result.is_error and "set by the server" in text


# --- devices -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_device_wipe_is_proposed_then_confirmed_once(ci):
    proposal = await propose("cloudidentity.devices.wipe", {"name": "devices/D1"})

    assert proposal["risk"] == "destructive"
    assert proposal["body"] == {"customer": "customers/C01"}
    assert _writes(ci) == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(ci) == [
        ("POST", "/v1/devices/D1:wipe", {}, {"customer": "customers/C01"})
    ]
    # Only the operation name, completion, and error code come back.
    assert json.loads(text)["result"] == {"name": "operations/w1", "done": True}
    assert "356938035643809" not in text

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(_writes(ci)) == 1
    assert [r["outcome"] for r in ci.audit()] == ["proposed", "succeeded"]


@pytest.mark.asyncio
async def test_device_read_omits_hardware_identifiers(ci):
    result, text = await operation("cloudidentity.devices.get", {"name": "devices/D1"})

    assert not result.is_error, text
    assert json.loads(text)["result"] == {"name": "devices/D1", "model": "Pixel"}
    assert ci.http.requests == [
        ("GET", "/v1/devices/D1", {"customer": "customers/C01"}, None)
    ]


@pytest.mark.asyncio
async def test_unknown_device_is_refused_before_a_proposal(ci):
    result, text = await operation("cloudidentity.devices.wipe", {"name": "devices/D9"})

    assert result.is_error and "Could not verify the resource" in text
    assert ci.audit() == [] and _writes(ci) == []


@pytest.mark.asyncio
async def test_tampered_and_changed_device_confirmations_are_refused(ci):
    proposal = await propose("cloudidentity.devices.wipe", {"name": "devices/D1"})
    result, text = await confirm(proposal, token="x" * 43)
    assert result.is_error and "does not match" in text
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text

    # The device left the customer between proposal and confirmation.
    proposal = await propose("cloudidentity.devices.wipe", {"name": "devices/D1"})
    ci.http.handlers[("GET", "/v1/devices/D1")] = 404
    result, text = await confirm(proposal)
    assert result.is_error and "Could not verify the resource" in text
    assert _writes(ci) == []


@pytest.mark.asyncio
async def test_device_permission_levels(ci, monkeypatch):
    monkeypatch.setattr(
        permissions, "_PERMISSIONS", {"admin-cloudidentity-devices": "manage"}
    )
    result, text = await operation("cloudidentity.devices.wipe", {"name": "devices/D1"})
    assert result.is_error and "destructive" in text

    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)
    result, text = await operation(
        "cloudidentity.devices.cancelWipe", {"name": "devices/D1"}
    )
    assert result.is_error and "allows up to read" in text
    result, text = await operation("cloudidentity.devices.get", {"name": "devices/D1"})
    assert not result.is_error, text
    assert _writes(ci) == []


@pytest.mark.asyncio
async def test_device_operations_need_the_devices_service(ci, monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN])

    result, text = await operation("cloudidentity.devices.get", {"name": "devices/D1"})

    assert result.is_error and "admin-cloudidentity-devices" in text
    assert ci.http.requests == []


# --- SSO and policies ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_oidc_client_secrets_are_never_accepted_stored_or_returned(ci):
    result, text = await operation(
        "cloudidentity.inboundOidcSsoProfiles.create",
        body={
            "displayName": "IdP",
            "idpConfig": {"issuerUri": "https://idp.example"},
            "rpConfig": {"clientId": "client-1", "clientSecret": OIDC_SECRET},
        },
    )
    assert result.is_error and OIDC_SECRET not in text

    result, text = await operation(
        "cloudidentity.inboundOidcSsoProfiles.get",
        {"name": "inboundOidcSsoProfiles/O1"},
    )
    assert not result.is_error, text
    assert json.loads(text)["result"]["rpConfig"] == {"clientId": "client-1"}
    assert OIDC_SECRET not in text

    stored = [p.read_text() for p in ci.confirmations.directory.glob("**/*")]
    assert not any(OIDC_SECRET in s for s in stored if s)
    assert ci.audit() == []


@pytest.mark.asyncio
async def test_saml_credential_upload_is_excluded(ci):
    result, text = await operation(
        "cloudidentity.inboundSamlSsoProfiles.idpCredentials.add",
        {"parent": "inboundSamlSsoProfiles/S1"},
        {"pemData": "-----BEGIN CERTIFICATE-----"},
    )

    assert result.is_error and "excluded (secret-in-payload)" in text
    assert "BEGIN CERTIFICATE" not in text
    assert ci.http.requests == [] and ci.audit() == []


@pytest.mark.asyncio
async def test_policy_change_is_destructive_and_confirmed_once(ci, monkeypatch):
    body = {"setting": {"type": POLICY["setting"]["type"], "value": {"enforced": True}}}
    monkeypatch.setattr(
        permissions, "_PERMISSIONS", {"admin-cloudidentity-policies": "manage"}
    )
    result, text = await operation(
        "cloudidentity.policies.patch", {"name": "policies/P1"}, body
    )
    assert result.is_error and "destructive" in text
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)

    proposal = await propose(
        "cloudidentity.policies.patch", {"name": "policies/P1"}, body
    )
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(ci) == [("PATCH", "/v1/policies/P1", {}, body)]
    assert json.loads(text)["result"] == {"name": "operations/p1", "done": False}


@pytest.mark.asyncio
async def test_another_customers_policy_and_group_targets_are_refused(ci):
    result, text = await operation(
        "cloudidentity.policies.delete", {"name": "policies/PX"}
    )
    assert result.is_error and "another customer" in text

    result, text = await operation(
        "cloudidentity.policies.create",
        body={
            "policyQuery": {"group": "groups/GX", "orgUnit": "orgUnits/OU1"},
            "setting": POLICY["setting"],
        },
    )
    assert result.is_error and "another customer" in text

    result, text = await operation(
        "cloudidentity.policies.create",
        body={
            "policyQuery": {"orgUnit": "orgUnits/OU_OTHER"},
            "setting": POLICY["setting"],
        },
    )
    assert result.is_error and "organizational unit" in text
    assert ci.audit() == [] and _writes(ci) == []


@pytest.mark.asyncio
async def test_policy_list_refuses_results_of_another_customer(ci):
    result, text = await operation("cloudidentity.policies.list")
    assert not result.is_error, text

    ci.http.handlers[("GET", "/v1/policies")] = {"policies": [POLICY, OTHER_POLICY]}
    result, text = await operation("cloudidentity.policies.list")
    assert result.is_error and "another customer" in text
    assert "policies/PX" not in text


# --- beta organizational unit memberships ------------------------------------------


@pytest.mark.asyncio
async def test_shared_drive_move_uses_the_pinned_beta_client(ci):
    name = "orgUnits/OU1/memberships/shared_drive;0AB"
    proposal = await propose(
        "cloudidentity.orgUnits.memberships.move",
        {"name": name},
        {"destinationOrgUnit": "orgUnits/OU2"},
    )
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(ci) == [
        (
            "POST",
            f"/v1beta1/{name}:move",
            {},
            {"customer": "customers/C01", "destinationOrgUnit": "orgUnits/OU2"},
        )
    ]

    result, text = await operation(
        "cloudidentity.orgUnits.memberships.move",
        {"name": name},
        {"destinationOrgUnit": "orgUnits/OU_OTHER"},
    )
    assert result.is_error and "organizational unit" in text


@pytest.mark.asyncio
async def test_capabilities_list_cloud_identity_and_the_remaining_gaps(ci):
    result, text = await call("list_admin_capabilities", user_google_email="x@y.z")

    assert not result.is_error, text
    assert "cloudidentity.devices.wipe" in text
    assert "Context-aware access level changes and app assignments" in text
    assert "Cloud Identity (devices, SSO, and access policies)" not in text
