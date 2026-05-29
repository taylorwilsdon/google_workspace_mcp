"""HTTP health endpoints for container and Kubernetes deployments."""

from threading import Event

_mcp_tools_ready = Event()


def mark_mcp_tools_ready() -> None:
    """Record that MCP tools are loaded and the server can accept tool traffic."""
    _mcp_tools_ready.set()


def mcp_tools_ready() -> bool:
    """Return whether mark_mcp_tools_ready has been called."""
    return _mcp_tools_ready.is_set()
