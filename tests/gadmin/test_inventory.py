"""Every method of the pinned admin APIs is either registered or excluded
with a reason, and registered operations line up with the pinned snapshots and
with the static discovery documents the Google client is built from."""

import json

import pytest
from googleapiclient.discovery_cache import get_static_doc

from gadmin.client import pinned_document
from gadmin.registry import (
    EXCLUSION_CATEGORIES,
    excluded_operations,
    get_exclusion,
    iter_operations,
)
from tests.gadmin.test_registry import DISCOVERY

# The operation ID prefixes each API family uses in the registry. Directory's
# newer device and Chrome printer methods are named "admin.customer(s).*".
PREFIXES = {
    ("admin", "directory_v1"): {"directory", "admin"},
    ("admin", "datatransfer_v1"): {"datatransfer"},
    ("admin", "reports_v1"): {"reports"},
    ("licensing", "v1"): {"licensing"},
    ("vault", "v1"): {"vault"},
    ("alertcenter", "v1beta1"): {"alertcenter"},
    ("groupssettings", "v1"): {"groupsSettings"},
    ("cloudidentity", "v1"): {"cloudidentity"},
    ("cloudidentity", "v1beta1"): {"cloudidentity"},
    ("chromemanagement", "v1"): {"chromemanagement"},
    ("chromepolicy", "v1"): {"chromepolicy"},
    ("accesscontextmanager", "v1"): {"accesscontextmanager"},
    ("admin", "contacts_v1"): {"admin"},
    ("gmail", "v1"): {"gmail"},
}
# Reads Google serves as POST because their filters travel in a request body.
POST_READS = {
    "chromepolicy.customers.policies.resolve",
    "chromepolicy.customers.policies.groups.listGroupPriorityOrdering",
}
# A beta API whose methods shared with the stable version are served by it; only
# its beta-only methods belong to its own registry file.
SERVED_BY = {("cloudidentity", "v1beta1"): ("cloudidentity", "v1")}


@pytest.mark.parametrize("api", list(DISCOVERY), ids=lambda api: "_".join(api))
def test_every_pinned_method_is_registered_or_excluded(api):
    pinned = set(DISCOVERY[api]["methods"])
    if api in SERVED_BY:
        pinned -= set(DISCOVERY[SERVED_BY[api]]["methods"])
    registered = {
        spec.id for spec in iter_operations() if (spec.service, spec.version) == api
    }
    excluded = {e.id for e in excluded_operations() if (e.service, e.version) == api}

    assert not registered & excluded
    assert registered | excluded == pinned
    assert all(
        e.discovery_revision == DISCOVERY[api]["revision"]
        for e in excluded_operations()
        if (e.service, e.version) == api
    )


@pytest.mark.parametrize(
    "exclusion",
    excluded_operations(),
    ids=lambda e: f"{e.id}@{e.service}_{e.version}",
)
def test_exclusions_name_a_category_and_a_concrete_reason(exclusion):
    assert exclusion.category in EXCLUSION_CATEGORIES
    assert len(exclusion.reason) >= 40 and exclusion.reason.endswith(".")


def test_excluded_methods_include_the_known_unsafe_ones():
    expected = {
        "directory.users.insert": "secret-in-payload",
        "directory.users.undelete": "unguarded-target",
        "directory.verificationCodes.list": "secret-exposure",
        "directory.users.watch": "push-channel",
        "licensing.licenseAssignments.update": "replacement-variant",
        "reports.activities.watch": "push-channel",
        "reports.entityUsageReports.get": "deprecated",
        "alertcenter.updateSettings": "push-channel",
        "vault.matters.holds.update": "replacement-variant",
        "groupsSettings.groups.update": "replacement-variant",
        "directory.chromeosdevices.update": "replacement-variant",
        "directory.customers.update": "replacement-variant",
        "directory.domains.insert": "tenant-config",
        "directory.domainAliases.delete": "tenant-config",
        "directory.roles.update": "replacement-variant",
        "directory.schemas.update": "replacement-variant",
        "gmail.users.settings.filters.create": "mailbox-setting",
        "gmail.users.settings.updateAutoForwarding": "mailbox-setting",
        "directory.resources.buildings.update": "replacement-variant",
        "directory.resources.calendars.update": "replacement-variant",
        "directory.resources.features.update": "replacement-variant",
        "admin.customers.chrome.printers.create": "caller-url",
        "admin.customers.chrome.printers.batchCreatePrinters": "caller-url",
        "admin.customers.chrome.printServers.create": "caller-url",
        "admin.customers.chrome.printServers.batchCreatePrintServers": "caller-url",
        "directory.chromeosdevices.action": "deprecated",
        "chromemanagement.customers.connectorConfigs.get": "secret-exposure",
        "chromemanagement.customers.connectorConfigs.create": "secret-in-payload",
        "chromemanagement.customers.certificateProvisioningProcesses.uploadCertificate": (
            "secret-in-payload"
        ),
        "chromemanagement.customers.certificateProvisioningProcesses.get": (
            "undocumented-scope"
        ),
        "chromemanagement.operations.list": "undocumented-scope",
        "chromemanagement.customers.telemetry.notificationConfigs.create": (
            "push-channel"
        ),
        "chromemanagement.customers.apps.web.get": "caller-url",
        "chromemanagement.customers.reports.findSaasUsageProfiles": (
            "sensitive-content"
        ),
        "chromepolicy.customers.policies.networks.defineNetwork": "secret-in-payload",
        "chromepolicy.customers.policies.networks.defineCertificate": (
            "secret-in-payload"
        ),
        "chromepolicy.media.upload": "unvalidated-schema",
        "accesscontextmanager.accessPolicies.accessLevels.create": "console-only",
        "accesscontextmanager.accessPolicies.accessLevels.replaceAll": "console-only",
        "accesscontextmanager.accessPolicies.setIamPolicy": "cloud-resource",
        "accesscontextmanager.accessPolicies.servicePerimeters.list": "cloud-resource",
        "accesscontextmanager.organizations.gcpUserAccessBindings.list": (
            "cloud-resource"
        ),
        "accesscontextmanager.accessPolicies.authorizedOrgsDescs.list": (
            "unguarded-target"
        ),
        "accesscontextmanager.operations.get": "unguarded-target",
    }
    for operation_id, category in expected.items():
        assert get_exclusion(operation_id).category == category, operation_id
    # The shared channel-stop method appears in two APIs; both are excluded.
    channel_stops = {
        e.version: e.category
        for e in excluded_operations()
        if e.id == "admin.channels.stop"
    }
    assert channel_stops == {
        "directory_v1": "push-channel",
        "reports_v1": "push-channel",
    }
    assert get_exclusion("directory.users.get") is None
    # Every Directory family has been assessed; none is deferred wholesale.
    assert not [e.id for e in excluded_operations() if e.category == "deferred-family"]


def _client_doc(spec) -> dict:
    """The discovery document the client for ``spec`` is built from: the pinned
    package copy for Cloud Identity and Chrome Management, else the installed
    static document."""
    document = pinned_document(spec.service, spec.version)
    return json.loads(document or get_static_doc(spec.service, spec.version))


def _static_method(spec):
    doc = _client_doc(spec)
    node = doc
    for resource in spec.resource_path:
        node = node["resources"][resource]
    return node["methods"][spec.method]


@pytest.mark.parametrize("spec", list(iter_operations()), ids=lambda spec: spec.id)
def test_registered_operation_is_addressed_by_its_discovery_id(spec):
    method = DISCOVERY[(spec.service, spec.version)]["methods"][spec.id]
    prefixes = PREFIXES[(spec.service, spec.version)]

    assert spec.id.split(".")[0] in prefixes and spec.id.endswith(f".{spec.method}")
    # GET methods and the listed POST reads are reads; every other verb
    # changes state.
    is_read = method["httpMethod"] == "GET" or spec.id in POST_READS
    assert is_read == (spec.risk == "read")
    # Each registered parameter must exist in the document the client is
    # built from.
    static = _static_method(spec)
    assert static["id"] == spec.id
    assert {p.name for p in spec.params} <= set(static.get("parameters", {}))


def _static_enums(doc, schema, fields, prefix=""):
    """Yield (path, registered enum, static enum) for every enum in a body."""
    for field in fields:
        # A string map (Group labels) declares its values once.
        props = schema.get("properties")
        prop = props[field.name] if props else schema["additionalProperties"]
        if prop.get("type") == "any":
            continue  # untyped in discovery (calendar resource feature instances)
        registered, node = field, prop
        if field.type == "array":
            registered, node = field.items, prop["items"]
        if "$ref" in node:
            node = doc["schemas"][node["$ref"]]
        if registered.enum:
            yield prefix + field.name, registered.enum, node.get("enum")
        if registered.fields:
            yield from _static_enums(
                doc, node, registered.fields, f"{prefix}{field.name}."
            )


@pytest.mark.parametrize("spec", list(iter_operations()), ids=lambda spec: spec.id)
def test_registered_enums_are_accepted_by_the_installed_client(spec):
    # The client refuses values outside an enum the static document declares,
    # so a registered value it does not know would fail every call.
    doc = _client_doc(spec)
    static = _static_method(spec)
    for param in spec.params:
        known = static["parameters"][param.name].get("enum")
        assert not (param.enum and known) or set(param.enum) <= set(known), param.name
    if spec.body_fields:
        schema = doc["schemas"][static["request"]["$ref"]]
        for path, registered, known in _static_enums(doc, schema, spec.body_fields):
            assert known is None or set(registered) <= set(known), path
