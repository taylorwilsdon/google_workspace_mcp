"""Synthetic read-only Directory and opt-in startup coverage; no Google calls."""

import json
import os
import subprocess
import sys
from contextlib import nullcontext
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
    assert (
        scopes.ADMIN_DIRECTORY_USER_READONLY_SCOPE not in scopes.get_scopes_for_tools()
    )
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
