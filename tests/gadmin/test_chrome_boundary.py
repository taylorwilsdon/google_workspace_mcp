"""Chrome Management and Chrome Policy references stay inside the verified
customer.

Customer names are set by the server and "customers/..." paths are rewritten to
the verified customer. A browser profile is trusted for a write only after a
fresh read with a real (pinned) Chrome Management client. Every Chrome Policy
target in a batch is read afresh from Directory, and a policy value is accepted
only if each member is a boolean, integer, or enum field of the schema read
afresh from Google, so no free text reaches a proposal."""

import pytest

from auth.scopes import (
    CHROME_MANAGEMENT_POLICY_READONLY_SCOPE,
    CHROME_MANAGEMENT_POLICY_SCOPE,
    CHROME_MANAGEMENT_PROFILES_READONLY_SCOPE,
    CHROME_MANAGEMENT_PROFILES_SCOPE,
)
from gadmin.auth import client_scopes
from gadmin.boundary import check_response, resolve_call, unbind
from gadmin.guard import AdminBoundaryError, AdminContext
from gadmin.registry import InvalidOperationInput, get_operation
from tests.gadmin.fake_directory import FakeDirectory
from tests.gadmin.fake_http import chrome_client

CONTEXT = AdminContext(
    actor_email="admin@op.example", customer_id="C01", actor_id="U_ADMIN"
)
PROFILE = {"name": "customers/C01/profiles/P1", "profileId": "P1"}
SCHEMA_NAME = "chrome.users.SafeBrowsing"
SCHEMA = {
    "name": f"customers/C01/policySchemas/{SCHEMA_NAME}",
    "schemaName": SCHEMA_NAME,
    "definition": {
        "package": "chrome.users",
        "messageType": [
            {
                "name": "SafeBrowsing",
                "field": [
                    {
                        "name": "protectionLevel",
                        "type": "TYPE_ENUM",
                        "typeName": ".chrome.users.ProtectionLevelEnum",
                        "label": "LABEL_OPTIONAL",
                    },
                    {
                        "name": "max_connections",
                        "jsonName": "maxConnections",
                        "type": "TYPE_INT64",
                        "label": "LABEL_OPTIONAL",
                    },
                    {"name": "allowOverride", "type": "TYPE_BOOL"},
                    {"name": "homepageUrl", "type": "TYPE_STRING"},
                    {
                        "name": "blockedPorts",
                        "type": "TYPE_INT32",
                        "label": "LABEL_REPEATED",
                    },
                    {
                        "name": "proxy",
                        "type": "TYPE_MESSAGE",
                        "typeName": "ProxySettings",
                    },
                ],
            }
        ],
        "enumType": [
            {
                "name": "ProtectionLevelEnum",
                "value": [
                    {"name": "PROTECTION_LEVEL_ENUM_STANDARD", "number": 1},
                    {"name": "PROTECTION_LEVEL_ENUM_ENHANCED", "number": 2},
                ],
            }
        ],
    },
}
VALUE = {
    "protectionLevel": "PROTECTION_LEVEL_ENUM_ENHANCED",
    "maxConnections": 32,
    "allowOverride": False,
}


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_org_unit("OU1")
    fake.add_org_unit("OU_OTHER", customer_id="C_OTHER")
    fake.add_group("G1", "team@op.example", [])
    fake.add_group("G2", "sales@op-alias.example", [])
    fake.add_group("GX", "team@other.example", [])
    return fake


@pytest.fixture
def management():
    client, http = chrome_client(
        "chromemanagement",
        {("GET", "/v1/customers/C01/profiles/P1"): PROFILE},
    )
    client.http = http
    return client


@pytest.fixture
def policy():
    client, http = chrome_client(
        "chromepolicy",
        {("GET", f"/v1/customers/C01/policySchemas/{SCHEMA_NAME}"): SCHEMA},
    )
    client.http = http
    return client


def _resolve(directory, operation_id, params=None, body=None, client=None):
    return resolve_call(
        CONTEXT, get_operation(operation_id), params, body, directory, client
    )


def _modify(target: str, value=None, schema: str = SCHEMA_NAME) -> dict:
    return {
        "policyTargetKey": {"targetResource": target},
        "policyValue": {"policySchema": schema, "value": value or dict(VALUE)},
        "updateMask": ",".join(value or VALUE),
    }


# --- customer names -----------------------------------------------------------------


def test_customer_names_are_set_by_the_server(directory):
    report = _resolve(
        directory, "chromemanagement.customers.reports.countChromeVersions"
    )
    listed = _resolve(
        directory,
        "chromemanagement.customers.telemetry.devices.list",
        {"pageSize": 10},
    )

    assert report.params == {"customer": "customers/C01"}
    assert listed.params == {"parent": "customers/C01", "pageSize": 10}
    assert directory.calls == []


@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        (
            "chromemanagement.customers.reports.countChromeVersions",
            {"customer": "customers/C_OTHER"},
        ),
        (
            "chromemanagement.customers.profiles.list",
            {"parent": "customers/my_customer"},
        ),
        ("chromepolicy.customers.policySchemas.list", {"parent": "customers/C01"}),
    ],
)
def test_caller_supplied_customer_is_refused(directory, operation_id, params):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, operation_id, params)


def test_customer_paths_are_rewritten_or_refused(directory):
    own = _resolve(
        directory,
        "chromemanagement.customers.profiles.get",
        {"name": "customers/my_customer/profiles/P1"},
    )
    schema = _resolve(
        directory,
        "chromepolicy.customers.policySchemas.get",
        {"name": f"customers/my_customer/policySchemas/{SCHEMA_NAME}"},
    )

    assert own.params == {"name": "customers/C01/profiles/P1"}
    assert schema.params == {"name": f"customers/C01/policySchemas/{SCHEMA_NAME}"}
    with pytest.raises(AdminBoundaryError, match="another customer"):
        _resolve(
            directory,
            "chromemanagement.customers.profiles.get",
            {"name": "customers/C_OTHER/profiles/P1"},
        )


@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        (
            "chromemanagement.customers.profiles.get",
            {"name": "customers/C01/profiles/../telemetry/devices/D1"},
        ),
        (
            "chromemanagement.customers.apps.chrome.get",
            {"name": "customers/C01/apps/chrome/https://evil.test"},
        ),
        (
            "chromemanagement.customers.telemetry.devices.list",
            {"readMask": "name,https://evil.test"},
        ),
        (
            "chromemanagement.customers.apps.fetchUsersRequestingExtension",
            {"extensionId": "not-an-extension"},
        ),
    ],
)
def test_malformed_names_and_masks_are_refused(directory, operation_id, params):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, operation_id, params)


def test_response_from_another_customer_is_refused(directory):
    spec = get_operation("chromemanagement.customers.telemetry.devices.list")

    check_response(
        CONTEXT, spec, {"devices": [{"customer": "customers/C01"}]}, directory
    )
    with pytest.raises(AdminBoundaryError, match="another customer"):
        check_response(
            CONTEXT, spec, {"devices": [{"customer": "customers/C_OTHER"}]}, directory
        )


# --- browser profiles ---------------------------------------------------------------


def test_profile_write_is_trusted_only_after_a_fresh_read(directory, management):
    deleted = _resolve(
        directory,
        "chromemanagement.customers.profiles.delete",
        {"name": "customers/my_customer/profiles/P1"},
        client=management,
    )
    command = _resolve(
        directory,
        "chromemanagement.customers.profiles.commands.create",
        {"parent": "customers/C01/profiles/P1"},
        {"commandType": "clearBrowsingData", "payload": {"clearCache": True}},
        management,
    )

    assert deleted.params == {"name": "customers/C01/profiles/P1"}
    assert deleted.risk == command.risk == "destructive"
    assert command.target == "customers/C01/profiles/P1"
    assert [r[:2] for r in management.http.requests] == [
        ("GET", "/v1/customers/C01/profiles/P1"),
        ("GET", "/v1/customers/C01/profiles/P1"),
    ]
    with pytest.raises(AdminBoundaryError, match="verify the resource"):
        _resolve(
            directory,
            "chromemanagement.customers.profiles.delete",
            {"name": "customers/C01/profiles/P_UNKNOWN"},
            client=management,
        )


@pytest.mark.parametrize(
    "body",
    [
        {"commandType": "wipeProfile", "payload": {}},
        {"commandType": "clearBrowsingData", "payload": {"clearHistory": True}},
        {"commandType": "clearBrowsingData", "payload": {"clearCache": "yes"}},
        {"commandType": "clearBrowsingData"},
    ],
)
def test_profile_commands_accept_only_documented_payloads(directory, management, body):
    with pytest.raises(InvalidOperationInput):
        _resolve(
            directory,
            "chromemanagement.customers.profiles.commands.create",
            {"parent": "customers/C01/profiles/P1"},
            body,
            management,
        )
    assert management.http.requests == []


def test_security_insights_accept_no_caller_org_unit_paths(directory):
    enabled = _resolve(
        directory, "chromemanagement.customers.enterprise.securityInsights.enable"
    )

    assert enabled.params == {"customer": "customers/C01"}
    assert enabled.body is None and enabled.risk == "destructive"
    with pytest.raises(InvalidOperationInput):
        _resolve(
            directory,
            "chromemanagement.customers.enterprise.securityInsights.enable",
            body={"targetOus": ["/"]},
        )


# --- policy targets -----------------------------------------------------------------


@pytest.mark.parametrize("target", ["orgunits/OU1", "groups/G1", "groups/G2"])
def test_policy_targets_in_the_customer_are_accepted(directory, target):
    resolved = _resolve(
        directory,
        "chromepolicy.customers.policies.resolve",
        body={
            "policySchemaFilter": "chrome.users.*",
            "policyTargetKey": {"targetResource": target},
        },
    )

    assert resolved.params == {"customer": "customers/C01"}
    assert resolved.body["policyTargetKey"] == {"targetResource": target}
    assert resolved.risk == "read" and resolved.target == target


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("orgunits/OU_OTHER", "organizational unit"),
        ("orgunits/OU_MISSING", "organizational unit"),
        ("groups/GX", "customer's domains"),
        ("groups/GMISSING", "group"),
    ],
)
def test_policy_targets_outside_the_customer_are_refused(directory, target, message):
    with pytest.raises(AdminBoundaryError, match=message):
        _resolve(
            directory,
            "chromepolicy.customers.policies.resolve",
            body={
                "policySchemaFilter": "chrome.users.*",
                "policyTargetKey": {"targetResource": target},
            },
        )


@pytest.mark.parametrize(
    ("operation_id", "target"),
    [
        ("chromepolicy.customers.policies.orgunits.batchModify", "groups/G1"),
        ("chromepolicy.customers.policies.groups.batchModify", "orgunits/OU1"),
        ("chromepolicy.customers.policies.orgunits.batchModify", "customers/C01"),
    ],
)
def test_each_batch_accepts_only_its_own_target_kind(directory, operation_id, target):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, operation_id, body={"requests": [_modify(target)]})


def test_every_target_of_a_batch_is_verified_before_any_schema_read(directory, policy):
    body = {"requests": [_modify("orgunits/OU1"), _modify("orgunits/OU_OTHER")]}

    with pytest.raises(AdminBoundaryError, match="organizational unit"):
        _resolve(
            directory,
            "chromepolicy.customers.policies.orgunits.batchModify",
            body=body,
            client=policy,
        )
    assert policy.http.requests == []


def test_group_priority_ordering_verifies_every_group(directory):
    body = {
        "policyTargetKey": {"additionalTargetKeys": {"app_id": "chrome:" + "a" * 32}},
        "policyNamespace": "chrome.users.apps",
        "groupIds": ["G2", "G1"],
    }
    resolved = _resolve(
        directory,
        "chromepolicy.customers.policies.groups.updateGroupPriorityOrdering",
        body=body,
    )

    assert resolved.body["groupIds"] == ["G2", "G1"]
    assert [c[1]["groupKey"] for c in directory.calls if "groups" in c[0]] == [
        "G2",
        "G1",
    ]
    assert resolved.risk == "destructive"
    with pytest.raises(AdminBoundaryError, match="customer's domains"):
        _resolve(
            directory,
            "chromepolicy.customers.policies.groups.updateGroupPriorityOrdering",
            body={**body, "groupIds": ["G1", "GX"]},
        )


# --- policy values ------------------------------------------------------------------


def test_policy_value_is_checked_against_the_fresh_schema(directory, policy):
    body = {"requests": [_modify("orgunits/OU1"), _modify("orgunits/OU1")]}

    resolved = _resolve(
        directory,
        "chromepolicy.customers.policies.orgunits.batchModify",
        body=body,
        client=policy,
    )

    assert resolved.body == body and resolved.risk == "destructive"
    assert resolved.target == "orgunits/OU1"
    # Each request's schema is read afresh, bound to the verified customer.
    assert [r[:2] for r in policy.http.requests] == [
        ("GET", f"/v1/customers/C01/policySchemas/{SCHEMA_NAME}")
    ] * 2


@pytest.mark.parametrize(
    "value",
    [
        {"homepageUrl": "https://evil.test/secret"},
        {"blockedPorts": [443]},
        {"proxy": {"host": "evil.test"}},
        {"unknownField": True},
        {"allowOverride": "true"},
        {"maxConnections": True},
        {"maxConnections": "32"},
        {"protectionLevel": "PROTECTION_LEVEL_ENUM_OFF"},
        {"protectionLevel": 2},
    ],
)
def test_policy_values_outside_scalar_schema_fields_are_refused(
    directory, policy, value
):
    body = {"requests": [_modify("orgunits/OU1", value)]}

    with pytest.raises(AdminBoundaryError) as refused:
        _resolve(
            directory,
            "chromepolicy.customers.policies.orgunits.batchModify",
            body=body,
            client=policy,
        )
    # The refusal never echoes the caller's keys or values.
    message = str(refused.value)
    assert all(str(v) not in message for item in value.items() for v in item)


def test_policy_value_for_an_unknown_schema_is_refused(directory, policy):
    body = {"requests": [_modify("orgunits/OU1", schema="chrome.users.Missing")]}

    with pytest.raises(AdminBoundaryError, match="verify the resource"):
        _resolve(
            directory,
            "chromepolicy.customers.policies.orgunits.batchModify",
            body=body,
            client=policy,
        )


def test_policy_writes_request_the_schema_read_scope():
    modify = get_operation("chromepolicy.customers.policies.groups.batchModify")
    delete = get_operation("chromemanagement.customers.profiles.delete")

    assert set(client_scopes(modify)) == {
        CHROME_MANAGEMENT_POLICY_SCOPE,
        CHROME_MANAGEMENT_POLICY_READONLY_SCOPE,
    }
    assert set(client_scopes(delete)) == {
        CHROME_MANAGEMENT_PROFILES_SCOPE,
        CHROME_MANAGEMENT_PROFILES_READONLY_SCOPE,
    }


def test_unbind_removes_the_server_customer(directory):
    resolved = _resolve(
        directory,
        "chromepolicy.customers.policies.orgunits.batchInherit",
        body={
            "requests": [
                {
                    "policyTargetKey": {"targetResource": "orgunits/OU1"},
                    "policySchema": SCHEMA_NAME,
                }
            ]
        },
    )
    spec = get_operation("chromepolicy.customers.policies.orgunits.batchInherit")

    assert unbind(spec, resolved.params, resolved.body)[0] == {}
