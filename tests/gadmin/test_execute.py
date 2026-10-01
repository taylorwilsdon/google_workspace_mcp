"""Registered-operation dispatch reaches only pinned methods and returns only
registered fields; Google errors become safe typed errors."""

import dataclasses
from unittest.mock import MagicMock

import pytest

from gadmin.execute import TYPED_RESPONSES, AdminApiError, execute_operation
from gadmin.registry import (
    InvalidOperationInput,
    UnknownOperation,
    get_operation,
    iter_operations,
)
from tests.gadmin.fake_directory import FakeDirectory, http_error
from tests.gadmin.test_registry import DISCOVERY


@pytest.fixture
def client():
    return MagicMock(name="directory")


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_user(
        "staff@op.example",
        "U_STAFF",
        etag='"secret-etag"',
        recoveryPhone="+15555550100",
        hashFunction="SHA-1",
    )
    return fake


# --- Only registered methods reach the client -----------------------------------


def test_unregistered_operation_never_reaches_client(client):
    with pytest.raises(UnknownOperation):
        execute_operation(client, "directory.users.undelete", {"userKey": "u"})
    assert client.mock_calls == []


def test_forged_spec_is_rejected(client):
    forged = dataclasses.replace(
        get_operation("directory.users.get"), method="makeAdmin"
    )

    with pytest.raises(UnknownOperation):
        execute_operation(client, forged, {"userKey": "u@op.example"})
    assert client.mock_calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"userKey": "https://evil.example/x"},
        {"userKey": "../customer/C02"},
        {"userKey": "u@op.example", "fields": "*"},
        {},
    ],
    ids=["url", "path-traversal", "unregistered-param", "missing-required"],
)
def test_invalid_input_never_reaches_client(client, params):
    with pytest.raises(InvalidOperationInput):
        execute_operation(client, "directory.users.get", params)
    assert client.mock_calls == []


def test_url_in_query_param_never_reaches_client(client):
    with pytest.raises(InvalidOperationInput):
        execute_operation(
            client, "directory.users.list", {"query": "email:https://evil.example"}
        )
    assert client.mock_calls == []


def test_unregistered_body_field_never_reaches_client(client):
    with pytest.raises(InvalidOperationInput):
        execute_operation(
            client,
            "directory.users.update",
            {"userKey": "u@op.example"},
            {"password": "hunter2"},
        )
    assert client.mock_calls == []


def test_dispatch_walks_registered_path_and_method(directory):
    execute_operation(
        directory,
        "directory.users.update",
        {"userKey": "staff@op.example"},
        {"orgUnitPath": "/IT"},
    )

    assert directory.calls == [
        (
            "directory.users.update",
            {"userKey": "staff@op.example", "body": {"orgUnitPath": "/IT"}},
        )
    ]


# --- Bounded responses ----------------------------------------------------------


def test_read_returns_only_registered_fields(directory):
    result = execute_operation(
        directory, "directory.users.get", {"userKey": "staff@op.example"}
    )

    assert result["primaryEmail"] == "staff@op.example"
    assert set(result) <= set(get_operation("directory.users.get").response_fields)
    assert not {"etag", "recoveryPhone", "hashFunction"} & set(result)


def test_list_returns_bounded_items_and_page_token(client):
    client.roleAssignments().list.return_value.execute.return_value = {
        "kind": "admin#directory#roleAssignments",
        "etag": '"etag"',
        "items": [
            {"roleAssignmentId": "A1", "roleId": "R1", "assignedTo": "U1", "etag": "x"}
        ],
        "nextPageToken": "next",
    }
    client.reset_mock()

    result = execute_operation(
        client, "directory.roleAssignments.list", {"customer": "my_customer"}
    )

    assert result == {
        "items": [{"roleAssignmentId": "A1", "roleId": "R1", "assignedTo": "U1"}],
        "nextPageToken": "next",
    }


def test_non_dict_response_is_empty(client):
    client.users().signOut.return_value.execute.return_value = ""
    assert execute_operation(client, "directory.users.signOut", {"userKey": "u"}) == {}


# --- Safe typed errors ----------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (http_error(403, "Insufficient Permission", code="insufficientPermissions"), "missing_scope"),
        (http_error(403, "Not Authorized to access this resource/api", code="forbidden"), "missing_admin_privilege"),
        (http_error(403, "Admin SDK API has not been used", code="accessNotConfigured"), "api_disabled"),
        (http_error(403, "Forbidden", code="forbidden"), "forbidden_unclassified"),
        (http_error(403, "Quota exceeded", code="userRateLimitExceeded"), "rate_limited"),
        (http_error(429, "Too Many Requests"), "rate_limited"),
        (http_error(404, "Resource Not Found: userKey"), "not_found"),
        (http_error(401, "Invalid Credentials"), "unauthenticated"),
        (http_error(400, "Invalid Input"), "invalid_request"),
        (http_error(503, "Backend Error"), "api_error"),
    ],
)  # fmt: skip
def test_http_errors_become_safe_typed_errors(directory, error, category):
    directory.failures["directory.users.get"] = error

    with pytest.raises(AdminApiError) as raised:
        execute_operation(
            directory, "directory.users.get", {"userKey": "staff@op.example"}
        )

    exc = raised.value
    assert (exc.status, exc.operation_id, exc.category) == (
        error.resp.status,
        "directory.users.get",
        category,
    )
    message = str(exc)
    assert "directory.users.get" in message and str(error.resp.status) in message
    assert "secret-uri" not in message and "googleapis" not in message
    assert "staff@op.example" not in message
    assert exc.__cause__ is None and exc.__suppress_context__


# --- Vault long-running operations ------------------------------------------------

# An Operation's response and metadata are untyped. A finished export operation
# carries the Export, including its download location.
EXPORT_OPERATION = {
    "name": "operations/E1",
    "done": True,
    "metadata": {
        "@type": "type.googleapis.com/google.vault.v1.ExportMetadata",
        "query": {"terms": "from:ceo@op.example"},
        "cloudStorageSink": {"files": [{"bucketName": "sink-bucket"}]},
    },
    "response": {
        "@type": "type.googleapis.com/google.vault.v1.Export",
        "id": "E1",
        "cloudStorageSink": {
            "files": [{"bucketName": "sink-bucket", "objectName": "mail.zip"}]
        },
        "apiKey": "secret-token",
    },
    "error": {"code": 7, "message": "denied for ceo@op.example", "details": [{}]},
}

COUNT_OPERATION = {
    "name": "operations/C1",
    "done": True,
    "metadata": {"query": {"terms": "from:ceo@op.example"}},
    "response": {
        "@type": "type.googleapis.com/google.vault.v1.CountArtifactsResponse",
        "totalCount": "12",
        "mailCountResult": {
            "matchingAccountsCount": "1",
            "queriedAccountsCount": "2",
            "nonQueryableAccounts": ["held@op.example"],
            "accountCounts": [
                {
                    "account": {"email": "a@op.example", "displayName": "A"},
                    "count": "12",
                    "sample": "message body",
                }
            ],
            "accountCountErrors": [
                {"account": {"email": "b@op.example"}, "errorType": "UNKNOWN"}
            ],
        },
        "cloudStorageSink": {"files": [{"bucketName": "sink-bucket"}]},
    },
}

COUNT_BODY = {"query": {"corpus": "MAIL", "method": "ENTIRE_ORG"}}


def _vault_call(client, operation_id, response):
    if operation_id == "vault.operations.list":
        client.operations().list.return_value.execute.return_value = {
            "operations": [response],
            "nextPageToken": "next",
        }
        return execute_operation(client, operation_id, {"name": "operations"})
    if operation_id == "vault.operations.get":
        client.operations().get.return_value.execute.return_value = response
        return execute_operation(client, operation_id, {"name": response["name"]})
    client.matters().count.return_value.execute.return_value = response
    return execute_operation(client, operation_id, {"matterId": "M1"}, COUNT_BODY)


VAULT_OPERATION_IDS = [
    "vault.operations.get",
    "vault.operations.list",
    "vault.matters.count",
]


def test_every_long_running_operation_has_a_typed_response():
    operation_ids = {
        spec.id
        for spec in iter_operations()
        if DISCOVERY[(spec.service, spec.version)]["methods"][spec.id]["response"]
        in ("Operation", "ListOperationsResponse")
    }

    assert operation_ids <= set(TYPED_RESPONSES)
    # The only other typed responses keep the OIDC client secret out of reads,
    # printer URIs and free-text errors out of batch results, untyped command
    # payloads out of browser profile commands, string settings out of
    # resolved Chrome policies, and undocumented members out of access levels.
    assert set(TYPED_RESPONSES) - operation_ids == {
        "chromemanagement.customers.profiles.commands.create",
        "chromemanagement.customers.profiles.commands.get",
        "chromemanagement.customers.profiles.commands.list",
        "chromepolicy.customers.policies.resolve",
        "cloudidentity.inboundOidcSsoProfiles.get",
        "cloudidentity.inboundOidcSsoProfiles.list",
        "admin.customer.devices.chromeos.batchChangeStatus",
        "admin.customer.devices.chromeos.commands.get",
        "admin.customers.chrome.printers.batchDeletePrinters",
        "admin.customers.chrome.printServers.batchDeletePrintServers",
        "accesscontextmanager.accessPolicies.accessLevels.get",
        "accesscontextmanager.accessPolicies.accessLevels.list",
    }
    assert set(VAULT_OPERATION_IDS) <= operation_ids
    for operation_id, schema in TYPED_RESPONSES.items():
        assert set(get_operation(operation_id).response_fields) == set(schema)


@pytest.mark.parametrize("operation_id", VAULT_OPERATION_IDS)
def test_vault_operation_never_returns_export_sink_or_untyped_payload(
    client, operation_id
):
    result = _vault_call(client, operation_id, EXPORT_OPERATION)

    output = repr(result)
    for leaked in ("sink-bucket", "mail.zip", "secret-token", "ceo@op.example"):
        assert leaked not in output
    assert "cloudStorageSink" not in output and "metadata" not in output
    operation = result["operations"][0] if "operations" in result else result
    assert operation == {"name": "operations/E1", "done": True, "error": {"code": 7}}


@pytest.mark.parametrize("operation_id", VAULT_OPERATION_IDS)
def test_vault_count_result_keeps_only_documented_count_fields(client, operation_id):
    result = _vault_call(client, operation_id, COUNT_OPERATION)

    operation = result["operations"][0] if "operations" in result else result
    assert operation == {
        "name": "operations/C1",
        "done": True,
        "response": {
            "totalCount": "12",
            "mailCountResult": {
                "matchingAccountsCount": "1",
                "queriedAccountsCount": "2",
                "nonQueryableAccounts": ["held@op.example"],
                "accountCounts": [
                    {
                        "account": {"email": "a@op.example", "displayName": "A"},
                        "count": "12",
                    }
                ],
                "accountCountErrors": [
                    {"account": {"email": "b@op.example"}, "errorType": "UNKNOWN"}
                ],
            },
        },
    }


def test_vault_count_fields_of_the_wrong_type_are_dropped(client):
    result = _vault_call(
        client,
        "vault.operations.get",
        {
            "name": "operations/C2",
            "done": "yes",
            "response": {
                "totalCount": {"secret": "s"},
                "mailCountResult": ["not", "an", "object"],
                "groupsCountResult": {
                    "accountCounts": [{"account": "x@op.example", "count": 3}, "raw"]
                },
            },
            "error": {"code": "7", "message": "m"},
        },
    )

    assert result == {"name": "operations/C2"}


def test_cloud_identity_operation_returns_only_name_status_and_error_code(client):
    client.devices().wipe.return_value.execute.return_value = {
        "name": "operations/W1",
        "done": True,
        "metadata": {"@type": "type.googleapis.com/x", "note": "free text"},
        "response": {"name": "devices/D1", "imei": "356938035643809"},
        "error": {"code": 9, "message": "user ceo@op.example is protected"},
    }

    result = execute_operation(
        client,
        "cloudidentity.devices.wipe",
        {"name": "devices/D1"},
        {"customer": "customers/C01"},
    )

    assert result == {"name": "operations/W1", "done": True, "error": {"code": 9}}


def test_oidc_profile_reads_never_return_the_client_secret(client):
    profile = {
        "name": "inboundOidcSsoProfiles/P1",
        "customer": "customers/C01",
        "idpConfig": {"issuerUri": "https://idp.example"},
        "rpConfig": {"clientId": "app", "clientSecret": "s3cr3t-value"},
    }
    client.inboundOidcSsoProfiles().list.return_value.execute.return_value = {
        "inboundOidcSsoProfiles": [profile]
    }

    result = execute_operation(client, "cloudidentity.inboundOidcSsoProfiles.list", {})

    assert "s3cr3t-value" not in repr(result)
    assert result["inboundOidcSsoProfiles"][0]["rpConfig"] == {"clientId": "app"}


def test_dotted_discovery_parameters_use_the_client_python_name(client):
    client.groups().lookup.return_value.execute.return_value = {"name": "groups/G1"}

    execute_operation(
        client, "cloudidentity.groups.lookup", {"groupKey.id": "team@op.example"}
    )

    client.groups().lookup.assert_called_with(groupKey_id="team@op.example")
