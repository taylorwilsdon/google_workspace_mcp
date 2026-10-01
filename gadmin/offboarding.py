"""Read-only offboarding plans and recoverable, one-write-at-a-time execution.

A saved pending attempt is reconciled with Google before another write. Operations
without an authoritative way to establish the earlier result stop for manual
review; they are never optimistically marked complete or blindly repeated.

Optional clients add checks. Contact Delegation adds a step that removes the
user's contact delegates one confirmed write at a time. Gmail mail delegates are
listed for manual review, never removed. Final deletion always needs a fresh
Vault hold check that finds no covering hold: without a Vault client, with a
check that could not finish, or with a covering hold it is blocked. Earlier steps
run without Vault, and ``none_found`` never certifies that nothing is preserved.
"""

import re
import secrets
from dataclasses import dataclass

from gadmin.audit import AuditSink
from gadmin.auth import AdminPermissionError, assert_admin_permission
from gadmin.confirm import (
    ConfirmationError,
    ConfirmationStore,
    confirm_operation,
    default_store as default_confirmations,
    propose_operation,
)
from gadmin.execute import AdminApiError, execute_operation, read_pages
from gadmin.guard import (
    AdminBoundaryError,
    AdminContext,
    guard_target,
    resolve_admin_context,
)
from gadmin.offboarding_store import (
    WorkflowStore,
    WorkflowStoreError,
    default_store as default_workflows,
)
from gadmin.registry import UnknownOperation, get_operation, validate_call
from gadmin.transfer import (
    USER_LICENSE_PRODUCTS as USER_LICENSE_PRODUCTS,
    LicenseLookup,
    TransferApplication as TransferApplication,
    choose_transfer_applications,
    find_transfers as find_transfers,
    find_user_licenses,
    list_transfer_applications,
    read_transfer,
    transfer_request,
)

STEPS = (
    "suspend_user",
    "sign_out",
    "revoke_tokens",
    "transfer_data",
    "wait_for_transfer",
    "remove_groups",
    "remove_licenses",
    "delete_user",
)
CONFIRMED_STEPS = ("suspend_user", "remove_licenses", "delete_user")
# Added after revoke_tokens when the plan lists contact delegates.
CONTACT_STEP = "remove_contact_delegates"
CONTACT_STEPS = (*STEPS[:3], CONTACT_STEP, *STEPS[3:])
_CONTACT_LIST = "admin.contacts.v1.users.delegates.list"
_CONTACT_DELETE = "admin.contacts.v1.users.delegates.delete"
_CONTACT_PAGES = 10
# Open matters and each matter's holds are read at most this many pages of 100.
_VAULT_PAGES = 5
_VAULT_PAGE_SIZE = 100
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")
_UNIT_ID = re.compile(r"id:[A-Za-z0-9_-]+")
# Vault corpus values documented for a hold; any other makes the check unknown.
_CORPORA = {"CALENDAR", "DRIVE", "GEMINI", "GROUPS", "HANGOUTS_CHAT", "MAIL", "VOICE"}


class OffboardingError(ValueError):
    """A workflow is unknown or belongs to another authenticated admin."""


@dataclass(frozen=True)
class AdminClients:
    directory: object
    transfer: object
    licensing: object
    contacts: object | None = None
    vault: object | None = None
    # A Gmail client is usable only for the mailbox its token was minted for.
    mailbox: object | None = None
    mailbox_owner: str = ""


@dataclass(frozen=True)
class VaultHold:
    matter_id: str
    hold_id: str
    corpus: str


@dataclass(frozen=True)
class OffboardingPlan:
    workflow_id: str
    target_email: str
    target_id: str
    recipient_email: str
    customer_id: str
    steps: tuple[str, ...]
    confirmed_steps: tuple[str, ...]
    transfer_applications: tuple[str, ...]
    unsupported_applications: tuple[str, ...]
    groups: tuple[str, ...]
    token_count: int
    licenses: LicenseLookup
    not_checked: tuple[str, ...]
    contact_delegates: tuple[str, ...] = ()
    gmail_delegates: tuple[str, ...] = ()
    vault_status: str = ""
    vault_holds: tuple[VaultHold, ...] = ()


@dataclass(frozen=True)
class StepState:
    id: str
    status: str
    message: str = ""


@dataclass(frozen=True)
class WorkflowState:
    workflow_id: str
    status: str
    steps: tuple[StepState, ...]
    confirmation: str | None = None
    not_checked: tuple[str, ...] = ()

    @property
    def current_step(self) -> str | None:
        return next((step.id for step in self.steps if step.status != "done"), None)


def _read_all(service: object, operation: str, params: dict, key: str) -> list[dict]:
    assert_admin_permission(get_operation(operation))
    items = []
    while True:
        result = execute_operation(service, operation, params)
        items.extend(result.get(key, []))
        if not result.get("nextPageToken"):
            return items
        params = {**params, "pageToken": result["nextPageToken"]}


def _fresh_context(context: AdminContext, directory) -> AdminContext:
    fresh = resolve_admin_context(context.actor_email, directory)
    if (
        fresh.actor_email.casefold() != context.actor_email.casefold()
        or fresh.customer_id != context.customer_id
        or (context.actor_id and fresh.actor_id != context.actor_id)
    ):
        raise AdminBoundaryError("The admin identity or customer changed.")
    return fresh


def _guard_users(
    context: AdminContext,
    clients: AdminClients,
    record: dict,
    spec_id: str,
    body: dict | None = None,
) -> AdminContext:
    actor = _fresh_context(context, clients.directory)
    target = guard_target(actor, record["target_id"], spec_id, clients.directory, body)
    recipient = guard_target(
        actor, record["recipient_id"], "directory.users.get", clients.directory
    )
    if (target.user_id, target.email.casefold()) != (
        record["target_id"],
        record["target_email"].casefold(),
    ) or (recipient.user_id, recipient.email.casefold()) != (
        record["recipient_id"],
        record["recipient_email"].casefold(),
    ):
        raise AdminBoundaryError("The target or transfer recipient changed; replan.")
    if target.user_id == recipient.user_id or not recipient.email:
        raise AdminBoundaryError("The transfer recipient must be a different user.")
    user = execute_operation(
        clients.directory, "directory.users.get", {"userKey": recipient.user_id}
    )
    if user.get("suspended") is not False or user.get("archived") is True:
        raise AdminBoundaryError("The transfer recipient must be an active user.")
    return actor


def _contact_delegates(clients: AdminClients, email: str) -> list[str]:
    """The addresses of the user's contact delegates, read afresh."""
    if clients.contacts is None:
        raise AdminPermissionError(
            "Contact delegates can be checked only with the admin-contact-delegation "
            "service; select it or remove them manually."
        )
    assert_admin_permission(get_operation(_CONTACT_LIST))
    items = read_pages(
        clients.contacts, _CONTACT_LIST, {"userId": email}, "delegates", _CONTACT_PAGES
    )
    if items is None:
        raise AdminBoundaryError(
            f"Could not list every contact delegate within {_CONTACT_PAGES} pages."
        )
    delegates = [item.get("email") for item in items]
    if not all(isinstance(d, str) and d for d in delegates):
        raise AdminBoundaryError(
            "A contact delegate has no verifiable address; review it manually."
        )
    return delegates


def _gmail_delegates(clients: AdminClients, email: str) -> tuple[str, ...]:
    """Each Gmail delegate as "address (status)"; raises if they cannot be read."""
    assert_admin_permission(get_operation("gmail.users.settings.delegates.list"))
    result = execute_operation(
        clients.mailbox, "gmail.users.settings.delegates.list", {"userId": email}
    )
    delegates = []
    for item in result.get("delegates", []):
        address = item.get("delegateEmail")
        if not isinstance(address, str) or not address:
            raise AdminBoundaryError("A Gmail delegate has no verifiable address.")
        delegates.append(f"{address} ({item.get('verificationStatus') or 'unknown'})")
    return tuple(delegates)


class _VaultUnchecked(Exception):
    """A Vault page, bound, or hold shape could not be checked."""


def _matches(pattern: re.Pattern, value) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _vault_pages(service, operation: str, params: dict, key: str) -> list[dict]:
    items = read_pages(
        service, operation, {**params, "pageSize": _VAULT_PAGE_SIZE}, key, _VAULT_PAGES
    )
    if items is None:
        raise _VaultUnchecked
    return items


def _hold_covers(
    clients: AdminClients, customer_id: str, user: dict, hold: dict, units: dict
) -> bool:
    """Whether a hold names the user or covers the user's unit or an ancestor.

    ``units`` caches org unit paths read afresh from Directory."""
    if not _matches(_SAFE_ID, hold.get("holdId")) or hold.get("corpus") not in _CORPORA:
        raise _VaultUnchecked
    if ("accounts" in hold) == ("orgUnit" in hold):
        raise _VaultUnchecked
    if "accounts" in hold:
        accounts = hold["accounts"]
        if not isinstance(accounts, list) or not all(
            isinstance(a, dict)
            and (
                _matches(_SAFE_ID, a.get("accountId"))
                or isinstance(a.get("email"), str)
            )
            for a in accounts
        ):
            raise _VaultUnchecked
        return any(
            a.get("accountId") == user["id"]
            or str(a.get("email", "")).casefold() == user["primaryEmail"].casefold()
            for a in accounts
        )
    unit = hold["orgUnit"]
    unit_id = unit.get("orgUnitId") if isinstance(unit, dict) else None
    if not _matches(_UNIT_ID, unit_id):
        raise _VaultUnchecked
    if unit_id not in units:
        found = execute_operation(
            clients.directory,
            "directory.orgunits.get",
            {"customerId": customer_id, "orgUnitPath": unit_id},
        )
        path = found.get("orgUnitPath")
        if found.get("orgUnitId") != unit_id or not str(path).startswith("/"):
            raise _VaultUnchecked
        units[unit_id] = path
    path = units[unit_id]
    user_path = user["orgUnitPath"]
    return path == "/" or user_path == path or user_path.startswith(path + "/")


def _vault_holds(
    clients: AdminClients, customer_id: str, user_id: str
) -> tuple[str, tuple[VaultHold, ...]]:
    """Return ("held", holds), ("none_found", ()), or ("unknown", ()).

    Only open matters the admin can see are read, so "none_found" is not proof
    that nothing preserves the user's data."""
    try:
        for operation in (
            "vault.matters.list",
            "vault.matters.holds.list",
            "directory.users.get",
            "directory.orgunits.get",
        ):
            assert_admin_permission(get_operation(operation))
        user = execute_operation(
            clients.directory, "directory.users.get", {"userKey": user_id}
        )
        if (
            user.get("id") != user_id
            or not isinstance(user.get("primaryEmail"), str)
            or not str(user.get("orgUnitPath")).startswith("/")
        ):
            raise _VaultUnchecked
        units: dict[str, str] = {}
        holds = []
        for matter in _vault_pages(
            clients.vault, "vault.matters.list", {"state": "OPEN"}, "matters"
        ):
            matter_id = matter.get("matterId")
            if not _matches(_SAFE_ID, matter_id):
                raise _VaultUnchecked
            holds += [
                VaultHold(matter_id, hold["holdId"], hold["corpus"])
                for hold in _vault_pages(
                    clients.vault,
                    "vault.matters.holds.list",
                    {"matterId": matter_id, "view": "FULL_HOLD"},
                    "holds",
                )
                if _hold_covers(clients, customer_id, user, hold, units)
            ]
    except Exception:
        # Any failed page, permission error, bound, or unexpected shape.
        return "unknown", ()
    return ("held", tuple(holds)) if holds else ("none_found", ())


def _state(record: dict) -> WorkflowState:
    steps = tuple(
        StepState(s["id"], s["status"], s.get("message", "")) for s in record["steps"]
    )
    active = next((s for s in record["steps"] if s["status"] != "done"), None)
    warnings = tuple(record.get("not_checked", ()))
    if active is None:
        return WorkflowState(
            record["workflow_id"], "completed", steps, not_checked=warnings
        )
    status = active["status"]
    if status in ("pending", "in_progress"):
        status = "in_progress"
    return WorkflowState(
        record["workflow_id"],
        status,
        steps,
        active.get("_issued_confirmation"),
        warnings,
    )


def _owned(context: AdminContext, workflow_id: str, store: WorkflowStore) -> dict:
    try:
        record = store.load(workflow_id)
    except WorkflowStoreError as exc:
        raise OffboardingError(str(exc)) from None
    if (record.get("actor_email", "").casefold(), record.get("customer_id")) != (
        context.actor_email.casefold(),
        context.customer_id,
    ) or (context.actor_id and record.get("actor_id") != context.actor_id):
        raise OffboardingError("This workflow belongs to another admin or customer.")
    if [step.get("id") for step in record["steps"]] not in (
        list(STEPS),
        list(CONTACT_STEPS),
    ):
        raise OffboardingError("The workflow has an invalid step list.")
    return record


def get_offboarding_status(
    context: AdminContext, workflow_id: str, *, store: WorkflowStore | None = None
) -> WorkflowState:
    return _state(_owned(context, workflow_id, store or default_workflows()))


def plan_user_offboarding(
    context: AdminContext,
    clients: AdminClients,
    target_email: str,
    transfer_recipient: str,
    *,
    applications: list[str] | None = None,
    store: WorkflowStore | None = None,
) -> OffboardingPlan:
    """Read the customer, target, recipient, access, transfers and licenses; no writes."""
    store = store or default_workflows()
    for user_key in (target_email, transfer_recipient):
        validate_call(get_operation("directory.users.get"), {"userKey": user_key}, None)
    for op in (
        "directory.users.get",
        "directory.groups.list",
        "directory.roleAssignments.list",
        "directory.tokens.list",
        "datatransfer.applications.list",
        "datatransfer.transfers.list",
        "licensing.licenseAssignments.listForProduct",
    ):
        assert_admin_permission(get_operation(op))
    actor = _fresh_context(context, clients.directory)
    target = guard_target(
        actor,
        target_email,
        "directory.users.update",
        clients.directory,
        {"suspended": True},
    )
    recipient = guard_target(
        actor, transfer_recipient, "directory.users.get", clients.directory
    )
    if (
        not target.user_id
        or not target.email
        or not recipient.user_id
        or not recipient.email
    ):
        raise AdminBoundaryError("Could not verify both user accounts.")
    if target.user_id == recipient.user_id:
        raise AdminBoundaryError("The transfer recipient must be a different user.")
    recipient_user = execute_operation(
        clients.directory, "directory.users.get", {"userKey": recipient.user_id}
    )
    if (
        recipient_user.get("suspended") is not False
        or recipient_user.get("archived") is True
    ):
        raise AdminBoundaryError("The transfer recipient must be an active user.")
    groups = _read_all(
        clients.directory,
        "directory.groups.list",
        {"userKey": target.user_id},
        "groups",
    )
    roles = _read_all(
        clients.directory,
        "directory.roleAssignments.list",
        {"customer": actor.customer_id, "userKey": target.user_id},
        "items",
    )
    tokens = _read_all(
        clients.directory, "directory.tokens.list", {"userKey": target.user_id}, "items"
    )
    available = list_transfer_applications(clients.transfer, actor.customer_id)
    chosen, unsupported = choose_transfer_applications(available, applications)
    licenses = find_user_licenses(clients.licensing, actor.customer_id, target.email)
    previous = _read_all(
        clients.transfer,
        "datatransfer.transfers.list",
        {
            "customerId": actor.customer_id,
            "oldOwnerUserId": target.user_id,
            "newOwnerUserId": recipient.user_id,
        },
        "dataTransfers",
    )
    contacts = None
    if clients.contacts is not None:
        contacts = _contact_delegates(clients, target.email)
    gmail, gmail_error = (), False
    use_mailbox = (
        clients.mailbox is not None
        and clients.mailbox_owner.casefold() == target.email.casefold()
    )
    if use_mailbox:
        try:
            gmail = _gmail_delegates(clients, target.email)
        except Exception:
            gmail_error = True
    vault_status, vault_holds = "", ()
    if clients.vault is not None:
        vault_status, vault_holds = _vault_holds(
            clients, actor.customer_id, target.user_id
        )
    steps = STEPS if contacts is None else CONTACT_STEPS
    workflow_id = secrets.token_urlsafe(18)
    record = {
        "workflow_id": workflow_id,
        "actor_email": actor.actor_email,
        "actor_id": actor.actor_id,
        "customer_id": actor.customer_id,
        "target_email": target.email,
        "target_id": target.user_id,
        "recipient_email": recipient.email,
        "recipient_id": recipient.user_id,
        "transfer_body": transfer_request(target.user_id, recipient.user_id, chosen),
        "transfer_names": [app.name for app in chosen],
        "unsupported": list(unsupported),
        "previous_transfer_ids": [t["id"] for t in previous if t.get("id")],
        "transfer_id": None,
        "steps": [
            {
                "id": name,
                "status": "pending",
                "message": "",
                "attempt": None,
                "proposal_id": None,
            }
            for name in steps
        ],
    }
    gaps = (
        "Vault retention rules have not been checked; review them in the Admin console before deletion.",
        "Vault matters this admin cannot see were not checked; their holds still apply.",
    )
    if clients.vault is None:
        gaps += (
            "Vault holds were not checked: final deletion is blocked until a fresh check with the admin-vault service finds no covering hold.",
        )
    elif vault_status == "held":
        gaps += (
            "Vault holds cover this user and block deletion: "
            + ", ".join(f"{h.matter_id}/{h.hold_id} ({h.corpus})" for h in vault_holds)
            + ".",
        )
    elif vault_status == "unknown":
        gaps += (
            "Vault holds could not be fully checked; deletion is blocked until they can be.",
        )
    if contacts is None:
        gaps += (
            "Contact delegates were not checked: this needs admin-contact-delegation. Review them manually.",
        )
    else:
        gaps += (
            "Contact delegation where this user is a delegate of another user cannot be looked up; review it manually.",
        )
    if not use_mailbox:
        gaps += (
            "Gmail mail delegates were not checked: this needs admin-gmail-delegates with a domain-wide delegation service account. Review them manually.",
        )
    elif gmail_error:
        gaps += ("Gmail mail delegates could not be checked; review them manually.",)
    elif gmail:
        gaps += (
            f"Gmail mail delegates are not removed by this workflow; review them manually: {', '.join(gmail)}.",
        )
    gaps += ("Other app-specific data has not been verified; review manually.",)
    if roles:
        gaps += ("Admin role assignments require manual review before deletion.",)
    if not licenses.complete:
        gaps += tuple(
            f"Licensing product {product} was not checked ({reason})."
            for product, reason in licenses.unchecked_products
        )
    record["not_checked"] = list(gaps)
    store.create(record)
    return OffboardingPlan(
        workflow_id,
        target.email,
        target.user_id,
        recipient.email,
        actor.customer_id,
        steps,
        tuple(s for s in steps if s in (*CONFIRMED_STEPS, CONTACT_STEP)),
        tuple(app.name for app in chosen),
        unsupported,
        tuple(group.get("email", "") for group in groups),
        len(tokens),
        licenses,
        gaps,
        tuple(contacts or ()),
        gmail,
        vault_status,
        vault_holds,
    )


def _update(
    record: dict,
    step: dict,
    store: WorkflowStore,
    status: str,
    message: str = "",
    **fields,
) -> None:
    step.update(status=status, message=message, **fields)
    store.save(record)


def _confirmation(
    context: AdminContext,
    record: dict,
    step: dict,
    store: WorkflowStore,
    confirmations: ConfirmationStore,
    supplied: str | None,
    operation: str,
    params: dict,
    body: dict | None = None,
) -> bool:
    """Return True when the exact fresh proposal is confirmed, otherwise issue it."""
    assert_admin_permission(get_operation(operation), body)
    if supplied is None:
        proposal, token = propose_operation(
            context, operation, params, body, confirmations
        )
        _update(
            record,
            step,
            store,
            "awaiting_confirmation",
            f"Confirm {operation} for {record['target_email']} in {context.customer_id}: {params} {body or ''}",
            proposal_id=proposal.id,
        )
        step["_issued_confirmation"] = f"{proposal.id}:{token}"
        return False
    if not isinstance(supplied, str) or supplied.count(":") != 1:
        raise ConfirmationError("Invalid offboarding confirmation.")
    proposal_id, token = supplied.split(":", 1)
    if not proposal_id or not token or step.get("proposal_id") != proposal_id:
        raise ConfirmationError("The confirmation does not belong to the current step.")
    proposal = confirm_operation(proposal_id, token, context, confirmations)
    if (
        proposal.operation_id != operation
        or proposal.params != params
        or proposal.body != body
        or proposal.target != params.get(get_operation(operation).target_param)
    ):
        raise ConfirmationError(
            "The confirmation no longer matches the proposed action."
        )
    step["proposal_id"] = None
    return True


def _write(
    context: AdminContext,
    clients: AdminClients,
    record: dict,
    step: dict,
    store: WorkflowStore,
    audit: AuditSink,
    operation: str,
    params: dict,
    body: dict | None = None,
) -> None:
    spec = get_operation(operation)
    assert_admin_permission(spec, body)
    _guard_users(context, clients, record, operation, body)
    _update(
        record,
        step,
        store,
        "in_progress",
        f"Checking Google state for {operation}.",
        attempt={"operation": operation, "params": params, "body": body},
    )
    if operation.startswith("datatransfer."):
        service = clients.transfer
    elif operation.startswith("licensing."):
        service = clients.licensing
    elif operation.startswith("admin.contacts."):
        service = clients.contacts
    else:
        service = clients.directory
    event = {
        "operation_id": operation,
        "method": spec.method,
        "actor": context.actor_email,
        "customer": context.customer_id,
        "target": record["target_email"],
        "workflow_id": record["workflow_id"],
        "params": params,
        "body": body,
    }
    try:
        result = execute_operation(service, spec, params, body)
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
        raise
    except Exception:
        # A transport failure: Google may or may not have applied the write.
        audit.record({**event, "outcome": "unknown"})
        raise
    audit.record({**event, "outcome": "accepted"})
    if operation == "datatransfer.transfers.insert":
        transfer_id = result.get("id")
        if not transfer_id:
            raise AdminBoundaryError(
                "Google did not return a transfer ID; inspect transfers manually."
            )
        record["transfer_id"] = transfer_id


def _transfer_matches(transfer: dict, record: dict) -> bool:
    expected = record["transfer_body"]
    applications = transfer.get("applicationDataTransfers")
    if not isinstance(applications, list):
        return False
    requested = [
        {
            "applicationId": app.get("applicationId"),
            "applicationTransferParams": app.get("applicationTransferParams"),
        }
        for app in applications
        if isinstance(app, dict)
    ]
    return (
        transfer.get("oldOwnerUserId") == expected["oldOwnerUserId"]
        and transfer.get("newOwnerUserId") == expected["newOwnerUserId"]
        and len(requested) == len(applications)
        and requested == expected["applicationDataTransfers"]
    )


def _reconcile_transfer(clients: AdminClients, record: dict) -> str | None:
    transfers = _read_all(
        clients.transfer,
        "datatransfer.transfers.list",
        {
            "customerId": record["customer_id"],
            "oldOwnerUserId": record["target_id"],
            "newOwnerUserId": record["recipient_id"],
        },
        "dataTransfers",
    )
    matches = [
        t["id"]
        for t in transfers
        if t.get("id") not in record["previous_transfer_ids"]
        and _transfer_matches(t, record)
        and t.get("id")
    ]
    if len(matches) != 1:
        raise AdminBoundaryError(
            "Cannot identify exactly one submitted transfer; inspect Google Data Transfer manually."
        )
    return matches[0]


def _remaining_groups(clients: AdminClients, record: dict) -> list[dict]:
    groups = _read_all(
        clients.directory,
        "directory.groups.list",
        {"userKey": record["target_id"]},
        "groups",
    )
    customer_groups = _read_all(
        clients.directory,
        "directory.groups.list",
        {"customer": record["customer_id"]},
        "groups",
    )
    customer_group_ids = {
        group.get("id") for group in customer_groups if group.get("id")
    }
    direct = []
    for group in groups:
        if not group.get("id") or group["id"] not in customer_group_ids:
            raise AdminBoundaryError(
                "A group is not verified inside the target customer; clean it up manually."
            )
        members = _read_all(
            clients.directory,
            "directory.members.list",
            {"groupKey": group["id"]},
            "members",
        )
        if any(m.get("id") == record["target_id"] for m in members):
            direct.append(group)
        elif any(
            m.get("email", "").casefold() == record["target_email"].casefold()
            for m in members
        ):
            direct.append(group)
        else:
            raise AdminBoundaryError(
                "An inherited or unverified group membership needs manual cleanup."
            )
    return direct


def _transfer_gate(clients: AdminClients, record: dict) -> str:
    transfer_id = record.get("transfer_id")
    if not transfer_id:
        raise AdminBoundaryError(
            "No transfer ID is recorded; do not remove access or licenses."
        )
    transfer = read_transfer(clients.transfer, transfer_id)
    if transfer.id != transfer_id or not _transfer_matches(
        transfer.request or {}, record
    ):
        raise AdminBoundaryError(
            "Google transfer ID or source, destination, and applications do not match this workflow."
        )
    return (
        transfer.state
        if transfer.state != "pending"
        else (transfer.google_status or "pending")
    )


def _perform_step(
    context: AdminContext,
    clients: AdminClients,
    record: dict,
    step: dict,
    store: WorkflowStore,
    confirmations: ConfirmationStore,
    audit: AuditSink,
    confirmation: str | None,
) -> bool:
    """Advance one step; return True when a Google write was attempted."""
    name = step["id"]
    if name == "suspend_user":
        body = {"suspended": True}
        actor = _guard_users(context, clients, record, "directory.users.update", body)
        user = execute_operation(
            clients.directory, "directory.users.get", {"userKey": record["target_id"]}
        )
        if user.get("suspended") is True:
            _update(
                record,
                step,
                store,
                "done",
                "Google reports the user already suspended.",
                attempt=None,
            )
            return False
        if step.get("attempt"):
            raise AdminBoundaryError(
                "Suspension result is uncertain; verify Google state before retrying with a new plan."
            )
        params = {"userKey": record["target_id"]}
        if not _confirmation(
            actor,
            record,
            step,
            store,
            confirmations,
            confirmation,
            "directory.users.update",
            params,
            body,
        ):
            return False
        _write(
            actor,
            clients,
            record,
            step,
            store,
            audit,
            "directory.users.update",
            params,
            body,
        )
        _update(
            record,
            step,
            store,
            "done",
            "Google reports the user suspended.",
            attempt=None,
        )
        return True

    if name == "sign_out":
        if step.get("attempt"):
            raise AdminBoundaryError(
                "Sign-out cannot be reconciled from Google state; inspect sessions manually."
            )
        _write(
            context,
            clients,
            record,
            step,
            store,
            audit,
            "directory.users.signOut",
            {"userKey": record["target_id"]},
        )
        _update(
            record, step, store, "done", "Sign-out accepted by Google.", attempt=None
        )
        return True

    if name == "revoke_tokens":
        tokens = _read_all(
            clients.directory,
            "directory.tokens.list",
            {"userKey": record["target_id"]},
            "items",
        )
        if not tokens:
            _update(
                record, step, store, "done", "No active tokens remain.", attempt=None
            )
            return False
        if step.get("attempt"):
            attempted = step["attempt"]["params"]["clientId"]
            if any(token.get("clientId") == attempted for token in tokens):
                raise AdminBoundaryError(
                    "Token revocation result is uncertain; inspect the token manually."
                )
            _update(
                record,
                step,
                store,
                "pending",
                "Prior token revocation verified.",
                attempt=None,
            )
        client_id = tokens[0].get("clientId")
        if not client_id:
            raise AdminBoundaryError(
                "A token has no verifiable client ID; revoke it manually."
            )
        _write(
            context,
            clients,
            record,
            step,
            store,
            audit,
            "directory.tokens.delete",
            {"userKey": record["target_id"], "clientId": client_id},
        )
        _update(
            record,
            step,
            store,
            "pending",
            f"Revoked one token for {record['target_email']}.",
            attempt=None,
        )
        return True

    if name == CONTACT_STEP:
        delegates = _contact_delegates(clients, record["target_email"])
        if step.get("attempt"):
            attempted = step["attempt"]["params"]["delegate"].casefold()
            if any(d.casefold() == attempted for d in delegates):
                raise AdminBoundaryError(
                    "Contact delegate removal is uncertain; inspect the delegate manually."
                )
            _update(
                record,
                step,
                store,
                "pending",
                "Prior contact delegate removal verified.",
                attempt=None,
            )
        if not delegates:
            _update(
                record,
                step,
                store,
                "done",
                "No contact delegates remain.",
                attempt=None,
            )
            return False
        params = {"userId": record["target_email"], "delegate": delegates[0]}
        # Do not consume a confirmation for a delegate list changed in Google.
        if (
            confirmation is not None
            and step.get("proposal_id")
            and step.get("proposed_params") != params
        ):
            raise ConfirmationError(
                "The confirmation no longer matches the proposed delegate."
            )
        step["proposed_params"] = params
        if not _confirmation(
            context,
            record,
            step,
            store,
            confirmations,
            confirmation,
            _CONTACT_DELETE,
            params,
        ):
            return False
        _write(context, clients, record, step, store, audit, _CONTACT_DELETE, params)
        step["proposed_params"] = None
        _update(
            record,
            step,
            store,
            "pending",
            f"Removed contact delegate {delegates[0]}.",
            attempt=None,
        )
        return True

    if name == "transfer_data":
        if record["unsupported"]:
            raise AdminBoundaryError(
                f"Unsupported transfer applications: {', '.join(record['unsupported'])}. Transfer these manually before continuing."
            )
        if not record["transfer_body"]["applicationDataTransfers"]:
            raise AdminBoundaryError(
                "No transferable applications were verified; inspect the user's data manually."
            )
        if step.get("attempt"):
            record["transfer_id"] = _reconcile_transfer(clients, record)
            _update(
                record,
                step,
                store,
                "done",
                "Recovered the Google transfer by exact payload.",
                attempt=None,
            )
            return False
        available = list_transfer_applications(clients.transfer, record["customer_id"])
        matched = [
            a
            for a in available
            if a.id
            in {
                x["applicationId"]
                for x in record["transfer_body"]["applicationDataTransfers"]
            }
        ]
        if (
            transfer_request(record["target_id"], record["recipient_id"], matched)
            != record["transfer_body"]
        ):
            raise AdminBoundaryError(
                "Transfer applications changed since planning; create a new plan."
            )
        _write(
            context,
            clients,
            record,
            step,
            store,
            audit,
            "datatransfer.transfers.insert",
            {},
            record["transfer_body"],
        )
        _update(
            record,
            step,
            store,
            "done",
            f"Google transfer {record['transfer_id']} submitted.",
            attempt=None,
        )
        return True

    if name == "wait_for_transfer":
        _guard_users(context, clients, record, "datatransfer.transfers.get")
        status = _transfer_gate(clients, record)
        if status == "failed":
            _update(
                record,
                step,
                store,
                "failed",
                "Google reports the data transfer failed.",
            )
        elif status != "completed":
            _update(
                record, step, store, "waiting", f"Google transfer status: {status}."
            )
        else:
            _update(
                record,
                step,
                store,
                "done",
                "Google reports the data transfer completed.",
            )
        return False

    # Every remaining step must re-check the transfer, including after a restart.
    if _transfer_gate(clients, record) != "completed":
        raise AdminBoundaryError(
            "Transfer is not completed; licenses and account must remain intact."
        )

    if name == "remove_groups":
        groups = _remaining_groups(clients, record)
        if not groups:
            _update(
                record,
                step,
                store,
                "done",
                "No direct group memberships remain.",
                attempt=None,
            )
            return False
        if step.get("attempt"):
            attempted = step["attempt"]["params"]["groupKey"]
            if any(g["id"] == attempted for g in groups):
                raise AdminBoundaryError(
                    "Group removal result is uncertain; inspect the membership manually."
                )
            _update(
                record,
                step,
                store,
                "pending",
                "Prior group removal verified.",
                attempt=None,
            )
        _write(
            context,
            clients,
            record,
            step,
            store,
            audit,
            "directory.members.delete",
            {"groupKey": groups[0]["id"], "memberKey": record["target_id"]},
        )
        _update(
            record,
            step,
            store,
            "pending",
            "Removed one direct group membership.",
            attempt=None,
        )
        return True

    if name == "remove_licenses":
        assert_admin_permission(
            get_operation("licensing.licenseAssignments.listForProduct")
        )
        _guard_users(context, clients, record, "licensing.licenseAssignments.delete")
        lookup = find_user_licenses(
            clients.licensing, record["customer_id"], record["target_email"]
        )
        if not lookup.complete:
            raise AdminBoundaryError(
                f"Unchecked licensing products: {', '.join(p for p, _ in lookup.unchecked_products)}. Inspect licenses manually."
            )
        assignments = lookup.assignments
        if step.get("attempt"):
            attempted = step["attempt"]["params"]
            if any(
                a.product_id == attempted["productId"]
                and a.sku_id == attempted["skuId"]
                for a in assignments
            ):
                raise AdminBoundaryError(
                    "License removal is uncertain; inspect the assignment manually."
                )
            _update(
                record,
                step,
                store,
                "pending",
                "Prior license removal verified.",
                attempt=None,
            )
        if not assignments:
            _update(
                record,
                step,
                store,
                "done",
                "No assigned licenses remain.",
                attempt=None,
            )
            return False
        assignment = assignments[0]
        params = {
            "productId": assignment.product_id,
            "skuId": assignment.sku_id,
            "userId": record["target_email"],
        }
        if confirmation is not None and step.get("proposal_id"):
            # Do not consume a confirmation for an assignment changed in Google.
            proposal = step.get("proposed_params")
            if proposal != params:
                raise ConfirmationError(
                    "The confirmation no longer matches the proposed license."
                )
        step["proposed_params"] = params
        if not _confirmation(
            context,
            record,
            step,
            store,
            confirmations,
            confirmation,
            "licensing.licenseAssignments.delete",
            params,
        ):
            return False
        _write(
            context,
            clients,
            record,
            step,
            store,
            audit,
            "licensing.licenseAssignments.delete",
            params,
        )
        step["proposed_params"] = None
        _update(
            record,
            step,
            store,
            "pending",
            "Removed one license assignment.",
            attempt=None,
        )
        return True

    if name == "delete_user":
        actor = _fresh_context(context, clients.directory)
        if step.get("attempt"):
            try:
                execute_operation(
                    clients.directory,
                    "directory.users.get",
                    {"userKey": record["target_id"]},
                )
            except AdminApiError as exc:
                if exc.category != "not_found":
                    raise
                _update(
                    record,
                    step,
                    store,
                    "done",
                    "Google confirms the account no longer exists.",
                    attempt=None,
                )
                return False
            raise AdminBoundaryError(
                "Deletion result is uncertain; account still exists. Inspect Google manually."
            )
        groups = _remaining_groups(clients, record)
        licenses = find_user_licenses(
            clients.licensing, record["customer_id"], record["target_email"]
        )
        tokens = _read_all(
            clients.directory,
            "directory.tokens.list",
            {"userKey": record["target_id"]},
            "items",
        )
        if groups or tokens or not licenses.complete or licenses.assignments:
            raise AdminBoundaryError(
                "Group, token, or license cleanup is incomplete; deletion is blocked."
            )
        if CONTACT_STEP in (s["id"] for s in record["steps"]) and _contact_delegates(
            clients, record["target_email"]
        ):
            raise AdminBoundaryError("A contact delegate remains; deletion is blocked.")
        # Checked before every proposal and again at confirmation, with no override.
        if clients.vault is None:
            raise AdminPermissionError(
                "Vault holds must be checked with the admin-vault service before deletion."
            )
        status, holds = _vault_holds(
            clients, record["customer_id"], record["target_id"]
        )
        if status == "held":
            raise AdminBoundaryError(
                "A Vault hold covers this user; deletion is blocked: "
                + ", ".join(f"{h.matter_id}/{h.hold_id}" for h in holds)
                + "."
            )
        if status != "none_found":
            raise AdminBoundaryError(
                "Vault holds could not be fully checked; deletion is blocked."
            )
        _guard_users(actor, clients, record, "directory.users.delete")
        params = {"userKey": record["target_id"]}
        if not _confirmation(
            actor,
            record,
            step,
            store,
            confirmations,
            confirmation,
            "directory.users.delete",
            params,
        ):
            return False
        _write(
            actor, clients, record, step, store, audit, "directory.users.delete", params
        )
        _update(
            record,
            step,
            store,
            "done",
            "Google accepted the separate final account deletion.",
            attempt=None,
        )
        return True

    raise OffboardingError("Unknown offboarding step.")


def advance_offboarding(
    context: AdminContext,
    clients: AdminClients,
    workflow_id: str,
    confirmation: str | None = None,
    *,
    store: WorkflowStore | None = None,
    confirmations: ConfirmationStore | None = None,
    audit: AuditSink | None = None,
) -> WorkflowState:
    """Advance through reads and at most one Google write under a file lock."""
    store = store or default_workflows()
    confirmations = confirmations or default_confirmations()
    if audit is None:
        audit = AuditSink(store.directory.parent / "audit.jsonl")
    try:
        with store.locked(workflow_id):
            record = _owned(context, workflow_id, store)
            _fresh_context(context, clients.directory)
            if (
                confirmation is not None
                and _state(record).status != "awaiting_confirmation"
            ):
                raise ConfirmationError(
                    "There is no current step awaiting confirmation."
                )
            for step in record["steps"]:
                if step["status"] == "done":
                    continue
                try:
                    wrote = _perform_step(
                        context,
                        clients,
                        record,
                        step,
                        store,
                        confirmations,
                        audit,
                        confirmation,
                    )
                except (
                    AdminBoundaryError,
                    AdminPermissionError,
                    AdminApiError,
                    UnknownOperation,
                ) as exc:
                    status = (
                        "failed"
                        if isinstance(exc, AdminApiError) and exc.status >= 500
                        else "manual_action_required"
                    )
                    message = (
                        f"Admin operation is not in the pinned registry: {exc.args[0]}."
                        if isinstance(exc, UnknownOperation)
                        else str(exc)
                    )
                    _update(record, step, store, status, message)
                    return _state(record)
                confirmation = None
                if wrote or step["status"] != "done":
                    break
            return _state(record)
    except WorkflowStoreError as exc:
        raise OffboardingError(str(exc)) from None
