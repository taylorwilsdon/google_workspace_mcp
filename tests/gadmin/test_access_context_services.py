"""Access Context Manager reads are one opt-in service whose only scope is
cloud-platform, the single scope Google offers for the API. It is requested only
when that service is selected, never by a default launch or another service."""

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
from auth.scopes import ACCESS_CONTEXT_SERVICES, get_scopes_for_tools
from auth.service_decorator import SERVICE_CONFIGS
from gadmin.auth import admin_service_for, assert_admin_permission, client_scopes
from gadmin.registry import excluded_operations, iter_operations

SERVICE = "admin-access-context"
CLOUD_PLATFORM = "https://www.googleapis.com/auth/cloud-platform"
OPERATIONS = [
    spec for spec in iter_operations() if spec.service == "accesscontextmanager"
]


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


def test_service_declares_only_cloud_platform_for_reads():
    assert ACCESS_CONTEXT_SERVICES == {SERVICE: ([CLOUD_PLATFORM], [])}
    assert scopes.CLOUD_PLATFORM_SCOPE == CLOUD_PLATFORM


def test_only_the_four_reads_are_registered():
    assert sorted(spec.id for spec in OPERATIONS) == [
        "accesscontextmanager.accessPolicies.accessLevels.get",
        "accesscontextmanager.accessPolicies.accessLevels.list",
        "accesscontextmanager.accessPolicies.get",
        "accesscontextmanager.accessPolicies.list",
    ]
    assert {spec.risk for spec in OPERATIONS} == {"read"}
    assert {admin_service_for(spec) for spec in OPERATIONS} == {SERVICE}
    assert {s for spec in OPERATIONS for s in client_scopes(spec)} == {CLOUD_PLATFORM}
    excluded = [e for e in excluded_operations() if e.service == "accesscontextmanager"]
    assert len(excluded) == 38


def test_no_other_service_or_default_launch_requests_cloud_platform():
    import main

    assert CLOUD_PLATFORM not in get_scopes_for_tools(None)
    others = [service for service in main.SERVICE_MODULES if service != SERVICE]
    assert CLOUD_PLATFORM not in get_scopes_for_tools(others)
    for service in others:
        assert CLOUD_PLATFORM not in scopes.TOOL_SCOPES_MAP.get(service, []), service


def test_service_is_opt_in_and_builds_its_own_client():
    import main

    assert SERVICE in scopes.OPT_IN_SERVICES
    assert SERVICE not in main.DEFAULT_SERVICES
    assert main.SERVICE_MODULES[SERVICE] == "gadmin.admin_tools"
    assert len(main.SERVICE_ICONS[SERVICE]) == 1
    assert SERVICE_CONFIGS[SERVICE] == {
        "service": "accesscontextmanager",
        "version": "v1",
    }


def test_selecting_the_service_requests_cloud_platform_even_read_only(monkeypatch):
    # Google offers no narrower scope for Access Context Manager, so read-only
    # mode requests the same scope; only read operations are registered.
    assert CLOUD_PLATFORM in get_scopes_for_tools([SERVICE])
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)
    assert CLOUD_PLATFORM in get_scopes_for_tools([SERVICE])


def test_every_permission_level_grants_the_same_single_scope():
    level = permissions.get_scopes_for_permission
    for name in ("readonly", "manage", "destructive"):
        assert level(SERVICE, name) == [CLOUD_PLATFORM], name


@pytest.mark.parametrize("spec", OPERATIONS, ids=lambda spec: spec.id)
def test_reads_run_at_the_readonly_level(spec, monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory", SERVICE])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {SERVICE: "readonly"})
    assert_admin_permission(spec)
