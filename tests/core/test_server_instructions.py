"""Tests for the MCP server instructions (WORKSPACE_MCP_SERVER_INSTRUCTIONS)."""

import os
import subprocess
import sys
from collections.abc import Callable
from typing import Optional

import pytest
from fastmcp import Client

import core.server
from core.server import SecureFastMCP, get_server_instructions

_ENV = "WORKSPACE_MCP_SERVER_INSTRUCTIONS"
CUSTOM = "This server is the user's Google Workspace. Search these tools first."
EMAIL_INSTRUCTIONS = """Connected Google account: user@example.com

When using Google Workspace tools, always use `user@example.com` as the `user_google_email` parameter. Do not ask the user for their email address."""


@pytest.fixture
def configure(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def _configure(
        custom: Optional[str] = None,
        email: Optional[str] = None,
        trust_gateway: bool = False,
    ) -> None:
        if custom is None:
            monkeypatch.delenv(_ENV, raising=False)
        else:
            monkeypatch.setenv(_ENV, custom)
        monkeypatch.setattr(core.server, "USER_GOOGLE_EMAIL", email)
        monkeypatch.setattr(
            core.server, "is_trust_gateway_identity", lambda: trust_gateway
        )

    return _configure


def test_no_instructions_when_nothing_is_configured(
    configure: Callable[..., None],
) -> None:
    configure()
    assert get_server_instructions() is None


@pytest.mark.parametrize("value", ["", "   ", "\n\t"])
def test_blank_custom_instructions_are_ignored(
    configure: Callable[..., None], value: str
) -> None:
    configure(custom=value)
    assert get_server_instructions() is None


def test_custom_instructions_are_used_and_stripped(
    configure: Callable[..., None],
) -> None:
    configure(custom=f"  {CUSTOM}\n")
    assert get_server_instructions() == CUSTOM


def test_single_user_instructions_are_unchanged(
    configure: Callable[..., None],
) -> None:
    configure(email="user@example.com")
    assert get_server_instructions() == EMAIL_INSTRUCTIONS


def test_custom_instructions_lead_the_single_user_context(
    configure: Callable[..., None],
) -> None:
    configure(custom=CUSTOM, email="user@example.com")
    assert get_server_instructions() == f"{CUSTOM}\n\n{EMAIL_INSTRUCTIONS}"


@pytest.mark.parametrize("custom, expected", [(CUSTOM, CUSTOM), (None, None)])
def test_trusted_gateway_drops_email(
    configure: Callable[..., None], custom: Optional[str], expected: Optional[str]
) -> None:
    configure(custom=custom, email="user@example.com", trust_gateway=True)
    assert get_server_instructions() == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["legacy", "auto"])
async def test_handshake_carries_the_instructions(mode: str) -> None:
    # "legacy" is the initialize handshake; "auto" negotiates server/discover.
    server = SecureFastMCP(name="test_server", instructions=CUSTOM)

    async with Client(server, mode=mode) as client:
        assert client.instructions == CUSTOM


def test_env_var_reaches_the_shared_server_in_multi_user_mode() -> None:
    # core.server builds the shared server at import time, so check a fresh interpreter.
    inherited = {
        key: value
        for key, value in os.environ.items()
        if key
        not in ("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "GOOGLE_SERVICE_ACCOUNT_KEY_JSON")
    }
    env = {
        **inherited,
        "TRUST_GATEWAY_IDENTITY": "false",
        "MCP_ENABLE_OAUTH21": "true",
        "USER_GOOGLE_EMAIL": "user@example.com",
        _ENV: CUSTOM,
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
