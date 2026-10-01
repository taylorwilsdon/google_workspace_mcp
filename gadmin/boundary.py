"""Keep a registered operation inside the admin's verified customer.

Each registry entry lists boundary rules (see gadmin.registry). ``resolve_call``
applies them with fresh Directory reads, rewrites every user, account, group, and
member reference to the verified ID or address, and sets customer parameters
itself, so a caller cannot reach another customer through a key, query, or body
field. An opaque resource name (a Cloud Identity group, device, or profile) is
trusted only after a fresh registered read with the operation's own client
shows it belongs to the customer. A Chrome policy target is an organizational
unit or group read afresh from Directory, and a policy value may set only
boolean, integer, and enum fields of its schema, read afresh from Google.
An Access Context Manager call is bound to the one active Google Cloud
organization linked to the customer, found afresh on the operation's own
transport. ``check_response`` refuses read results owned outside the customer
or its organization. A delegate is removed only when a fresh, bounded list read
with the operation's own client still shows it.
Every check fails closed. Calls are synchronous; run them with
``asyncio.to_thread``.
"""

import re
from dataclasses import dataclass, replace
from typing import Any

from gadmin.client import organization_client
from gadmin.execute import execute_operation, read_pages
from gadmin.guard import (
    AdminBoundaryError,
    AdminContext,
    _has_other_active_super_admin,
    _super_admin_ids,
    guard_target,
)
from gadmin.registry import (
    BoundaryRule,
    InvalidOperationInput,
    OperationSpec,
    classify_risk,
    registered_spec,
    validate_call,
)


@dataclass(frozen=True)
class ResolvedCall:
    """Validated params and body with every reference rewritten to verified values."""

    params: dict
    body: dict | None
    target: str
    risk: str


def _read(request, what: str) -> dict:
    try:
        result = request.execute()
    except Exception:
        raise AdminBoundaryError(f"Could not verify the {what}.") from None
    if not isinstance(result, dict):
        raise AdminBoundaryError(f"Could not verify the {what}.")
    return result


def _customer_domains(context: AdminContext, directory) -> set[str]:
    """The customer's domains and domain aliases; each is verified by one customer."""
    listed = _read(
        directory.domains().list(customer=context.customer_id), "customer's domains"
    )
    names = set()
    for domain in listed.get("domains", []):
        names.add(domain.get("domainName", "").casefold())
        for alias in domain.get("domainAliases", []):
            names.add(alias.get("domainAliasName", "").casefold())
    names.discard("")
    return names


def _check_role_assignment(
    context: AdminContext, assignment_id: str, directory
) -> None:
    customer = context.customer_id
    assignment = _read(
        directory.roleAssignments().get(
            customer=customer, roleAssignmentId=assignment_id
        ),
        "role assignment",
    )
    assignee = assignment.get("assignedTo") or ""
    if not assignee:
        raise AdminBoundaryError("Could not verify the role assignment.")
    if assignee == context.actor_id:
        raise AdminBoundaryError("Removing your own role assignment is not allowed.")
    role = _read(
        directory.roles().get(customer=customer, roleId=assignment.get("roleId", "")),
        "role",
    )
    if role.get("isSuperAdminRole") is True:
        super_ids = _super_admin_ids(directory, customer)
        if not _has_other_active_super_admin(directory, customer, super_ids, assignee):
            raise AdminBoundaryError(
                "This action would remove the last active super admin."
            )


# Rules applied by the server itself, by Google, or after the call.
_UNRESOLVED_KINDS = (
    "customer",
    "deny",
    "organization",
    "global",
    "tenant",
    "response_user",
    "response_customer",
    "response_organization",
    "response_unscoped",
    "customer_record",
    "role_privileges",
)
# Parameters the server sets; a caller-supplied value is refused.
_SERVER_PARAM_KINDS = ("customer", "deny", "organization")
_ORGANIZATION_KINDS = ("organization", "response_organization")
_ORGANIZATION_NAME = re.compile(r"organizations/[0-9]+")
# Pages of organizations the admin can see that are searched for the customer's.
_ORGANIZATION_PAGES = 5


def _slots(
    params: dict, body: dict | None, rule: BoundaryRule
) -> list[tuple[dict | list, Any]]:
    """The (container, key) pairs a rule's parameter or dotted body path names.

    A path through an array reaches every element; an array of strings at the
    end yields one slot per element. Absent optional references yield none."""
    if rule.param:
        return [(params, rule.param)] if rule.param in params else []
    *parents, leaf = rule.field.split(".")
    holders = [body] if body is not None else []
    for name in parents:
        children = []
        for holder in holders:
            child = holder.get(name)
            children += child if isinstance(child, list) else [child]
        holders = [c for c in children if isinstance(c, dict)]
    slots: list[tuple[dict | list, Any]] = []
    for holder in holders:
        if isinstance(holder.get(leaf), list):
            slots += [(holder[leaf], i) for i in range(len(holder[leaf]))]
        elif leaf in holder:
            slots.append((holder, leaf))
    return slots


def _same_customer(value, customer_id: str) -> bool:
    """Alert Center reports the customer ID with or without its leading "C", and
    Cloud Identity as a "customers/{id}" name."""
    if isinstance(value, str):
        value = value.removeprefix("customers/")
    return isinstance(value, str) and value in {
        customer_id,
        customer_id.removeprefix("C"),
    }


def _customer_value(rule: BoundaryRule, customer_id: str) -> str:
    return f"customers/{customer_id}" if rule.as_ == "name" else customer_id


def _server_body_fields(spec: OperationSpec) -> list[str]:
    return [r.field for r in spec.boundary if r.kind == "customer" and r.field]


def unbind(
    operation: OperationSpec | str, params: dict, body: dict | None
) -> tuple[dict, dict | None]:
    """Remove the customer values ``resolve_call`` set, so a stored call can be
    resolved again."""
    spec = registered_spec(operation)
    server_params = {
        r.param for r in spec.boundary if r.kind in ("customer", "organization")
    }
    params = {k: v for k, v in params.items() if k not in server_params}
    if body is not None:
        body = {k: v for k, v in body.items() if k not in _server_body_fields(spec)}
    return params, body


def _needs_organization(spec: OperationSpec) -> bool:
    """Whether the operation, or a read that verifies its resources, is bound
    to the customer's Google Cloud organization."""
    specs = [spec, *(registered_spec(r.read) for r in spec.boundary if r.read)]
    return any(r.kind in _ORGANIZATION_KINDS for s in specs for r in s.boundary)


def bind_organization(
    context: AdminContext, operation: OperationSpec | str, client
) -> AdminContext:
    """Return ``context`` bound to the one active Google Cloud organization
    whose directory customer is the verified customer, or raise.

    The organizations the admin can see are searched afresh with Cloud Resource
    Manager on ``client``'s own transport and matched here, not by a query."""
    spec = registered_spec(operation)
    if not _needs_organization(spec):
        return context
    organizations = organization_client(client).organizations()
    found: set[str] = set()
    page_token = None
    for _ in range(_ORGANIZATION_PAGES):
        request = organizations.search(pageSize=100, pageToken=page_token)
        page = _read(request, "Google Cloud organization")
        for organization in page.get("organizations", []):
            if (
                isinstance(organization, dict)
                and organization.get("state") == "ACTIVE"
                and _same_customer(
                    organization.get("directoryCustomerId"), context.customer_id
                )
            ):
                found.add(organization.get("name"))
        page_token = page.get("nextPageToken")
        if not page_token:
            break
    else:
        raise AdminBoundaryError(
            "Too many Google Cloud organizations to find the customer's."
        )
    if len(found) > 1:
        raise AdminBoundaryError(
            "More than one active Google Cloud organization is linked to this customer."
        )
    name = next(iter(found), None)
    if not isinstance(name, str) or not _ORGANIZATION_NAME.fullmatch(name):
        raise AdminBoundaryError(
            "No active Google Cloud organization is linked to this customer."
        )
    return replace(context, organization=name)


def _customer_path(context: AdminContext, value: str) -> str:
    """Rewrite "customers/{x}/..." to the verified customer, or refuse."""
    _, _, rest = value.partition("/")
    customer, _, tail = rest.partition("/")
    if not value.startswith("customers/") or not (
        customer == "my_customer" or _same_customer(customer, context.customer_id)
    ):
        raise AdminBoundaryError("The resource belongs to another customer.")
    return f"customers/{context.customer_id}/{tail}"


def _check_customer_record(context: AdminContext, directory) -> None:
    customer = _read(
        directory.customers().get(customerKey=context.customer_id), "customer"
    )
    if customer.get("id") != context.customer_id:
        raise AdminBoundaryError("Could not verify the customer.")


def _check_role_privileges(context: AdminContext, body: dict | None, directory) -> None:
    privileges = body.get("rolePrivileges") if body else None
    if not isinstance(privileges, list) or not privileges or len(privileges) > 30:
        raise AdminBoundaryError("A custom role needs 1 to 30 verified privileges.")
    page = _read(
        directory.privileges().list(customer=context.customer_id), "role privileges"
    )
    items = page.get("items")
    if not isinstance(items, list):
        raise AdminBoundaryError("Could not verify the role privileges.")
    allowed = {
        (item.get("serviceId"), item.get("privilegeName"))
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("serviceId"), str)
        and isinstance(item.get("privilegeName"), str)
    }
    chosen = [(p["serviceId"], p["privilegeName"]) for p in privileges]
    if len(set(chosen)) != len(chosen) or not set(chosen) <= allowed:
        raise AdminBoundaryError("The role names unverified or repeated privileges.")


def _check_custom_role(
    context: AdminContext, role_id: str, spec: OperationSpec, directory
) -> None:
    role = _read(
        directory.roles().get(customer=context.customer_id, roleId=role_id),
        "custom role",
    )
    if (
        role.get("roleId") != role_id
        or role.get("isSystemRole") is not False
        or role.get("isSuperAdminRole") is not False
    ):
        raise AdminBoundaryError("The role is not a verified custom role.")
    if spec.id != "directory.roles.delete":
        return
    token = None
    for _ in range(10):
        page = _read(
            directory.roleAssignments().list(
                customer=context.customer_id,
                roleId=role_id,
                includeIndirectRoleAssignments=True,
                maxResults=500,
                **({"pageToken": token} if token else {}),
            ),
            "role assignments",
        )
        if page.get("items") != []:
            raise AdminBoundaryError(
                "The custom role still has or may have assignments."
            )
        token = page.get("nextPageToken")
        if not token:
            return
    raise AdminBoundaryError("Could not check all role assignments.")


def _check_custom_schema(
    context: AdminContext, schema_id: str, spec: OperationSpec, directory
) -> None:
    schema = _read(
        directory.schemas().get(customerId=context.customer_id, schemaKey=schema_id),
        "custom schema",
    )
    if schema.get("schemaId") != schema_id:
        raise AdminBoundaryError("Could not verify the custom schema.")
    if spec.id == "directory.schemas.delete" and schema.get("fields") != []:
        raise AdminBoundaryError("Only an empty custom schema can be deleted.")


def _check_org_unit(context: AdminContext, value: str, directory) -> None:
    """Verify "orgUnits/{id}[/...]" or "id:{id}" names an organizational unit of
    the customer."""
    unit = value if value.startswith("id:") else "id:" + value.split("/")[1]
    found = _read(
        directory.orgunits().get(customerId=context.customer_id, orgUnitPath=unit),
        "organizational unit",
    )
    if found.get("orgUnitId") != unit:
        raise AdminBoundaryError("Could not verify the organizational unit.")


def _check_resource(
    context: AdminContext, rule: BoundaryRule, value: str, directory, client
) -> dict:
    """Read the named resource afresh with the operation's client; the read's own
    rules bind it to the customer and its response must pass them. Returns the
    bounded read result."""
    if client is None:
        raise AdminBoundaryError("Could not verify the resource.")
    name = rule.prefix + "/".join(value.split("/")[: rule.segments or None])
    read = registered_spec(rule.read)
    call = resolve_call(context, read, {read.target_param: name}, None, directory)
    try:
        result = execute_operation(client, read, call.params)
    except Exception:
        raise AdminBoundaryError("Could not verify the resource.") from None
    check_response(context, read, result, directory)
    return result


# Pages of a delegate list searched for the delegate being removed.
_LISTED_PAGES = 10


def _check_listed(
    context: AdminContext, rule: BoundaryRule, params: dict, value, directory, client
) -> str:
    """Return ``value`` as a fresh list read with the operation's client shows it.

    The read reuses the call's already verified parameters, such as the user."""
    refused = AdminBoundaryError("Could not verify the delegate.")
    if client is None or not isinstance(value, str):
        raise refused
    read = registered_spec(rule.read)
    names = {p.name for p in read.params} - {"pageSize", "pageToken"}
    call = resolve_call(
        context, read, {k: v for k, v in params.items() if k in names}, None, directory
    )
    try:
        items = read_pages(
            client, read, call.params, read.response_items_key, _LISTED_PAGES
        )
    except Exception:
        raise refused from None
    if items is None:
        raise refused
    for item in items:
        listed = item.get(rule.field)
        if isinstance(listed, str) and listed.casefold() == value.casefold():
            return listed
    raise AdminBoundaryError("The delegate is not listed for this user.")


_INTEGER_TYPES = {
    f"TYPE_{kind}"
    for kind in (
        "INT32",
        "INT64",
        "UINT32",
        "UINT64",
        "SINT32",
        "SINT64",
        "FIXED32",
        "FIXED64",
        "SFIXED32",
        "SFIXED64",
    )
}


def _schema_types(definition: dict) -> tuple[dict, dict]:
    """Every message (by name) and enum (name to value names) of a policy
    schema's descriptor, including nested ones."""
    messages, enums = {}, {}
    pending = [definition]
    while pending:
        node = pending.pop()
        for message in [*node.get("messageType", []), *node.get("nestedType", [])]:
            messages[message["name"]] = message
            pending.append(message)
        for enum in node.get("enumType", []):
            enums[enum["name"]] = {value["name"] for value in enum.get("value", [])}
    return messages, enums


def _check_policy_value(schema: dict, schema_name: str, value) -> None:
    """Accept a policy value only if every member is a single boolean, integer,
    or declared enum field of the schema's message, so no free text is stored."""
    refused = AdminBoundaryError(
        "The policy value may set only boolean, integer, and enum fields of its schema."
    )
    try:
        messages, enums = _schema_types(schema["definition"])
        message = messages[schema_name.rpartition(".")[2]]
        fields = {}
        for field in message.get("field", []):
            fields[field["name"]] = fields[field.get("jsonName", field["name"])] = field
    except (AttributeError, KeyError, TypeError):
        raise refused from None
    if not isinstance(value, dict) or not value:
        raise refused
    for name, member in value.items():
        field = fields.get(name, {})
        kind = field.get("type")
        if field.get("label") == "LABEL_REPEATED":
            valid = False
        elif kind == "TYPE_BOOL":
            valid = isinstance(member, bool)
        elif kind in _INTEGER_TYPES:
            valid = isinstance(member, int) and not isinstance(member, bool)
        elif kind == "TYPE_ENUM":
            enum = str(field.get("typeName", "")).rpartition(".")[2]
            valid = isinstance(member, str) and member in enums.get(enum, ())
        else:
            valid = False
        if not valid:
            raise refused


def resolve_call(
    context: AdminContext,
    operation: OperationSpec | str,
    params: dict | None,
    body: dict | None,
    directory,
    client=None,
) -> ResolvedCall:
    """Validate a call and bind it to the verified customer, or raise.

    ``client`` is the operation's own Google client, needed by resource rules."""
    spec = registered_spec(operation)
    params = dict(params or {})
    refused = sorted(
        {r.param for r in spec.boundary if r.kind in _SERVER_PARAM_KINDS} & set(params)
    )
    server_fields = _server_body_fields(spec)
    if isinstance(body, dict):
        refused += sorted(set(server_fields) & set(body))
    if refused:
        raise InvalidOperationInput(
            f"Parameters {refused} are set by the server or not accepted"
        )
    for rule in spec.boundary:
        if rule.kind == "customer" and rule.param:
            params[rule.param] = _customer_value(rule, context.customer_id)
        elif rule.kind == "organization":
            if not context.organization:
                raise AdminBoundaryError(
                    "No Google Cloud organization is bound to this call."
                )
            params[rule.param] = context.organization
    if server_fields and (body is None or isinstance(body, dict)):
        body = {
            **(body or {}),
            **dict.fromkeys(server_fields, f"customers/{context.customer_id}"),
        }
    params, body = validate_call(spec, params, body)
    risk = classify_risk(spec, body)
    if any(r.kind == "customer_record" for r in spec.boundary):
        _check_customer_record(context, directory)
    if any(r.kind == "role_privileges" for r in spec.boundary):
        _check_role_privileges(context, body, directory)

    domains: set[str] | None = None
    group_id = ""
    resolved: list[str] = []
    for rule in spec.boundary:
        if rule.kind in _UNRESOLVED_KINDS:
            continue
        # Optional references are checked only when given.
        for holder, key in _slots(params, body, rule):
            value = holder[key]
            if rule.kind in ("user", "account"):
                if rule.allow_all and value == "all":
                    continue  # every user of the customer bound by customerId
                target = guard_target(
                    context,
                    value,
                    spec,
                    directory,
                    body,
                    protect_account=rule.kind == "user",
                )
                holder[key] = target.user_id if rule.as_ == "id" else target.email
                if not holder[key]:
                    raise AdminBoundaryError("Could not verify the target account.")
            elif rule.kind == "policy_target" and value.startswith("orgunits/"):
                _check_org_unit(context, value, directory)
            elif rule.kind in ("group", "domain", "policy_target"):
                if rule.kind != "domain":
                    # A policy target names its group as "groups/{id}".
                    group_key = value
                    if rule.kind == "policy_target":
                        group_key = value.removeprefix("groups/")
                    group = _read(directory.groups().get(groupKey=group_key), "group")
                    value, group_id = group.get("email") or "", group.get("id") or ""
                    if rule.kind == "group":
                        holder[key] = group_id if rule.as_ == "id" else value
                    elif group_id != group_key:
                        raise AdminBoundaryError("Could not verify the group.")
                if domains is None:
                    domains = _customer_domains(context, directory)
                domain = value.rpartition("@")[2].casefold() if "@" in value else ""
                if domain not in domains or (rule.kind != "domain" and not group_id):
                    raise AdminBoundaryError(
                        "The address must be in one of the customer's domains."
                    )
            elif rule.kind == "member":
                member = _read(
                    directory.members().get(groupKey=group_id, memberKey=value),
                    "group member",
                )
                holder[key] = member.get("id") or ""
                if not holder[key]:
                    raise AdminBoundaryError("Could not verify the group member.")
            elif rule.kind == "role":
                _read(
                    directory.roles().get(customer=context.customer_id, roleId=value),
                    "role",
                )
            elif rule.kind == "custom_role":
                _check_custom_role(context, value, spec, directory)
            elif rule.kind == "custom_schema":
                _check_custom_schema(context, value, spec, directory)
            elif rule.kind == "role_assignment":
                _check_role_assignment(context, value, directory)
            elif rule.kind == "customer_path":
                holder[key] = _customer_path(context, value)
            elif rule.kind == "org_unit":
                _check_org_unit(context, value, directory)
            elif rule.kind == "resource":
                _check_resource(context, rule, value, directory, client)
            elif rule.kind == "policy_value":
                schema = _check_resource(context, rule, value, directory, client)
                _check_policy_value(schema, value, holder.get("value"))
            elif rule.kind == "listed":
                holder[key] = _check_listed(
                    context, rule, params, value, directory, client
                )
            resolved.append(str(holder[key]))

    params, body = validate_call(spec, params, body)
    target = params.get(spec.target_param) if spec.target_param else None
    return ResolvedCall(
        params=params,
        body=body,
        target=str(target if target is not None else (resolved or [""])[0]),
        risk=risk,
    )


def check_response(
    context: AdminContext, operation: OperationSpec | str, result, directory
) -> None:
    """Refuse a read result owned by a user, customer, or organization outside
    the verified one, or an access policy scoped to Google Cloud resources."""
    spec = registered_spec(operation)
    result = result if isinstance(result, dict) else {}
    items = (
        result.get(spec.response_items_key, []) if spec.response_items_key else [result]
    )
    for rule in spec.boundary:
        if rule.kind == "response_user":
            owner = result.get(rule.field)
            if not owner:
                raise AdminBoundaryError("Could not verify the result's owner.")
            guard_target(context, owner, spec, directory)
        elif rule.kind == "response_customer":
            if not all(
                isinstance(item, dict)
                and _same_customer(item.get(rule.field), context.customer_id)
                for item in items
            ):
                raise AdminBoundaryError("The result belongs to another customer.")
        elif rule.kind == "response_organization":
            if not context.organization or not all(
                isinstance(item, dict) and item.get(rule.field) == context.organization
                for item in items
            ):
                raise AdminBoundaryError("The result belongs to another organization.")
        elif rule.kind == "response_unscoped":
            if any(isinstance(item, dict) and item.get(rule.field) for item in items):
                raise AdminBoundaryError(
                    "The access policy is scoped to Google Cloud folders or projects."
                )
