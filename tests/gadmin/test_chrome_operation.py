"""Chrome Management and Chrome Policy operations cross the real MCP boundary
through admin_operation.

Only the transport is faked: each client is the installed client, rebuilt from
the pinned document for Chrome Management exactly as get_admin_service does, so
each test shows the request that would reach Google. Profiles and policy schemas
are read afresh before a write, every policy target is verified in Directory,
and every write is proposed, then sent once by a matching confirmation."""

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
from tests.gadmin.test_chrome_boundary import SCHEMA, SCHEMA_NAME, VALUE

CHROME = [
    "admin-chrome-reports",
    "admin-chrome-telemetry",
    "admin-chrome-profiles",
    "admin-chrome-insights",
    "admin-chrome-policy",
]
PROFILE = {
    "name": "customers/C01/profiles/P1",
    "profileId": "P1",
    "displayName": "Staff",
    "userEmail": "staff@op.example",
    # Attestation keys and raw report data never leave the server.
    "attestationCredential": {"publicKeyData": "cHVibGljLWtleQ=="},
    "reportingData": {"profilePath": "/home/staff/.config"},
    "etag": "e1",
}
COMMAND = {
    "name": "customers/C01/profiles/P1/commands/K1",
    "commandType": "clearBrowsingData",
    "commandState": "PENDING",
    "payload": {"clearCache": True, "clearCookies": False},
    "commandResult": {"resultType": "SUCCESS", "resultMessage": "free text"},
}
DEVICE = {
    "name": "customers/C01/telemetry/devices/D1",
    "customer": "customers/C01",
    "deviceId": "D1",
    "serialNumber": "SN1",
    "networkInfo": {"networkDevices": [{"macAddress": "00:11:22:33:44:55"}]},
    "networkStatusReport": [{"lanIpAddress": "10.0.0.7"}],
}
PRINT_JOB = {"id": "J1", "printerId": "PR1", "title": "Salary review.pdf"}
SCHEMA_PATH = f"/v1/customers/C01/policySchemas/{SCHEMA_NAME}"
TARGET = {"targetResource": "orgunits/OU1"}
MODIFY = {
    "requests": [
        {
            "policyTargetKey": TARGET,
            "policyValue": {"policySchema": SCHEMA_NAME, "value": VALUE},
            "updateMask": ",".join(VALUE),
        }
    ]
}
RESOLVED = {
    "resolvedPolicies": [
        {
            "targetKey": TARGET,
            "sourceKey": {"targetResource": "orgunits/ROOT"},
            "value": {
                "policySchema": SCHEMA_NAME,
                "value": {
                    "protectionLevel": "PROTECTION_LEVEL_ENUM_ENHANCED",
                    "allowOverride": True,
                    "homepageUrl": "https://intranet.op.example/secret-path",
                },
            },
        }
    ],
    "nextPageToken": "n1",
}


@pytest.fixture
def chrome(ws, monkeypatch):  # noqa: F811 - the imported fixture
    http = RoutingHttp(
        {
            ("GET", "/v1/customers/C01/profiles/P1"): PROFILE,
            ("POST", "/v1/customers/C01/profiles/P1/commands"): COMMAND,
            ("GET", "/v1/customers/C01/telemetry/devices/D1"): DEVICE,
            ("GET", "/v1/customers/C01/reports:enumeratePrintJobs"): {
                "printJobs": [PRINT_JOB]
            },
            ("GET", SCHEMA_PATH): SCHEMA,
            ("POST", "/v1/customers/C01/policies:resolve"): RESOLVED,
            ("POST", "/v1/customers/C01/policies/orgunits:batchModify"): {},
        }
    )
    installed = {
        (service, "v1"): build(service, "v1", http=http)
        for service in ("chromemanagement", "chromepolicy")
    }
    apis = ws.apis
    monkeypatch.setattr(ws, "apis", lambda: {**apis(), **installed})
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, *CHROME])
    ws.directory.add_org_unit("OU1")
    ws.directory.add_org_unit("OU_OTHER", customer_id="C_OTHER")
    ws.directory.add_group("GX", "team@other.example", [])
    ws.http = http
    return ws


def _writes(chrome) -> list:
    return [r for r in chrome.http.requests if r[0] != "GET" and ":resolve" not in r[1]]


# --- reports, telemetry, and profiles -----------------------------------------------


@pytest.mark.asyncio
async def test_report_missing_from_the_static_document_runs_on_the_pinned_client(
    chrome,
):
    static = build("chromemanagement", "v1", http=RoutingHttp())
    assert not hasattr(static.customers().reports(), "countChromeProfileVersions")
    path = "/v1/customers/C01/reports:countChromeProfileVersions"
    chrome.http.handlers[("GET", path)] = {
        "profileBrowserVersions": [{"version": "140.0", "count": "3"}],
        "totalSize": 1,
    }

    result, text = await operation(
        "chromemanagement.customers.reports.countChromeProfileVersions",
        {"orgUnitId": "OU1"},
    )

    assert not result.is_error, text
    assert chrome.http.requests == [("GET", path, {"orgUnitId": "OU1"}, None)]
    assert json.loads(text)["result"]["profileBrowserVersions"] == [
        {"version": "140.0", "count": "3"}
    ]
    assert chrome.requested_scopes[-1] == [
        scopes.CHROME_MANAGEMENT_REPORTS_READONLY_SCOPE
    ]


@pytest.mark.asyncio
async def test_telemetry_and_print_job_reads_omit_network_data_and_titles(chrome):
    result, text = await operation(
        "chromemanagement.customers.telemetry.devices.get",
        {"name": "customers/my_customer/telemetry/devices/D1"},
    )
    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "name": "customers/C01/telemetry/devices/D1",
        "customer": "customers/C01",
        "deviceId": "D1",
        "serialNumber": "SN1",
    }
    assert "10.0.0.7" not in text and "00:11:22" not in text

    result, text = await operation(
        "chromemanagement.customers.reports.enumeratePrintJobs"
    )
    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "printJobs": [{"id": "J1", "printerId": "PR1"}]
    }
    assert "Salary" not in text


@pytest.mark.asyncio
async def test_telemetry_device_of_another_customer_is_not_returned(chrome):
    chrome.http.handlers[("GET", "/v1/customers/C01/telemetry/devices/D1")] = {
        **DEVICE,
        "customer": "customers/C_OTHER",
    }

    result, text = await operation(
        "chromemanagement.customers.telemetry.devices.get",
        {"name": "customers/C01/telemetry/devices/D1"},
    )

    assert result.is_error and "another customer" in text
    assert "SN1" not in text and "C_OTHER" not in text


@pytest.mark.asyncio
async def test_profile_read_omits_attestation_keys_and_report_data(chrome):
    result, text = await operation(
        "chromemanagement.customers.profiles.get",
        {"name": "customers/C01/profiles/P1"},
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "name": "customers/C01/profiles/P1",
        "profileId": "P1",
        "displayName": "Staff",
        "userEmail": "staff@op.example",
    }
    assert "cHVibGljLWtleQ" not in text and ".config" not in text


@pytest.mark.asyncio
async def test_profile_command_is_proposed_then_confirmed_once(chrome):
    body = {"commandType": "clearBrowsingData", "payload": {"clearCache": True}}
    proposal = await propose(
        "chromemanagement.customers.profiles.commands.create",
        {"parent": "customers/my_customer/profiles/P1"},
        body,
    )

    assert proposal["risk"] == "destructive"
    assert proposal["params"] == {"parent": "customers/C01/profiles/P1"}
    assert _writes(chrome) == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    assert _writes(chrome) == [
        ("POST", "/v1/customers/C01/profiles/P1/commands", {}, body)
    ]
    # The profile was read afresh at proposal and again at confirmation.
    reads = [r for r in chrome.http.requests if r[1].endswith("/profiles/P1")]
    assert len(reads) == 2
    assert json.loads(text)["result"] == {
        "name": "customers/C01/profiles/P1/commands/K1",
        "commandType": "clearBrowsingData",
        "commandState": "PENDING",
        "payload": {"clearCache": True, "clearCookies": False},
        "commandResult": {"resultType": "SUCCESS"},
    }
    assert "free text" not in text

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(_writes(chrome)) == 1
    assert [r["outcome"] for r in chrome.audit()] == ["proposed", "succeeded"]


@pytest.mark.asyncio
async def test_unknown_profile_is_refused_before_a_proposal(chrome):
    result, text = await operation(
        "chromemanagement.customers.profiles.delete",
        {"name": "customers/C01/profiles/P9"},
    )

    assert result.is_error and "Could not verify the resource" in text
    assert chrome.audit() == [] and _writes(chrome) == []


# --- Chrome Policy ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolved_policies_return_only_scalar_values(chrome):
    result, text = await operation(
        "chromepolicy.customers.policies.resolve",
        body={"policySchemaFilter": "chrome.users.*", "policyTargetKey": TARGET},
    )

    assert not result.is_error, text
    assert json.loads(text)["result"] == {
        "resolvedPolicies": [
            {
                "targetKey": TARGET,
                "sourceKey": {"targetResource": "orgunits/ROOT"},
                "value": {
                    "policySchema": SCHEMA_NAME,
                    "value": {
                        "protectionLevel": "PROTECTION_LEVEL_ENUM_ENHANCED",
                        "allowOverride": True,
                    },
                },
            }
        ],
        "nextPageToken": "n1",
    }
    assert "intranet" not in text
    assert chrome.requested_scopes[-1] == [
        scopes.CHROME_MANAGEMENT_POLICY_READONLY_SCOPE
    ]
    assert chrome.audit() == []


@pytest.mark.asyncio
async def test_policy_change_is_proposed_then_confirmed_once(chrome, monkeypatch):
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-chrome-policy": "manage"})
    result, text = await operation(
        "chromepolicy.customers.policies.orgunits.batchModify", body=MODIFY
    )
    assert result.is_error and "destructive" in text
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)

    proposal = await propose(
        "chromepolicy.customers.policies.orgunits.batchModify", body=MODIFY
    )
    assert proposal["params"] == {"customer": "customers/C01"}
    assert _writes(chrome) == []

    result, text = await confirm(proposal)

    assert not result.is_error, text
    path = "/v1/customers/C01/policies/orgunits:batchModify"
    assert _writes(chrome) == [("POST", path, {}, MODIFY)]
    # The schema was read afresh at proposal and again at confirmation.
    assert [r[1] for r in chrome.http.requests].count(SCHEMA_PATH) == 2
    assert set(chrome.requested_scopes[-1]) == {
        scopes.CHROME_MANAGEMENT_POLICY_SCOPE,
        scopes.CHROME_MANAGEMENT_POLICY_READONLY_SCOPE,
    }

    result, text = await confirm(proposal)
    assert result.is_error and "already used" in text
    assert len(_writes(chrome)) == 1
    assert [r["outcome"] for r in chrome.audit()] == ["proposed", "succeeded"]


@pytest.mark.asyncio
async def test_string_policy_values_are_never_stored_or_echoed(chrome):
    url = "https://intranet.op.example/secret-path"
    body = {
        "requests": [
            {
                "policyTargetKey": TARGET,
                "policyValue": {
                    "policySchema": SCHEMA_NAME,
                    "value": {"homepageUrl": url},
                },
                "updateMask": "homepageUrl",
            }
        ]
    }

    result, text = await operation(
        "chromepolicy.customers.policies.orgunits.batchModify", body=body
    )

    assert result.is_error and url not in text and "intranet" not in text
    stored = [p.read_text() for p in chrome.confirmations.directory.glob("**/*")]
    assert not any("intranet" in s for s in stored if s)
    assert chrome.audit() == [] and _writes(chrome) == []


@pytest.mark.asyncio
async def test_policy_targets_of_another_customer_are_refused_before_any_call(chrome):
    body = {
        "requests": [
            MODIFY["requests"][0],
            {
                **MODIFY["requests"][0],
                "policyTargetKey": {"targetResource": "orgunits/OU_OTHER"},
            },
        ]
    }
    result, text = await operation(
        "chromepolicy.customers.policies.orgunits.batchModify", body=body
    )
    assert result.is_error and "organizational unit" in text

    result, text = await operation(
        "chromepolicy.customers.policies.groups.batchDelete",
        body={
            "requests": [
                {
                    "policyTargetKey": {"targetResource": "groups/GX"},
                    "policySchema": SCHEMA_NAME,
                }
            ]
        },
    )
    assert result.is_error and "customer's domains" in text

    result, text = await operation(
        "chromepolicy.customers.policySchemas.list", {"parent": "customers/C_OTHER"}
    )
    assert result.is_error and "set by the server" in text
    assert chrome.http.requests == [] and chrome.audit() == []


# --- errors, services, and levels -------------------------------------------------


@pytest.mark.asyncio
async def test_google_errors_are_categorised_without_details(chrome):
    result, text = await operation(
        "chromemanagement.customers.profiles.get",
        {"name": "customers/C01/profiles/P9"},
    )
    assert result.is_error and "not_found" in text
    assert "is private" not in text

    chrome.http.handlers[("GET", "/v1/customers/C01/telemetry/devices")] = 403
    result, text = await operation("chromemanagement.customers.telemetry.devices.list")
    assert result.is_error and "forbidden_unclassified" in text
    assert "is private" not in text


@pytest.mark.asyncio
async def test_chrome_operations_need_their_own_service(chrome, monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [*ALL_ADMIN, "admin-chrome-reports"])

    result, text = await operation(
        "chromemanagement.customers.telemetry.devices.get",
        {"name": "customers/C01/telemetry/devices/D1"},
    )

    assert result.is_error and "admin-chrome-telemetry" in text
    assert chrome.http.requests == []


@pytest.mark.asyncio
async def test_read_only_mode_allows_reads_and_refuses_policy_writes(
    chrome, monkeypatch
):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    result, text = await operation(
        "chromepolicy.customers.policies.orgunits.batchModify", body=MODIFY
    )
    assert result.is_error and "allows up to read" in text

    result, text = await operation(
        "chromepolicy.customers.policies.resolve",
        body={"policySchemaFilter": "chrome.users.*", "policyTargetKey": TARGET},
    )
    assert not result.is_error, text
    assert _writes(chrome) == []


@pytest.mark.asyncio
async def test_connector_credentials_are_excluded(chrome):
    result, text = await operation(
        "chromemanagement.customers.connectorConfigs.create",
        {"parent": "customers/C01"},
        {"displayName": "SIEM", "credential": "connector-secret"},
    )

    assert result.is_error and "excluded (secret-in-payload)" in text
    assert "connector-secret" not in text
    assert chrome.http.requests == [] and chrome.audit() == []


@pytest.mark.asyncio
async def test_capabilities_list_chrome_operations(chrome):
    result, text = await call("list_admin_capabilities", user_google_email="x@y.z")

    assert not result.is_error, text
    assert "chromepolicy.customers.policies.orgunits.batchModify" in text
    assert "chromemanagement.customers.reports.countChromeProfileVersions" in text
    assert "chromepolicy.media.upload (unvalidated-schema)" in text
    not_implemented = text.split("Not implemented yet:", 1)[1]
    assert "Chrome" not in not_implemented
    assert "Context-aware access level changes and app assignments" in not_implemented
