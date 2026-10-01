"""Directory device, Chrome printer, and calendar resource IDs stay inside the
verified customer.

The customer is always set by the server. Every device, printer, print server,
building, calendar resource, and feature a write names, including each element
of a batch, is read afresh inside the customer with a real Directory client
first; one unknown or foreign ID refuses the whole call."""

import pytest

from gadmin.auth import admin_service_for
from gadmin.boundary import resolve_call
from gadmin.guard import AdminBoundaryError, AdminContext
from gadmin.registry import (
    InvalidOperationInput,
    _load_operations,
    classify_risk,
    get_operation,
)
from tests.gadmin.fake_directory import FakeDirectory
from tests.gadmin.fake_http import directory_client

CONTEXT = AdminContext(
    actor_email="admin@op.example", customer_id="C01", actor_id="U_ADMIN"
)
BASE = "/admin/directory/v1"
CHROMEOS = f"{BASE}/customer/C01/devices/chromeos"
MOBILE = f"{BASE}/customer/C01/devices/mobile"
PRINTERS = f"{BASE}/customers/C01/chrome/printers"
SERVERS = f"{BASE}/customers/C01/chrome/printServers"
RESOURCES = f"{BASE}/customer/C01/resources"


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_org_unit("OU1")
    fake.add_org_unit("OU_OTHER", customer_id="C_OTHER")
    return fake


@pytest.fixture
def client():
    # Only IDs of the verified customer are found; anything else is a 404.
    client, http = directory_client(
        {
            ("GET", f"{CHROMEOS}/D1"): {"deviceId": "D1"},
            ("GET", f"{CHROMEOS}/D2"): {"deviceId": "D2"},
            ("GET", f"{MOBILE}/M1"): {"resourceId": "M1"},
            ("GET", f"{PRINTERS}/P1"): {"name": "customers/C01/chrome/printers/P1"},
            ("GET", f"{PRINTERS}/P2"): {"name": "customers/C01/chrome/printers/P2"},
            ("GET", f"{SERVERS}/S1"): {"name": "customers/C01/chrome/printServers/S1"},
            ("GET", f"{RESOURCES}/buildings/B1"): {"buildingId": "B1"},
            ("GET", f"{RESOURCES}/features/Projector"): {"name": "Projector"},
            ("GET", f"{RESOURCES}/calendars/R1"): {"resourceId": "R1"},
        }
    )
    client.http = http
    return client


def _resolve(directory, operation_id, params=None, body=None, client=None):
    return resolve_call(
        CONTEXT, get_operation(operation_id), params, body, directory, client
    )


def _reads(client) -> list[str]:
    assert all(verb == "GET" for verb, *_ in client.http.requests)
    return [path for _, path, *_ in client.http.requests]


# --- customer ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operation_id", "param"),
    [
        ("directory.chromeosdevices.list", "customerId"),
        ("directory.mobiledevices.list", "customerId"),
        ("admin.customer.devices.chromeos.countChromeOsDevices", "customerId"),
        ("directory.resources.buildings.list", "customer"),
        ("admin.customers.chrome.printers.list", "parent"),
        ("admin.customers.chrome.printServers.list", "parent"),
    ],
)
def test_customer_is_set_by_the_server_and_never_accepted(
    directory, operation_id, param
):
    call = _resolve(directory, operation_id)
    expected = "customers/C01" if param == "parent" else "C01"

    assert call.params[param] == expected
    with pytest.raises(InvalidOperationInput, match="set by the server"):
        _resolve(directory, operation_id, {param: "C_OTHER"})


@pytest.mark.parametrize("customer", ["my_customer", "C01", "01"])
def test_printer_names_are_rewritten_to_the_verified_customer(
    directory, client, customer
):
    call = _resolve(
        directory,
        "admin.customers.chrome.printers.delete",
        {"name": f"customers/{customer}/chrome/printers/P1"},
        None,
        client,
    )

    assert call.params["name"] == "customers/C01/chrome/printers/P1"
    assert call.risk == "destructive"
    assert _reads(client) == [f"{PRINTERS}/P1"]


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("customers/C_OTHER/chrome/printers/P1", AdminBoundaryError),
        ("customers/C01/chrome/printers/P_OTHER", AdminBoundaryError),
        ("customers/C01/chrome/printServers/S1", InvalidOperationInput),
        ("customers/C01/chrome/printers/P1/x", InvalidOperationInput),
    ],
)
def test_printers_outside_the_customer_are_refused(directory, client, name, error):
    with pytest.raises(error):
        _resolve(
            directory,
            "admin.customers.chrome.printers.patch",
            {"name": name},
            {"displayName": "Lobby"},
            client,
        )


# --- single device, printer, and resource IDs ------------------------------------------


@pytest.mark.parametrize(
    ("operation_id", "params", "body", "read"),
    [
        (
            "directory.chromeosdevices.patch",
            {"deviceId": "D1"},
            {"annotatedLocation": "Lobby"},
            f"{CHROMEOS}/D1",
        ),
        (
            "directory.mobiledevices.action",
            {"resourceId": "M1"},
            {"action": "approve"},
            f"{MOBILE}/M1",
        ),
        ("directory.mobiledevices.delete", {"resourceId": "M1"}, None, f"{MOBILE}/M1"),
        (
            "admin.customers.chrome.printServers.patch",
            {"name": "customers/my_customer/chrome/printServers/S1"},
            {"displayName": "Main"},
            f"{SERVERS}/S1",
        ),
        (
            "directory.resources.buildings.patch",
            {"buildingId": "B1"},
            {"buildingName": "HQ"},
            f"{RESOURCES}/buildings/B1",
        ),
        (
            "directory.resources.calendars.delete",
            {"calendarResourceId": "R1"},
            None,
            f"{RESOURCES}/calendars/R1",
        ),
        (
            "directory.resources.features.rename",
            {"oldName": "Projector"},
            {"newName": "Beamer"},
            f"{RESOURCES}/features/Projector",
        ),
    ],
)
def test_write_targets_are_read_afresh_inside_the_customer(
    directory, client, operation_id, params, body, read
):
    _resolve(directory, operation_id, params, body, client)

    assert _reads(client) == [read]


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        ("directory.chromeosdevices.patch", {"deviceId": "D_OTHER"}, {"notes": "x"}),
        ("directory.mobiledevices.delete", {"resourceId": "M_OTHER"}, None),
        ("directory.resources.buildings.delete", {"buildingId": "B_OTHER"}, None),
        ("directory.resources.features.delete", {"featureKey": "Other"}, None),
    ],
)
def test_ids_outside_the_customer_are_refused(
    directory, client, operation_id, params, body
):
    with pytest.raises(AdminBoundaryError, match="Could not verify the resource"):
        _resolve(directory, operation_id, params, body, client)
    _reads(client)


def test_resource_checks_fail_closed_without_a_client(directory):
    with pytest.raises(AdminBoundaryError, match="Could not verify the resource"):
        _resolve(directory, "directory.mobiledevices.delete", {"resourceId": "M1"})


def test_calendar_resources_verify_their_building_and_every_feature(directory, client):
    body = {
        "resourceId": "R2",
        "resourceName": "Board room",
        "buildingId": "B1",
        "capacity": 12,
        "featureInstances": [{"feature": {"name": "Projector"}}],
    }
    _resolve(directory, "directory.resources.calendars.insert", None, body, client)

    assert _reads(client) == [
        f"{RESOURCES}/buildings/B1",
        f"{RESOURCES}/features/Projector",
    ]
    body["featureInstances"].append({"feature": {"name": "Unknown"}})
    with pytest.raises(AdminBoundaryError):
        _resolve(directory, "directory.resources.calendars.insert", None, body, client)


# --- batches ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operation_id", "field", "ids", "reads"),
    [
        (
            "admin.customers.chrome.printers.batchDeletePrinters",
            "printerIds",
            ["P1", "P2"],
            [f"{PRINTERS}/P1", f"{PRINTERS}/P2"],
        ),
        (
            "admin.customers.chrome.printServers.batchDeletePrintServers",
            "printServerIds",
            ["S1"],
            [f"{SERVERS}/S1"],
        ),
        (
            "admin.customer.devices.chromeos.batchChangeStatus",
            "deviceIds",
            ["D1", "D2"],
            [f"{CHROMEOS}/D1", f"{CHROMEOS}/D2"],
        ),
    ],
)
def test_every_batch_id_is_verified_and_one_foreign_id_refuses_all(
    directory, client, operation_id, field, ids, reads
):
    body = {field: ids}
    if field == "deviceIds":
        body["changeChromeOsDeviceStatusAction"] = (
            "CHANGE_CHROME_OS_DEVICE_STATUS_ACTION_REENABLE"
        )
    call = _resolve(directory, operation_id, None, body, client)

    assert call.body[field] == ids
    assert _reads(client) == reads
    body[field] = [*ids, "X_OTHER"]
    with pytest.raises(AdminBoundaryError):
        _resolve(directory, operation_id, None, body, client)
    _reads(client)


def test_batch_ids_are_bare_ids_and_capped(directory, client):
    operation_id = "admin.customers.chrome.printers.batchDeletePrinters"
    for ids in (["customers/C_OTHER/chrome/printers/P1"], ["P1"] * 51):
        with pytest.raises(InvalidOperationInput):
            _resolve(directory, operation_id, None, {"printerIds": ids}, client)
    assert client.http.requests == []


def test_devices_move_only_to_a_verified_org_unit(directory, client):
    operation_id = "directory.chromeosdevices.moveDevicesToOu"
    call = _resolve(
        directory,
        operation_id,
        {"orgUnitPath": "id:OU1"},
        {"deviceIds": ["D1", "D2"]},
        client,
    )

    assert call.risk == "destructive"
    assert call.params == {"customerId": "C01", "orgUnitPath": "id:OU1"}
    assert [c for c in directory.calls if c[0] == "directory.orgunits.get"] == [
        ("directory.orgunits.get", {"customerId": "C01", "orgUnitPath": "id:OU1"})
    ]
    assert _reads(client) == [f"{CHROMEOS}/D1", f"{CHROMEOS}/D2"]
    with pytest.raises(AdminBoundaryError, match="organizational unit"):
        _resolve(
            directory,
            operation_id,
            {"orgUnitPath": "id:OU_OTHER"},
            {"deviceIds": ["D1"]},
            client,
        )
    # Org unit paths are not accepted; only verified IDs.
    with pytest.raises(InvalidOperationInput):
        _resolve(
            directory,
            operation_id,
            {"orgUnitPath": "/Staff"},
            {"deviceIds": ["D1"]},
            client,
        )


# --- risk -------------------------------------------------------------------------------

_STATUS = "CHANGE_CHROME_OS_DEVICE_STATUS_ACTION_"


@pytest.mark.parametrize(
    ("operation_id", "field", "value", "risk"),
    [
        ("admin.customer.devices.chromeos.batchChangeStatus", "x", "REENABLE", "manage"),
        ("admin.customer.devices.chromeos.batchChangeStatus", "x", "DISABLE", "destructive"),
        ("admin.customer.devices.chromeos.batchChangeStatus", "x", "DEPROVISION", "destructive"),
        ("directory.mobiledevices.action", "action", "approve", "manage"),
        ("directory.mobiledevices.action", "action", "cancel_remote_wipe_then_activate", "manage"),
        ("directory.mobiledevices.action", "action", "block", "destructive"),
        ("directory.mobiledevices.action", "action", "admin_remote_wipe", "destructive"),
        ("directory.mobiledevices.action", "action", "admin_account_wipe", "destructive"),
        ("directory.mobiledevices.action", "action", "cancel_remote_wipe_then_block", "destructive"),
    ],
)  # fmt: skip
def test_device_actions_are_classified_by_their_effect(
    operation_id, field, value, risk
):
    spec = get_operation(operation_id)
    if field == "x":
        field, value = "changeChromeOsDeviceStatusAction", _STATUS + value

    assert classify_risk(spec, {field: value}) == risk


def test_every_device_action_value_is_classified():
    action = get_operation("directory.mobiledevices.action")
    status = get_operation("admin.customer.devices.chromeos.batchChangeStatus")
    safe = {"approve", "cancel_remote_wipe_then_activate", _STATUS + "REENABLE"}

    for spec, field in (
        (action, "action"),
        (status, "changeChromeOsDeviceStatusAction"),
    ):
        values = next(f for f in spec.body_fields if f.name == field).enum
        assert values
        for value in values:
            expected = "manage" if value in safe else "destructive"
            assert classify_risk(spec, {field: value}) == expected, value


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        # Unspecified status actions and free-form commands are not accepted.
        (
            "admin.customer.devices.chromeos.batchChangeStatus",
            None,
            {"deviceIds": ["D1"], "changeChromeOsDeviceStatusAction": _STATUS + "UNSPECIFIED"},
        ),
        ("directory.mobiledevices.action", {"resourceId": "M1"}, {"action": "reboot"}),
        # Moves go only through moveDevicesToOu, which verifies the org unit.
        ("directory.chromeosdevices.patch", {"deviceId": "D1"}, {"orgUnitPath": "/x"}),
        # No printer URI, and no update mask that could change it.
        (
            "admin.customers.chrome.printers.patch",
            {"name": "customers/C01/chrome/printers/P1"},
            {"uri": "ipp://10.0.0.1"},
        ),
        (
            "admin.customers.chrome.printers.patch",
            {"name": "customers/C01/chrome/printers/P1", "updateMask": "uri"},
            {"displayName": "x"},
        ),
        (
            "admin.customers.chrome.printers.patch",
            {"name": "customers/C01/chrome/printers/P1", "clearMask": "uri"},
            {"displayName": "x"},
        ),
        # Feature names cannot smuggle a path or URL.
        ("directory.resources.features.insert", None, {"name": "a/b"}),
        ("directory.resources.features.insert", None, {"name": "http://x"}),
        ("directory.resources.buildings.insert", None, {"buildingId": "B/1", "buildingName": "x"}),
    ],
)  # fmt: skip
def test_restrictive_schemas_refuse_commands_urls_and_moves(
    directory, client, operation_id, params, body
):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, operation_id, params, body, client)
    assert client.http.requests == []


# --- registry and services ----------------------------------------------------------------


def _doc(rule, body):
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
                "params": {},
                "body": body,
                "boundary": [{"kind": "tenant"}, rule],
            }
        },
    }


@pytest.mark.parametrize(
    ("rule", "body"),
    [
        # Only a resource rule reads through a prefix.
        ({"kind": "account", "field": "ids", "prefix": "users/"}, {"ids": {"type": "array", "items": "string"}}),
        # A length cap applies only to arrays.
        ({"kind": "account", "field": "id"}, {"id": {"type": "string", "max_items": 5}}),
    ],
)  # fmt: skip
def test_registry_refuses_prefix_and_cap_outside_their_kind(rule, body):
    with pytest.raises(ValueError):
        _load_operations(_doc(rule, body))


@pytest.mark.parametrize(
    ("operation_id", "service"),
    [
        ("directory.customers.get", "admin-directory"),
        ("directory.orgunits.get", "admin-directory"),
        ("directory.chromeosdevices.get", "admin-directory-devices"),
        ("directory.mobiledevices.delete", "admin-directory-devices"),
        ("admin.customer.devices.chromeos.batchChangeStatus", "admin-directory-devices"),
        ("directory.resources.features.rename", "admin-directory-resources"),
        ("admin.customers.chrome.printers.get", "admin-directory-printers"),
        ("admin.customers.chrome.printServers.delete", "admin-directory-printers"),
        ("cloudidentity.devices.get", "admin-cloudidentity-devices"),
        ("vault.matters.list", "admin-vault"),
    ],
)  # fmt: skip
def test_the_longest_resource_prefix_selects_the_service(operation_id, service):
    assert admin_service_for(get_operation(operation_id)) == service
