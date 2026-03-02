"""
Tool Registry for Conditional Tool Registration

This module provides a registry system that allows tools to be conditionally registered
based on tier configuration, replacing direct @server.tool() decorators.
"""

import logging
from typing import Set, Optional, Callable

from auth.oauth_config import is_oauth21_enabled
from auth.permissions import is_permissions_mode, get_allowed_scopes_set
from auth.scopes import is_read_only_mode, get_all_read_only_scopes

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


def get_tool_components(server) -> dict:
    """Get tool components dict from server's local_provider.

    Returns a dict mapping tool_name -> tool_object for introspection.

    Note: Uses local_provider._components because the public list_tools()
    is async-only, and callers (startup filtering, CLI) run synchronously.
    """
    lp = getattr(server, "local_provider", None)
    if lp is None:
        return {}
    components = getattr(lp, "_components", {})
    tools = {}
    for key, component in components.items():
        if key.startswith("tool:"):
            # Keys are like "tool:name@version", extract the name
            name = key.split(":", 1)[1].rsplit("@", 1)[0]
            tools[name] = component
    return tools


def filter_server_tools(server):
    """Remove disabled tools from the server after registration."""
    enabled_tools = get_enabled_tools()
    oauth21_enabled = is_oauth21_enabled()
    permissions_mode = is_permissions_mode()
    if (
        enabled_tools is None
        and not oauth21_enabled
        and not is_read_only_mode()
        and not permissions_mode
    ):
        return

    tools_removed = 0
    tool_components = get_tool_components(server)

    read_only_mode = is_read_only_mode()
    allowed_scopes = set(get_all_read_only_scopes()) if read_only_mode else None

    tools_to_remove = set()

    # 1. Tier filtering
    if enabled_tools is not None:
        for tool_name in tool_components:
            if not is_tool_enabled(tool_name):
                tools_to_remove.add(tool_name)

    # 2. OAuth 2.1 filtering
    if oauth21_enabled and "start_google_auth" in tool_components:
        tools_to_remove.add("start_google_auth")
        logger.info("OAuth 2.1 enabled: disabling start_google_auth tool")

    # 3. Read-only mode filtering (skipped when granular permissions are active)
    if read_only_mode and not permissions_mode:
        for tool_name, tool_obj in tool_components.items():
            if tool_name in tools_to_remove:
                continue

            # Check if tool has required scopes attached (from @require_google_service)
            func_to_check = tool_obj
            if hasattr(tool_obj, "fn"):
                func_to_check = tool_obj.fn

            required_scopes = getattr(func_to_check, "_required_google_scopes", [])

            if required_scopes:
                # If ANY required scope is not in the allowed read-only scopes, disable the tool
                if not all(scope in allowed_scopes for scope in required_scopes):
                    logger.info(
                        f"Read-only mode: Disabling tool '{tool_name}' (requires write scopes: {required_scopes})"
                    )
                    tools_to_remove.add(tool_name)

    # 4. Granular permissions filtering
    # No scope hierarchy expansion here — permission levels are already cumulative
    # and explicitly define allowed scopes. Hierarchy expansion would defeat the
    # purpose (e.g. gmail.modify in the hierarchy covers gmail.send, but the
    # "organize" permission level intentionally excludes gmail.send).
    if permissions_mode:
        perm_allowed = get_allowed_scopes_set() or set()

        for tool_name, tool_obj in tool_components.items():
            if tool_name in tools_to_remove:
                continue

            func_to_check = tool_obj
            if hasattr(tool_obj, "fn"):
                func_to_check = tool_obj.fn

            required_scopes = getattr(func_to_check, "_required_google_scopes", [])
            if required_scopes:
                if not all(scope in perm_allowed for scope in required_scopes):
                    logger.info(
                        "Permissions mode: Disabling tool '%s' (requires: %s)",
                        tool_name,
                        required_scopes,
                    )
                    tools_to_remove.add(tool_name)

    for tool_name in tools_to_remove:
        try:
            server.local_provider.remove_tool(tool_name)
        except AttributeError:
            logger.warning(
                "Failed to remove tool '%s': remove_tool not available on server.local_provider",
                tool_name,
            )
            continue
        except Exception as exc:
            logger.warning(
                "Failed to remove tool '%s': %s",
                tool_name,
                exc,
            )
            continue
        tools_removed += 1

    if tools_removed > 0:
        enabled_count = len(enabled_tools) if enabled_tools is not None else "all"
        if permissions_mode:
            mode = "Permissions"
        elif is_read_only_mode():
            mode = "Read-Only"
        else:
            mode = "Full"
        logger.info(
            f"Tool filtering: removed {tools_removed} tools, {enabled_count} enabled. Mode: {mode}"
        )
