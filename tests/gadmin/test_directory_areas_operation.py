"""Directory device, Chrome printer, and calendar resource operations cross the
real MCP boundary through admin_operation.

Only the transport is faked: the operation client is the installed Directory
client, so each test shows the request that would reach Google. Every ID a
write names is read afresh inside the customer at proposal and again at
confirmation, and each write is sent once by a matching confirmation."""

import json

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
from tests.gadmin.fake_http import directory_client
from tests.gadmin.test_admin_operation import (  # noqa: F401 - ws is a fixture
    ALL_ADMIN,
    call,
    confirm,
    operation,
    propose,
    ws,
)

AREAS = [
    "admin-directory-devices",
    "admin-directory-resources",
    "admin-directory-printers",
]
BASE = "/admin/directory/v1"
CHROMEOS = f"{BASE}/customer/C01/devices/chromeos"
MOBILE = f"{BASE}/customer/C01/devices/mobile"
PRINTERS = f"{BASE}/customers/C01/chrome/printers"
RESOURCES = f"{BASE}/customer/C01/resources"
STATUS = "CHANGE_CHROME_OS_DEVICE_STATUS_ACTION_"

CHROMEBOOK = {
    "deviceId": "D1",
    "serialNumber": "SN1",
    "status": "ACTIVE",
    "annotatedLocation": "Lobby",
    "macAddress": "001122334455",
    "recentUsers": [{"email": "someone@op.example"}],
    "lastKnownNetwork": [{"ipAddress": "10.0.0.7"}],
}
PHONE = {
    "resourceId": "M1",
    "model": "Pixel",
    "status": "APPROVED",
    "imei": "356938035643809",
    "serialNumber": "SERIAL9",
    "wifiMacAddress": "00:11:22:33:44:55",
}
PRINTER = {
    "name": "customers/C01/chrome/printers/P1",
    "displayName": "Lobby",
    "uri": "ipp://user:secret@10.0.0.1",
}


@pytest.fixture
def areas(ws, monkeypatch):  # noqa: F811 - the imported fixture
    client, http = directory_client(
        {
            ("GET", f"{CHROMEOS}/D1"): CHROMEBOOK,
            ("GET", f"{CHROMEOS}/D2"): {"deviceId": "D2"},
            ("PATCH", f"{CHROMEOS}/D1"): CHROMEBOOK,
            ("POST", f"{BASE}/customer/C01/devices/chromeos:batchChangeStatus"): {
                "changeChromeOsDeviceStatusResults": [
                    {"deviceId": "D1", "response": {}},
                    {"deviceId": "D2", "error": {"code": 9, "message": "free text"}},
                ]
            },
            ("POST", f"{CHROMEOS}/moveDevicesToOu"): {},
            ("GET", f"{MOBILE}/M1"): PHONE,
            ("POST", f"{MOBILE}/M1/action"): {},
            ("GET", f"{PRINTERS}/P1"): PRINTER,
            ("GET", f"{PRINTERS}/P2"): {"name": "customers/C01/chrome/printers/P2"},
            ("PATCH", f"{PRINTERS}/P1"): PRINTER,
            ("POST", f"{PRINTERS}:batchDeletePrinters"): {
                "printerIds": ["P1"],
                "failedPrinters": [
                    {"printerId": "P2", "errorCode": "NOT_FOUND", "printer": PRINTER}
                ],
            },
            ("GET", f"{RESOURCES}/buildings/B1"): {"buildingId": "B1"},
            ("DELETE", f"{RESOURCES}/buildings/B1"): {},
            ("GET", f"{RESOURCES}/features/Projector"): {"name": "Projector"},
            ("POST", f"{RESOURCES}/calendars"): {"resourceId": "R2"},
            ("POST", f"{RESOURCES}/features/Projector/rename"): {},
        }
    )
    apis = ws.apis
    monkeypatch.setattr(
        ws, "apis", lambda: {**apis(), ("admin", "directory_v1"): client}
    )
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, *AREAS])
    ws.directory.add_org_unit("OU1")
    ws.directory.add_org_unit("OU_OTHER", customer_id="C_OTHER")
    ws.http = http
    return ws


def _writes(areas) -> list:
    return [r for r in areas.http.requests if r[0] != "GET"]


def _level(monkeypatch, service, level):
    monkeypatch.setattr(permissions, "_PERMISSIONS", {service: level})


# --- ChromeOS devices ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_chromeos_read_omits_addresses_users_and_networks(areas):
    result, text = await operation("directory.chromeosdevices.get", {"deviceId": "D1"})

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "deviceId": "D1",
        "serialNumber": "SN1",
        "status": "ACTIVE",
        "annotatedLocation": "Lobby",
    }
    assert areas.http.requests == [("GET", f"{CHROMEOS}/D1", {}, None)]
    assert areas.requested_scopes[-1] == [
        scopes.ADMIN_DIRECTORY_DEVICE_CHROMEOS_READONLY_SCOPE
    ]


@pytest.mark.asyncio
async def test_status_change_is_proposed_then_confirmed_once(areas):
    body = {
        "deviceIds": ["D1", "D2"],
        "changeChromeOsDeviceStatusAction": STATUS + "DISABLE",
    }
    proposal = await propose(
        "admin.customer.devices.chromeos.batchChangeStatus", body=body
    )

    assert proposal["risk"] == "destructive"
    assert _writes(areas) == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [
        ("POST", f"{BASE}/customer/C01/devices/chromeos:batchChangeStatus", {}, body)
    ]
    # Only device IDs and numeric error codes come back.
    assert json.loads(text)["result"] == {
        "changeChromeOsDeviceStatusResults": [
            {"deviceId": "D1"},
            {"deviceId": "D2", "error": {"code": 9}},
        ]
    }
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(_writes(areas)) == 1
    assert [r["outcome"] for r in areas.audit()] == ["proposed", "succeeded"]


@pytest.mark.asyncio
async def test_a_batch_with_one_foreign_device_is_refused_whole(areas):
    result, text = await operation(
        "directory.chromeosdevices.moveDevicesToOu",
        {"orgUnitPath": "id:OU1"},
        {"deviceIds": ["D1", "D_OTHER"]},
    )
    assert result.is_error and "Could not verify the resource" in text

    result, text = await operation(
        "directory.chromeosdevices.moveDevicesToOu",
        {"orgUnitPath": "id:OU_OTHER"},
        {"deviceIds": ["D1"]},
    )
    assert result.is_error and "organizational unit" in text
    assert areas.audit() == [] and _writes(areas) == []


@pytest.mark.asyncio
async def test_devices_move_to_a_verified_org_unit(areas):
    proposal = await propose(
        "directory.chromeosdevices.moveDevicesToOu",
        {"orgUnitPath": "id:OU1"},
        {"deviceIds": ["D1", "D2"]},
    )
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [
        (
            "POST",
            f"{CHROMEOS}/moveDevicesToOu",
            {"orgUnitPath": "id:OU1"},
            {"deviceIds": ["D1", "D2"]},
        )
    ]


@pytest.mark.asyncio
async def test_device_removed_before_confirmation_is_not_changed(areas):
    proposal = await propose(
        "directory.chromeosdevices.patch", {"deviceId": "D1"}, {"notes": "Spare"}
    )
    areas.http.handlers[("GET", f"{CHROMEOS}/D1")] = 404

    result, text = await confirm(proposal)

    assert result.is_error and "Could not verify the resource" in text
    assert _writes(areas) == []


# --- mobile devices -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_mobile_read_omits_hardware_identifiers(areas):
    result, text = await operation("directory.mobiledevices.get", {"resourceId": "M1"})

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "resourceId": "M1",
        "model": "Pixel",
        "status": "APPROVED",
    }
    assert "356938035643809" not in text and "SERIAL9" not in text


@pytest.mark.asyncio
async def test_mobile_wipe_needs_the_destructive_level(areas, monkeypatch):
    wipe = {"action": "admin_account_wipe"}
    _level(monkeypatch, "admin-directory-devices", "manage")
    result, text = await operation(
        "directory.mobiledevices.action", {"resourceId": "M1"}, wipe
    )
    assert result.is_error and "destructive" in text
    proposal = await propose(
        "directory.mobiledevices.action", {"resourceId": "M1"}, {"action": "approve"}
    )
    assert proposal["risk"] == "manage"

    monkeypatch.setattr(permissions, "_PERMISSIONS", None)
    proposal = await propose(
        "directory.mobiledevices.action", {"resourceId": "M1"}, wipe
    )
    assert proposal["risk"] == "destructive"
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [("POST", f"{MOBILE}/M1/action", {}, wipe)]


@pytest.mark.asyncio
async def test_foreign_mobile_devices_and_free_form_actions_are_refused(areas):
    result, text = await operation(
        "directory.mobiledevices.delete", {"resourceId": "M_OTHER"}
    )
    assert result.is_error and "Could not verify the resource" in text

    result, text = await operation(
        "directory.mobiledevices.action", {"resourceId": "M1"}, {"action": "reboot"}
    )
    assert result.is_error
    assert areas.audit() == [] and _writes(areas) == []


@pytest.mark.asyncio
async def test_device_writes_are_refused_in_read_only_mode(areas, monkeypatch):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    result, text = await operation(
        "directory.chromeosdevices.patch", {"deviceId": "D1"}, {"notes": "x"}
    )
    assert result.is_error and "allows up to read" in text
    result, text = await operation("directory.mobiledevices.get", {"resourceId": "M1"})
    assert not result.is_error, text
    assert _writes(areas) == []


@pytest.mark.asyncio
async def test_device_operations_need_the_devices_service(areas, monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN])

    result, text = await operation("directory.chromeosdevices.get", {"deviceId": "D1"})

    assert result.is_error and "admin-directory-devices" in text
    assert areas.http.requests == []


# --- Chrome printers ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_printer_read_never_returns_the_uri(areas):
    result, text = await operation(
        "admin.customers.chrome.printers.get",
        {"name": "customers/my_customer/chrome/printers/P1"},
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "name": "customers/C01/chrome/printers/P1",
        "displayName": "Lobby",
    }
    assert "secret" not in text and "10.0.0.1" not in text
    assert areas.http.requests == [("GET", f"{PRINTERS}/P1", {}, None)]


@pytest.mark.asyncio
async def test_printer_patch_is_bound_to_the_customer_and_confirmed_once(areas):
    params = {
        "name": "customers/my_customer/chrome/printers/P1",
        "updateMask": "displayName",
    }
    proposal = await propose(
        "admin.customers.chrome.printers.patch", params, {"displayName": "Front"}
    )
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [
        (
            "PATCH",
            f"{PRINTERS}/P1",
            {"updateMask": "displayName"},
            {"displayName": "Front"},
        )
    ]
    assert "secret" not in text
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text

    result, text = await operation(
        "admin.customers.chrome.printers.patch",
        {"name": "customers/C_OTHER/chrome/printers/P1"},
        {"displayName": "x"},
    )
    assert result.is_error and "another customer" in text
    assert len(_writes(areas)) == 1


@pytest.mark.asyncio
async def test_printer_batch_delete_verifies_every_id(areas, monkeypatch):
    operation_id = "admin.customers.chrome.printers.batchDeletePrinters"
    result, text = await operation(operation_id, body={"printerIds": ["P1", "P_OTHER"]})
    assert result.is_error and "Could not verify the resource" in text
    assert areas.audit() == [] and _writes(areas) == []

    _level(monkeypatch, "admin-directory-printers", "manage")
    result, text = await operation(operation_id, body={"printerIds": ["P1"]})
    assert result.is_error and "destructive" in text

    monkeypatch.setattr(permissions, "_PERMISSIONS", None)
    proposal = await propose(operation_id, body={"printerIds": ["P1", "P2"]})
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [
        ("POST", f"{PRINTERS}:batchDeletePrinters", {}, {"printerIds": ["P1", "P2"]})
    ]
    # Failed printers come back without the printer and its URI.
    assert json.loads(text)["result"] == {
        "printerIds": ["P1"],
        "failedPrinters": [{"printerId": "P2", "errorCode": "NOT_FOUND"}],
    }


@pytest.mark.asyncio
async def test_printer_creation_is_excluded(areas):
    result, text = await operation(
        "admin.customers.chrome.printers.create",
        {"parent": "customers/C01"},
        {"displayName": "x", "uri": "ipp://10.0.0.1"},
    )

    assert result.is_error and "excluded (caller-url)" in text
    assert areas.http.requests == [] and areas.audit() == []


# --- buildings, calendar resources, and features ------------------------------------


@pytest.mark.asyncio
async def test_calendar_resource_insert_verifies_building_and_features(areas):
    body = {
        "resourceId": "R2",
        "resourceName": "Board room",
        "buildingId": "B1",
        "featureInstances": [{"feature": {"name": "Projector"}}],
    }
    proposal = await propose("directory.resources.calendars.insert", body=body)
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [("POST", f"{RESOURCES}/calendars", {}, body)]
    assert areas.requested_scopes[-1] == [
        scopes.ADMIN_DIRECTORY_RESOURCE_CALENDAR_SCOPE,
        scopes.ADMIN_DIRECTORY_RESOURCE_CALENDAR_READONLY_SCOPE,
    ]

    result, text = await operation(
        "directory.resources.calendars.insert",
        body={**body, "buildingId": "B_OTHER"},
    )
    assert result.is_error and "Could not verify the resource" in text
    assert len(_writes(areas)) == 1


@pytest.mark.asyncio
async def test_feature_rename_reads_the_feature_first(areas, monkeypatch):
    _level(monkeypatch, "admin-directory-resources", "readonly")
    result, text = await operation(
        "directory.resources.features.rename",
        {"oldName": "Projector"},
        {"newName": "Beamer"},
    )
    assert result.is_error and "allows up to read" in text

    monkeypatch.setattr(permissions, "_PERMISSIONS", None)
    proposal = await propose(
        "directory.resources.features.rename",
        {"oldName": "Projector"},
        {"newName": "Beamer"},
    )
    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(areas) == [
        ("POST", f"{RESOURCES}/features/Projector/rename", {}, {"newName": "Beamer"})
    ]


@pytest.mark.asyncio
async def test_building_removed_before_confirmation_is_not_deleted(areas):
    proposal = await propose(
        "directory.resources.buildings.delete", {"buildingId": "B1"}
    )
    assert proposal["risk"] == "destructive"
    areas.http.handlers[("GET", f"{RESOURCES}/buildings/B1")] = 404

    result, text = await confirm(proposal)

    assert result.is_error and "Could not verify the resource" in text
    assert _writes(areas) == []


@pytest.mark.asyncio
async def test_capabilities_list_the_new_areas_and_their_exclusions(areas):
    result, text = await call("list_admin_capabilities", user_google_email="x@y.z")

    assert not result.is_error, text
    assert "directory.mobiledevices.action" in text
    assert "admin.customers.chrome.printers.create (caller-url)" in text
    assert "deferred-family" not in text
