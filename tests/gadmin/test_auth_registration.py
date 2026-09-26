"""Admin services are opt-in, and admin clients stay bound to the selected account."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import auth.permissions as permissions
import auth.scopes as scopes
import auth.service_decorator as service_decorator
import gadmin.auth as admin_auth
from auth.request_identity import RequestIdentity
from auth.scopes import (
    ADMIN_DIRECTORY_GROUP_MEMBER_SCOPE,
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    ADMIN_DIRECTORY_USER_SCOPE,
    ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
    get_scopes_for_tools,
)
from core.tool_tier_loader import resolve_tools_from_tier
from gadmin.auth import (
    AdminAuthenticationError,
    AdminPermissionError,
    assert_admin_permission,
    get_admin_service,
)
from gadmin.registry import get_operation, iter_operations

ADMIN = "admin@op.example"
_DIRECTORY = "https://www.googleapis.com/auth/admin.directory."
READ_SCOPES = {
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
    *(
        _DIRECTORY + name
        for name in (
            "domain.readonly",
            "customer.readonly",
            "orgunit.readonly",
            "userschema.readonly",
        )
    ),
}
MANAGE_SCOPES = {
    ADMIN_DIRECTORY_USER_SCOPE,
    ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
    ADMIN_DIRECTORY_GROUP_MEMBER_SCOPE,
    _DIRECTORY + "group",
    _DIRECTORY + "orgunit",
}
# Scopes with no read-only variant: reads that need them need "manage".
NO_READ_ONLY_VARIANT = {
    ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
    scopes.LICENSING_SCOPE,
    scopes.ALERTCENTER_SCOPE,
    scopes.GROUPS_SETTINGS_SCOPE,
}
LEVEL_FOR_RISK = {"read": "readonly", "manage": "manage", "destructive": "destructive"}


def _is_admin_scope(scope: str) -> bool:
    return "admin.directory" in scope


@pytest.fixture(autouse=True)
def _reset_selection(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", None)
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", False)
    monkeypatch.setattr(permissions, "_PERMISSIONS", None)


# --- Opt-in registration -------------------------------------------------------


def test_default_selection_has_no_admin_scope():
    selected = get_scopes_for_tools(["gmail", "calendar", "drive", "docs", "sheets"])
    assert not any(_is_admin_scope(scope) for scope in selected)


def test_all_services_default_has_no_admin_scope():
    assert not any(_is_admin_scope(scope) for scope in get_scopes_for_tools(None))
    assert not any(_is_admin_scope(scope) for scope in scopes.SCOPES)


def test_admin_directory_selection_requests_directory_scopes():
    selected = set(get_scopes_for_tools(["admin-directory"]))

    assert (
        READ_SCOPES
        | {
            ADMIN_DIRECTORY_USER_SCOPE,
            ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
        }
        <= selected
    )


def test_read_only_admin_selection_requests_only_read_scopes(monkeypatch):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    admin_scopes = {
        s for s in get_scopes_for_tools(["admin-directory"]) if _is_admin_scope(s)
    }

    assert admin_scopes == READ_SCOPES


def test_main_default_services_exclude_opt_in_admin():
    import main

    assert "admin-directory" in scopes.OPT_IN_SERVICES
    assert not set(main.DEFAULT_SERVICES) & scopes.OPT_IN_SERVICES
    assert set(main.DEFAULT_SERVICES) == set(main.SERVICE_MODULES) - set(
        scopes.OPT_IN_SERVICES
    )


def test_default_tier_selection_has_no_admin_tools():
    import main

    tools, services = resolve_tools_from_tier("complete", list(main.DEFAULT_SERVICES))

    assert "admin-directory" not in services
    assert "get_admin_user" not in tools


def test_fastmcp_entrypoint_does_not_import_admin_modules():
    source = (Path(__file__).parents[2] / "fastmcp_server.py").read_text()
    assert "gadmin" not in source


# --- Permission levels ---------------------------------------------------------


def test_admin_permission_levels_are_cumulative():
    readonly = set(permissions.get_scopes_for_permission("admin-directory", "readonly"))
    manage = set(permissions.get_scopes_for_permission("admin-directory", "manage"))
    destructive = set(
        permissions.get_scopes_for_permission("admin-directory", "destructive")
    )

    assert readonly == READ_SCOPES
    assert manage == READ_SCOPES | MANAGE_SCOPES
    # Tenant, custom-schema, and role-definition writes are destructive only.
    assert destructive == manage | {
        _DIRECTORY + "rolemanagement",
        _DIRECTORY + "customer",
        _DIRECTORY + "userschema",
    }
    assert permissions.parse_permissions_arg(["admin-directory:readonly"]) == {
        "admin-directory": "readonly"
    }


def _lowest_level(spec) -> str:
    if spec.risk == "read" and set(spec.scopes) & NO_READ_ONLY_VARIANT:
        return "manage"
    return LEVEL_FOR_RISK[spec.risk]


@pytest.mark.parametrize("spec", list(iter_operations()), ids=lambda spec: spec.id)
def test_every_operation_is_permitted_at_the_level_its_risk_needs(monkeypatch, spec):
    service = admin_auth.admin_service_for(spec)
    level = _lowest_level(spec)
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", [service])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {service: level})

    assert_admin_permission(spec)
    if service in scopes.DELEGATED_SERVICES:
        # Minted for a verified mailbox owner only; never part of an OAuth grant.
        read, write = scopes.DELEGATED_SERVICES[service]
        allowed = set(read if level == "readonly" else read + write)
        assert set(admin_auth.client_scopes(spec)) <= allowed
        return
    # The client for the operation, with its guard scopes, fits the level too.
    granted = set(permissions.get_scopes_for_permission(service, level))
    assert set(admin_auth.client_scopes(spec)) <= granted
    assert set(admin_auth.client_scopes(spec)) <= set(scopes.TOOL_SCOPES_MAP[service])
    if level == "readonly":
        assert set(admin_auth.client_scopes(spec)) <= set(
            scopes.TOOL_READONLY_SCOPES_MAP[service]
        )


def test_admin_permission_denied_when_service_not_selected(monkeypatch):
    for enabled in (None, ["gmail", "drive"]):
        monkeypatch.setattr(scopes, "_ENABLED_TOOLS", enabled)
        with pytest.raises(AdminPermissionError):
            assert_admin_permission(get_operation("directory.users.get"))


def test_full_admin_selection_allows_destructive(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory"])

    assert_admin_permission(get_operation("directory.users.get"))
    assert_admin_permission(get_operation("directory.users.delete"))


def test_read_only_mode_allows_reads_only(monkeypatch):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory"])
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", True)

    assert_admin_permission(get_operation("directory.users.get"))
    with pytest.raises(AdminPermissionError):
        assert_admin_permission(
            get_operation("directory.users.update"), {"orgUnitPath": "/Staff"}
        )
    # tokens.list is a read but Google offers no read-only scope for it.
    with pytest.raises(AdminPermissionError):
        assert_admin_permission(get_operation("directory.tokens.list"))


@pytest.mark.parametrize(
    ("level", "operation_id", "body", "allowed"),
    [
        ("readonly", "directory.users.get", None, True),
        ("readonly", "directory.users.update", {"orgUnitPath": "/Staff"}, False),
        ("manage", "directory.users.update", {"orgUnitPath": "/Staff"}, True),
        ("manage", "directory.users.update", {"suspended": True}, False),
        ("manage", "directory.users.delete", None, False),
        ("manage", "directory.tokens.list", None, True),
        ("destructive", "directory.users.update", {"suspended": True}, True),
        ("destructive", "directory.users.delete", None, True),
    ],
)
def test_permission_level_limits_risk_in_handler(
    monkeypatch, level, operation_id, body, allowed
):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory"])
    monkeypatch.setattr(permissions, "_PERMISSIONS", {"admin-directory": level})
    operation = get_operation(operation_id)

    if allowed:
        assert_admin_permission(operation, body)
    else:
        with pytest.raises(AdminPermissionError):
            assert_admin_permission(operation, body)


# --- Request-scoped admin client -----------------------------------------------


@pytest.fixture
def auth_mode(monkeypatch):
    """Configure the auth mode and a fake authenticator; returns captured calls."""

    def configure(*, oauth21=False, gateway=False, returned_email=ADMIN):
        monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: oauth21)
        monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: gateway)
        monkeypatch.setattr(admin_auth, "_current_session_id", lambda: "session-1")
        fake_service = MagicMock(name="directory")
        authenticate = AsyncMock(return_value=(fake_service, returned_email))
        monkeypatch.setattr(admin_auth, "_authenticate_service", authenticate)
        return SimpleNamespace(service=fake_service, authenticate=authenticate)

    return configure


@pytest.mark.asyncio
async def test_oauth21_requires_verified_identity(auth_mode):
    fake = auth_mode(oauth21=True)

    with pytest.raises(AdminAuthenticationError):
        await get_admin_service(ADMIN, get_operation("directory.users.get"), None)
    with pytest.raises(AdminAuthenticationError):
        await get_admin_service(
            ADMIN, get_operation("directory.users.get"), RequestIdentity(None, None)
        )
    fake.authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_caller_email_different_from_oauth21_identity_is_rejected(auth_mode):
    fake = auth_mode(oauth21=True)

    with pytest.raises(AdminAuthenticationError, match="request identity"):
        await get_admin_service(
            "other-admin@op.example",
            get_operation("directory.users.get"),
            RequestIdentity(ADMIN, "oauth21"),
        )
    fake.authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_oauth21_matching_identity_builds_bound_client(auth_mode):
    fake = auth_mode(oauth21=True)

    service = await get_admin_service(
        ADMIN.upper(),
        get_operation("directory.users.get"),
        RequestIdentity(ADMIN, "oauth21"),
    )

    assert service is fake.service
    args = fake.authenticate.await_args.args
    assert args[0] is True  # OAuth 2.1 path
    assert (args[1], args[2], args[3]) == (
        "admin",
        "directory_v1",
        "directory.users.get",
    )
    assert ADMIN_DIRECTORY_USER_READONLY_SCOPE in args[5]
    assert args[6] == "session-1"
    assert args[7] == ADMIN


@pytest.mark.asyncio
async def test_gateway_mode_requires_gateway_assertion(auth_mode):
    auth_mode(gateway=True)

    with pytest.raises(AdminAuthenticationError):
        await get_admin_service(
            ADMIN,
            get_operation("directory.users.get"),
            RequestIdentity(ADMIN, "oauth21"),
        )
    service = await get_admin_service(
        ADMIN,
        get_operation("directory.users.get"),
        RequestIdentity(ADMIN, "gateway_assertion"),
    )
    assert service is not None


@pytest.mark.asyncio
async def test_legacy_returned_email_mismatch_is_rejected_and_closed(auth_mode):
    fake = auth_mode(returned_email="someone-else@op.example")

    with pytest.raises(AdminAuthenticationError, match="Authenticated account"):
        await get_admin_service(ADMIN, get_operation("directory.users.get"), None)
    fake.service.close.assert_called_once()


@pytest.mark.asyncio
async def test_legacy_matching_credentials_return_service(auth_mode):
    fake = auth_mode()

    service = await get_admin_service(ADMIN, get_operation("directory.users.get"), None)

    assert service is fake.service
    assert fake.authenticate.await_args.args[0] is False


@pytest.mark.asyncio
async def test_service_account_uses_only_configured_subject(monkeypatch):
    monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(admin_auth, "_current_session_id", lambda: None)
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", ADMIN)
    monkeypatch.setattr(service_decorator, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: SimpleNamespace(dwd_allowed_domains=[]),
    )
    captured = {}

    def fake_credentials(requested_scopes, subject):
        captured["subject"] = subject
        captured["scopes"] = requested_scopes
        return object()

    monkeypatch.setattr(
        service_decorator, "_get_service_account_credentials", fake_credentials
    )
    monkeypatch.setattr(
        service_decorator, "build", lambda name, version, credentials: MagicMock()
    )
    # gadmin.auth must reach the real authenticator for this test.
    monkeypatch.setattr(
        admin_auth, "_authenticate_service", service_decorator._authenticate_service
    )

    await get_admin_service(ADMIN, get_operation("directory.users.get"), None)
    assert captured["subject"] == ADMIN
    assert ADMIN_DIRECTORY_USER_READONLY_SCOPE in captured["scopes"]

    captured.clear()
    with pytest.raises(service_decorator.GoogleAuthenticationError):
        await get_admin_service(
            "other-admin@op.example", get_operation("directory.users.get"), None
        )
    assert "subject" not in captured


# --- Typed tools via require_google_service -------------------------------------


def _bound_tool(monkeypatch, *, identity_email, returned_email):
    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        service_decorator,
        "_get_auth_context",
        AsyncMock(return_value=(identity_email, "oauth21", None)),
    )
    monkeypatch.setattr(service_decorator, "_detect_oauth_version", lambda *args: False)
    fake_service = MagicMock(name="directory")
    authenticate = AsyncMock(return_value=(fake_service, returned_email))
    monkeypatch.setattr(service_decorator, "_authenticate_service", authenticate)

    @service_decorator.require_google_service(
        "admin-directory", "admin_directory_user_read", bind_identity=True
    )
    async def sample_admin_tool(service, user_google_email: str) -> str:
        return user_google_email

    return sample_admin_tool, fake_service, authenticate


@pytest.mark.asyncio
async def test_bound_decorator_rejects_selection_that_differs_from_identity(
    monkeypatch,
):
    tool, _, authenticate = _bound_tool(
        monkeypatch, identity_email=ADMIN, returned_email=ADMIN
    )

    with pytest.raises(service_decorator.GoogleAuthenticationError):
        await tool(user_google_email="other-admin@op.example")
    authenticate.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_decorator_rejects_mismatched_credentials(monkeypatch):
    tool, fake_service, _ = _bound_tool(
        monkeypatch, identity_email=None, returned_email="someone-else@op.example"
    )

    with pytest.raises(service_decorator.GoogleAuthenticationError):
        await tool(user_google_email=ADMIN)
    fake_service.close.assert_called()


@pytest.mark.asyncio
async def test_bound_decorator_allows_matching_selection(monkeypatch):
    tool, _, authenticate = _bound_tool(
        monkeypatch, identity_email=None, returned_email=ADMIN
    )

    assert await tool(user_google_email=ADMIN) == ADMIN
    assert authenticate.await_args.args[1:3] == ("admin", "directory_v1")
