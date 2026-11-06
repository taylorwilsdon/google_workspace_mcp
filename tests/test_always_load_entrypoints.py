"""WORKSPACE_MCP_ALWAYS_LOAD_TOOLS reaches tools/list through both entrypoints.

Each case runs in a fresh interpreter because the shared server is built and its
tools registered at import time. Listing through a Client exercises the full
SecureFastMCP stack, including the middleware that rewrites tool schemas.
"""

import json
import os
import subprocess
import sys

import pytest

_LIST_MARKED = """
import asyncio, json
from fastmcp import Client

async def list_marked(server):
    async with Client(server) as client:
        tools = await client.list_tools()
    return sorted(
        tool.name
        for tool in tools
        if (tool.meta or {}).get("anthropic/alwaysLoad") is True
    )
"""

_FASTMCP_SERVER = (
    _LIST_MARKED
    + """
import fastmcp_server
print(json.dumps(asyncio.run(list_marked(fastmcp_server.server))))
"""
)

# main.main() marks tools right before server.run(), so stand in for run().
_MAIN = (
    _LIST_MARKED
    + """
import sys
import main

def fake_run(*args, **kwargs):
    print(json.dumps(asyncio.run(list_marked(main.server))))
    raise SystemExit(0)

main.server.run = fake_run
sys.argv = ["main.py", "--tools", "gmail"]
main.main()
"""
)


@pytest.mark.parametrize(
    "script", [_FASTMCP_SERVER, _MAIN], ids=["fastmcp_server", "main"]
)
def test_listed_tool_is_marked_through_the_entrypoint(tmp_path, script):
    env = {
        **os.environ,
        "WORKSPACE_MCP_ALWAYS_LOAD_TOOLS": "Search_Gmail_Messages",
        # An inherited block list would filter the listed tool out.
        "WORKSPACE_MCP_DISABLED_TOOLS": "",
        "GOOGLE_OAUTH_CLIENT_ID": "test-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "test-client-secret",
        "MCP_ENABLE_OAUTH21": "false",
        "WORKSPACE_MCP_STATELESS_MODE": "false",
        "WORKSPACE_MCP_LOG_DIR": str(tmp_path / "logs"),
        "WORKSPACE_MCP_CREDENTIALS_DIR": str(tmp_path / "credentials"),
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == [
        "search_gmail_messages"
    ]
