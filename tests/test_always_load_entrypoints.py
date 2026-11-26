"""WORKSPACE_MCP_ALWAYS_LOAD_TOOLS reaches tools/list through both entrypoints."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_LIST_MARKED = """
import asyncio, json
import dotenv
from fastmcp import Client

dotenv.load_dotenv = lambda *args, **kwargs: False

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

_SERVER_ENV_PREFIXES = (
    "WORKSPACE_",
    "GOOGLE_",
    "MCP_",
    "OAUTH",
    "FASTMCP_",
    "TRUST_GATEWAY_",
    "GATEWAY_IDENTITY_",
    "EXTERNAL_OAUTH21_",
    "USER_GOOGLE_EMAIL",
)


@pytest.mark.parametrize(
    "script", [_FASTMCP_SERVER, _MAIN], ids=["fastmcp_server", "main"]
)
def test_listed_tool_is_marked_through_the_entrypoint(
    tmp_path: Path, script: str
) -> None:
    # The shared server registers its tools at import time, so use a fresh interpreter.
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_SERVER_ENV_PREFIXES)
    }
    env = {
        **inherited,
        "WORKSPACE_MCP_ALWAYS_LOAD_TOOLS": "Search_Gmail_Messages",
        "WORKSPACE_MCP_DISABLED_TOOLS": "",
        "TRUST_GATEWAY_IDENTITY": "false",
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
