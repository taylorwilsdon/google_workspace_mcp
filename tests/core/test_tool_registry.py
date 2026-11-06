"""Tests for ``core.tool_registry`` block-list resolution and tool filtering."""

import logging
from collections.abc import Iterable, Iterator
from types import SimpleNamespace

import pytest
from fastmcp import Client, FastMCP

from core import tool_registry
from core.tool_registry import (
    ALWAYS_LOAD_META_KEY,
    filter_server_tools,
    get_tool_components,
    mark_always_load_tools,
    resolve_always_load_tools,
    resolve_disabled_tools,
    set_disabled_tools,
    set_enabled_tools,
)

ENV_VAR = "WORKSPACE_MCP_DISABLED_TOOLS"
ALWAYS_LOAD_ENV_VAR = "WORKSPACE_MCP_ALWAYS_LOAD_TOOLS"


class _FakeLocalProvider:
    """Minimal stand-in for the FastMCP local provider component registry."""

    def __init__(self, tool_names: Iterable[str]) -> None:
        self._components = {f"tool:{name}@1": SimpleNamespace() for name in tool_names}
        self.removed: list[str] = []

    def remove_tool(self, tool_name: str) -> None:
        del self._components[f"tool:{tool_name}@1"]
        self.removed.append(tool_name)


def _fake_server(*tool_names: str) -> SimpleNamespace:
    return SimpleNamespace(local_provider=_FakeLocalProvider(tool_names))


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Reset the module globals and neutralize the other filtering modes."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv(ALWAYS_LOAD_ENV_VAR, raising=False)
    monkeypatch.setattr(tool_registry, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: False)
    monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: False)
    set_enabled_tools(None)
    set_disabled_tools(set())
    yield
    set_enabled_tools(None)
    set_disabled_tools(set())


def test_resolve_disabled_tools_parses_and_normalizes_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, " Send_Gmail_Message , delete_drive_file ,, ")

    assert resolve_disabled_tools() == {"send_gmail_message", "delete_drive_file"}


@pytest.mark.parametrize("value", ["", "   ", ",", " , "])
def test_resolve_disabled_tools_returns_empty_set_for_blank_env_var(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ENV_VAR, value)

    assert resolve_disabled_tools() == set()


def test_resolve_disabled_tools_returns_empty_set_when_unset() -> None:
    assert resolve_disabled_tools() == set()


def test_resolve_disabled_tools_prefers_cli_names_over_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_VAR, "send_gmail_message")

    assert resolve_disabled_tools([" Search_Gmail_Messages "]) == {
        "search_gmail_messages"
    }


def test_block_list_alone_triggers_filtering() -> None:
    server = _fake_server("search_gmail_messages", "send_gmail_message")
    set_disabled_tools({"send_gmail_message"})

    assert filter_server_tools(server) == 1
    assert server.local_provider.removed == ["send_gmail_message"]


def test_unmatched_block_list_entry_warns_without_removing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = _fake_server("search_gmail_messages")
    set_disabled_tools({"send_gmail_mesage"})

    with caplog.at_level(logging.WARNING, logger="core.tool_registry"):
        assert filter_server_tools(server) == 0

    assert server.local_provider.removed == []
    assert "send_gmail_mesage" in caplog.text


def test_block_list_overrides_tier_selection() -> None:
    server = _fake_server("search_gmail_messages", "send_gmail_message")
    set_enabled_tools({"search_gmail_messages", "send_gmail_message"})
    set_disabled_tools({"send_gmail_message"})

    assert filter_server_tools(server) == 1
    assert server.local_provider.removed == ["send_gmail_message"]
    assert "tool:search_gmail_messages@1" in server.local_provider._components


def test_no_filtering_when_nothing_is_configured() -> None:
    server = _fake_server("search_gmail_messages")

    assert filter_server_tools(server) == 0
    assert server.local_provider.removed == []


def _real_server(*tool_names: str) -> FastMCP:
    """Build a FastMCP server with one no-op tool per name."""
    server = FastMCP(name="test_server")
    for name in tool_names:

        def tool_fn() -> str:
            return "ok"

        server.tool(name=name)(tool_fn)
    return server


def test_resolve_always_load_tools_parses_and_normalizes_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ALWAYS_LOAD_ENV_VAR, " Get_Events , search_gmail_messages ,, ")

    assert resolve_always_load_tools() == {"get_events", "search_gmail_messages"}


@pytest.mark.parametrize("value", ["", "   ", ",", " , "])
def test_resolve_always_load_tools_returns_empty_set_for_blank_env_var(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(ALWAYS_LOAD_ENV_VAR, value)

    assert resolve_always_load_tools() == set()


def test_resolve_always_load_tools_returns_empty_set_when_unset() -> None:
    assert resolve_always_load_tools() == set()


@pytest.mark.asyncio
async def test_always_load_meta_is_listed_on_marked_tools_only() -> None:
    server = _real_server("get_events", "search_gmail_messages", "send_gmail_message")

    assert mark_always_load_tools(server, {"get_events", "search_gmail_messages"}) == 2

    async with Client(server) as client:
        listed = {tool.name: tool.meta for tool in await client.list_tools()}
    assert listed["get_events"][ALWAYS_LOAD_META_KEY] is True
    assert listed["search_gmail_messages"][ALWAYS_LOAD_META_KEY] is True
    assert ALWAYS_LOAD_META_KEY not in listed["send_gmail_message"]
    # FastMCP's own metadata is still sent alongside the new key.
    assert "fastmcp" in listed["get_events"]


def test_mark_always_load_tools_keeps_existing_meta() -> None:
    server = FastMCP(name="test_server")

    def get_events() -> str:
        return "ok"

    server.tool(meta={"example/key": "kept"})(get_events)

    assert mark_always_load_tools(server, {"get_events"}) == 1
    assert get_tool_components(server)["get_events"].meta == {
        "example/key": "kept",
        ALWAYS_LOAD_META_KEY: True,
    }


def test_always_load_entry_for_a_filtered_tool_warns_without_marking(
    caplog: pytest.LogCaptureFixture,
) -> None:
    server = _real_server("search_gmail_messages", "send_gmail_message")
    set_disabled_tools({"send_gmail_message"})
    filter_server_tools(server)

    with caplog.at_level(logging.WARNING, logger="core.tool_registry"):
        assert mark_always_load_tools(server, {"send_gmail_message"}) == 0

    assert "send_gmail_message" in caplog.text
    assert set(get_tool_components(server)) == {"search_gmail_messages"}
