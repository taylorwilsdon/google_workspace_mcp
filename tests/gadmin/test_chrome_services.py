"""Chrome Management areas and Chrome Policy are separate opt-in services with
exact scopes and permission levels; none of their scopes join a default launch."""

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
from auth.scopes import CHROME_SERVICES, get_scopes_for_tools
from auth.service_decorator import SERVICE_CONFIGS
from gadmin.auth import admin_service_for, assert_admin_permission, client_scopes
from gadmin.registry import iter_operations

_CHROME = "https://www.googleapis.com/auth/chrome.management."
# The exact (read, write) scopes of each service.
EXPECTED = {
    "admin-chrome-reports": (
        {_CHROME + "reports.readonly", _CHROME + "appdetails.readonly"},
        set(),
    ),
    "admin-chrome-telemetry": ({_CHROME + "telemetry.readonly"}, set()),
    "admin-chrome-profiles": (
        {_CHROME + "profiles.readonly"},
        {_CHROME + "profiles"},
    ),
    "admin-chrome-insights": (
        {_CHROME + "securityinsights.readonly"},
        {_CHROME + "securityinsights"},
    ),
    "admin-chrome-policy": ({_CHROME + "policy.readonly"}, {_CHROME + "policy"}),
}
SERVICES = list(EXPECTED)
NEW_SCOPES = {s for read, write in EXPECTED.values() for s in read | write}
OPERATIONS = [
    spec
    for spec in iter_operations()
    if spec.service in ("chromemanagement", "chromepolicy")
]


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


def test_services_declare_exactly_the_expected_scopes():
    assert {
        service: (set(read), set(write))
        for service, (read, write) in CHROME_SERVICES.items()
    } == EXPECTED


def test_every_service_has_operations():
    assert len(OPERATIONS) == 48
    assert {admin_service_for(spec) for spec in OPERATIONS} == set(SERVICES)


def test_default_launches_request_no_chrome_scope():
    for selection in (None, ["gmail", "drive", "admin-directory"]):
        assert not set(get_scopes_for_tools(selection)) & NEW_SCOPES


@pytest.mark.parametrize("service", SERVICES)
def test_service_is_opt_in_and_builds_its_own_client(service):
    import main

    assert service in scopes.OPT_IN_SERVICES
    assert service not in main.DEFAULT_SERVICES
    assert main.SERVICE_MODULES[service] == "gadmin.admin_tools"
    assert len(main.SERVICE_ICONS[service]) == 1
    api = "chromepolicy" if service == "admin-chrome-policy" else "chromemanagement"
    assert SERVICE_CONFIGS[service] == {"service": api, "version": "v1"}


@pytest.mark.parametrize("service", SERVICES)
def test_service_requests_only_its_own_scopes(service, monkeypatch):
    read, write = EXPECTED[service]

    assert set(get_scopes_for_tools([service])) & NEW_SCOPES == read | write
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    assert set(get_scopes_for_tools([service])) & NEW_SCOPES == read


@pytest.mark.parametrize("service", SERVICES)
def test_permission_levels_are_cumulative(service):
    read, write = EXPECTED[service]
    level = permissions.get_scopes_for_permission

    assert set(level(service, "readonly")) == read
    assert set(level(service, "manage")) == read | write
    assert set(level(service, "destructive")) == read | write


@pytest.mark.parametrize("spec", OPERATIONS, ids=lambda spec: spec.id)
def test_operation_scopes_fit_its_service_level(spec, monkeypatch):
    service = admin_service_for(spec)
    level = {"read": "readonly", "manage": "manage", "destructive": "destructive"}
    allowed = permissions.get_scopes_for_permission(service, level[spec.risk])

    # The client, including the reads that verify its targets and policy
    # values, can be granted at the lowest level that permits the operation.
    assert set(client_scopes(spec)) <= set(allowed)
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory", service])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {service: level[spec.risk]})
    assert_admin_permission(spec)


def test_readonly_level_refuses_chrome_writes(monkeypatch):
    from gadmin.auth import AdminPermissionError
    from gadmin.registry import get_operation

    monkeypatch.setattr(
        scopes, "_ENABLED_TOOLS", ["admin-directory", "admin-chrome-policy"]
    )
    monkeypatch.setattr(
        permissions, "_PERMISSIONS", {"admin-chrome-policy": "readonly"}
    )

    assert_admin_permission(get_operation("chromepolicy.customers.policies.resolve"))
    with pytest.raises(AdminPermissionError):
        assert_admin_permission(
            get_operation("chromepolicy.customers.policies.orgunits.batchModify")
        )
