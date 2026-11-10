"""Per-request tool narrowing via the X-MCP-Toolsets and X-MCP-Tools headers (#509)."""

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.utilities.tests import asgi_client

import gdrive.drive_tools  # noqa: F401
import gmail.gmail_tools  # noqa: F401
from core import tool_registry, toolset_header_middleware
from core.server import server
from core.tool_tier_loader import get_tools_for_tier

DRIVE_TOOLS = set(get_tools_for_tier("complete", ["drive"]))
GMAIL_TOOLS = set(get_tools_for_tier("complete", ["gmail"]))


async def _listed(headers: dict[str, str] | None = None) -> set[str]:
    async with asgi_client(server, headers=headers) as client:
        return {tool.name for tool in await client.list_tools()}


async def _call_error(name: str, headers: dict[str, str]) -> str:
    async with asgi_client(server, headers=headers) as client:
        with pytest.raises(ToolError) as excinfo:
            await client.call_tool(name, {})
    return str(excinfo.value)


@pytest.fixture
def operator_disabled_send(monkeypatch):
    """Remove send_gmail_message the way WORKSPACE_MCP_DISABLED_TOOLS does at startup."""
    tool = tool_registry.get_tool_components(server)["send_gmail_message"]
    for check in ("is_oauth21_enabled", "is_permissions_mode", "is_read_only_mode"):
        monkeypatch.setattr(tool_registry, check, lambda: False)
    monkeypatch.setattr(tool_registry, "_enabled_tools", None)
    monkeypatch.setattr(tool_registry, "_disabled_tools", {"send_gmail_message"})
    assert tool_registry.filter_server_tools(server) == 1
    yield
    server.add_tool(tool)


@pytest.mark.asyncio
async def test_toolsets_header_narrows_tools_list():
    unfiltered = await _listed()

    assert await _listed({"X-MCP-Toolsets": "drive"}) == DRIVE_TOOLS & unfiltered


@pytest.mark.asyncio
async def test_tools_header_adds_to_toolsets():
    unfiltered = await _listed()
    tool = {"X-MCP-Tools": "search_gmail_messages"}
    expected = (DRIVE_TOOLS | {"search_gmail_messages"}) & unfiltered

    assert await _listed(tool) == {"search_gmail_messages"}
    assert await _listed({"X-MCP-Toolsets": "drive", **tool}) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"X-MCP-Toolsets": ""},
        {"X-MCP-Toolsets": " , ,"},
        {"X-MCP-Tools": "   "},
    ],
)
async def test_absent_or_blank_headers_leave_tools_unchanged(headers):
    unfiltered = await _listed()

    assert unfiltered >= DRIVE_TOOLS | GMAIL_TOOLS
    assert await _listed(headers) == unfiltered


@pytest.mark.asyncio
async def test_header_names_ignore_case_whitespace_and_duplicates():
    unfiltered = await _listed()
    headers = {
        "x-mcp-toolsets": " Drive ,, DRIVE ",
        "X-MCP-TOOLS": " Search_Gmail_Messages ,search_gmail_messages",
    }
    expected = (DRIVE_TOOLS | {"search_gmail_messages"}) & unfiltered

    assert await _listed(headers) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"X-MCP-Toolsets": "drive,nope"}, DRIVE_TOOLS),
        ({"X-MCP-Toolsets": "nope"}, set()),
        ({"X-MCP-Tools": "not_a_tool"}, set()),
        ({"X-MCP-Toolsets": "dr\u0456ve".encode()}, set()),
    ],
)
async def test_unknown_names_match_nothing(headers, expected):
    unfiltered = await _listed()

    assert await _listed(headers) == expected & unfiltered


@pytest.mark.parametrize(
    "headers",
    [
        {"x-mcp-toolsets": "dr\u0456ve"},
        {"x-mcp-tools": "search_gmail_me\u00dfages"},
    ],
)
def test_lookalike_names_match_nothing(monkeypatch, headers):
    monkeypatch.setattr(toolset_header_middleware, "get_http_headers", lambda: headers)

    assert toolset_header_middleware.requested_tools().isdisjoint(
        DRIVE_TOOLS | GMAIL_TOOLS
    )


@pytest.mark.asyncio
async def test_call_outside_header_is_refused_as_unknown():
    headers = {"X-MCP-Toolsets": "drive"}

    assert "Unknown tool" in await _call_error("send_gmail_message", headers)
    assert "Unknown tool" not in await _call_error("search_drive_files", headers)


@pytest.mark.asyncio
async def test_headers_cannot_expose_operator_disabled_tool(operator_disabled_send):
    headers = {"X-MCP-Toolsets": "gmail", "X-MCP-Tools": "send_gmail_message"}

    listed = await _listed(headers)

    assert "send_gmail_message" not in listed
    assert listed == GMAIL_TOOLS & await _listed()
    assert "Unknown tool" in await _call_error("send_gmail_message", headers)
