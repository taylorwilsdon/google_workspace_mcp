"""Reports, Vault, Alert Center, and Groups Settings are separate opt-in services
with their own scopes and permission levels, bound to the verified admin."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
import gadmin.auth as admin_auth
from auth.scopes import (
    ALERTCENTER_SCOPE,
    GROUPS_SETTINGS_SCOPE,
    REPORTS_AUDIT_READONLY_SCOPE,
    REPORTS_USAGE_READONLY_SCOPE,
    VAULT_READONLY_SCOPE,
    VAULT_SCOPE,
    get_scopes_for_tools,
    has_required_scopes,
)
from auth.service_decorator import SERVICE_CONFIGS
from gadmin.auth import AdminPermissionError, assert_admin_permission, get_admin_service
from gadmin.registry import get_operation

ADMIN = "admin@op.example"
SERVICES = {
    "admin-reports": ("admin", "reports_v1"),
    "admin-vault": ("vault", "v1"),
    "admin-alertcenter": ("alertcenter", "v1beta1"),
    "admin-groupssettings": ("groupssettings", "v1"),
}
NEW_SCOPES = {
    REPORTS_AUDIT_READONLY_SCOPE,
    REPORTS_USAGE_READONLY_SCOPE,
    VAULT_READONLY_SCOPE,
    VAULT_SCOPE,
    ALERTCENTER_SCOPE,
    GROUPS_SETTINGS_SCOPE,
}


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


def test_default_launches_request_none_of_the_new_scopes():
    for selection in (None, ["gmail", "drive", "admin-directory"]):
        assert not set(get_scopes_for_tools(selection)) & NEW_SCOPES


@pytest.mark.parametrize("service", SERVICES)
def test_service_is_opt_in_and_builds_its_own_client(service):
    import main

    assert service in scopes.OPT_IN_SERVICES
    assert main.SERVICE_MODULES[service] == "gadmin.admin_tools"
    assert service in main.SERVICE_ICONS
    assert service not in main.DEFAULT_SERVICES
    config = SERVICE_CONFIGS[service]
    assert (config["service"], config["version"]) == SERVICES[service]
    assert admin_auth.ADMIN_SERVICES[SERVICES[service]] == service


def test_each_service_requests_only_its_own_scopes():
    assert set(get_scopes_for_tools(["admin-reports"])) & NEW_SCOPES == {
        REPORTS_AUDIT_READONLY_SCOPE,
        REPORTS_USAGE_READONLY_SCOPE,
    }
    assert set(get_scopes_for_tools(["admin-vault"])) & NEW_SCOPES == {
        VAULT_READONLY_SCOPE,
        VAULT_SCOPE,
    }
    assert set(get_scopes_for_tools(["admin-alertcenter"])) & NEW_SCOPES == {
        ALERTCENTER_SCOPE
    }
    assert set(get_scopes_for_tools(["admin-groupssettings"])) & NEW_SCOPES == {
        GROUPS_SETTINGS_SCOPE
    }


def test_read_only_launch_requests_only_read_scopes(monkeypatch):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    selected = set(get_scopes_for_tools(list(SERVICES))) & NEW_SCOPES

    # Alert Center and Groups Settings have no read-only scope.
    assert selected == {
        REPORTS_AUDIT_READONLY_SCOPE,
        REPORTS_USAGE_READONLY_SCOPE,
        VAULT_READONLY_SCOPE,
    }


def test_permission_levels_are_cumulative():
    level = permissions.get_scopes_for_permission

    assert set(level("admin-reports", "readonly")) == {
        REPORTS_AUDIT_READONLY_SCOPE,
        REPORTS_USAGE_READONLY_SCOPE,
    }
    assert level("admin-vault", "readonly") == [VAULT_READONLY_SCOPE]
    assert set(level("admin-vault", "destructive")) == {
        VAULT_READONLY_SCOPE,
        VAULT_SCOPE,
    }
    for service, scope in (
        ("admin-alertcenter", ALERTCENTER_SCOPE),
        ("admin-groupssettings", GROUPS_SETTINGS_SCOPE),
    ):
        assert level(service, "readonly") == []
        assert level(service, "manage") == [scope]


def test_full_vault_scope_covers_vault_reads():
    assert has_required_scopes([VAULT_SCOPE], [VAULT_READONLY_SCOPE])


@pytest.mark.parametrize(
    ("service", "level", "operation_id", "allowed"),
    [
        ("admin-reports", "readonly", "reports.activities.list", True),
        ("admin-reports", "readonly", "reports.customerUsageReports.get", True),
        ("admin-vault", "readonly", "vault.matters.holds.list", True),
        ("admin-vault", "readonly", "vault.matters.create", False),
        ("admin-vault", "manage", "vault.matters.holds.addHeldAccounts", True),
        ("admin-vault", "manage", "vault.matters.exports.create", False),
        ("admin-vault", "manage", "vault.matters.addPermissions", False),
        ("admin-vault", "destructive", "vault.matters.exports.create", True),
        ("admin-alertcenter", "readonly", "alertcenter.alerts.list", False),
        ("admin-alertcenter", "manage", "alertcenter.alerts.list", True),
        ("admin-alertcenter", "manage", "alertcenter.alerts.delete", False),
        ("admin-alertcenter", "destructive", "alertcenter.alerts.batchDelete", True),
        ("admin-groupssettings", "readonly", "groupsSettings.groups.get", False),
        ("admin-groupssettings", "manage", "groupsSettings.groups.patch", True),
    ],
)
def test_permission_level_limits_each_service(
    monkeypatch, service, level, operation_id, allowed
):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [service])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {service: level})

    if allowed:
        assert_admin_permission(get_operation(operation_id))
    else:
        with pytest.raises(AdminPermissionError):
            assert_admin_permission(get_operation(operation_id))


def test_opening_a_group_to_anyone_needs_destructive(monkeypatch):
    patch = get_operation("groupsSettings.groups.patch")
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-groupssettings"])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-groupssettings": "manage"})

    assert_admin_permission(patch, {"whoCanJoin": "INVITED_CAN_JOIN"})
    with pytest.raises(AdminPermissionError, match="destructive"):
        assert_admin_permission(patch, {"allowExternalMembers": "true"})


def test_operation_needs_its_own_service_selected(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory", "admin-reports"])

    assert_admin_permission(get_operation("reports.activities.list"))
    with pytest.raises(AdminPermissionError, match="admin-vault"):
        assert_admin_permission(get_operation("vault.matters.list"))


@pytest.mark.parametrize(
    ("operation_id", "scope"),
    [
        ("reports.activities.list", REPORTS_AUDIT_READONLY_SCOPE),
        ("reports.userUsageReport.get", REPORTS_USAGE_READONLY_SCOPE),
        ("vault.matters.list", VAULT_READONLY_SCOPE),
        ("vault.matters.exports.create", VAULT_SCOPE),
        ("alertcenter.getSettings", ALERTCENTER_SCOPE),
        ("groupsSettings.groups.patch", GROUPS_SETTINGS_SCOPE),
    ],
)
@pytest.mark.asyncio
async def test_each_family_gets_its_own_bound_client(monkeypatch, operation_id, scope):
    monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(admin_auth, "_current_session_id", lambda: None)
    fake = SimpleNamespace(service=MagicMock(name="client"))
    fake.mock = AsyncMock(return_value=(fake.service, ADMIN))
    monkeypatch.setattr(admin_auth, "_authenticate_service", fake.mock)
    spec = get_operation(operation_id)

    service = await get_admin_service(ADMIN, spec, None)

    args = fake.mock.await_args
    assert service is fake.service
    assert (args.args[1], args.args[2]) == (spec.service, spec.version)
    assert args.args[5] == [scope]
    assert args.kwargs["verify_account"] is True
