"""ChromeOS command requests use the installed client and a verified device."""

import json

import pytest

import auth.scopes as scopes
from tests.gadmin.fake_http import directory_client
from tests.gadmin.test_admin_operation import (  # noqa: F401 - fixture
    ALL_ADMIN,
    confirm,
    operation,
    propose,
    ws,
)

BASE = "/admin/directory/v1/customer/C01/devices/chromeos/D1"


@pytest.fixture
def command_api(ws, monkeypatch):  # noqa: F811 - imported fixture
    client, http = directory_client(
        {
            ("GET", BASE): {"deviceId": "D1", "serialNumber": "private-hardware"},
            ("POST", BASE + ":issueCommand"): {"commandId": "123"},
            ("GET", BASE + "/commands/123"): {
                "commandId": "123",
                "type": "REBOOT",
                "state": "EXECUTED_BY_CLIENT",
                "payload": "private-payload",
                "commandResult": {"result": "SUCCESS", "message": "private-message"},
            },
        }
    )
    apis = ws.apis
    monkeypatch.setattr(
        ws, "apis", lambda: {**apis(), ("admin", "directory_v1"): client}
    )
    monkeypatch.setattr(
        scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, "admin-directory-devices"]
    )
    return ws, http


@pytest.mark.asyncio
async def test_reboot_requires_verified_device_and_one_confirmation(command_api):
    ws, http = command_api  # noqa: F811 - imported fixture
    proposal = await propose(
        "admin.customer.devices.chromeos.issueCommand",
        {"deviceId": "D1"},
        {"commandType": "REBOOT"},
    )
    assert proposal["risk"] == "destructive" and http.requests == [
        ("GET", BASE, {}, None)
    ]
    result, text = await confirm(proposal)
    assert not result.is_error, text
    assert json.loads(text)["result"] == {"commandId": "123"}
    assert [r for r in http.requests if r[0] == "POST"] == [
        ("POST", BASE + ":issueCommand", {}, {"commandType": "REBOOT"})
    ]
    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len([r for r in http.requests if r[0] == "POST"]) == 1
    assert "private-hardware" not in ws.audit_path.read_text()


@pytest.mark.asyncio
async def test_command_read_omits_payload_and_untrusted_results(command_api):
    _, http = command_api
    result, text = await operation(
        "admin.customer.devices.chromeos.commands.get",
        {"deviceId": "D1", "commandId": "123"},
    )
    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "commandId": "123",
        "state": "EXECUTED_BY_CLIENT",
        "type": "REBOOT",
        "commandResult": {"result": "SUCCESS"},
    }
    assert "private" not in text
    assert http.requests == [
        ("GET", BASE, {}, None),
        ("GET", BASE + "/commands/123", {}, None),
    ]


@pytest.mark.asyncio
async def test_command_refuses_foreign_device_and_any_payload(command_api):
    _, http = command_api
    for body in (
        {"commandType": "TAKE_A_SCREENSHOT"},
        {"commandType": "REBOOT", "payload": "{}"},
    ):
        result, text = await operation(
            "admin.customer.devices.chromeos.issueCommand", {"deviceId": "D1"}, body
        )
        assert result.is_error
    result, text = await operation(
        "admin.customer.devices.chromeos.issueCommand",
        {"deviceId": "FOREIGN"},
        {"commandType": "REBOOT"},
    )
    assert result.is_error and "resource" in text
    assert not [r for r in http.requests if r[0] == "POST"]


@pytest.mark.asyncio
async def test_command_read_refuses_a_mismatched_command_id(command_api):
    _, http = command_api
    http.handlers[("GET", BASE + "/commands/123")] = {
        "commandId": "456",
        "state": "EXECUTED_BY_CLIENT",
    }
    result, text = await operation(
        "admin.customer.devices.chromeos.commands.get",
        {"deviceId": "D1", "commandId": "123"},
    )
    assert result.is_error and "Could not verify the ChromeOS command" in text


@pytest.mark.asyncio
async def test_command_read_drops_untrusted_result_text(command_api):
    _, http = command_api
    http.handlers[("GET", BASE + "/commands/123")] = {
        "commandId": "123",
        "type": "private-type",
        "state": "private-state",
        "commandResult": {"result": "private-result", "message": "private-message"},
    }
    result, text = await operation(
        "admin.customer.devices.chromeos.commands.get",
        {"deviceId": "D1", "commandId": "123"},
    )
    assert not result.is_error, text
    assert json.loads(text)["result"] == {"commandId": "123"}
    assert "private" not in text


@pytest.mark.asyncio
async def test_device_missing_before_confirmation_prevents_reboot(command_api):
    _, http = command_api
    proposal = await propose(
        "admin.customer.devices.chromeos.issueCommand",
        {"deviceId": "D1"},
        {"commandType": "REBOOT"},
    )
    http.handlers[("GET", BASE)] = 404
    result, text = await confirm(proposal)
    assert result.is_error and "resource" in text
    assert not [r for r in http.requests if r[0] == "POST"]
