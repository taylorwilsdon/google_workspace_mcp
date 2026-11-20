"""Tests for the MCP server instructions (WORKSPACE_MCP_SERVER_INSTRUCTIONS)."""

import os
import subprocess
import sys

import pytest
from fastmcp import Client

from core.server import SecureFastMCP, build_server_instructions

CUSTOM = "This server is the user's Google Workspace. Search these tools first."

# The single-user text as it stood before WORKSPACE_MCP_SERVER_INSTRUCTIONS existed.
# Deployments that only set USER_GOOGLE_EMAIL must keep receiving it unchanged.
LEGACY_EMAIL_INSTRUCTIONS = """Connected Google account: user@example.com

When using Google Workspace tools, always use `user@example.com` as the `user_google_email` parameter. Do not ask the user for their email address."""


def test_no_instructions_when_nothing_is_configured():
    assert build_server_instructions(None, None, False) is None


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_custom_instructions_are_ignored(value):
    assert build_server_instructions(value, None, False) is None


def test_custom_instructions_are_used_and_stripped():
    assert build_server_instructions(f"  {CUSTOM}\n", None, False) == CUSTOM


def test_single_user_instructions_are_unchanged():
    assert (
        build_server_instructions(None, "user@example.com", False)
        == LEGACY_EMAIL_INSTRUCTIONS
    )


def test_custom_instructions_lead_the_single_user_context():
    assert (
        build_server_instructions(CUSTOM, "user@example.com", False)
        == f"{CUSTOM}\n\n{LEGACY_EMAIL_INSTRUCTIONS}"
    )


def test_trusted_gateway_keeps_custom_instructions_but_drops_email():
    assert build_server_instructions(CUSTOM, "user@example.com", True) == CUSTOM
    assert build_server_instructions(None, "user@example.com", True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_handshake_carries_the_instructions(mode):
    # "legacy" is the initialize handshake; "auto" negotiates server/discover.
    server = SecureFastMCP(
        name="test_server",
        instructions=build_server_instructions(CUSTOM, None, False),
    )

    async with Client(server, mode=mode) as client:
        assert client.instructions == CUSTOM


def test_env_var_reaches_the_shared_server_in_multi_user_mode():
    # core.server builds the shared server at import time, so check it in a fresh
    # interpreter. With MCP_ENABLE_OAUTH21=true, core.config ignores USER_GOOGLE_EMAIL,
    # which left these deployments with no instructions before the env var existed.
    env = {
        **os.environ,
        "MCP_ENABLE_OAUTH21": "true",
        "USER_GOOGLE_EMAIL": "user@example.com",
        "WORKSPACE_MCP_SERVER_INSTRUCTIONS": CUSTOM,
    }

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.server import server; print(server.instructions)",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == CUSTOM
