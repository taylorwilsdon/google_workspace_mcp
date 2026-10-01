"""Registered operations are resolved against fresh Directory reads so that every
customer, user, group, member, domain, role, and role-assignment reference stays
inside the admin's verified customer before anything is sent to Google."""

import pytest

from gadmin.boundary import check_response, resolve_call
from gadmin.guard import AdminBoundaryError, AdminContext
from gadmin.registry import InvalidOperationInput, get_operation
from tests.gadmin.fake_directory import SUPER_ADMIN_ROLE_ID, FakeDirectory

ADMIN = "admin@op.example"
CONTEXT = AdminContext(actor_email=ADMIN, customer_id="C01", actor_id="U_ADMIN")
DELEGATED = AdminContext(
    actor_email="help@op.example", customer_id="C01", actor_id="U_HELP"
)


@pytest.fixture
def directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_user(ADMIN, "U_ADMIN", super_admin=True)
    fake.add_user("help@op.example", "U_HELP", delegated_admin=True)
    fake.add_user("staff@op.example", "U_STAFF")
    fake.add_user("u@other.example", "U_OTHER", customer_id="C_OTHER")
    fake.add_group("G1", "team@op.example", ["U_STAFF"])
    fake.add_group("G_ALIAS", "crew@op-alias.example", [])
    fake.add_group("G_OTHER", "team@other.example", ["U_OTHER"])
    fake.assign("U_STAFF", "R_HELP")
    return fake


def _resolve(directory, operation_id, params=None, body=None, context=CONTEXT):
    return resolve_call(context, get_operation(operation_id), params, body, directory)


# --- customer and deny rules ------------------------------------------------------


def test_customer_parameters_are_set_by_the_server(directory):
    call = _resolve(directory, "directory.users.list", {"query": "isSuspended=true"})

    assert call.params == {"customer": "C01", "query": "isSuspended=true"}
    assert call.risk == "read"
    assert directory.calls == []


@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        ("directory.users.list", {"customer": "C_OTHER"}),
        ("directory.users.list", {"customer": "my_customer"}),
        ("directory.customers.get", {"customerKey": "C_OTHER"}),
        (
            "licensing.licenseAssignments.listForProduct",
            {"productId": "p", "customerId": "x"},
        ),
        ("directory.users.list", {"domain": "other.example"}),
        ("directory.groups.list", {"userKey": "u@other.example"}),
    ],
)
def test_caller_customer_and_denied_parameters_are_refused(
    directory, operation_id, params
):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, operation_id, params)
    assert directory.calls == []


def test_global_catalog_read_needs_no_lookup(directory):
    call = _resolve(directory, "datatransfer.applications.get", {"applicationId": "55"})

    assert call.params == {"applicationId": "55"}
    assert directory.calls == []


# --- user rules -------------------------------------------------------------------


def test_user_parameter_is_rewritten_to_the_verified_id(directory):
    call = _resolve(
        directory,
        "directory.users.patch",
        {"userKey": "STAFF@op.example"},
        {"orgUnitPath": "/Staff"},
    )

    assert call.params == {"userKey": "U_STAFF"}
    assert call.target == "U_STAFF"
    assert call.risk == "manage"
    assert directory.write_calls == []


def test_user_rule_can_rewrite_to_the_primary_email(directory):
    call = _resolve(
        directory,
        "licensing.licenseAssignments.get",
        {"productId": "Google-Apps", "skuId": "1010020020", "userId": "U_STAFF"},
    )

    assert call.params["userId"] == "staff@op.example"


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        ("directory.users.patch", {"userKey": "u@other.example"}, {"orgUnitPath": "/"}),
        ("directory.asps.list", {"userKey": "U_OTHER"}, None),
        ("directory.roleAssignments.list", {"userKey": "u@other.example"}, None),
        (
            "datatransfer.transfers.insert",
            None,
            {"oldOwnerUserId": "U_OTHER", "newOwnerUserId": "U_STAFF"},
        ),
        (
            "directory.members.insert",
            {"groupKey": "G1"},
            {"email": "u@other.example"},
        ),
        (
            "licensing.licenseAssignments.insert",
            {"productId": "Google-Apps", "skuId": "1010020020"},
            {"userId": "u@other.example"},
        ),
        ("directory.users.get", {"userKey": "missing@op.example"}, None),
    ],
)
def test_users_outside_the_customer_are_refused(directory, operation_id, params, body):
    with pytest.raises(AdminBoundaryError):
        _resolve(directory, operation_id, params, body)
    assert directory.write_calls == []


def test_body_user_fields_are_rewritten(directory):
    transfer = _resolve(
        directory,
        "datatransfer.transfers.insert",
        None,
        {"oldOwnerUserId": "staff@op.example", "newOwnerUserId": ADMIN},
    )
    member = _resolve(
        directory,
        "directory.members.insert",
        {"groupKey": "team@op.example"},
        {"email": "U_STAFF", "role": "MEMBER"},
    )

    assert transfer.body == {"oldOwnerUserId": "U_STAFF", "newOwnerUserId": "U_ADMIN"}
    assert member.params == {"groupKey": "G1"}
    assert member.body == {"email": "staff@op.example", "role": "MEMBER"}


def test_optional_user_parameters_are_checked_only_when_given(directory):
    call = _resolve(directory, "datatransfer.transfers.list", {"status": "completed"})

    assert call.params == {"customerId": "C01", "status": "completed"}
    assert directory.calls == []


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        ("directory.users.makeAdmin", {"userKey": ADMIN}, {"status": False}),
        ("directory.twoStepVerification.turnOff", {"userKey": ADMIN}, None),
        ("directory.users.patch", {"userKey": ADMIN}, {"suspended": True}),
        (
            "licensing.licenseAssignments.delete",
            {"productId": "Google-Apps", "skuId": "1010020020", "userId": ADMIN},
            None,
        ),
        (
            "directory.roleAssignments.insert",
            None,
            {
                "roleId": SUPER_ADMIN_ROLE_ID,
                "assignedTo": ADMIN,
                "scopeType": "CUSTOMER",
            },
        ),
    ],
)
def test_destructive_operations_refuse_the_actor_themselves(
    directory, operation_id, params, body
):
    with pytest.raises(AdminBoundaryError, match="own account"):
        _resolve(directory, operation_id, params, body)


def test_destructive_operations_protect_the_last_super_admin(directory):
    with pytest.raises(AdminBoundaryError, match="last active super admin"):
        _resolve(
            directory,
            "directory.users.makeAdmin",
            {"userKey": ADMIN},
            {"status": False},
            context=DELEGATED,
        )


# --- group, member, and domain rules ----------------------------------------------


def test_group_is_verified_by_its_domain_and_rewritten_to_its_id(directory):
    by_email = _resolve(
        directory, "directory.groups.get", {"groupKey": "team@op.example"}
    )
    by_alias = _resolve(directory, "directory.groups.get", {"groupKey": "G_ALIAS"})

    assert by_email.params == {"groupKey": "G1"} and by_email.target == "G1"
    assert by_alias.params == {"groupKey": "G_ALIAS"}
    assert ("directory.domains.list", {"customer": "C01"}) in directory.calls


@pytest.mark.parametrize(
    "group_key", ["team@other.example", "G_OTHER", "nope@op.example"]
)
def test_groups_outside_the_customer_are_refused(directory, group_key):
    with pytest.raises(AdminBoundaryError):
        _resolve(directory, "directory.groups.delete", {"groupKey": group_key})
    assert directory.write_calls == []


def test_member_is_resolved_inside_the_verified_group(directory):
    call = _resolve(
        directory,
        "directory.members.patch",
        {"groupKey": "team@op.example", "memberKey": "staff@op.example"},
        {"role": "MANAGER"},
    )

    assert call.params == {"groupKey": "G1", "memberKey": "U_STAFF"}
    assert (
        "directory.members.get",
        {"groupKey": "G1", "memberKey": "staff@op.example"},
    ) in directory.calls
    with pytest.raises(AdminBoundaryError):
        _resolve(
            directory,
            "directory.members.delete",
            {"groupKey": "G1", "memberKey": ADMIN},
        )


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        (
            "directory.users.aliases.insert",
            {"userKey": "U_STAFF"},
            {"alias": "s@other.example"},
        ),
        (
            "directory.users.aliases.delete",
            {"userKey": "U_STAFF", "alias": "s@other.example"},
            None,
        ),
        (
            "directory.groups.aliases.insert",
            {"groupKey": "G1"},
            {"alias": "t@other.example"},
        ),
        ("directory.groups.insert", None, {"email": "new@other.example"}),
        ("directory.groups.insert", None, {"email": "no-domain"}),
    ],
)
def test_addresses_outside_the_customer_domains_are_refused(
    directory, operation_id, params, body
):
    with pytest.raises(AdminBoundaryError, match="customer's domains"):
        _resolve(directory, operation_id, params, body)


def test_addresses_in_customer_domains_and_aliases_are_accepted(directory):
    alias = _resolve(
        directory,
        "directory.users.aliases.insert",
        {"userKey": "staff@op.example"},
        {"alias": "S@OP-ALIAS.example"},
    )
    group = _resolve(
        directory, "directory.groups.insert", None, {"email": "new@op.example"}
    )

    assert alias.params == {"userKey": "U_STAFF"}
    assert group.body == {"email": "new@op.example"}


# --- role and role-assignment rules -----------------------------------------------


def test_role_assignment_needs_a_role_of_the_customer(directory):
    call = _resolve(
        directory,
        "directory.roleAssignments.insert",
        None,
        {"roleId": "R_HELP", "assignedTo": "staff@op.example", "scopeType": "CUSTOMER"},
    )

    assert call.params == {"customer": "C01"}
    assert call.body["assignedTo"] == "U_STAFF"
    assert call.risk == "destructive"
    with pytest.raises(AdminBoundaryError):
        _resolve(
            directory,
            "directory.roleAssignments.insert",
            None,
            {"roleId": "R_NONE", "assignedTo": "U_STAFF", "scopeType": "CUSTOMER"},
        )


def test_role_assignment_removal_is_guarded(directory):
    staff_assignment = next(
        a["roleAssignmentId"]
        for a in directory.assignments
        if a["assignedTo"] == "U_STAFF"
    )
    own_assignment = next(
        a["roleAssignmentId"]
        for a in directory.assignments
        if a["assignedTo"] == "U_ADMIN"
    )

    call = _resolve(
        directory,
        "directory.roleAssignments.delete",
        {"roleAssignmentId": staff_assignment},
    )
    assert call.params == {"customer": "C01", "roleAssignmentId": staff_assignment}
    with pytest.raises(AdminBoundaryError, match="your own"):
        _resolve(
            directory,
            "directory.roleAssignments.delete",
            {"roleAssignmentId": own_assignment},
        )
    with pytest.raises(AdminBoundaryError, match="last active super admin"):
        _resolve(
            directory,
            "directory.roleAssignments.delete",
            {"roleAssignmentId": own_assignment},
            context=DELEGATED,
        )
    with pytest.raises(AdminBoundaryError):
        _resolve(
            directory, "directory.roleAssignments.delete", {"roleAssignmentId": "A99"}
        )
    assert directory.write_calls == []


# --- responses and failures -------------------------------------------------------


def test_transfer_reads_are_released_only_for_customer_users(directory):
    spec = get_operation("datatransfer.transfers.get")

    inside = {"id": "T1", "oldOwnerUserId": "U_STAFF", "newOwnerUserId": "U_ADMIN"}

    check_response(CONTEXT, spec, inside, directory)
    # Both the source and the destination account must be in the customer.
    for field in ("oldOwnerUserId", "newOwnerUserId"):
        with pytest.raises(AdminBoundaryError):
            check_response(CONTEXT, spec, {**inside, field: "U_OTHER"}, directory)
        missing = {k: v for k, v in inside.items() if k != field}
        with pytest.raises(AdminBoundaryError):
            check_response(CONTEXT, spec, missing, directory)


def test_lookup_failures_fail_closed(directory):
    directory.failures["directory.domains.list"] = RuntimeError("boom")

    with pytest.raises(AdminBoundaryError) as error:
        _resolve(directory, "directory.groups.get", {"groupKey": "G1"})
    assert "boom" not in str(error.value)


# --- phase 2 rules: tenant, account, all, group email, customer on results -------


def test_tenant_scoped_calls_need_no_lookup(directory):
    call = _resolve(directory, "vault.matters.list", {"state": "OPEN"})

    assert call.params == {"state": "OPEN"}
    assert directory.calls == []


def test_account_references_in_nested_bodies_are_rewritten(directory):
    call = _resolve(
        directory,
        "vault.matters.addPermissions",
        {"matterId": "M1"},
        {"matterPermission": {"role": "COLLABORATOR", "accountId": "STAFF@op.example"}},
    )

    assert call.body["matterPermission"]["accountId"] == "U_STAFF"
    assert call.risk == "destructive"
    assert call.target == "M1"


def test_account_rules_skip_the_self_and_super_admin_protections(directory):
    # Naming yourself as a matter collaborator or custodian is not a suspension.
    call = _resolve(
        directory,
        "vault.matters.holds.accounts.delete",
        {"matterId": "M1", "holdId": "H1", "accountId": ADMIN},
    )

    assert call.params["accountId"] == "U_ADMIN"


def test_every_array_element_is_resolved(directory):
    added = _resolve(
        directory,
        "vault.matters.holds.addHeldAccounts",
        {"matterId": "M1", "holdId": "H1"},
        {"emails": ["STAFF@op.example"], "accountIds": ["help@op.example", "U_ADMIN"]},
    )
    hold = _resolve(
        directory,
        "vault.matters.holds.create",
        {"matterId": "M1"},
        {
            "name": "Legal",
            "corpus": "MAIL",
            "accounts": [{"accountId": "staff@op.example"}, {"accountId": "U_HELP"}],
        },
    )
    export = _resolve(
        directory,
        "vault.matters.exports.create",
        {"matterId": "M1"},
        {
            "name": "Export",
            "query": {
                "corpus": "MAIL",
                "dataScope": "ALL_DATA",
                "method": "ACCOUNT",
                "accountInfo": {"emails": ["U_STAFF"]},
            },
        },
    )

    assert added.body == {
        "emails": ["staff@op.example"],
        "accountIds": ["U_HELP", "U_ADMIN"],
    }
    assert hold.body["accounts"] == [{"accountId": "U_STAFF"}, {"accountId": "U_HELP"}]
    assert export.body["query"]["accountInfo"]["emails"] == ["staff@op.example"]


@pytest.mark.parametrize(
    ("operation_id", "params", "body"),
    [
        (
            "vault.matters.addPermissions",
            {"matterId": "M1"},
            {"matterPermission": {"role": "OWNER", "accountId": "u@other.example"}},
        ),
        (
            "vault.matters.holds.addHeldAccounts",
            {"matterId": "M1", "holdId": "H1"},
            {"accountIds": ["U_STAFF", "U_OTHER"]},
        ),
        (
            "vault.matters.holds.create",
            {"matterId": "M1"},
            {"name": "x", "corpus": "MAIL", "accounts": [{"accountId": "U_OTHER"}]},
        ),
        (
            "vault.matters.count",
            {"matterId": "M1"},
            {
                "query": {
                    "corpus": "MAIL",
                    "dataScope": "ALL_DATA",
                    "method": "ACCOUNT",
                    "accountInfo": {"emails": ["u@other.example"]},
                }
            },
        ),
        (
            "vault.matters.removePermissions",
            {"matterId": "M1"},
            {"accountId": "U_OTHER"},
        ),
        (
            "reports.activities.list",
            {"userKey": "u@other.example", "applicationName": "login"},
            None,
        ),
        ("groupsSettings.groups.get", {"groupUniqueId": "team@other.example"}, None),
    ],
)
def test_phase2_references_outside_the_customer_are_refused(
    directory, operation_id, params, body
):
    with pytest.raises(AdminBoundaryError):
        _resolve(directory, operation_id, params, body)


def test_reports_accept_all_users_only_with_the_customer_bound(directory):
    every = _resolve(
        directory,
        "reports.activities.list",
        {"userKey": "all", "applicationName": "login"},
    )
    lookups = list(directory.calls)
    one = _resolve(
        directory,
        "reports.userUsageReport.get",
        {"userKey": "staff@op.example", "date": "2026-09-20"},
    )

    assert every.params == {
        "userKey": "all",
        "applicationName": "login",
        "customerId": "C01",
    }
    assert lookups == []
    assert one.params["userKey"] == "U_STAFF"
    assert one.params["customerId"] == "C01"


def test_group_settings_address_the_verified_group_email(directory):
    call = _resolve(directory, "groupsSettings.groups.get", {"groupUniqueId": "G1"})
    alias = _resolve(
        directory, "groupsSettings.groups.get", {"groupUniqueId": "G_ALIAS"}
    )

    assert call.params == {"groupUniqueId": "team@op.example"}
    assert alias.params == {"groupUniqueId": "crew@op-alias.example"}


@pytest.mark.parametrize(
    ("operation_id", "params"),
    [
        ("alertcenter.alerts.list", {"customerId": "01"}),
        ("alertcenter.alerts.get", {"alertId": "A1", "customerId": "C_OTHER"}),
        ("alertcenter.getSettings", {"customerId": "01"}),
    ],
)
def test_alert_center_never_accepts_a_customer(directory, operation_id, params):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, operation_id, params)


def test_alert_results_must_belong_to_the_verified_customer(directory):
    listing = get_operation("alertcenter.alerts.list")
    single = get_operation("alertcenter.alerts.get")

    # Alert Center reports the customer ID without the leading "C".
    check_response(CONTEXT, listing, {"alerts": [{"customerId": "01"}]}, directory)
    check_response(CONTEXT, listing, {"alerts": []}, directory)
    check_response(CONTEXT, single, {"alertId": "A1", "customerId": "C01"}, directory)
    for result in (
        {"alerts": [{"customerId": "01"}, {"customerId": "02"}]},
        {"alerts": [{"alertId": "A2"}]},
    ):
        with pytest.raises(AdminBoundaryError):
            check_response(CONTEXT, listing, result, directory)
    with pytest.raises(AdminBoundaryError):
        check_response(CONTEXT, single, {"alertId": "A1", "customerId": "X"}, directory)


@pytest.mark.parametrize("body", [None, {"name": "Only a name"}])
def test_full_replacement_bodies_must_name_every_field(directory, body):
    with pytest.raises(InvalidOperationInput):
        _resolve(directory, "vault.matters.update", {"matterId": "M1"}, body)
