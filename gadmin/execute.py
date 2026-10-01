"""Dispatch of registered admin operations to a Google API client.

The call is built only from the pinned OperationSpec: its resource path and
method are looked up on the client after the inputs pass schema validation, and
the response is cut down to the registered fields. Google errors are replaced
by AdminApiError, which never carries the request URI or Google's free-text
message. Calls are synchronous; run them with ``asyncio.to_thread``.
"""

import json
import re

from googleapiclient.discovery import key2param
from googleapiclient.errors import HttpError

from gadmin.registry import (
    InvalidOperationInput,
    OperationSpec,
    registered_spec,
    validate_call,
)

_SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}
_DISABLED_REASONS = {"accessNotConfigured", "SERVICE_DISABLED"}
_RATE_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "quotaExceeded",
    "RATE_LIMIT_EXCEEDED",
}
# Directory's wording when the caller is not an admin with the needed privilege.
_NOT_AUTHORIZED_MESSAGE = "Not Authorized to access this resource/api"

_CATEGORY_MESSAGES = {
    "missing_scope": "the granted OAuth scopes do not cover this operation",
    "missing_admin_privilege": "the account lacks the admin privilege for this operation",
    "api_disabled": "the API is not enabled for this Google Cloud project",
    "forbidden_unclassified": (
        "Google refused the request without saying why; the cause may be a missing "
        "admin privilege, an edition restriction, or a policy"
    ),
    "rate_limited": "Google rate limit or quota reached; retry later",
    "not_found": "the requested resource was not found",
    "unauthenticated": "the credentials were rejected; reauthenticate",
    "invalid_request": "Google rejected the request as invalid",
    "api_error": "Google returned an error",
}


class AdminApiError(Exception):
    """A Google API error reduced to status, operation ID, and a safe category."""

    def __init__(self, status: int, operation_id: str, category: str):
        self.status = status
        self.operation_id = operation_id
        self.category = category
        super().__init__(
            f"{operation_id} failed with HTTP {status} ({category}): "
            f"{_CATEGORY_MESSAGES[category]}."
        )


def _google_reasons(error: HttpError) -> tuple[set[str], str]:
    """Return Google's machine-readable reason codes and top-level message."""
    try:
        detail = json.loads(error.content.decode("utf-8"))["error"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return set(), ""
    if not isinstance(detail, dict):
        return set(), ""
    entries = [*detail.get("errors", []), *detail.get("details", [])]
    reasons = {e["reason"] for e in entries if isinstance(e, dict) and "reason" in e}
    message = detail.get("message")
    return reasons, message if isinstance(message, str) else ""


def _classify(error: HttpError) -> str:
    status = error.resp.status
    reasons, message = _google_reasons(error)
    if status == 429 or reasons & _RATE_REASONS:
        return "rate_limited"
    if status == 403:
        if reasons & _SCOPE_REASONS:
            return "missing_scope"
        if reasons & _DISABLED_REASONS:
            return "api_disabled"
        if message == _NOT_AUTHORIZED_MESSAGE:
            return "missing_admin_privilege"
        return "forbidden_unclassified"
    return {401: "unauthenticated", 400: "invalid_request", 404: "not_found"}.get(
        status, "api_error"
    )


# A Vault long-running Operation's response, metadata, and error are untyped:
# a finished export carries its download location, and error messages are free
# text. Only the documented CountArtifactsResponse members and the numeric error
# code are returned, each with its checked type.
_USER_INFO = {"email": str, "displayName": str}
_COUNT_RESULT = {
    "matchingAccountsCount": str,
    "queriedAccountsCount": str,
    "nonQueryableAccounts": [str],
    "accountCounts": [{"account": _USER_INFO, "count": str}],
    "accountCountErrors": [{"account": _USER_INFO, "errorType": str}],
}
_VAULT_OPERATION = {
    "name": str,
    "done": bool,
    "response": {
        "totalCount": str,
        "mailCountResult": _COUNT_RESULT,
        "groupsCountResult": _COUNT_RESULT,
    },
    "error": {"code": int},
}
# A Cloud Identity long-running Operation echoes the changed resource in its
# response, and its metadata and error message are free text; only its name,
# completion, and numeric error code are returned.
_OPERATION = {"name": str, "done": bool, "error": {"code": int}}
_CLOUD_IDENTITY_OPERATIONS = (
    "cloudidentity.allowlistedDomains.create",
    "cloudidentity.allowlistedDomains.delete",
    "cloudidentity.customers.userinvitations.cancel",
    "cloudidentity.customers.userinvitations.send",
    "cloudidentity.devices.cancelWipe",
    "cloudidentity.devices.create",
    "cloudidentity.devices.delete",
    "cloudidentity.devices.deviceUsers.approve",
    "cloudidentity.devices.deviceUsers.block",
    "cloudidentity.devices.deviceUsers.cancelWipe",
    "cloudidentity.devices.deviceUsers.delete",
    "cloudidentity.devices.deviceUsers.wipe",
    "cloudidentity.devices.wipe",
    "cloudidentity.groups.create",
    "cloudidentity.groups.delete",
    "cloudidentity.groups.memberships.create",
    "cloudidentity.groups.memberships.delete",
    "cloudidentity.groups.patch",
    "cloudidentity.groups.updateSecuritySettings",
    "cloudidentity.inboundOidcSsoProfiles.create",
    "cloudidentity.inboundOidcSsoProfiles.delete",
    "cloudidentity.inboundOidcSsoProfiles.patch",
    "cloudidentity.inboundSamlSsoProfiles.create",
    "cloudidentity.inboundSamlSsoProfiles.delete",
    "cloudidentity.inboundSamlSsoProfiles.idpCredentials.delete",
    "cloudidentity.inboundSamlSsoProfiles.patch",
    "cloudidentity.inboundSsoAssignments.create",
    "cloudidentity.inboundSsoAssignments.delete",
    "cloudidentity.inboundSsoAssignments.patch",
    "cloudidentity.orgUnits.memberships.move",
    "cloudidentity.policies.create",
    "cloudidentity.policies.delete",
    "cloudidentity.policies.patch",
)
# OIDC profiles are typed so that the input-only client secret can never be
# returned, even if Google echoed it.
_OIDC_PROFILE = {
    "name": str,
    "customer": str,
    "displayName": str,
    "idpConfig": {"issuerUri": str, "changePasswordUri": str},
    "rpConfig": {"clientId": str, "redirectUris": [str]},
}
# Batch results echo only IDs and error codes; failed printers and print
# servers would otherwise carry their URI and free-text messages.
_CHROMEOS_STATUS_RESULTS = {
    "changeChromeOsDeviceStatusResults": [{"deviceId": str, "error": {"code": int}}]
}
_DELETED_PRINTERS = {
    "printerIds": [str],
    "failedPrinters": [{"printerId": str, "errorCode": str}],
}
_DELETED_PRINT_SERVERS = {
    "printServerIds": [str],
    "failedPrintServers": [{"printServerId": str, "errorCode": str}],
}
# A browser profile command's result message is free text; only its codes and
# the documented boolean payload come back.
_PROFILE_COMMAND = {
    "name": str,
    "commandType": str,
    "commandState": str,
    "issueTime": str,
    "validDuration": str,
    "commandResult": {"resultType": str, "resultCode": str, "clientExecutionTime": str},
    "payload": {"clearCache": bool, "clearCookies": bool},
}
_CHROMEOS_COMMAND = {
    "commandId": str,
    "type": str,
    "state": str,
    "commandResult": {"result": str},
}
_CHROMEOS_COMMAND_TYPES = {
    "REBOOT",
    "TAKE_A_SCREENSHOT",
    "SET_VOLUME",
    "WIPE_USERS",
    "REMOTE_POWERWASH",
    "DEVICE_START_CRD_SESSION",
    "CAPTURE_LOGS",
    "FETCH_CRD_AVAILABILITY_INFO",
    "FETCH_SUPPORT_PACKET",
}
_CHROMEOS_COMMAND_STATES = {
    "PENDING",
    "EXPIRED",
    "CANCELLED",
    "SENT_TO_CLIENT",
    "ACKED_BY_CLIENT",
    "EXECUTED_BY_CLIENT",
}
_CHROMEOS_COMMAND_RESULTS = {"IGNORED", "FAILURE", "SUCCESS"}
_PROFILE_COMMANDS = (
    "chromemanagement.customers.profiles.commands.create",
    "chromemanagement.customers.profiles.commands.get",
    "chromemanagement.customers.profiles.commands.list",
)
# A resolved Chrome policy value is untyped and may hold URLs or free text; it
# comes back with only its boolean, integer, and enum-name members.
_SCALARS = "scalars"
_SCALAR_STRING = re.compile(r"[A-Z][A-Z0-9_]*_ENUM_[A-Z0-9_]+|-?[0-9]+")
_TARGET_KEY = {"targetResource": str, "additionalTargetKeys": {"app_id": str}}
_RESOLVED_POLICY = {
    "targetKey": _TARGET_KEY,
    "sourceKey": _TARGET_KEY,
    "addedSourceKey": _TARGET_KEY,
    "value": {"policySchema": str, "value": _SCALARS},
}
# Access levels are returned with only their documented members; a custom
# level's expression keeps its text and title, not its source location.
_ACCESS_LEVEL_CONDITION = {
    "ipSubnetworks": [str],
    "regions": [str],
    "requiredAccessLevels": [str],
    "members": [str],
    "negate": bool,
    "devicePolicy": {
        "requireScreenlock": bool,
        "requireAdminApproval": bool,
        "requireCorpOwned": bool,
        "allowedEncryptionStatuses": [str],
        "allowedDeviceManagementLevels": [str],
        "osConstraints": [
            {"osType": str, "minimumVersion": str, "requireVerifiedChromeOs": bool}
        ],
    },
    "vpcNetworkSources": [
        {"vpcSubnetwork": {"network": str, "vpcIpSubnetworks": [str]}}
    ],
}
_ACCESS_LEVEL = {
    "name": str,
    "title": str,
    "description": str,
    "basic": {"combiningFunction": str, "conditions": [_ACCESS_LEVEL_CONDITION]},
    "custom": {"expr": {"expression": str, "title": str, "description": str}},
}
TYPED_RESPONSES = {
    "vault.matters.count": _VAULT_OPERATION,
    "vault.operations.get": _VAULT_OPERATION,
    "vault.operations.list": _VAULT_OPERATION,
    **dict.fromkeys(_CLOUD_IDENTITY_OPERATIONS, _OPERATION),
    "cloudidentity.inboundOidcSsoProfiles.get": _OIDC_PROFILE,
    "cloudidentity.inboundOidcSsoProfiles.list": _OIDC_PROFILE,
    "admin.customer.devices.chromeos.batchChangeStatus": _CHROMEOS_STATUS_RESULTS,
    "admin.customer.devices.chromeos.commands.get": _CHROMEOS_COMMAND,
    "admin.customers.chrome.printers.batchDeletePrinters": _DELETED_PRINTERS,
    "admin.customers.chrome.printServers.batchDeletePrintServers": _DELETED_PRINT_SERVERS,
    **dict.fromkeys(_PROFILE_COMMANDS, _PROFILE_COMMAND),
    "chromepolicy.customers.policies.resolve": _RESOLVED_POLICY,
    "accesscontextmanager.accessPolicies.accessLevels.get": _ACCESS_LEVEL,
    "accesscontextmanager.accessPolicies.accessLevels.list": _ACCESS_LEVEL,
}


def _project(schema, value):
    """Keep only the members of ``value`` named in ``schema`` whose type matches.

    A mismatched value becomes None, and None or emptied members are dropped.
    """
    if schema is _SCALARS:
        if not isinstance(value, dict):
            return None
        return {
            k: v
            for k, v in value.items()
            if isinstance(v, (bool, int))
            or (isinstance(v, str) and _SCALAR_STRING.fullmatch(v))
        }
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return None
        kept = {k: _project(schema[k], value[k]) for k in schema if k in value}
        return {k: v for k, v in kept.items() if v not in (None, {}, [])}
    if isinstance(schema, list):
        if not isinstance(value, list):
            return None
        kept = [_project(schema[0], item) for item in value]
        return [item for item in kept if item not in (None, {}, [])]
    # bool is a subclass of int; an integer member must not accept True/False.
    if isinstance(value, schema) and (schema is bool or not isinstance(value, bool)):
        return value
    return None


def _bound(spec: OperationSpec, response) -> dict:
    if not isinstance(response, dict):
        return {}

    typed = TYPED_RESPONSES.get(spec.id)

    def keep(item: dict) -> dict:
        if spec.id == "admin.customer.devices.chromeos.commands.get":
            command = _project(_CHROMEOS_COMMAND, item)
            if not re.fullmatch(r"[0-9]{1,20}", command.get("commandId", "")):
                command.pop("commandId", None)
            for key, allowed in (
                ("type", _CHROMEOS_COMMAND_TYPES),
                ("state", _CHROMEOS_COMMAND_STATES),
            ):
                if command.get(key) not in allowed:
                    command.pop(key, None)
            if (
                command.get("commandResult", {}).get("result")
                not in _CHROMEOS_COMMAND_RESULTS
            ):
                command.pop("commandResult", None)
            return command
        if typed:
            return _project(typed, item)
        return {k: item[k] for k in spec.response_fields if k in item}

    if spec.response_items_key is None:
        return keep(response)
    result = {
        spec.response_items_key: [
            keep(item)
            for item in response.get(spec.response_items_key, [])
            if isinstance(item, dict)
        ]
    }
    if isinstance(response.get("nextPageToken"), str):
        result["nextPageToken"] = response["nextPageToken"]
    return result


def execute_operation(
    service,
    spec: OperationSpec | str,
    params: dict | None,
    body: dict | None = None,
) -> dict:
    """Call one registered operation and return its bounded response."""
    spec = registered_spec(spec)
    params, body = validate_call(spec, params, body)
    # The client names dotted discovery parameters ("groupKey.id") "groupKey_id".
    kwargs = {key2param(name): value for name, value in params.items()}
    if body is not None:
        kwargs["body"] = body
    resource = service
    for name in spec.resource_path:
        resource = getattr(resource, name)()
    try:
        response = getattr(resource, spec.method)(**kwargs).execute()
    except HttpError as exc:
        raise AdminApiError(exc.resp.status, spec.id, _classify(exc)) from None
    if spec.id == "admin.customer.devices.chromeos.commands.get" and (
        not isinstance(response, dict)
        or response.get("commandId") != params["commandId"]
    ):
        raise InvalidOperationInput("Could not verify the ChromeOS command.")
    if spec.id == "admin.customer.devices.chromeos.issueCommand" and (
        not isinstance(response, dict)
        or not isinstance(response.get("commandId"), str)
        or not re.fullmatch(r"[0-9]{1,20}", response["commandId"])
    ):
        raise InvalidOperationInput("Could not verify the issued ChromeOS command.")
    return _bound(spec, response)


def read_pages(
    service, spec: OperationSpec | str, params: dict, key: str, pages: int
) -> list[dict] | None:
    """Every ``key`` item of a paged read, or None if it runs past ``pages`` pages."""
    items: list[dict] = []
    for _ in range(pages):
        result = execute_operation(service, spec, params)
        items.extend(result.get(key, []))
        if not result.get("nextPageToken"):
            return items
        params = {**params, "pageToken": result["nextPageToken"]}
    return None
