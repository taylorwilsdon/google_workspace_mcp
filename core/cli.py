"""
Persistent CLI wrapper for interacting with a running Workspace MCP server.

Reuses the project's existing FileTreeStore and FernetEncryptionWrapper to
cache OAuth tokens on disk so that ``fastmcp list/call`` does not re-trigger
the full browser-based OAuth flow on every invocation.

Usage::

    uv run workspace-cli list
    uv run workspace-cli call search_gmail_messages query="is:unread" max_results=5
"""

import argparse
import asyncio
import json
import logging
import os
import stat
import sys
from typing import Any

from cryptography.fernet import Fernet
from fastmcp import Client
from fastmcp.client.auth import OAuth
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from core.storage import make_sanitized_file_store

logger = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:8000/mcp"
CLI_HOME = os.path.expanduser("~/.workspace-mcp")
TOKEN_DIR = os.path.join(CLI_HOME, "cli-tokens")
KEY_PATH = os.path.join(CLI_HOME, ".cli-encryption-key")


def _get_token_storage() -> FernetEncryptionWrapper:
    """Return an encrypted, disk-backed token store.

    On first run the directory tree and a random Fernet key are created.
    The key file is restricted to owner-only access (0o600).
    """
    os.makedirs(TOKEN_DIR, exist_ok=True)

    try:
        fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with open(KEY_PATH, "rb") as fh:
            key = fh.read()
    else:
        key = Fernet.generate_key()
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
        os.chmod(KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)

    return FernetEncryptionWrapper(
        key_value=make_sanitized_file_store(TOKEN_DIR),
        fernet=Fernet(key),
    )


def _build_oauth() -> OAuth:
    """Build an OAuth helper with persistent encrypted token storage."""
    storage = _get_token_storage()
    return OAuth(token_storage=storage)


async def _list_tools(url: str) -> None:
    """Connect, authenticate once, and print available tools."""
    try:
        auth = _build_oauth()
    except Exception as e:
        print(
            f"Error: failed to initialize OAuth ({type(e).__name__})", file=sys.stderr
        )
        sys.exit(1)
    try:
        async with Client(url, auth=auth) as client:
            tools = await client.list_tools()
    except Exception as e:
        print(f"Error: failed to list tools ({type(e).__name__})", file=sys.stderr)
        sys.exit(1)
    for tool in tools:
        desc = (tool.description or "").split("\n")[0]
        print(f"  {tool.name:40s} {desc}")
    print(f"\n{len(tools)} tools available")


async def _call_tool(url: str, tool_name: str, raw_args: list[str]) -> None:
    """Connect, authenticate once, call a single tool, and print the result."""
    kwargs: dict[str, Any] = {}
    for arg in raw_args:
        if "=" not in arg:
            print(f"Error: argument '{arg}' must be in key=value form", file=sys.stderr)
            sys.exit(1)
        k, v = arg.split("=", 1)
        try:
            kwargs[k] = json.loads(v)
        except json.JSONDecodeError:
            kwargs[k] = v

    async with Client(url, auth=_build_oauth()) as client:
        result = await client.call_tool(tool_name, kwargs)
        for block in result.content:
            if hasattr(block, "text"):
                try:
                    parsed = json.loads(block.text)
                    print(json.dumps(parsed, indent=2))
                except (json.JSONDecodeError, TypeError):
                    print(block.text)
            else:
                print(block)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="workspace-cli",
        description="CLI for Workspace MCP with persistent OAuth token caching",
    )
    parser.add_argument(
        "--url",
        default=os.getenv("WORKSPACE_MCP_URL", DEFAULT_URL),
        help=f"MCP server URL (default: {DEFAULT_URL})",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List available tools")

    call_parser = sub.add_parser("call", help="Call a tool")
    call_parser.add_argument("tool", help="Tool name")
    call_parser.add_argument("args", nargs="*", help="key=value arguments")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "list":
        asyncio.run(_list_tools(args.url))
    elif args.command == "call":
        asyncio.run(_call_tool(args.url, args.tool, args.args))


if __name__ == "__main__":
    main()
