"""
Google Workspace Admin MCP Tools (Admin SDK Directory, Data Transfer, Licensing)

Tools for Workspace administrators. Every call re-verifies the selected account
as an active admin, keeps every reference inside that admin's customer, and
dispatches only pinned registry operations; writes run only after a one-use
confirmation. Loaded only when an opt-in admin service is selected, and a tool
is exposed only when all its services are.
"""

import asyncio
import json
from dataclasses import asdict, replace

from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from auth.request_identity import get_request_identity
from auth.scopes import (
    ADMIN_DIRECTORY_GROUP_MEMBER_SCOPE,
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_ORGUNIT_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    ADMIN_DIRECTORY_USER_SCOPE,
    ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
    is_service_enabled,
)
from auth.service_decorator import require_google_service
from core.server import server
from core.tool_registry import get_tool_components
from core.utils import handle_http_errors
from gadmin.auth import (
    ADMIN_SERVICE,
    ADMIN_SERVICES,
    GUARD_SCOPES,
    MAILBOX_SERVICE,
    REFERENCE_GUARD_SCOPES,
    AdminPermissionError,
    admin_service_for,
    admin_writes_allowed,
    assert_admin_permission,
    get_admin_service,
    mailbox_client,
)
from gadmin.audit import AuditSink
from gadmin.boundary import bind_organization, check_response, resolve_call, unbind
from gadmin.confirm import (
    ConfirmationError,
    confirm_operation,
    default_store as default_confirmations,
    propose_operation,
)
from gadmin.offboarding import (
    AdminClients,
    OffboardingError,
    advance_offboarding,
    get_offboarding_status as read_offboarding_status,
    plan_user_offboarding as build_offboarding_plan,
)
from gadmin.offboarding_store import (
    WorkflowStoreError,
    default_store as default_workflows,
)
from gadmin.execute import AdminApiError, execute_operation
from gadmin.guard import AdminBoundaryError, guard_target, resolve_admin_context
from gadmin.registry import (
    InvalidOperationInput,
    OperationSpec,
    UnknownOperation,
    excluded_operations,
    get_exclusion,
    get_operation,
    iter_operations,
    validate_call,
)

_ADMIN_ERRORS = (
    AdminPermissionError,
    AdminBoundaryError,
    AdminApiError,
    InvalidOperationInput,
    UnknownOperation,
    OffboardingError,
    ConfirmationError,
    WorkflowStoreError,
)

# Opt-in services each admin tool needs. A launch that loads this module without
# selecting all of a tool's services does not expose that tool.
_TOOL_SERVICES = {
    "get_admin_user": {"admin-directory"},
    "list_admin_capabilities": set(),
    "plan_user_offboarding": {
        "admin-directory",
        "admin-datatransfer",
        "admin-licensing",
    },
    "advance_user_offboarding": {
        "admin-directory",
        "admin-datatransfer",
        "admin-licensing",
    },
    "get_offboarding_status": {"admin-directory"},
    "admin_operation": {"admin-directory"},
    "confirm_admin_operation": {"admin-directory"},
}

# Registered operations that already have a dedicated MCP tool.
_TOOLS_BY_OPERATION = {"directory.users.get": "get_admin_user"}

_NOT_IMPLEMENTED = (
    "Context-aware access level changes and app assignments (Admin console only)",
)

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)


def _format_value(value) -> str:
    return value if isinstance(value, str) else json.dumps(value)


@server.tool(title="Get Admin User", annotations=_READ_ONLY)
@require_google_service(
    "admin-directory", "admin_directory_user_read", bind_identity=True
)
@handle_http_errors("get_admin_user", is_read_only=True, service_type="admin-directory")
async def get_admin_user(service, user_google_email: str, target_email: str) -> str:
    """
    Get a user's Workspace account summary, for admins of that user's customer.

    Args:
        user_google_email (str): The admin account making the request.
        target_email (str): Primary email, alias, or user ID of the user to read.

    Returns:
        str: The user's ID, customer, status, admin flags, and organizational unit.
    """
    spec = get_operation("directory.users.get")
    try:
        validate_call(spec, {"userKey": target_email}, None)
        assert_admin_permission(spec)
        context = await asyncio.to_thread(
            resolve_admin_context, user_google_email, service
        )
        target = await asyncio.to_thread(
            guard_target, context, target_email, spec, service
        )
        user = await asyncio.to_thread(
            execute_operation, service, spec, {"userKey": target.user_id}
        )
    except _ADMIN_ERRORS as exc:
        raise ToolError(str(exc)) from None
    return "\n".join(f"{key}: {_format_value(value)}" for key, value in user.items())


@server.tool(title="List Admin Capabilities", annotations=_READ_ONLY)
async def list_admin_capabilities(user_google_email: str) -> str:
    """
    List the admin operations this server supports and which are permitted.

    Makes no Google API call. Admin status and customer are verified on each
    admin tool call, not here.

    Args:
        user_google_email (str): The admin account the operations would run as.

    Returns:
        str: Registered operations with risk and availability, and the admin
        areas that are not implemented yet.
    """
    lines = [
        f"Admin capabilities for {user_google_email} on this server.",
        "Admin status and customer are verified on every admin tool call.",
        "",
    ]
    for spec in iter_operations():
        # Every operation runs through admin_operation, which needs admin-directory.
        services = (admin_service_for(spec), ADMIN_SERVICE)
        missing = [service for service in services if not is_service_enabled(service)]
        try:
            if missing:
                status = f"select the {missing[0]} service to use it"
            else:
                assert_admin_permission(spec)
                tools = filter(
                    None, (_TOOLS_BY_OPERATION.get(spec.id), "admin_operation")
                )
                status = "available via " + " or ".join(tools)
                if spec.risk != "read":
                    status += ", then confirm_admin_operation"
                if services[0] == MAILBOX_SERVICE:
                    status += " (service-account domain-wide delegation only)"
        except AdminPermissionError:
            status = "not permitted at this server's permission level"
        lines.append(
            f"- {spec.id} ({spec.risk}, {spec.service} {spec.version} "
            f"rev {spec.discovery_revision}): {status}"
        )
    lines += ["", "Excluded:"]
    lines += [f"- {e.id} ({e.category}): {e.reason}" for e in excluded_operations()]
    lines += ["", "Not implemented yet:"]
    lines += [f"- {area}" for area in _NOT_IMPLEMENTED]
    return "\n".join(lines)


_PLAN_SCOPES = [
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
    ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
    # Vault org-unit holds are matched against the user's unit and its ancestors.
    ADMIN_DIRECTORY_ORGUNIT_READONLY_SCOPE,
]
_ADVANCE_SCOPES = [
    *_PLAN_SCOPES,
    ADMIN_DIRECTORY_USER_SCOPE,
    ADMIN_DIRECTORY_GROUP_MEMBER_SCOPE,
]
# The Directory reads admin_operation's boundary rules make for every API family.
_BOUNDARY_SCOPES = [
    *GUARD_SCOPES,
    *REFERENCE_GUARD_SCOPES,
    ADMIN_DIRECTORY_ORGUNIT_READONLY_SCOPE,
]
_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


def _audit_sink() -> AuditSink:
    return AuditSink(default_workflows().directory.parent / "audit.jsonl")


def _render_state(state) -> str:
    return json.dumps({**asdict(state), "current_step": state.current_step})


def _close(clients: AdminClients | None) -> None:
    if clients:
        for client in (
            clients.transfer,
            clients.licensing,
            clients.contacts,
            clients.vault,
            clients.mailbox,
        ):
            if client is not None:
                client.close()


async def _optional_client(user_google_email: str, operation_id: str, identity):
    """A client for an optional offboarding check when its service is selected."""
    spec = get_operation(operation_id)
    if not is_service_enabled(admin_service_for(spec)):
        return None
    assert_admin_permission(spec)
    return await get_admin_service(user_google_email, spec, identity)


async def _mailbox_for(context, target_email: str, directory):
    """(Gmail client, owner) for listing the target's mail delegates, or (None, "")
    when that is not possible; the plan then says they were not checked."""
    spec = get_operation("gmail.users.settings.delegates.list")
    if not is_service_enabled(MAILBOX_SERVICE):
        return None, ""
    try:
        assert_admin_permission(spec)
        return await asyncio.to_thread(
            mailbox_client, context, spec, target_email, directory
        )
    except Exception:
        return None, ""


async def _offboarding_clients(
    user_google_email: str,
    directory,
    write: bool,
    context=None,
    target_email: str = "",
) -> AdminClients:
    """Clients for offboarding. Contact Delegation and Vault clients are added
    when selected; a Gmail client only for planning, as the target's mailbox."""
    identity = await get_request_identity()
    transfer_op = get_operation(
        "datatransfer.transfers.insert" if write else "datatransfer.applications.list"
    )
    license_op = get_operation(
        "licensing.licenseAssignments.delete"
        if write
        else "licensing.licenseAssignments.listForProduct"
    )
    assert_admin_permission(transfer_op)
    assert_admin_permission(license_op)
    contact_op = "admin.contacts.v1.users.delegates.list"
    if write:
        # Without delete permission the list client keeps the earlier steps usable;
        # the delete step itself then stops for manual action.
        delete_op = "admin.contacts.v1.users.delegates.delete"
        try:
            assert_admin_permission(get_operation(delete_op))
            contact_op = delete_op
        except AdminPermissionError:
            pass
    clients = AdminClients(
        directory,
        await get_admin_service(user_google_email, transfer_op, identity),
        None,
    )
    try:
        clients = replace(
            clients,
            licensing=await get_admin_service(user_google_email, license_op, identity),
        )
        clients = replace(
            clients,
            contacts=await _optional_client(user_google_email, contact_op, identity),
        )
        clients = replace(
            clients,
            vault=await _optional_client(
                user_google_email, "vault.matters.holds.list", identity
            ),
        )
        if context is not None and target_email:
            mailbox, owner = await _mailbox_for(context, target_email, directory)
            clients = replace(clients, mailbox=mailbox, mailbox_owner=owner)
    except BaseException:
        _close(clients)
        raise
    return clients


@server.tool(title="Plan User Offboarding", annotations=_READ_ONLY)
@require_google_service("admin-directory", _PLAN_SCOPES, bind_identity=True)
async def plan_user_offboarding(
    service,
    user_google_email: str,
    target_email: str,
    transfer_recipient: str,
    applications: list[str] | None = None,
) -> str:
    """Read a user's customer, access, data-transfer options and licenses; do not write.

    Requires all three opt-in admin services. Returns a persisted workflow ID,
    warnings about data not covered by the APIs, and exact steps needing confirmation.
    """
    clients = None
    try:
        for operation_id in (
            "directory.users.get",
            "directory.groups.list",
            "directory.roleAssignments.list",
            "directory.tokens.list",
            "datatransfer.applications.list",
            "licensing.licenseAssignments.listForProduct",
        ):
            assert_admin_permission(get_operation(operation_id))
        context = await asyncio.to_thread(
            resolve_admin_context, user_google_email, service
        )
        clients = await _offboarding_clients(
            user_google_email, service, False, context, target_email
        )
        plan = await asyncio.to_thread(
            build_offboarding_plan,
            context,
            clients,
            target_email,
            transfer_recipient,
            applications=applications,
            store=default_workflows(),
        )
        return json.dumps(asdict(plan))
    except _ADMIN_ERRORS as exc:
        raise ToolError(str(exc)) from None
    finally:
        _close(clients)


@server.tool(title="Get Offboarding Status", annotations=_READ_ONLY)
@require_google_service(
    "admin-directory", "admin_directory_user_read", bind_identity=True
)
async def get_offboarding_status(
    service, user_google_email: str, workflow_id: str
) -> str:
    """Read persisted offboarding progress without returning a confirmation token."""
    try:
        assert_admin_permission(get_operation("directory.users.get"))
        context = await asyncio.to_thread(
            resolve_admin_context, user_google_email, service
        )
        return _render_state(
            read_offboarding_status(context, workflow_id, store=default_workflows())
        )
    except _ADMIN_ERRORS as exc:
        raise ToolError(str(exc)) from None


@server.tool(title="Advance User Offboarding", annotations=_WRITE)
@require_google_service("admin-directory", _ADVANCE_SCOPES, bind_identity=True)
async def advance_user_offboarding(
    service,
    user_google_email: str,
    workflow_id: str,
    confirmation: str | None = None,
) -> str:
    """Recheck the actor, customer, target, and Google state; make at most one write.

    A destructive step first returns the exact proposal and a short-lived token.
    Pass that token back for that step only. The final deletion is a separate step.
    """
    clients = None
    try:
        assert_admin_permission(get_operation("directory.users.get"))
        context = await asyncio.to_thread(
            resolve_admin_context, user_google_email, service
        )
        clients = await _offboarding_clients(user_google_email, service, True)
        state = await asyncio.to_thread(
            advance_offboarding,
            context,
            clients,
            workflow_id,
            confirmation,
            store=default_workflows(),
            confirmations=default_confirmations(),
            audit=_audit_sink(),
        )
        return _render_state(state)
    except _ADMIN_ERRORS as exc:
        raise ToolError(str(exc)) from None
    finally:
        _close(clients)


# Proposals from admin_operation can be confirmed only by confirm_admin_operation.
_PURPOSE = "admin_operation"

_PROPOSE = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def _registered_operation(operation_id: str) -> OperationSpec:
    exclusion = get_exclusion(operation_id)
    if exclusion:
        raise ToolError(
            f"{operation_id} is excluded ({exclusion.category}): {exclusion.reason}"
        )
    try:
        return get_operation(operation_id)
    except UnknownOperation:
        raise ToolError(
            f"{operation_id} is not a registered admin operation."
        ) from None


async def _operation_client(
    user_google_email: str, spec: OperationSpec, directory, context, params
):
    """Return (operation client, Directory client for its boundary checks, Gmail
    mailbox owner or None)."""
    service = admin_service_for(spec)
    if service == MAILBOX_SERVICE:
        owner = params.get("userId") if isinstance(params, dict) else None
        if not isinstance(owner, str) or not owner:
            raise AdminBoundaryError("Could not verify the mailbox owner account.")
        client, owner = await asyncio.to_thread(
            mailbox_client, context, spec, owner, directory
        )
        return client, directory, owner
    identity = await get_request_identity()
    client = await get_admin_service(user_google_email, spec, identity)
    return client, client if service == ADMIN_SERVICE else directory, None


def _check_mailbox_owner(call, owner: str | None) -> None:
    """A Gmail call must name the mailbox its token was minted for."""
    if owner and str(call.params.get("userId", "")).casefold() != owner.casefold():
        raise AdminBoundaryError("The call must name the verified mailbox owner.")


@server.tool(title="Admin Operation", annotations=_PROPOSE)
@require_google_service("admin-directory", _BOUNDARY_SCOPES, bind_identity=True)
async def admin_operation(
    service,
    user_google_email: str,
    operation_id: str,
    params: dict | None = None,
    body: dict | None = None,
) -> str:
    """Run a registered admin read, or propose a registered admin write.

    Only operations listed by list_admin_capabilities are accepted. Customer
    parameters are set by the server, and every user, group, member, domain, role,
    organizational unit, and Cloud Identity resource reference is checked against
    the admin's own customer, and access policies against its Google Cloud
    organization. A read returns
    its bounded result. A write is not sent: it returns the exact validated
    request with a short-lived confirmation token for confirm_admin_operation.

    Args:
        user_google_email (str): The admin account making the request.
        operation_id (str): Registered operation ID, e.g. "directory.groups.get".
        params (dict): Path and query parameters for the operation.
        body (dict): Request body, for operations that take one.

    Returns:
        str: JSON with the read result, or the write proposal to confirm.
    """
    client = None
    try:
        spec = _registered_operation(operation_id)
        assert_admin_permission(get_operation("directory.users.get"))
        assert_admin_permission(spec, body if isinstance(body, dict) else None)
        context = await asyncio.to_thread(
            resolve_admin_context, user_google_email, service
        )
        client, directory, owner = await _operation_client(
            user_google_email, spec, service, context, params
        )
        context = await asyncio.to_thread(bind_organization, context, spec, client)
        call = await asyncio.to_thread(
            resolve_call, context, spec, params, body, directory, client
        )
        _check_mailbox_owner(call, owner)
        assert_admin_permission(spec, call.body)
        if call.risk == "read":
            result = await asyncio.to_thread(
                execute_operation, client, spec, call.params, call.body
            )
            await asyncio.to_thread(check_response, context, spec, result, directory)
            return json.dumps(
                {"operation": spec.id, "target": call.target, "result": result}
            )
        proposal, token = propose_operation(
            context,
            spec,
            call.params,
            call.body,
            store=default_confirmations(),
            purpose=_PURPOSE,
            target=call.target,
        )
        _audit_sink().record(
            {
                "operation_id": spec.id,
                "actor": context.actor_email,
                "customer": context.customer_id,
                "target": call.target,
                "outcome": "proposed",
                "proposal_id": proposal.id,
                "params": call.params,
                "body": call.body,
            }
        )
        return json.dumps(
            {
                "operation": spec.id,
                "risk": proposal.risk,
                "target": proposal.target,
                "params": proposal.params,
                "body": proposal.body,
                "proposal_id": proposal.id,
                "confirmation_token": token,
                "expires_at": proposal.expires_at,
            }
        )
    except _ADMIN_ERRORS as exc:
        raise ToolError(str(exc)) from None
    finally:
        if client:
            client.close()


@server.tool(title="Confirm Admin Operation", annotations=_WRITE)
@require_google_service("admin-directory", _BOUNDARY_SCOPES, bind_identity=True)
async def confirm_admin_operation(
    service,
    user_google_email: str,
    proposal_id: str,
    confirmation_token: str,
) -> str:
    """Execute a write proposed by admin_operation, once, as the same admin.

    The token is consumed by any attempt. The admin, customer, permission, and
    every reference are checked again first; if the request would now differ from
    the proposal, nothing is sent and the write must be proposed again.

    Args:
        user_google_email (str): The admin account that made the proposal.
        proposal_id (str): The proposal_id returned by admin_operation.
        confirmation_token (str): The confirmation_token returned with it.

    Returns:
        str: JSON with the operation, target, and bounded result.
    """
    client = None
    try:
        assert_admin_permission(get_operation("directory.users.get"))
        context = await asyncio.to_thread(
            resolve_admin_context, user_google_email, service
        )
        proposal = await asyncio.to_thread(
            confirm_operation,
            proposal_id,
            confirmation_token,
            context,
            default_confirmations(),
            _PURPOSE,
        )
        spec = get_operation(proposal.operation_id)
        assert_admin_permission(spec, proposal.body)
        client, directory, owner = await _operation_client(
            user_google_email, spec, service, context, proposal.params
        )
        params, body = unbind(spec, proposal.params, proposal.body)
        call = await asyncio.to_thread(
            resolve_call, context, spec, params, body, directory, client
        )
        _check_mailbox_owner(call, owner)
        if (call.params, call.body, call.target, call.risk) != (
            proposal.params,
            proposal.body,
            proposal.target,
            proposal.risk,
        ):
            raise ConfirmationError(f"{spec.id} changed after proposal; propose again.")
        event = {
            "operation_id": spec.id,
            "actor": context.actor_email,
            "customer": context.customer_id,
            "target": call.target,
            "proposal_id": proposal.id,
            "params": call.params,
            "body": call.body,
        }
        audit = _audit_sink()
        try:
            result = await asyncio.to_thread(
                execute_operation, client, spec, call.params, call.body
            )
        except AdminApiError as exc:
            uncertain = exc.status in (408, 429) or exc.status >= 500
            audit.record(
                {
                    **event,
                    "outcome": "unknown" if uncertain else "failed",
                    "status": exc.status,
                    "category": exc.category,
                }
            )
            if uncertain:
                raise ToolError(
                    f"{exc} Outcome is unknown; check Google before proposing again."
                ) from None
            raise
        except Exception:
            audit.record({**event, "outcome": "unknown"})
            raise ToolError(
                f"{spec.id} outcome is unknown; check Google before proposing again."
            ) from None
        audit.record({**event, "outcome": "succeeded"})
        return json.dumps(
            {"operation": spec.id, "target": call.target, "result": result}
        )
    except _ADMIN_ERRORS as exc:
        raise ToolError(str(exc)) from None
    finally:
        if client:
            client.close()


def hide_tools_for_unselected_services(selected_services) -> None:
    """Remove admin tools whose opt-in services were not all selected, and the
    confirmation tool when no selected admin service may write."""
    registered = get_tool_components(server)
    selected = set(selected_services)
    for tool, needed in _TOOL_SERVICES.items():
        if tool in registered and not needed <= selected:
            server.local_provider.remove_tool(tool)
    if "confirm_admin_operation" in get_tool_components(server) and not any(
        admin_writes_allowed(s) for s in selected & set(ADMIN_SERVICES.values())
    ):
        server.local_provider.remove_tool("confirm_admin_operation")
