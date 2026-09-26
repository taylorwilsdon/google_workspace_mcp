"""Customer, self, and last-super-admin checks for admin targets.

Every check reads fresh Directory data with the admin's own client and fails
closed: when identity, customer, or super-admin status cannot be established the
action is refused. Calls are synchronous; run them with ``asyncio.to_thread``.
"""

from dataclasses import dataclass

from gadmin.registry import OperationSpec, classify_risk, registered_spec


class AdminBoundaryError(PermissionError):
    """The action falls outside the admin's customer or protected accounts."""


@dataclass(frozen=True)
class AdminContext:
    actor_email: str
    customer_id: str
    actor_id: str = ""
    # The Google Cloud organization linked to the customer, as
    # "organizations/{id}", bound only for Access Context Manager calls.
    organization: str = ""


@dataclass(frozen=True)
class VerifiedTarget:
    email: str
    user_id: str
    customer_id: str


def _get_user(directory_service, user_key: str, purpose: str) -> dict:
    try:
        user = directory_service.users().get(userKey=user_key).execute()
    except Exception:
        raise AdminBoundaryError(f"Could not verify the {purpose} account.") from None
    if not isinstance(user, dict):
        raise AdminBoundaryError(f"Could not verify the {purpose} account.")
    return user


def _is_active_in(user: dict, customer_id: str) -> bool:
    return (
        user.get("customerId") == customer_id
        and user.get("suspended") is False
        and user.get("archived") is not True
    )


def resolve_admin_context(actor_email: str, directory_service) -> AdminContext:
    """Build the actor's context from a fresh lookup of the actor's own account."""
    user = _get_user(directory_service, actor_email, "admin")
    primary_email = user.get("primaryEmail") or ""
    customer_id = user.get("customerId") or ""
    if primary_email.casefold() != actor_email.casefold():
        raise AdminBoundaryError("The admin account does not match the selection.")
    if not customer_id:
        raise AdminBoundaryError("The admin account's customer could not be resolved.")
    if not user.get("id"):
        raise AdminBoundaryError(
            "The admin account's immutable ID could not be resolved."
        )
    if user.get("isAdmin") is not True and user.get("isDelegatedAdmin") is not True:
        raise AdminBoundaryError("The selected account is not a Workspace admin.")
    if not _is_active_in(user, customer_id):
        raise AdminBoundaryError("The admin account is not active.")
    return AdminContext(
        actor_email=primary_email, customer_id=customer_id, actor_id=user.get("id", "")
    )


def _list_all(list_request, **params) -> list[dict]:
    items: list[dict] = []
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        page = list_request(**params).execute()
        items.extend(page.get("items", []))
        page_token = page.get("nextPageToken")
        if not page_token:
            return items


def _super_admin_ids(directory_service, customer_id: str) -> set[str]:
    """User IDs directly assigned a super-admin role. Group assignees are not
    expanded, so the count can only err low, which refuses more, not less."""
    try:
        roles = _list_all(directory_service.roles().list, customer=customer_id)
        super_role_ids = [
            r["roleId"] for r in roles if r.get("isSuperAdminRole") is True
        ]
        if not super_role_ids:
            raise LookupError("no super admin role")
        user_ids: set[str] = set()
        for role_id in super_role_ids:
            for assignment in _list_all(
                directory_service.roleAssignments().list,
                customer=customer_id,
                roleId=role_id,
            ):
                if assignment.get("assigneeType", "user") == "user":
                    user_ids.add(assignment["assignedTo"])
        return user_ids
    except Exception:
        raise AdminBoundaryError(
            "Could not determine the customer's super admins."
        ) from None


def _has_other_active_super_admin(
    directory_service, customer_id: str, super_ids: set[str], target_id: str
) -> bool:
    for user_id in sorted(super_ids - {target_id}):
        try:
            other = _get_user(directory_service, user_id, "other admin")
        except AdminBoundaryError:
            continue  # unverifiable admins do not count as active
        if _is_active_in(other, customer_id):
            return True
    return False


def guard_target(
    context: AdminContext,
    target_user: str,
    operation: OperationSpec | str,
    directory_service,
    body: dict | None = None,
    *,
    protect_account: bool = True,
) -> VerifiedTarget:
    """Return the verified target or raise AdminBoundaryError.

    ``protect_account=False`` only checks the customer: the reference names a
    custodian or collaborator (Vault), not an account being suspended or deleted,
    so the self and last-super-admin protections do not apply."""
    spec = registered_spec(operation)
    if not context.customer_id:
        raise AdminBoundaryError("The admin's customer could not be resolved.")

    target = _get_user(directory_service, target_user, "target")
    target_customer = target.get("customerId") or ""
    target_id = target.get("id") or ""
    target_email = target.get("primaryEmail") or ""
    if not target_customer:
        raise AdminBoundaryError("The target's customer could not be resolved.")
    if target_customer != context.customer_id:
        raise AdminBoundaryError("The target belongs to another customer.")

    if protect_account and classify_risk(spec, body) == "destructive":
        if not target_id or not target_email:
            raise AdminBoundaryError("Could not verify the target account.")
        if target_email.casefold() == context.actor_email.casefold() or (
            context.actor_id and target_id == context.actor_id
        ):
            raise AdminBoundaryError(
                "Suspending or deleting your own account is not allowed."
            )
        super_ids = _super_admin_ids(directory_service, context.customer_id)
        is_super_admin = target_id in super_ids or target.get("isAdmin") is not False
        if is_super_admin and not _has_other_active_super_admin(
            directory_service, context.customer_id, super_ids, target_id
        ):
            raise AdminBoundaryError(
                "This action would remove the last active super admin."
            )

    return VerifiedTarget(
        email=target_email, user_id=target_id, customer_id=target_customer
    )
