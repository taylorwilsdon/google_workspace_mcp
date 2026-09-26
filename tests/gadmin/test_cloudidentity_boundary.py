"""Cloud Identity references stay inside the verified customer.

Customer names are set by the server, "customers/..." paths are rewritten to
the verified customer, organizational units are read afresh from Directory, and
an opaque group, device, profile, assignment, or policy name is trusted only
after a fresh registered read with a real (pinned) Cloud Identity client shows
it belongs to the customer."""

import pytest

from auth.scopes import (
    CLOUD_IDENTITY_GROUPS_READONLY_SCOPE,
    CLOUD_IDENTITY_INBOUNDSSO_SCOPE,
)
from gadmin.auth import client_scopes
from gadmin.boundary import check_response, resolve_call, unbind
from gadmin.guard import AdminBoundaryError, AdminContext
from gadmin.registry import InvalidOperationInput, get_operation
from tests.gadmin.fake_directory import FakeDirectory
from tests.gadmin.fake_http import cloud_identity_client

CONTEXT = AdminContext(
    actor_email="admin@op.example", customer_id="C01", actor_id="U_ADMIN"
)
GROUP = {"name": "groups/G1", "parent": "customers/C01", "displayName": "Team"}
OTHER_GROUP = {"name": "groups/GX", "parent": "customers/C_OTHER"}
PROFILE = {"name": "inboundSamlSsoProfiles/P1", "customer": "customers/C01"}
OTHER_PROFILE = {"name": "inboundSamlSsoProfiles/PX", "customer": "customers/CX"}


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_org_unit("OU1")
    fake.add_org_unit("OU2")
    fake.add_org_unit("OU_OTHER", customer_id="C_OTHER")
    return fake


@pytest.fixture
def ci():
    client, http = cloud_identity_client(
        {
            ("GET", "/v1/groups/G1"): GROUP,
            ("GET", "/v1/groups/GX"): OTHER_GROUP,
            ("GET", "/v1/devices/D1"): {"name": "devices/D1"},
            ("GET", "/v1/inboundSamlSsoProfiles/P1"): PROFILE,
            ("GET", "/v1/inboundSamlSsoProfiles/PX"): OTHER_PROFILE,
        }
    )
    client.http = http
    return client


def _resolve(directory, operation_id, params=None, body=None, client=None):
    return resolve_call(
        CONTEXT, get_operation(operation_id), params, body, directory, client
    )


# --- customer names -----------------------------------------------------------------


def test_customer_names_are_set_by_the_server(directory):
    listed = _resolve(directory, "cloudidentity.groups.list", {"view": "BASIC"})
    created = _resolve(
        directory,
        "cloudidentity.inboundSamlSsoProfiles.create",
        body={
            "displayName": "IdP",
            "idpConfig": {
                "entityId": "https://idp.example/entity",
                "singleSignOnServiceUri": "https://idp.example/sso",
            },
        },
    )

    assert listed.params == {"parent": "customers/C01", "view": "BASIC"}
    assert created.body["customer"] == "customers/C01"
    assert directory.calls == []


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        ("cloudidentity.groups.list", {"parent": "customers/C_OTHER"}, None),
        ("cloudidentity.devices.list", {"customer": "customers/C_OTHER"}, None),
        (
            "cloudidentity.devices.wipe",
            {"name": "devices/D1"},
            {"customer": "customers/C_OTHER"},
        ),
        ("cloudidentity.inboundSamlSsoProfiles.list", {"filter": "customer=x"}, None),
        (
            "cloudidentity.inboundOidcSsoProfiles.create",
            None,
            {"customer": "customers/C_OTHER", "displayName": "x"},
        ),
    ],
)
def test_caller_customers_and_filters_are_refused(
    directory, ci, operation_id, params, body
):
    with pytest.raises(InvalidOperationInput, match="set by the server"):
        _resolve(directory, operation_id, params, body, ci)
    assert ci.http.requests == []


def test_unbind_removes_only_the_server_set_customer(directory, ci):
    call = _resolve(
        directory,
        "cloudidentity.devices.wipe",
        {"name": "devices/D1"},
        {"removeResetLock": True},
        ci,
    )

    assert call.body == {"customer": "customers/C01", "removeResetLock": True}
    assert unbind("cloudidentity.devices.wipe", call.params, call.body) == (
        {"name": "devices/D1"},
        {"removeResetLock": True},
    )


# --- customer paths and domains ----------------------------------------------------


@pytest.mark.parametrize("customer", ["my_customer", "C01", "01"])
def test_invitation_path_is_rewritten_to_the_verified_customer(directory, customer):
    call = _resolve(
        directory,
        "cloudidentity.customers.userinvitations.send",
        {"name": f"customers/{customer}/userinvitations/pat@op.example"},
    )

    assert call.params == {"name": "customers/C01/userinvitations/pat@op.example"}
    assert call.risk == "manage"


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("customers/C_OTHER/userinvitations/pat@op.example", "another customer"),
        ("customers/C01/userinvitations/pat@other.example", "customer's domains"),
    ],
)
def test_invitations_outside_the_customer_are_refused(directory, name, error):
    with pytest.raises(AdminBoundaryError, match=error):
        _resolve(
            directory, "cloudidentity.customers.userinvitations.get", {"name": name}
        )


# --- organizational units ----------------------------------------------------------


def test_org_units_are_read_inside_the_customer(directory):
    call = _resolve(
        directory,
        "cloudidentity.orgUnits.memberships.move",
        {"name": "orgUnits/OU1/memberships/shared_drive;0AB"},
        {"destinationOrgUnit": "orgUnits/OU2"},
    )

    assert call.body == {
        "customer": "customers/C01",
        "destinationOrgUnit": "orgUnits/OU2",
    }
    assert [c[1]["orgUnitPath"] for c in directory.calls] == ["id:OU1", "id:OU2"]
    assert all(c[1]["customerId"] == "C01" for c in directory.calls)


@pytest.mark.parametrize("unit", ["OU_OTHER", "OU_MISSING"])
def test_org_units_of_another_customer_are_refused(directory, unit):
    with pytest.raises(AdminBoundaryError, match="organizational unit"):
        _resolve(
            directory,
            "cloudidentity.orgUnits.memberships.list",
            {"parent": f"orgUnits/{unit}"},
        )


# --- opaque resource names --------------------------------------------------------


def test_resource_names_are_verified_with_a_fresh_registered_read(directory, ci):
    call = _resolve(
        directory,
        "cloudidentity.groups.updateSecuritySettings",
        {"name": "groups/G1/securitySettings", "updateMask": "memberRestriction.query"},
        {"memberRestriction": {"query": "member.customer_id == 'C01'"}},
        ci,
    )

    assert call.risk == "destructive"
    # The security settings name is verified through its group.
    assert ci.http.requests == [("GET", "/v1/groups/G1", {}, None)]


def test_device_reads_for_verification_carry_the_verified_customer(directory, ci):
    _resolve(
        directory, "cloudidentity.devices.delete", {"name": "devices/D1"}, None, ci
    )

    assert ci.http.requests == [
        ("GET", "/v1/devices/D1", {"customer": "customers/C01"}, None)
    ]


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        ("cloudidentity.groups.delete", {"name": "groups/GX"}, None),
        ("cloudidentity.groups.memberships.list", {"parent": "groups/GX"}, None),
        (
            "cloudidentity.groups.memberships.delete",
            {"name": "groups/GX/memberships/M1"},
            None,
        ),
        (
            "cloudidentity.inboundSamlSsoProfiles.delete",
            {"name": OTHER_PROFILE["name"]},
            None,
        ),
        (
            "cloudidentity.inboundSamlSsoProfiles.idpCredentials.delete",
            {"name": "inboundSamlSsoProfiles/PX/idpCredentials/K1"},
            None,
        ),
        # A device another customer owns is not found inside the verified one.
        ("cloudidentity.devices.wipe", {"name": "devices/D_OTHER"}, {}),
        (
            "cloudidentity.policies.create",
            None,
            {
                "policyQuery": {"group": "groups/GX", "orgUnit": "orgUnits/OU1"},
                "setting": {"type": "settings/x", "value": {}},
            },
        ),
    ],
)
def test_resources_outside_the_customer_are_refused(
    directory, ci, operation_id, params, body
):
    with pytest.raises(AdminBoundaryError):
        _resolve(directory, operation_id, params, body, ci)
    assert all(verb == "GET" for verb, *_ in ci.http.requests)


def test_resource_rules_fail_closed_without_a_client(directory):
    with pytest.raises(AdminBoundaryError, match="Could not verify the resource"):
        _resolve(directory, "cloudidentity.groups.delete", {"name": "groups/G1"})


def test_sso_assignment_verifies_its_org_unit_group_and_profile(directory, ci):
    body = {
        "targetOrgUnit": "orgUnits/OU1",
        "ssoMode": "SAML_SSO",
        "samlSsoInfo": {"inboundSamlSsoProfile": "inboundSamlSsoProfiles/P1"},
    }

    call = _resolve(
        directory, "cloudidentity.inboundSsoAssignments.create", None, body, ci
    )

    assert call.body == {**body, "customer": "customers/C01"}
    assert call.risk == "destructive"
    assert ci.http.requests == [("GET", "/v1/inboundSamlSsoProfiles/P1", {}, None)]
    with pytest.raises(AdminBoundaryError, match="another customer"):
        _resolve(
            directory,
            "cloudidentity.inboundSsoAssignments.create",
            None,
            {**body, "samlSsoInfo": {"inboundSamlSsoProfile": OTHER_PROFILE["name"]}},
            ci,
        )


def test_sso_assignment_client_can_make_its_group_read():
    scopes = client_scopes(get_operation("cloudidentity.inboundSsoAssignments.create"))

    assert CLOUD_IDENTITY_INBOUNDSSO_SCOPE in scopes
    assert CLOUD_IDENTITY_GROUPS_READONLY_SCOPE in scopes


# --- inputs and results ------------------------------------------------------------


@pytest.mark.parametrize(
    "uri", ["http://idp.example/sso", "javascript:alert(1)", "https://idp.example/a b"]
)
def test_sso_endpoints_must_be_https(directory, uri):
    with pytest.raises(InvalidOperationInput):
        _resolve(
            directory,
            "cloudidentity.inboundSamlSsoProfiles.create",
            body={"idpConfig": {"entityId": "urn:idp", "singleSignOnServiceUri": uri}},
        )


def test_read_results_of_another_customer_are_refused(directory):
    listed = {"groups": [GROUP, OTHER_GROUP]}

    check_response(CONTEXT, "cloudidentity.groups.get", GROUP, directory)
    with pytest.raises(AdminBoundaryError, match="another customer"):
        check_response(CONTEXT, "cloudidentity.groups.search", listed, directory)
