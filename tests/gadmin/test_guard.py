"""The guard keeps admin actions inside the actor's customer and away from the
actor's own account and the last active super admin."""

from unittest.mock import MagicMock

import pytest

from gadmin.guard import (
    AdminBoundaryError,
    AdminContext,
    guard_target,
    resolve_admin_context,
)
from tests.gadmin.fake_directory import FakeDirectory, http_error

ADMIN = "admin@op.example"


@pytest.fixture
def directory_client():
    return MagicMock(name="directory")


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_user(ADMIN, "U_ADMIN", super_admin=True)
    fake.add_user("staff@op.example", "U_STAFF")
    fake.add_user("u@other.example", "U_OTHER", customer_id="C02")
    return fake


@pytest.fixture
def context():
    return AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN")


# --- Customer boundary ---------------------------------------------------------


def test_cross_customer_target_is_rejected(directory_client):
    context = AdminContext(actor_email="admin@op.example", customer_id="C01")
    directory_client.users().get.return_value.execute.return_value = {
        "primaryEmail": "u@other.example",
        "customerId": "C02",
    }
    with pytest.raises(AdminBoundaryError):
        guard_target(
            context, "u@other.example", "directory.users.delete", directory_client
        )


def test_same_customer_target_is_verified(directory, context):
    target = guard_target(context, "staff@op.example", "directory.users.get", directory)

    assert (target.email, target.user_id, target.customer_id) == (
        "staff@op.example",
        "U_STAFF",
        "C01",
    )
    assert directory.write_calls == []


def test_other_customer_read_is_rejected(directory, context):
    with pytest.raises(AdminBoundaryError, match="customer"):
        guard_target(context, "u@other.example", "directory.users.get", directory)


def test_target_without_customer_is_rejected(directory, context):
    directory.add_user("ghost@op.example", "U_GHOST")
    directory.users_by_key["ghost@op.example"]["customerId"] = ""

    with pytest.raises(AdminBoundaryError):
        guard_target(context, "ghost@op.example", "directory.users.get", directory)


def test_unresolved_actor_customer_is_rejected(directory):
    context = AdminContext(actor_email=ADMIN, customer_id="")

    with pytest.raises(AdminBoundaryError):
        guard_target(context, "staff@op.example", "directory.users.get", directory)


def test_target_lookup_failure_fails_closed(directory, context):
    with pytest.raises(AdminBoundaryError):
        guard_target(context, "missing@op.example", "directory.users.get", directory)
    directory.failures["directory.users.get"] = http_error(503)
    with pytest.raises(AdminBoundaryError):
        guard_target(context, "staff@op.example", "directory.users.get", directory)


# --- Actor resolution ----------------------------------------------------------


def test_actor_context_comes_from_fresh_directory_lookup(directory):
    context = resolve_admin_context(ADMIN.upper(), directory)

    assert context == AdminContext(
        actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN"
    )
    assert directory.calls == [("directory.users.get", {"userKey": ADMIN.upper()})]


def test_delegated_admin_actor_is_accepted(directory):
    directory.add_user("helpdesk@op.example", "U_HELP", delegated_admin=True)

    assert resolve_admin_context("helpdesk@op.example", directory).customer_id == "C01"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda user: user.update(primaryEmail="someone@op.example"),
        lambda user: user.update(customerId=""),
        lambda user: user.update(id=""),
        lambda user: user.update(isAdmin=False, isDelegatedAdmin=False),
        lambda user: user.update(isAdmin="true"),
        lambda user: user.update(suspended=True),
    ],
    ids=[
        "email-mismatch",
        "no-customer",
        "no-actor-id",
        "not-admin",
        "non-bool-admin",
        "suspended",
    ],
)
def test_unverifiable_actor_is_rejected(directory, mutate):
    mutate(directory.users_by_key[ADMIN])

    with pytest.raises(AdminBoundaryError):
        resolve_admin_context(ADMIN, directory)


def test_actor_lookup_failure_fails_closed(directory):
    directory.failures["directory.users.get"] = http_error(403, "Not Authorized")

    with pytest.raises(AdminBoundaryError):
        resolve_admin_context(ADMIN, directory)


# --- Self protection -----------------------------------------------------------


def test_self_suspension_is_rejected(directory, context):
    with pytest.raises(AdminBoundaryError, match="own account"):
        guard_target(
            context, ADMIN, "directory.users.update", directory, {"suspended": True}
        )


def test_self_deletion_is_rejected_by_alias_or_id(directory, context):
    for target in (ADMIN, "U_ADMIN"):
        with pytest.raises(AdminBoundaryError, match="own account"):
            guard_target(context, target, "directory.users.delete", directory)


def test_self_profile_edit_is_allowed(directory, context):
    target = guard_target(
        context, ADMIN, "directory.users.update", directory, {"orgUnitPath": "/IT"}
    )
    assert target.user_id == "U_ADMIN"


# --- Last active super admin ----------------------------------------------------


@pytest.fixture
def two_super_admins(directory):
    directory.add_user("owner@op.example", "U_OWNER", super_admin=True)
    return directory


def test_deleting_non_admin_is_allowed_after_role_check(directory, context):
    target = guard_target(
        context, "staff@op.example", "directory.users.delete", directory
    )

    assert target.user_id == "U_STAFF"
    # isAdmin alone is not trusted: role assignments are always consulted.
    assert any(c[0] == "directory.roleAssignments.list" for c in directory.calls)
    assert directory.write_calls == []


def test_deleting_super_admin_allowed_when_another_is_active(two_super_admins):
    context = AdminContext(actor_email="owner@op.example", customer_id="C01")
    two_super_admins.page_size = 1  # proves pagination is followed

    target = guard_target(context, ADMIN, "directory.users.delete", two_super_admins)

    assert target.user_id == "U_ADMIN"


@pytest.mark.parametrize(
    "other_state",
    [
        {"suspended": True},
        {"archived": True},
        {"customerId": "C02"},
    ],
    ids=["suspended", "archived", "other-customer"],
)
@pytest.mark.parametrize(
    ("operation", "body"),
    [("directory.users.delete", None), ("directory.users.update", {"suspended": True})],
    ids=["delete", "suspend"],
)
def test_last_active_super_admin_is_protected(
    two_super_admins, other_state, operation, body
):
    two_super_admins.users_by_key["U_OWNER"].update(other_state)
    context = AdminContext(actor_email="helpdesk@op.example", customer_id="C01")

    with pytest.raises(AdminBoundaryError, match="last active super admin"):
        guard_target(context, ADMIN, operation, two_super_admins, body)


def test_group_assigned_super_admin_is_not_counted(directory):
    directory.assign("G_ADMINS", "R_SUPER", assignee_type="group")
    context = AdminContext(actor_email="helpdesk@op.example", customer_id="C01")

    with pytest.raises(AdminBoundaryError, match="last active super admin"):
        guard_target(context, ADMIN, "directory.users.delete", directory)


def test_is_admin_flag_without_assignment_still_protected(directory):
    directory.assignments.clear()
    context = AdminContext(actor_email="helpdesk@op.example", customer_id="C01")

    with pytest.raises(AdminBoundaryError, match="last active super admin"):
        guard_target(context, ADMIN, "directory.users.delete", directory)


@pytest.mark.parametrize(
    "break_directory",
    [
        lambda d: d.role_items.clear(),
        lambda d: d.failures.update({"directory.roles.list": http_error(403)}),
        lambda d: d.failures.update(
            {"directory.roleAssignments.list": http_error(500)}
        ),
    ],
    ids=["no-super-role", "roles-forbidden", "assignments-error"],
)
def test_unknown_super_admin_status_fails_closed(two_super_admins, break_directory):
    break_directory(two_super_admins)
    context = AdminContext(actor_email="owner@op.example", customer_id="C01")

    with pytest.raises(AdminBoundaryError):
        guard_target(
            context, "staff@op.example", "directory.users.delete", two_super_admins
        )


def test_other_admin_lookup_failure_counts_as_inactive(two_super_admins):
    del two_super_admins.users_by_key["U_OWNER"]
    context = AdminContext(actor_email="helpdesk@op.example", customer_id="C01")

    with pytest.raises(AdminBoundaryError, match="last active super admin"):
        guard_target(context, ADMIN, "directory.users.delete", two_super_admins)
