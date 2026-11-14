"""Synthetic read-only Directory and opt-in startup coverage; no Google calls."""

import json
import os
import subprocess
import sys
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

import auth.service_decorator as decorators
from auth import scopes
from gadmin import admin_tools
from gadmin.admin_helpers import USER_FIELDS, get_users
from core.utils import UserInputError


@pytest.mark.asyncio
async def test_directory_requests_and_pagination():
    users = Mock(spec=["get", "list"])
    service = Mock(spec=["users"])
    service.users.return_value = users
    user = {"id": "synthetic", "aliases": ["alias@example.invalid"]}
    users.list.return_value.execute.return_value = {
        "users": [user],
        "nextPageToken": "synthetic-next",
    }
    result = json.loads(
        await get_users(
            service, None, "isSuspended=true", "my_customer", 20, "synthetic-prev"
        )
    )
    assert result == {"users": [user], "nextPageToken": "synthetic-next"}
    users.list.assert_called_once_with(
        customer="my_customer",
        query="isSuspended=true",
        maxResults=20,
        pageToken="synthetic-prev",
        projection="basic",
        fields=f"users({USER_FIELDS}),nextPageToken",
    )
    users.list.return_value.execute.return_value = {}
    assert json.loads(
        await get_users(service, None, None, "customer-id", 100, None)
    ) == {"users": []}
    users.get.return_value.execute.return_value = user
    assert (
        json.loads(
            await get_users(
                service, "alias@example.invalid", None, "my_customer", 100, None
            )
        )
        == user
    )
    users.get.assert_called_once_with(
        userKey="alias@example.invalid", projection="basic", fields=USER_FIELDS
    )
    error = HttpError(Response({"status": 403}), b'{"error":{"message":"Forbidden"}}')
    users.get.return_value.execute.side_effect = error
    with pytest.raises(HttpError):
        await get_users(service, "id", None, "my_customer", 100, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        (None, None, "my_customer", 0, None),
        (None, None, "my_customer", 501, None),
        (" ", None, "my_customer", 100, None),
        (None, None, " ", 100, None),
        ("id", "name=foo", "my_customer", 100, None),
        ("id", None, "customer-id", 100, None),
        ("id", None, "my_customer", 20, None),
        ("id", None, "my_customer", 100, "page"),
    ],
)
async def test_invalid_requests_do_not_call_directory(arguments):
    service = Mock()
    with pytest.raises(UserInputError):
        await get_users(service, *arguments)
    service.users.assert_not_called()


@pytest.mark.parametrize("readonly", [False, True])
def test_admin_scope_requires_explicit_selection(monkeypatch, readonly):
    monkeypatch.setattr(scopes, "_READ_ONLY_MODE", readonly)
    assert not {
        scopes.ADMIN_DIRECTORY_USER_SCOPE,
        scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    }.intersection(scopes.get_scopes_for_tools())
    assert set(scopes.get_scopes_for_tools(["admin"])) == {
        *scopes.BASE_SCOPES,
        scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    }


@pytest.mark.parametrize(
    "args,enabled",
    [
        ([], False),
        (["--read-only"], False),
        (["--tool-tier", "core"], False),
        (["--tool-tier", "extended"], False),
        (["--tool-tier", "complete"], False),
        (["--tools", "admin"], True),
        (["--tools", "admin", "--read-only"], True),
        (["--tools", "admin", "--tool-tier", "core", "--read-only"], True),
    ],
)
def test_startup_admin_exposure(args, enabled, tmp_path):
    # Stop immediately after real tool filtering, before credentials or server I/O.
    script = """
import asyncio, json, main
from auth.scopes import get_current_scopes
from core.tool_registry import get_tool_components
main.configure_safe_logging = lambda: None
main.resolve_callback_port_for_transport = lambda transport: None
original = main.filter_server_tools
def capture(server):
    original(server)
    tools = get_tool_components(server)
    admin = tools.get("get_admin_users")
    print(json.dumps({"enabled": admin is not None, "scopes": get_current_scopes(),
        "read_only": admin.annotations.readOnlyHint if admin else None}))
    raise SystemExit(0)
main.filter_server_tools = capture
main.main()
"""
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "MCP_ENABLE_OAUTH21": "false",
        "WORKSPACE_MCP_LOG_LEVEL": "ERROR",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    actual = json.loads(result.stdout)
    assert actual["enabled"] is enabled
    assert scopes.ADMIN_DIRECTORY_USER_SCOPE not in actual["scopes"]
    assert (scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE in actual["scopes"]) is enabled
    if enabled:
        assert actual["read_only"] is True


@pytest.mark.asyncio
async def test_tool_uses_existing_authorization_and_readonly_scope(monkeypatch):
    email = "admin@example.invalid"
    service = Mock()
    authenticate = AsyncMock(return_value=(service, email))
    read = AsyncMock(return_value="synthetic response")
    monkeypatch.setattr(
        decorators, "_get_auth_context", AsyncMock(return_value=(None, None, None))
    )
    monkeypatch.setattr(decorators, "_user_email_is_managed", lambda: False)
    monkeypatch.setattr(decorators, "_detect_oauth_version", lambda *args: False)
    monkeypatch.setattr(decorators, "_authenticate_service", authenticate)
    monkeypatch.setattr(decorators, "recycling", nullcontext)
    monkeypatch.setattr(admin_tools, "get_users", read)
    assert (
        await admin_tools.get_admin_users(
            user_google_email=email, user_key="user@example.invalid"
        )
        == "synthetic response"
    )
    authenticate.assert_awaited_once_with(
        False,
        "admin",
        "directory_v1",
        "get_admin_users",
        email,
        [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
        None,
        None,
    )
    read.assert_awaited_once_with(
        service, "user@example.invalid", None, "my_customer", 100, None
    )


@pytest.mark.parametrize(
    "available,required,expected",
    [
        (
            [scopes.ADMIN_DIRECTORY_USER_SCOPE],
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            True,
        ),
        (
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            True,
        ),
        (
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            [scopes.ADMIN_DIRECTORY_USER_SCOPE],
            False,
        ),
        ([], [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE], False),
        (
            ["https://www.googleapis.com/auth/admin.directory.user.alias"],
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            False,
        ),
        (
            ["https://www.googleapis.com/auth/admin.directory.group"],
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            False,
        ),
        (
            ["https://www.googleapis.com/auth/cloud-platform"],
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            False,
        ),
    ],
)
def test_admin_scope_compatibility_is_one_way(available, required, expected):
    original_available, original_required = available.copy(), required.copy()
    assert scopes.has_required_scopes(available, required) is expected
    assert available == original_available
    assert required == original_required


@pytest.mark.asyncio
@pytest.mark.parametrize("flow", ["token", "session"])
@pytest.mark.parametrize(
    "granted,accepted",
    [(scopes.ADMIN_DIRECTORY_USER_SCOPE, True), (scopes.GMAIL_READONLY_SCOPE, False)],
)
async def test_admin_oauth21_uses_existing_identity_grants(
    monkeypatch, flow, granted, accepted
):
    email = "admin@example.invalid"
    credentials = SimpleNamespace(scopes=[granted])
    token = SimpleNamespace(claims={"email": email}, scopes=[granted])
    ensure = AsyncMock(return_value=credentials)
    store = Mock()
    store.get_credentials_with_validation.return_value = credentials
    build = Mock(return_value=object())
    monkeypatch.setattr(
        decorators, "get_auth_provider", lambda: object() if flow == "token" else None
    )
    monkeypatch.setattr(
        decorators, "get_access_token", lambda: token if flow == "token" else None
    )
    monkeypatch.setattr(decorators, "ensure_session_from_access_token", ensure)
    monkeypatch.setattr(decorators, "get_oauth21_session_store", lambda: store)
    monkeypatch.setattr(decorators, "build_google_service", build)
    call = decorators.get_authenticated_google_service_oauth21(
        "admin",
        "directory_v1",
        "get_admin_users",
        email,
        [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
        session_id="synthetic-session",
        auth_token_email=email,
    )
    if accepted:
        assert await call == (build.return_value, email)
        build.assert_called_once_with("admin", "directory_v1", credentials)
    else:
        with pytest.raises(decorators.GoogleScopeError):
            await call
        build.assert_not_called()
    if flow == "token":
        ensure.assert_awaited_once_with(token, email, "synthetic-session")
        store.get_credentials_with_validation.assert_not_called()
    else:
        ensure.assert_not_awaited()
        store.get_credentials_with_validation.assert_called_once_with(
            requested_user_email=email,
            session_id="synthetic-session",
            auth_token_email=email,
            allow_recent_auth=False,
        )
    assert credentials.scopes == [granted]


@pytest.mark.asyncio
async def test_admin_full_grant_does_not_bypass_token_identity(monkeypatch):
    token = SimpleNamespace(
        claims={"email": "other@example.invalid"},
        scopes=[scopes.ADMIN_DIRECTORY_USER_SCOPE],
    )
    ensure, build = AsyncMock(), Mock()
    monkeypatch.setattr(decorators, "get_auth_provider", lambda: object())
    monkeypatch.setattr(decorators, "get_access_token", lambda: token)
    monkeypatch.setattr(decorators, "ensure_session_from_access_token", ensure)
    monkeypatch.setattr(decorators, "build_google_service", build)
    with pytest.raises(decorators.GoogleAuthenticationError, match="does not match"):
        await decorators.get_authenticated_google_service_oauth21(
            "admin",
            "directory_v1",
            "get_admin_users",
            "admin@example.invalid",
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
        )
    ensure.assert_not_awaited()
    build.assert_not_called()


@pytest.mark.asyncio
async def test_admin_service_account_still_requests_only_readonly(monkeypatch):
    email = "admin@example.invalid"
    get_credentials = Mock(return_value=object())
    build = Mock(return_value=object())
    monkeypatch.setattr(decorators, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(decorators, "_get_configured_user_google_email", lambda: email)
    monkeypatch.setattr(
        decorators, "get_oauth_config", lambda: SimpleNamespace(dwd_allowed_domains=[])
    )
    monkeypatch.setattr(decorators, "_get_service_account_credentials", get_credentials)
    monkeypatch.setattr(decorators, "build_google_service", build)
    monkeypatch.setattr(decorators, "record_google_principal", Mock())
    result = await decorators._authenticate_service(
        False,
        "admin",
        "directory_v1",
        "get_admin_users",
        email,
        [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
        None,
        None,
    )
    assert result == (build.return_value, email)
    get_credentials.assert_called_once_with(
        [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE], email
    )


@pytest.mark.asyncio
async def test_admin_scope_compatibility_does_not_bypass_session_denial(monkeypatch):
    store = Mock()
    store.get_credentials_with_validation.return_value = None
    build = Mock()
    monkeypatch.setattr(decorators, "get_auth_provider", lambda: None)
    monkeypatch.setattr(decorators, "get_access_token", lambda: None)
    monkeypatch.setattr(decorators, "get_oauth21_session_store", lambda: store)
    monkeypatch.setattr(decorators, "build_google_service", build)
    with pytest.raises(decorators.GoogleAuthenticationError, match="Access denied"):
        await decorators.get_authenticated_google_service_oauth21(
            "admin",
            "directory_v1",
            "get_admin_users",
            "other@example.invalid",
            [scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE],
            session_id="synthetic-session",
            auth_token_email="admin@example.invalid",
        )
    store.get_credentials_with_validation.assert_called_once_with(
        requested_user_email="other@example.invalid",
        session_id="synthetic-session",
        auth_token_email="admin@example.invalid",
        allow_recent_auth=False,
    )
    build.assert_not_called()
