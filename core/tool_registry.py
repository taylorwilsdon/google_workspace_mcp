"""
Tool Registry for Conditional Tool Registration

This module provides a registry system that allows tools to be conditionally registered
based on tier configuration, replacing direct @server.tool() decorators.
"""

import logging
from typing import Set, Optional, Callable

logger = logging.getLogger(__name__)

# Global registry of enabled tools
_enabled_tools: Optional[Set[str]] = None


def set_enabled_tools(tool_names: Optional[Set[str]]):
    """Set the globally enabled tools."""
    global _enabled_tools
    _enabled_tools = tool_names


def get_enabled_tools() -> Optional[Set[str]]:
    """Get the set of enabled tools, or None if all tools are enabled."""
    return _enabled_tools


def is_tool_enabled(tool_name: str) -> bool:
    """Check if a specific tool is enabled."""
    if _enabled_tools is None:
        return True  # All tools enabled by default
    return tool_name in _enabled_tools


def conditional_tool(server, tool_name: str):
    """
    Decorator that conditionally registers a tool based on the enabled tools set.

    Args:
        server: The FastMCP server instance
        tool_name: The name of the tool to register

    Returns:
        Either the registered tool decorator or a no-op decorator
    """

    def decorator(func: Callable) -> Callable:
        if is_tool_enabled(tool_name):
            logger.debug(f"Registering tool: {tool_name}")
            return server.tool()(func)
        else:
            logger.debug(f"Skipping tool registration: {tool_name}")
            return func

    return decorator


def wrap_server_tool_method(server):
    """
    Track tool registrations and filter them post-registration.
    """
    original_tool = server.tool
    server._tracked_tools = []

    def tracking_tool(*args, **kwargs):
        original_decorator = original_tool(*args, **kwargs)

        def wrapper_decorator(func: Callable) -> Callable:
            tool_name = func.__name__
            server._tracked_tools.append(tool_name)
            # Always apply the original decorator to register the tool
            return original_decorator(func)

        return wrapper_decorator

    server.tool = tracking_tool


def filter_server_tools(server):
    """Remove disabled tools from the server after registration."""
    enabled_tools = get_enabled_tools()
    if enabled_tools is None:
        return

    tools_removed = 0

    # Access FastMCP's tool registry via _tool_manager._tools
    if hasattr(server, "_tool_manager"):
        tool_manager = server._tool_manager
        if hasattr(tool_manager, "_tools"):
            tool_registry = tool_manager._tools

            tools_to_remove = []
            for tool_name in list(tool_registry.keys()):
                if not is_tool_enabled(tool_name):
                    tools_to_remove.append(tool_name)

            for tool_name in tools_to_remove:
                del tool_registry[tool_name]
                tools_removed += 1

    if tools_removed > 0:
        logger.info(
            f"Tool tier filtering: removed {tools_removed} tools, {len(enabled_tools)} enabled"
        )
