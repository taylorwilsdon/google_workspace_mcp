"""The Directory read tools run authentication, the customer guard, and dispatch
in that order through a real FastMCP client, and appear only when the
admin-directory service is selected."""

import os
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

import auth.scopes as scopes
import auth.service_decorator as sd
import gadmin.admin_tools  # noqa: F401  (registers the tools on the shared server)
import gadmin.auth as admin_auth
from core.server import server
from core.tool_registry import get_tool_components
from tests.gadmin.fake_directory import FakeDirectory

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
ADMIN = "admin@op.example"
ADMIN_TOOLS = {
    "get_admin_user",
    "list_admin_capabilities",
    "get_offboarding_status",
    "admin_operation",
}
CONFIRM = {"confirm_admin_operation"}


@pytest.fixture
def fake_directory():
    fake = FakeDirectory(customer_id="C01")
    fake.add_user(ADMIN, "U_ADMIN", super_admin=True)
    fake.add_user("staff@op.example", "U_STAFF", recoveryPhone="+15555550100")
    fake.add_user("u@other.example", "U_OTHER", customer_id="C_OTHER")
    return fake


@pytest.fixture
def authenticate(monkeypatch, fake_directory):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["admin-directory"])
    auth = AsyncMock(return_value=(fake_directory, ADMIN))
    monkeypatch.setattr(sd, "_authenticate_service", auth)
    return auth


async def _call(tool, **arguments):
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    return result, result.content[0].text


# --- get_admin_user through the MCP client ----------------------------------------


@pytest.mark.asyncio
async def test_get_admin_user_returns_same_customer_summary(
    authenticate, fake_directory
):
    result, text = await _call(
        "get_admin_user", user_google_email=ADMIN, target_email="staff@op.example"
    )

    assert not result.is_error, text
    assert "staff@op.example" in text and "U_STAFF" in text and "C01" in text
    assert "15555550100" not in text
    # Actor lookup, then target guard, then dispatch by the verified user ID.
    assert fake_directory.calls == [
        ("directory.users.get", {"userKey": ADMIN}),
        ("directory.users.get", {"userKey": "staff@op.example"}),
        ("directory.users.get", {"userKey": "U_STAFF"}),
    ]
    assert fake_directory.write_calls == []
    assert fake_directory.closed


@pytest.mark.asyncio
async def test_get_admin_user_denies_other_customer(authenticate, fake_directory):
    result, text = await _call(
        "get_admin_user", user_google_email=ADMIN, target_email="u@other.example"
    )

    assert result.is_error
    assert "customer" in text.lower()
    assert ("directory.users.get", {"userKey": "U_OTHER"}) not in fake_directory.calls
    assert "C_OTHER" not in text
    assert fake_directory.write_calls == []


@pytest.mark.asyncio
async def test_get_admin_user_denies_selection_other_than_request_identity(
    authenticate, fake_directory, monkeypatch
):
    monkeypatch.setattr(
        sd,
        "_get_auth_context",
        AsyncMock(return_value=("someone@op.example", "oauth21", None)),
    )

    result, text = await _call(
        "get_admin_user", user_google_email=ADMIN, target_email="staff@op.example"
    )

    assert result.is_error
    assert "does not match" in text
    authenticate.assert_not_awaited()
    assert fake_directory.calls == []


@pytest.mark.asyncio
async def test_get_admin_user_denies_credentials_for_another_account(
    authenticate, fake_directory
):
    authenticate.return_value = (fake_directory, "someone@op.example")

    result, text = await _call(
        "get_admin_user", user_google_email=ADMIN, target_email="staff@op.example"
    )

    assert result.is_error
    assert "does not match" in text
    assert fake_directory.calls == []
    assert fake_directory.closed


@pytest.mark.asyncio
async def test_get_admin_user_denies_non_admin_actor(authenticate, fake_directory):
    authenticate.return_value = (fake_directory, "staff@op.example")

    result, text = await _call(
        "get_admin_user",
        user_google_email="staff@op.example",
        target_email="staff@op.example",
    )

    assert result.is_error
    assert "not a Workspace admin" in text
    assert fake_directory.calls == [
        ("directory.users.get", {"userKey": "staff@op.example"})
    ]


@pytest.mark.asyncio
async def test_handler_refuses_when_service_not_enabled(
    authenticate, fake_directory, monkeypatch
):
    monkeypatch.setattr(scopes, "_ENABLED_TOOLS", ["gmail"])

    result, text = await _call(
        "get_admin_user", user_google_email=ADMIN, target_email="staff@op.example"
    )

    assert result.is_error
    assert "not enabled" in text
    assert fake_directory.calls == []


@pytest.mark.asyncio
async def test_invalid_target_is_rejected_before_any_lookup(
    authenticate, fake_directory
):
    result, text = await _call(
        "get_admin_user", user_google_email=ADMIN, target_email="https://evil/x"
    )

    assert result.is_error
    assert fake_directory.calls == []


# --- list_admin_capabilities ------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_list_operations_and_gaps(authenticate):
    result, text = await _call("list_admin_capabilities", user_google_email=ADMIN)

    assert not result.is_error, text
    assert "directory.users.get" in text and "get_admin_user" in text
    assert "directory.users.delete" in text
    assert "not implemented" in text.lower()
    # Pinned families are listed by operation; the rest are named as pending.
    assert "licensing.licenseAssignments.get" in text
    assert "datatransfer.transfers.list" in text
    assert "vault.matters.holds.list" in text
    not_implemented = text.split("Not implemented yet:", 1)[1]
    assert "Context-aware access level changes and app assignments" in not_implemented
    for family in (
        "Vault",
        "Reports",
        "Alert Center",
        "Groups Settings",
        "Cloud Identity",
    ):
        assert family not in not_implemented


@pytest.mark.asyncio
async def test_capabilities_reflect_read_only_launch(authenticate, monkeypatch):
    monkeypatch.setattr(admin_auth, "is_read_only_mode", lambda: True)

    _, text = await _call("list_admin_capabilities", user_google_email=ADMIN)

    delete_line = next(
        line for line in text.splitlines() if "directory.users.delete" in line
    )
    get_line = next(
        line for line in text.splitlines() if "directory.users.get " in line
    )
    assert "not permitted" in delete_line
    assert "not permitted" not in get_line


@pytest.mark.asyncio
async def test_capabilities_name_the_service_an_operation_needs(authenticate):
    _, text = await _call("list_admin_capabilities", user_google_email=ADMIN)
    lines = text.splitlines()

    delete_line = next(
        line for line in lines if "licensing.licenseAssignments.delete" in line
    )
    insert_line = next(
        line for line in lines if "datatransfer.transfers.insert" in line
    )
    assert "select the admin-licensing service" in delete_line
    assert "select the admin-datatransfer service" in insert_line
    groups_line = next(line for line in lines if "directory.groups.get " in line)
    users_line = next(line for line in lines if "directory.users.get " in line)
    assert "available via admin_operation" in groups_line
    assert "available via get_admin_user" in users_line
    # Every excluded method is listed with its category; other areas are pending.
    excluded = text.split("Excluded:", 1)[1].split("Not implemented yet:", 1)[0]
    assert "directory.users.insert (secret-in-payload)" in excluded
    assert "licensing.licenseAssignments.update (replacement-variant)" in excluded
    not_implemented = text.split("Not implemented yet:", 1)[1]
    assert "licenseAssignments" not in not_implemented
    assert "Context-aware access" in not_implemented
    vault_line = next(line for line in lines if "vault.matters.list " in line)
    assert "select the admin-vault service" in vault_line


def test_admin_tools_are_read_only():
    for name in ADMIN_TOOLS:
        tool = get_tool_components(server)[name]
        func = getattr(tool, "fn", tool)
        assert tool.annotations.readOnlyHint is True
        assert set(getattr(func, "_required_google_scopes", [])) <= set(
            scopes.get_all_read_only_scopes()
        )


# --- Launch-time tool lists -------------------------------------------------------

LIST_TOOLS = """
import sys
import main
from core.tool_registry import get_tool_components

real_filter = main.filter_server_tools

def list_and_stop(server):
    real_filter(server)
    for name, tool in sorted(get_tool_components(server).items()):
        module = getattr(getattr(tool, "fn", tool), "__module__", "")
        print(f"TOOL:{name}:{module}")
    raise SystemExit(0)

main.filter_server_tools = list_and_stop
main.configure_safe_logging = lambda: None
main.main()
"""


def _admin_tools_at_launch(*args: str) -> set[str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "MCP_ENABLE_OAUTH21",
            "EXTERNAL_OAUTH21_PROVIDER",
            "WORKSPACE_MCP_STATELESS_MODE",
            "MCP_SINGLE_USER_MODE",
            "TOOLS",
            "TOOL_TIER",
            "GOOGLE_SERVICE_ACCOUNT_KEY_FILE",
        )
    }
    env.update(
        GOOGLE_OAUTH_CLIENT_ID="test-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="test-client-secret",
    )
    result = subprocess.run(
        [sys.executable, "-c", LIST_TOOLS, *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    tools = [
        line.split(":")[1:]
        for line in result.stdout.splitlines()
        if line.startswith("TOOL:")
    ]
    assert tools, result.stderr[-2000:]
    return {name for name, module in tools if module.startswith("gadmin")}


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("--tools", "gmail", "calendar", "drive", "docs", "sheets"),
        ("--tool-tier", "complete"),
    ],
    ids=["default", "five-service-launcher", "complete-tier"],
)
def test_existing_launches_expose_no_admin_tools(args):
    assert _admin_tools_at_launch(*args) == set()


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (("--tools", "admin-directory"), ADMIN_TOOLS | CONFIRM),
        (("--tools", "admin-directory", "--read-only"), ADMIN_TOOLS),
        (("--permissions", "admin-directory:readonly"), ADMIN_TOOLS),
        (("--tools", "admin-directory", "--tool-tier", "core"), ADMIN_TOOLS),
        (
            ("--tools", "admin-directory", "--tool-tier", "extended"),
            ADMIN_TOOLS | CONFIRM,
        ),
        (
            ("--permissions", "admin-directory:readonly", "admin-licensing:manage"),
            ADMIN_TOOLS | CONFIRM,
        ),
    ],
    ids=[
        "tools",
        "read-only",
        "readonly-permission",
        "core-tier",
        "extended-tier",
        "licensing-writes",
    ],
)
def test_selecting_admin_directory_exposes_read_tools(args, expected):
    # Confirmation is hidden when no selected admin service may write.
    assert _admin_tools_at_launch(*args) == expected


def test_tools_are_hidden_unless_their_services_are_selected():
    assert _admin_tools_at_launch(
        "--tools", "admin-datatransfer", "admin-licensing"
    ) == {"list_admin_capabilities"}


def test_offboarding_tools_need_all_services_and_write_permissions():
    selected = ("--tools", "admin-directory", "admin-datatransfer", "admin-licensing")
    assert _admin_tools_at_launch(*selected) == ADMIN_TOOLS | CONFIRM | {
        "plan_user_offboarding",
        "advance_user_offboarding",
    }
    assert _admin_tools_at_launch(*selected, "--read-only") == ADMIN_TOOLS
    assert (
        _admin_tools_at_launch(
            "--permissions",
            "admin-directory:readonly",
            "admin-datatransfer:readonly",
            "admin-licensing:readonly",
        )
        == ADMIN_TOOLS
    )
    assert (
        _admin_tools_at_launch("--tools", "admin-directory", "admin-datatransfer")
        == ADMIN_TOOLS | CONFIRM
    )
