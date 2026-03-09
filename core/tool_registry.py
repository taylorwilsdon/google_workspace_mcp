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
from auth.service_decorator import SCOPE_GROUPS

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


def _resolve_scope_urls(scope_groups: tuple) -> list:
    """Convert scope group names to full OAuth URLs."""
    urls = []
    for group in scope_groups:
        if group in SCOPE_GROUPS:
            scopes = SCOPE_GROUPS[group]
            if isinstance(scopes, list):
                urls.extend(scopes)
            else:
                urls.append(scopes)
    return urls


def _get_required_scopes_from_obj(tool_obj) -> list:
    """Fallback: introspect _required_google_scopes from wrapper chain."""
    func_to_check = tool_obj
    if hasattr(tool_obj, "fn"):
        func_to_check = tool_obj.fn
    return getattr(func_to_check, "_required_google_scopes", [])


def filter_server_tools(server):
    """Remove disabled tools from the server after registration."""
    from core.tool_schema import SchemaRegistry

    registry = SchemaRegistry.instance()
    enabled_tools = get_enabled_tools()
    oauth21_enabled = is_oauth21_enabled()
    permissions_mode = is_permissions_mode()
    read_only_mode = is_read_only_mode()

    if (
        enabled_tools is None
        and not oauth21_enabled
        and not read_only_mode
        and not permissions_mode
    ):
        return

    allowed_ro_scopes = set(get_all_read_only_scopes()) if read_only_mode else None
    perm_allowed = get_allowed_scopes_set() if permissions_mode else None
    tool_components = get_tool_components(server)
    tools_to_remove = set()

    for tool_name, tool_obj in tool_components.items():
        # 1. Tier filtering
        if enabled_tools is not None and tool_name not in enabled_tools:
            tools_to_remove.add(tool_name)
            continue

        # 2. OAuth 2.1 filtering
        if oauth21_enabled and tool_name == "start_google_auth":
            tools_to_remove.add(tool_name)
            logger.info("OAuth 2.1 enabled: disabling start_google_auth tool")
            continue

        # Resolve required scopes — prefer SchemaRegistry, fall back to introspection
        schema = registry.get(tool_name)
        if schema is not None:
            required_scopes = _resolve_scope_urls(schema.scopes)
        else:
            required_scopes = _get_required_scopes_from_obj(tool_obj)

        # 3. Read-only mode filtering (skipped when granular permissions active)
        if read_only_mode and not permissions_mode:
            if required_scopes and not all(
                s in allowed_ro_scopes for s in required_scopes
            ):
                logger.info(
                    "Read-only mode: Disabling tool '%s' (requires write scopes: %s)",
                    tool_name,
                    required_scopes,
                )
                tools_to_remove.add(tool_name)
                continue

        # 4. Granular permissions filtering
        if permissions_mode and perm_allowed is not None:
            if required_scopes and not all(s in perm_allowed for s in required_scopes):
                logger.info(
                    "Permissions mode: Disabling tool '%s' (requires: %s)",
                    tool_name,
                    required_scopes,
                )
                tools_to_remove.add(tool_name)
                continue

    tools_removed = 0
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
        elif read_only_mode:
            mode = "Read-Only"
        else:
            mode = "Full"
        logger.info(
            "Tool filtering: removed %d tools, %s enabled. Mode: %s",
            tools_removed,
            enabled_count,
            mode,
        )
