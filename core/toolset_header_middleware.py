"""
Middleware that narrows the tool set per request via X-MCP-Toolsets / X-MCP-Tools.
"""

from collections.abc import Mapping
from functools import cache
from typing import Optional

from fastmcp.exceptions import NotFoundError
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

from core.tool_tier_loader import ToolTierLoader

TOOLSETS_HEADER = "x-mcp-toolsets"
TOOLS_HEADER = "x-mcp-tools"


@cache
def _toolset_tools() -> dict[str, frozenset[str]]:
    """Map each service in tool_tiers.yaml to every tool it provides."""
    loader = ToolTierLoader()
    return {
        service: frozenset(loader.get_tools_up_to_tier("complete", [service]))
        for service in loader.get_available_services()
    }


def _header_names(headers: Mapping[str, str], header: str) -> set[str]:
    return {
        name.strip().lower()
        for name in headers.get(header, "").split(",")
        if name.strip()
    }


def requested_tools() -> Optional[set[str]]:
    """Return the tool names the current request's headers allow, or None for no limit."""
    headers = get_http_headers()
    toolsets = _header_names(headers, TOOLSETS_HEADER)
    tools = _header_names(headers, TOOLS_HEADER)
    if not toolsets and not tools:
        return None
    toolset_tools = _toolset_tools()
    return tools.union(*(toolset_tools.get(name, ()) for name in toolsets))


class ToolsetHeaderMiddleware(Middleware):
    """Hide and refuse tools outside the request's X-MCP-Toolsets / X-MCP-Tools.

    Only narrows what the server already registered, so a header can never
    expose a tool the operator's configuration removed.
    """

    async def on_list_tools(self, context: MiddlewareContext, call_next: CallNext):
        tools = await call_next(context)
        allowed = requested_tools()
        if allowed is None:
            return tools
        return [tool for tool in tools if tool.name in allowed]

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        allowed = requested_tools()
        name = context.message.name
        if allowed is not None and name not in allowed:
            raise NotFoundError(f"Unknown tool: {name!r}")
        return await call_next(context)
