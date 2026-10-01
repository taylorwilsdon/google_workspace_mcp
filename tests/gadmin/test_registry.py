"""The pinned Directory registry is the only source of callable admin methods."""

import json
from pathlib import Path

import pytest

from gadmin.registry import (
    InvalidOperationInput,
    UnknownOperation,
    _load_operations,
    classify_risk,
    get_operation,
    iter_operations,
    validate_call,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Pinned discovery snapshots for each registered API family, keyed by
# (service, version); regenerate with fixtures/snapshot_discovery.py.
DISCOVERY = {
    ("admin", "directory_v1"): "admin_directory_v1_discovery.json",
    ("admin", "datatransfer_v1"): "admin_datatransfer_v1_discovery.json",
    ("admin", "reports_v1"): "admin_reports_v1_discovery.json",
    ("licensing", "v1"): "licensing_v1_discovery.json",
    ("vault", "v1"): "vault_v1_discovery.json",
    ("alertcenter", "v1beta1"): "alertcenter_v1beta1_discovery.json",
    ("groupssettings", "v1"): "groupssettings_v1_discovery.json",
    ("cloudidentity", "v1"): "cloudidentity_v1_discovery.json",
    ("cloudidentity", "v1beta1"): "cloudidentity_v1beta1_discovery.json",
    ("chromemanagement", "v1"): "chromemanagement_v1_discovery.json",
    ("chromepolicy", "v1"): "chromepolicy_v1_discovery.json",
    ("accesscontextmanager", "v1"): "accesscontextmanager_v1_discovery.json",
    ("admin", "contacts_v1"): "admin_contacts_v1_discovery.json",
    ("gmail", "v1"): "gmail_v1_discovery.json",
}
DISCOVERY = {
    pair: json.loads((FIXTURES / name).read_text()) for pair, name in DISCOVERY.items()
}


def test_registry_rejects_unknown_method_and_field():
    assert get_operation("directory.users.get").risk == "read"
    assert (
        classify_risk(get_operation("directory.users.update"), {"suspended": True})
        == "destructive"
    )
    with pytest.raises(UnknownOperation):
        get_operation("directory.users.makeOwner")
    with pytest.raises(InvalidOperationInput):
        validate_call(
            get_operation("directory.users.get"),
            {"userKey": "u@example.com", "url": "https://evil.test"},
            None,
        )


def test_profile_edit_and_unsuspend_stay_manage_risk():
    update = get_operation("directory.users.update")

    assert classify_risk(update, {"orgUnitPath": "/Staff"}) == "manage"
    assert classify_risk(update, {"suspended": False}) == "manage"
    assert classify_risk(update, None) == "manage"


def test_delete_is_destructive_without_body():
    assert classify_risk(get_operation("directory.users.delete"), None) == "destructive"


def test_validate_call_returns_clean_copies():
    params = {"userKey": "u@example.com", "projection": "full"}
    body = {"suspended": True}

    clean_params, clean_body = validate_call(
        get_operation("directory.users.update"), {"userKey": "u@example.com"}, body
    )
    get_params, get_body = validate_call(
        get_operation("directory.users.get"), params, None
    )

    assert clean_params == {"userKey": "u@example.com"}
    assert clean_body == {"suspended": True}
    assert clean_body is not body
    assert get_params == params and get_params is not params
    assert get_body is None


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        ("directory.users.get", {}, None),  # missing required userKey
        ("directory.users.get", {"userKey": ""}, None),
        ("directory.users.get", {"userKey": 42}, None),
        (
            "directory.users.get",
            {"userKey": "u@example.com", "projection": "all"},
            None,
        ),
        ("directory.users.get", {"userKey": "../../customer/x"}, None),
        ("directory.users.get", {"userKey": "https://evil.test/u"}, None),
        ("directory.users.get", {"userKey": "u@example.com"}, {"suspended": True}),
        ("directory.users.list", {"customer": "my_customer", "maxResults": "5"}, None),
        ("directory.users.list", {"customer": "my_customer", "maxResults": True}, None),
        ("directory.users.list", {"query": "https://evil.test"}, None),
        ("directory.users.update", {"userKey": "u@example.com"}, {"password": "x"}),
        ("directory.users.update", {"userKey": "u@example.com"}, {"suspended": "true"}),
        ("directory.users.update", {"userKey": "u@example.com"}, ["suspended"]),
        (
            "directory.users.update",
            {"userKey": "u@example.com"},
            {"name": {"fullName": "x"}},
        ),
        ("directory.orgunits.get", {"orgUnitPath": "/Sales"}, None),
        ("directory.orgunits.get", {"orgUnitPath": "id:../x"}, None),
        ("directory.orgunits.insert", {}, {"parentOrgUnitId": "/"}),
        ("directory.members.insert", {"groupKey": "g1"}, {"role": "ADMIN"}),
        ("directory.members.insert", {"groupKey": "g1"}, {"email": "u@x", "id": "1"}),
        ("directory.roleAssignments.insert", {}, {"scopeType": "DOMAIN"}),
    ],
)
def test_validate_call_rejects_malformed_input(operation_id, params, body):
    with pytest.raises(InvalidOperationInput):
        validate_call(get_operation(operation_id), params, body)


def test_registry_is_pinned_to_the_checked_discovery_revision():
    operations = list(iter_operations())

    assert {(spec.service, spec.version) for spec in operations} == set(DISCOVERY)
    for spec in operations:
        discovery = DISCOVERY[(spec.service, spec.version)]
        assert spec.discovery_revision == discovery["revision"], spec.id


def _assert_body_matches_schema(fields, schema_name, discovery):
    schema = discovery["schemas"][schema_name]
    for field in fields:
        assert field.name in schema, field.name
        declared = schema[field.name]
        if field.type == "object" and declared == "object":
            # A map (Group labels, Chrome target keys, command payloads): its
            # registered keys are strings or booleans.
            assert all(f.type in ("string", "boolean") for f in field.fields)
        elif field.type == "object" and field.fields:
            _assert_body_matches_schema(field.fields, declared, discovery)
        if field.type != "array":
            continue
        item_type = UNTYPED_BODY_ITEMS.get((schema_name, field.name))
        item_type = item_type or declared.split(":", 1)[1]
        if field.items.type == "object":
            if item_type == "object":
                # Role privileges are inline objects in Discovery, not a named schema.
                assert (schema_name, field.name) == ("Role", "rolePrivileges")
                assert {f.name for f in field.items.fields} == {
                    "serviceId",
                    "privilegeName",
                }
            else:
                _assert_body_matches_schema(field.items.fields, item_type, discovery)
        else:
            assert field.items.type == item_type, field.name


# Discovery declares these list items as "any"; the items are these schemas.
UNTYPED_ITEMS = {"Aliases": "Alias"}
UNTYPED_BODY_ITEMS = {("CalendarResource", "featureInstances"): "FeatureInstance"}
# Discovery lists no scopes for user invitations; these come from the public
# reference, https://cloud.google.com/identity/docs/reference/rest/v1/customers.userinvitations
DOCUMENTED_SCOPES = {
    "cloudidentity.customers.userinvitations.": {
        "https://www.googleapis.com/auth/cloud-identity.userinvitations",
        "https://www.googleapis.com/auth/cloud-identity.userinvitations.readonly",
    }
}


def _discovery_scopes(spec, method) -> set[str]:
    documented = (s for p, s in DOCUMENTED_SCOPES.items() if spec.id.startswith(p))
    return set(method["scopes"]) or next(documented, set())


@pytest.mark.parametrize("spec", list(iter_operations()), ids=lambda spec: spec.id)
def test_registered_operation_matches_pinned_discovery(spec):
    discovery = DISCOVERY[(spec.service, spec.version)]
    method = discovery["methods"][spec.id]
    discovery_params = method["parameters"]

    assert set(spec.scopes) <= _discovery_scopes(spec, method)
    for param in spec.params:
        assert param.name in discovery_params, param.name
        assert param.type == discovery_params[param.name]["type"], param.name
        assert param.location == discovery_params[param.name]["location"], param.name
    required = {name for name, p in discovery_params.items() if p.get("required")}
    assert set(spec.required_params) == required

    if method["request"] is None:
        assert not spec.allowed_body_fields
    else:
        _assert_body_matches_schema(spec.body_fields, method["request"], discovery)

    if method["response"] is None or not discovery["schemas"][method["response"]]:
        assert not spec.response_fields
        return
    response_schema = discovery["schemas"][method["response"]]
    if spec.response_items_key:
        # List responses are recorded as "array:<ItemSchema>" in the fixture.
        item_schema_name = response_schema[spec.response_items_key].split(":", 1)[1]
        item_schema_name = UNTYPED_ITEMS.get(method["response"], item_schema_name)
        response_schema = discovery["schemas"][item_schema_name]
    assert spec.response_fields
    assert set(spec.response_fields) <= set(response_schema)


def test_patterns_and_enums_accept_valid_values():
    validate_call(
        get_operation("directory.orgunits.get"),
        {"customerId": "C01", "orgUnitPath": "id:03ph8a2z1"},
        None,
    )
    validate_call(
        get_operation("directory.orgunits.insert"),
        {"customerId": "C01"},
        {"name": "Sales", "parentOrgUnitId": "id:03ph8a2z1"},
    )
    validate_call(
        get_operation("directory.members.insert"),
        {"groupKey": "g1"},
        {"email": "u@op.example", "role": "MEMBER"},
    )
    validate_call(
        get_operation("directory.users.update"),
        {"userKey": "u@op.example"},
        {"name": {"givenName": "A", "familyName": "B"}},
    )


# --- Customer boundaries ----------------------------------------------------------

# Parameters that name a customer are always set by the server, or refused.
CUSTOMER_PARAMS = {"customer", "customerId", "customerKey"}
# Parameters that name a user or group must be resolved inside the customer, or
# refused by a deny rule.
KEY_PARAMS = {
    "userKey",
    "groupKey",
    "userId",
    "oldOwnerUserId",
    "newOwnerUserId",
    "accountId",
    "groupUniqueId",
}


@pytest.mark.parametrize("spec", list(iter_operations()), ids=lambda spec: spec.id)
def test_every_operation_declares_a_customer_boundary(spec):
    rules = {(rule.kind, rule.param) for rule in spec.boundary}
    kinds = {rule.kind for rule in spec.boundary}

    assert kinds - {"deny", "role", "member", "role_assignment"}, spec.id
    for param in spec.params:
        if param.name in CUSTOMER_PARAMS:
            assert {("customer", param.name), ("deny", param.name)} & rules
        if param.name in KEY_PARAMS:
            assert {
                (kind, param.name) for kind in ("user", "account", "group", "deny")
            } & rules, param.name


def test_boundaries_resolve_body_references():
    members = get_operation("directory.members.insert").boundary
    assignment = get_operation("directory.roleAssignments.insert").boundary

    assert [(r.kind, r.param, r.field, r.as_) for r in members] == [
        ("group", "groupKey", None, "id"),
        ("user", None, "email", "email"),
    ]
    assert ("user", "assignedTo") in {(r.kind, r.field) for r in assignment}
    assert ("role", "roleId") in {(r.kind, r.field) for r in assignment}


def _doc(boundary, params=None, body=None):
    return {
        "service": "admin",
        "version": "directory_v1",
        "discovery": {"revision": "1"},
        "operations": {
            "directory.x.get": {
                "resource_path": ["x"],
                "method": "get",
                "scopes": [],
                "risk": "read",
                "params": params or {"userKey": {"type": "string", "location": "path"}},
                "body": body or {},
                "boundary": boundary,
            }
        },
    }


@pytest.mark.parametrize(
    "doc",
    [
        _doc([]),
        _doc([{"kind": "tenancy"}]),
        _doc([{"kind": "tenant", "param": "userKey"}]),
        _doc([{"kind": "account", "field": "x.missing"}], body={"x": "object"}),
        _doc([{"kind": "response_customer", "field": "customerId"}]),
        # "all" is accepted only when the call is also bound to the customer.
        _doc([{"kind": "user", "param": "userKey", "all": True}]),
        _doc(
            [
                {"kind": "customer", "param": "customerId"},
                {"kind": "account", "param": "userKey", "all": True},
            ],
            params={
                "userKey": {"type": "string", "location": "path"},
                "customerId": {"type": "string", "location": "query"},
            },
        ),
        _doc([{"kind": "user", "param": "missing"}]),
        _doc([{"kind": "user", "field": "missing"}]),
        _doc([{"kind": "user", "param": "userKey", "as": "name"}]),
        _doc([{"kind": "user", "param": "userKey", "field": "userKey"}]),
        _doc([{"kind": "member", "param": "userKey"}]),
        _doc([{"kind": "deny", "param": "userKey"}]),
        _doc(
            [{"kind": "global"}],
            params={
                "orgUnitPath": {"type": "string", "location": "path", "pattern": "id:"}
            },
        ),
        _doc([{"kind": "global"}], body={"x": {"type": "string", "pattern": "^a"}}),
    ],
)
def test_registry_refuses_unsafe_boundary_declarations(doc):
    with pytest.raises(ValueError):
        _load_operations(doc)


def test_registry_accepts_nested_and_array_body_paths():
    body = {
        "grant": {"type": "object", "fields": {"accountId": "string"}},
        "holders": {
            "type": "array",
            "items": {"type": "object", "fields": {"accountId": "string"}},
        },
        "emails": {"type": "array", "items": "string"},
    }
    rules = [
        {"kind": "tenant"},
        {"kind": "account", "field": "grant.accountId"},
        {"kind": "account", "field": "holders.accountId"},
        {"kind": "account", "field": "emails", "as": "email"},
    ]

    spec = _load_operations(_doc(rules, params={}, body=body))["directory.x.get"]

    assert [r.field for r in spec.boundary if r.kind == "account"] == [
        "grant.accountId",
        "holders.accountId",
        "emails",
    ]
