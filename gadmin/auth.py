"""Request-scoped admin clients and call-time permission checks.

Admin clients reuse the MCP's existing authentication path. They are always bound
to the selected account: a selection that differs from the verified request
identity, or credentials whose Google-verified (userinfo) account is another
account, are refused. Unsigned ID-token claims never count as proof.

Gmail delegate settings are the exception: domain-wide delegation acts as the
mailbox owner, so their clients come only from ``mailbox_client``, with a
service-account token minted for an owner freshly verified in the admin's
customer. An admin OAuth grant never builds a Gmail client.
"""

from auth.gateway_identity import GatewayIdentityError, require_gateway_principal
from auth.google_auth import GoogleAuthenticationError
from auth.oauth_config import (
    get_oauth_config,
    is_oauth21_enabled,
    is_service_account_enabled,
    is_trust_gateway_identity,
)
from auth.permissions import get_permissions, get_scopes_for_permission
from auth.request_identity import RequestIdentity
from auth.scopes import (
    ADMIN_DIRECTORY_CUSTOMER_READONLY_SCOPE,
    ADMIN_DIRECTORY_DOMAIN_READONLY_SCOPE,
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    DELEGATED_SERVICES,
    TOOL_READONLY_SCOPES_MAP,
    is_read_only_mode,
    is_service_enabled,
)
from auth.service_decorator import (
    _authenticate_service,
    _get_service_account_credentials,
    _validate_dwd_domain,
    assert_identity_binding,
)
from fastmcp.server.dependencies import get_context
from googleapiclient.discovery import build
from gadmin.client import TRANSPORT_APIS, pinned_client
from gadmin.guard import (
    AdminBoundaryError,
    AdminContext,
    _get_user,
    _is_active_in,
    guard_target,
)
from gadmin.registry import RISK_LEVELS, OperationSpec, classify_risk, get_operation

ADMIN_SERVICE = "admin-directory"
# Gmail delegate settings run only as a verified mailbox owner.
MAILBOX_SERVICE = "admin-gmail-delegates"
_NEEDS_SERVICE_ACCOUNT = (
    "Gmail delegate settings need a service account with domain-wide "
    "delegation; an admin OAuth grant cannot be used."
)

# The opt-in MCP service that must be selected for each registered API family.
ADMIN_SERVICES = {
    ("admin", "directory_v1"): ADMIN_SERVICE,
    ("admin", "datatransfer_v1"): "admin-datatransfer",
    ("licensing", "v1"): "admin-licensing",
    ("admin", "reports_v1"): "admin-reports",
    ("vault", "v1"): "admin-vault",
    ("alertcenter", "v1beta1"): "admin-alertcenter",
    ("groupssettings", "v1"): "admin-groupssettings",
    # Directory devices, calendar resources, and Chrome printers are separate
    # services, keyed by resource path.
    ("admin", "directory_v1", "chromeosdevices"): "admin-directory-devices",
    ("admin", "directory_v1", "mobiledevices"): "admin-directory-devices",
    ("admin", "directory_v1", "customer", "devices"): "admin-directory-devices",
    ("admin", "directory_v1", "resources"): "admin-directory-resources",
    ("admin", "directory_v1", "customers", "chrome"): "admin-directory-printers",
    # Cloud Identity areas are separate services, keyed by top-level resource.
    ("cloudidentity", "v1", "groups"): "admin-cloudidentity-groups",
    ("cloudidentity", "v1", "devices"): "admin-cloudidentity-devices",
    ("cloudidentity", "v1", "inboundSamlSsoProfiles"): "admin-cloudidentity-sso",
    ("cloudidentity", "v1", "inboundOidcSsoProfiles"): "admin-cloudidentity-sso",
    ("cloudidentity", "v1", "inboundSsoAssignments"): "admin-cloudidentity-sso",
    ("cloudidentity", "v1", "policies"): "admin-cloudidentity-policies",
    ("cloudidentity", "v1", "customers"): "admin-cloudidentity-invitations",
    ("cloudidentity", "v1", "allowlistedDomains"): "admin-cloudidentity-domains",
    ("cloudidentity", "v1beta1", "orgUnits"): "admin-cloudidentity-orgunits",
    # Chrome Management areas are separate services, keyed by customer resource.
    ("chromemanagement", "v1", "customers", "reports"): "admin-chrome-reports",
    ("chromemanagement", "v1", "customers", "apps"): "admin-chrome-reports",
    ("chromemanagement", "v1", "customers", "telemetry"): "admin-chrome-telemetry",
    ("chromemanagement", "v1", "customers", "profiles"): "admin-chrome-profiles",
    ("chromemanagement", "v1", "customers", "enterprise"): "admin-chrome-insights",
    ("chromepolicy", "v1"): "admin-chrome-policy",
    ("accesscontextmanager", "v1"): "admin-access-context",
    ("admin", "contacts_v1"): "admin-contact-delegation",
    ("gmail", "v1"): MAILBOX_SERVICE,
}

# The target guard reads users and role assignments with the Directory client.
GUARD_SCOPES = (
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
)
# Group, member, and domain boundary rules also read groups and customer domains.
REFERENCE_GUARD_SCOPES = (
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_DOMAIN_READONLY_SCOPE,
)

_MAX_RISK_BY_LEVEL = {
    "readonly": "read",
    "manage": "manage",
    "destructive": "destructive",
}


class AdminAuthenticationError(GoogleAuthenticationError):
    """The admin identity could not be verified or bound to the selection."""


class AdminPermissionError(PermissionError):
    """The configured permission level does not allow this admin operation."""


def admin_service_for(operation: OperationSpec) -> str:
    """Return the opt-in MCP service for ``operation``: the entry for its longest
    resource path prefix, else its API family's."""
    path = (operation.service, operation.version, *operation.resource_path)
    for end in range(len(path), 1, -1):
        if path[:end] in ADMIN_SERVICES:
            return ADMIN_SERVICES[path[:end]]
    raise KeyError(operation.id)


def _allowed_scopes_and_risk(service: str) -> tuple[set[str] | None, str]:
    """Return (allowed scopes or None for all, maximum risk) for this launch."""
    level = (get_permissions() or {}).get(service)
    if service in DELEGATED_SERVICES:
        # No OAuth scope backs these services; the level only caps the risk.
        if level is not None:
            max_risk = _MAX_RISK_BY_LEVEL[level]
        else:
            max_risk = "read" if is_read_only_mode() else "destructive"
        read, write = DELEGATED_SERVICES[service]
        return set(read if max_risk == "read" else read + write), max_risk
    if level is not None:
        return (
            set(get_scopes_for_permission(service, level)),
            _MAX_RISK_BY_LEVEL[level],
        )
    if is_read_only_mode():
        return set(TOOL_READONLY_SCOPES_MAP[service]), "read"
    return None, "destructive"


def admin_writes_allowed(service: str) -> bool:
    """Whether this launch lets ``service`` run any operation above read risk."""
    return _allowed_scopes_and_risk(service)[1] != "read"


def assert_admin_permission(operation: OperationSpec, body: dict | None = None) -> None:
    """Raise AdminPermissionError unless this launch permits the operation."""
    service = admin_service_for(operation)
    if not is_service_enabled(service):
        raise AdminPermissionError(f"The {service} service is not enabled.")
    allowed_scopes, max_risk = _allowed_scopes_and_risk(service)
    risk = classify_risk(operation, body)
    if RISK_LEVELS.index(risk) > RISK_LEVELS.index(max_risk):
        raise AdminPermissionError(
            f"{operation.id} is a {risk} operation; this server allows up to {max_risk}."
        )
    if allowed_scopes is not None and not set(operation.scopes) <= allowed_scopes:
        raise AdminPermissionError(
            f"{operation.id} needs scopes this server's permission level does not grant."
        )


def client_scopes(operation: OperationSpec) -> list[str]:
    """Scopes a client for ``operation`` requests, including its guard reads."""
    scopes = list(operation.scopes)
    # Resource and policy value rules verify a name with a fresh read on the
    # same client.
    for rule in operation.boundary:
        if rule.read:
            scopes += [s for s in get_operation(rule.read).scopes if s not in scopes]
    if admin_service_for(operation) == ADMIN_SERVICE:
        guards = GUARD_SCOPES
        if any(r.kind in ("group", "member", "domain") for r in operation.boundary):
            guards += REFERENCE_GUARD_SCOPES
        if any(r.kind == "customer_record" for r in operation.boundary):
            guards += (ADMIN_DIRECTORY_CUSTOMER_READONLY_SCOPE,)
        scopes = list(dict.fromkeys((*scopes, *guards)))
    return scopes


def _current_session_id() -> str | None:
    try:
        return get_context().session_id
    except RuntimeError:
        return None


async def get_admin_service(
    user_google_email: str,
    operation: OperationSpec,
    identity: RequestIdentity | None,
):
    """Return a Google client for ``operation`` bound to the selected account."""
    if admin_service_for(operation) == MAILBOX_SERVICE:
        raise AdminPermissionError(_NEEDS_SERVICE_ACCOUNT)
    identity_email = identity.email if identity else None
    if is_trust_gateway_identity():
        try:
            identity_email = require_gateway_principal(
                identity_email, identity.via if identity else None
            )
        except GatewayIdentityError as exc:
            raise AdminAuthenticationError(str(exc)) from None
    elif is_oauth21_enabled() and not identity_email:
        raise AdminAuthenticationError("Verified admin identity is required.")

    try:
        assert_identity_binding(user_google_email, identity_email=identity_email)
    except GoogleAuthenticationError as exc:
        raise AdminAuthenticationError(str(exc)) from None

    service_name, version = TRANSPORT_APIS.get(
        (operation.service, operation.version), (operation.service, operation.version)
    )
    service, actual_email = await _authenticate_service(
        is_oauth21_enabled(),
        service_name,
        version,
        operation.id,
        user_google_email,
        client_scopes(operation),
        _current_session_id(),
        identity_email,
        verify_account=True,
    )
    try:
        assert_identity_binding(user_google_email, actual_email=actual_email)
    except GoogleAuthenticationError as exc:
        service.close()
        raise AdminAuthenticationError(str(exc)) from None
    return pinned_client(service, operation.service, operation.version)


def mailbox_client(
    context: AdminContext, operation: OperationSpec, owner: str, directory
) -> tuple[object, str]:
    """Return (Gmail client, owner's primary address) for a delegate operation.

    Domain-wide delegation skips Google's own admin checks, so the actor must be
    a super admin and the owner an active user of the actor's customer, both read
    afresh. The token carries only the operation's Gmail settings scopes."""
    if not is_service_account_enabled():
        raise AdminPermissionError(_NEEDS_SERVICE_ACCOUNT)
    actor = _get_user(directory, context.actor_email, "admin")
    if actor.get("isAdmin") is not True:
        raise AdminBoundaryError(
            "Gmail delegate settings need a super admin, because domain-wide "
            "delegation acts as the mailbox owner."
        )
    target = guard_target(context, owner, operation, directory, protect_account=False)
    if not target.user_id or not _is_active_in(
        _get_user(directory, target.user_id, "mailbox owner"), context.customer_id
    ):
        raise AdminBoundaryError("The mailbox owner must be an active user.")
    try:
        _validate_dwd_domain(target.email, get_oauth_config())
    except GoogleAuthenticationError as exc:
        raise AdminBoundaryError(str(exc)) from None
    credentials = _get_service_account_credentials(
        client_scopes(operation), target.email
    )
    return build("gmail", "v1", credentials=credentials), target.email
