"""
CLI Handler for Google Workspace MCP

This module provides a command-line interface mode for directly invoking
MCP tools without running the full server. Designed for use by coding agents
(Codex, Claude Code) and command-line users.

Usage:
    workspace-mcp --cli                     # List available tools
    workspace-mcp --cli list                # List available tools
    workspace-mcp --cli <tool_name>         # Run tool (reads JSON args from stdin)
    workspace-mcp --cli <tool_name> --args '{"key": "value"}'  # Run with inline args
    workspace-mcp --cli <tool_name> --help  # Show tool details
"""

import asyncio
import json
import logging
import sys
from typing import Any, Dict, List, Optional

from auth.oauth_config import set_transport_mode

logger = logging.getLogger(__name__)


def get_registered_tools(server) -> Dict[str, Any]:
    """
    Get all registered tools from the FastMCP server.

    Args:
        server: The FastMCP server instance

    Returns:
        Dictionary mapping tool names to their metadata
    """
    tools = {}

    if hasattr(server, "_tool_manager") and hasattr(server._tool_manager, "_tools"):
        tool_registry = server._tool_manager._tools
        for name, tool in tool_registry.items():
            tools[name] = {
                "name": name,
                "description": getattr(tool, "description", None)
                or _extract_docstring(tool),
                "parameters": _extract_parameters(tool),
                "tool_obj": tool,
            }

    return tools


def _extract_docstring(tool) -> Optional[str]:
    """Extract the first meaningful line of a tool's docstring as its description."""
    fn = getattr(tool, "fn", None) or tool
    if fn and fn.__doc__:
        # Get first non-empty line that's not just "Args:" etc.
        for line in fn.__doc__.strip().split("\n"):
            line = line.strip()
            # Skip empty lines and common section headers
            if line and not line.startswith(
                ("Args:", "Returns:", "Raises:", "Example", "Note:")
            ):
                return line
    return None


def _extract_parameters(tool) -> Dict[str, Any]:
    """Extract parameter information from a tool."""
    params = {}

    # Try to get parameters from the tool's schema
    if hasattr(tool, "parameters"):
        schema = tool.parameters
        if isinstance(schema, dict):
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            for name, prop in props.items():
                params[name] = {
                    "type": prop.get("type", "any"),
                    "description": prop.get("description", ""),
                    "required": name in required,
                    "default": prop.get("default"),
                }

    return params


def list_tools(server, output_format: str = "text") -> str:
    """
    List all available tools.

    Args:
        server: The FastMCP server instance
        output_format: Output format ("text" or "json")

    Returns:
        Formatted string listing all tools
    """
    tools = get_registered_tools(server)

    if output_format == "json":
        # Return JSON format for programmatic use
        tool_list = []
        for name, info in sorted(tools.items()):
            tool_list.append(
                {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["parameters"],
                }
            )
        return json.dumps({"tools": tool_list}, indent=2)

    # Text format for human reading
    lines = [
        f"Available tools ({len(tools)}):",
        "",
    ]

    # Group tools by service
    services = {}
    for name, info in tools.items():
        # Extract service prefix from tool name
        prefix = name.split("_")[0] if "_" in name else "other"
        if prefix not in services:
            services[prefix] = []
        services[prefix].append((name, info))

    for service in sorted(services.keys()):
        lines.append(f"  {service.upper()}:")
        for name, info in sorted(services[service]):
            desc = info["description"] or "(no description)"
            # Get first line only and truncate
            first_line = desc.split("\n")[0].strip()
            if len(first_line) > 70:
                first_line = first_line[:67] + "..."
            lines.append(f"    {name}")
            lines.append(f"      {first_line}")
        lines.append("")

    lines.append("Use --cli <tool_name> --help for detailed tool information")
    lines.append("Use --cli <tool_name> --args '{...}' to run a tool")

    return "\n".join(lines)


def show_tool_help(server, tool_name: str) -> str:
    """
    Show detailed help for a specific tool.

    Args:
        server: The FastMCP server instance
        tool_name: Name of the tool

    Returns:
        Formatted help string for the tool
    """
    tools = get_registered_tools(server)

    if tool_name not in tools:
        available = ", ".join(sorted(tools.keys())[:10])
        return f"Error: Tool '{tool_name}' not found.\n\nAvailable tools include: {available}..."

    tool_info = tools[tool_name]
    tool_obj = tool_info["tool_obj"]

    # Get full docstring
    fn = getattr(tool_obj, "fn", None) or tool_obj
    docstring = fn.__doc__ if fn and fn.__doc__ else "(no documentation)"

    lines = [
        f"Tool: {tool_name}",
        "=" * (len(tool_name) + 6),
        "",
        docstring,
        "",
        "Parameters:",
    ]

    params = tool_info["parameters"]
    if params:
        for name, param_info in params.items():
            req = "(required)" if param_info.get("required") else "(optional)"
            param_type = param_info.get("type", "any")
            desc = param_info.get("description", "")
            default = param_info.get("default")

            lines.append(f"  {name}: {param_type} {req}")
            if desc:
                lines.append(f"    {desc}")
            if default is not None:
                lines.append(f"    Default: {default}")
    else:
        lines.append("  (no parameters)")

    lines.extend(
        [
            "",
            "Example usage:",
            f'  workspace-mcp --cli {tool_name} --args \'{{"param": "value"}}\'',
            "",
            "Or pipe JSON from stdin:",
            f'  echo \'{{"param": "value"}}\' | workspace-mcp --cli {tool_name}',
        ]
    )

    return "\n".join(lines)


async def run_tool(server, tool_name: str, args: Dict[str, Any]) -> str:
    """
    Execute a tool with the provided arguments.

    Args:
        server: The FastMCP server instance
        tool_name: Name of the tool to execute
        args: Dictionary of arguments to pass to the tool

    Returns:
        Tool result as a string
    """
    tools = get_registered_tools(server)

    if tool_name not in tools:
        raise ValueError(f"Tool '{tool_name}' not found")

    tool_info = tools[tool_name]
    tool_obj = tool_info["tool_obj"]

    # Get the actual function to call
    fn = getattr(tool_obj, "fn", None)
    if fn is None:
        raise ValueError(f"Tool '{tool_name}' has no callable function")

    call_args = dict(args)

    try:
        logger.debug(
            f"[CLI] Executing tool: {tool_name} with args: {list(call_args.keys())}"
        )

        # Call the tool function
        if asyncio.iscoroutinefunction(fn):
            result = await fn(**call_args)
        else:
            result = fn(**call_args)

        # Convert result to string if needed
        if isinstance(result, str):
            return result
        else:
            return json.dumps(result, indent=2, default=str)

    except TypeError as e:
        # Provide helpful error for missing/invalid arguments
        error_msg = str(e)
        params = tool_info["parameters"]
        required = [n for n, p in params.items() if p.get("required")]

        return (
            f"Error calling {tool_name}: {error_msg}\n\n"
            f"Required parameters: {required}\n"
            f"Provided parameters: {list(call_args.keys())}"
        )
    except Exception as e:
        logger.error(f"[CLI] Error executing {tool_name}: {e}", exc_info=True)
        return f"Error: {type(e).__name__}: {e}"


def parse_cli_args(args: List[str]) -> Dict[str, Any]:
    """
    Parse CLI arguments for tool execution.

    Args:
        args: List of arguments after --cli

    Returns:
        Dictionary with parsed values:
            - command: "list", "help", or "run"
            - tool_name: Name of tool (if applicable)
            - tool_args: Arguments for the tool (if applicable)
            - output_format: "text" or "json"
    """
    result = {
        "command": "list",
        "tool_name": None,
        "tool_args": {},
        "output_format": "text",
    }

    if not args:
        return result

    i = 0
    while i < len(args):
        arg = args[i]

        if arg in ("list", "-l", "--list"):
            result["command"] = "list"
            i += 1
        elif arg in ("--json", "-j"):
            result["output_format"] = "json"
            i += 1
        elif arg in ("help", "--help", "-h"):
            # Help command - if tool_name already set, show help for that tool
            if result["tool_name"]:
                result["command"] = "help"
            else:
                # Check if next arg is a tool name
                if i + 1 < len(args) and not args[i + 1].startswith("-"):
                    result["tool_name"] = args[i + 1]
                    result["command"] = "help"
                    i += 1
                else:
                    # No tool specified, show general help
                    result["command"] = "list"
            i += 1
        elif arg in ("--args", "-a") and i + 1 < len(args):
            # Parse inline JSON arguments
            json_str = args[i + 1]
            try:
                result["tool_args"] = json.loads(json_str)
            except json.JSONDecodeError as e:
                # Provide helpful debug info
                raise ValueError(
                    f"Invalid JSON in --args: {e}\n"
                    f"Received: {repr(json_str)}\n"
                    f"Tip: Try using stdin instead: echo '<json>' | workspace-mcp --cli <tool>"
                )
            i += 2
        elif not arg.startswith("-") and not result["tool_name"]:
            # First non-flag argument is the tool name
            result["tool_name"] = arg
            result["command"] = "run"
            i += 1
        else:
            i += 1

    return result


def read_stdin_args() -> Dict[str, Any]:
    """
    Read JSON arguments from stdin if available.

    Returns:
        Dictionary of arguments or empty dict if stdin is a TTY or no data is provided.
    """
    if sys.stdin.isatty():
        logger.debug("[CLI] stdin is a TTY; no JSON args will be read from stdin")
        return {}

    try:
        stdin_data = sys.stdin.read().strip()
        if stdin_data:
            return json.loads(stdin_data)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from stdin: {e}")

    return {}


async def handle_cli_mode(server, cli_args: List[str]) -> int:
    """
    Main entry point for CLI mode.

    Args:
        server: The FastMCP server instance
        cli_args: Arguments passed after --cli

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Set transport mode to "stdio" so OAuth callback server starts when needed
    # This is required for authentication flow when no cached credentials exist
    set_transport_mode("stdio")

    try:
        parsed = parse_cli_args(cli_args)

        if parsed["command"] == "list":
            output = list_tools(server, parsed["output_format"])
            print(output)
            return 0

        if parsed["command"] == "help":
            output = show_tool_help(server, parsed["tool_name"])
            print(output)
            return 0

        if parsed["command"] == "run":
            # Merge stdin args with inline args (inline takes precedence)
            args = read_stdin_args()
            args.update(parsed["tool_args"])

            result = await run_tool(server, parsed["tool_name"], args)
            print(result)
            return 0

        # Unknown command
        print(f"Unknown command: {parsed['command']}")
        return 1

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        logger.error(f"[CLI] Unexpected error: {e}", exc_info=True)
        print(f"Error: {e}", file=sys.stderr)
        return 1
