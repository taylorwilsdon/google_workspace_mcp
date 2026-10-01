"""Directory devices, calendar resources, and Chrome printers are separate
opt-in services with exact scopes and permission levels; none of their scopes
join a default launch."""

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
from auth.scopes import DIRECTORY_AREA_SERVICES, get_scopes_for_tools
from auth.service_decorator import SERVICE_CONFIGS
from gadmin.auth import admin_service_for, assert_admin_permission, client_scopes
from gadmin.registry import iter_operations

SERVICES = list(DIRECTORY_AREA_SERVICES)
NEW_SCOPES = {
    s
    for levels in DIRECTORY_AREA_SERVICES.values()
    for scopes_ in levels
    for s in scopes_
}
OPERATIONS = [spec for spec in iter_operations() if admin_service_for(spec) in SERVICES]


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


def test_every_area_has_operations():
    assert len(OPERATIONS) == 39
    assert {admin_service_for(spec) for spec in OPERATIONS} == set(SERVICES)


def test_default_launches_request_no_area_scope():
    for selection in (None, ["gmail", "drive", "admin-directory"]):
        assert not set(get_scopes_for_tools(selection)) & NEW_SCOPES


@pytest.mark.parametrize("service", SERVICES)
def test_service_is_opt_in_and_uses_the_directory_client(service):
    import main

    assert service in scopes.OPT_IN_SERVICES
    assert service not in main.DEFAULT_SERVICES
    assert main.SERVICE_MODULES[service] == "gadmin.admin_tools"
    assert len(main.SERVICE_ICONS[service]) == 1
    assert SERVICE_CONFIGS[service] == {"service": "admin", "version": "directory_v1"}


@pytest.mark.parametrize("service", SERVICES)
def test_service_requests_only_its_own_scopes(service, monkeypatch):
    read, manage, destructive = DIRECTORY_AREA_SERVICES[service]

    assert set(get_scopes_for_tools([service])) & NEW_SCOPES == {
        *read,
        *manage,
        *destructive,
    }
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    assert set(get_scopes_for_tools([service])) & NEW_SCOPES == set(read)


@pytest.mark.parametrize("service", SERVICES)
def test_permission_levels_are_cumulative(service):
    read, manage, destructive = DIRECTORY_AREA_SERVICES[service]
    level = permissions.get_scopes_for_permission

    assert set(level(service, "readonly")) == set(read)
    assert set(level(service, "manage")) == {*read, *manage}
    assert set(level(service, "destructive")) == {*read, *manage, *destructive}


@pytest.mark.parametrize("spec", OPERATIONS, ids=lambda spec: spec.id)
def test_operation_scopes_fit_its_service_level(spec, monkeypatch):
    service = admin_service_for(spec)
    level = {"read": "readonly", "manage": "manage", "destructive": "destructive"}
    allowed = permissions.get_scopes_for_permission(service, level[spec.risk])

    # The client, including the reads that verify its targets, can be granted
    # at the lowest level that permits the operation.
    assert set(client_scopes(spec)) <= set(allowed)
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory", service])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {service: level[spec.risk]})
    assert_admin_permission(spec)
