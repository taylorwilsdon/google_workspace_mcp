import pytest
from fastmcp import FastMCP

import core.tool_registry as tool_registry


def _set_mode_flags(monkeypatch, *, oauth21: bool, read_only: bool, permissions: bool):
    monkeypatch.setattr(tool_registry, "is_oauth21_enabled", lambda: oauth21)
    monkeypatch.setattr(tool_registry, "is_read_only_mode", lambda: read_only)
    monkeypatch.setattr(tool_registry, "is_permissions_mode", lambda: permissions)


@pytest.fixture(autouse=True)
def _reset_enabled_tools():
    previous = tool_registry.get_enabled_tools()
    tool_registry.set_enabled_tools(None)
    yield
    tool_registry.set_enabled_tools(previous)


def test_normalize_string_tool_output_schemas_is_targeted():
    server = FastMCP("test-tool-registry")

    @server.tool()
    def text_tool() -> str:
        return "hello"

    @server.tool()
    def int_tool() -> int:
        return 7

    @server.tool()
    def object_tool() -> dict[str, int]:
        return {"value": 1}

    tool_components = tool_registry.get_tool_components(server)
    assert tool_components["text_tool"].output_schema["x-fastmcp-wrap-result"] is True
    assert tool_components["int_tool"].output_schema["x-fastmcp-wrap-result"] is True
    assert "x-fastmcp-wrap-result" not in tool_components["object_tool"].output_schema

    cleared = tool_registry.normalize_string_tool_output_schemas(server)

    assert cleared == 1
    assert tool_components["text_tool"].output_schema is None
    assert tool_components["int_tool"].output_schema["x-fastmcp-wrap-result"] is True
    assert "x-fastmcp-wrap-result" not in tool_components["object_tool"].output_schema


@pytest.mark.asyncio
async def test_default_mode_pipeline_returns_clean_text_content(monkeypatch):
    _set_mode_flags(monkeypatch, oauth21=False, read_only=False, permissions=False)
    server = FastMCP("test-tool-registry-default")

    @server.tool()
    def text_tool() -> str:
        return "hello"

    # In default mode, filter_server_tools no-ops; normalization must still run.
    tool_registry.filter_server_tools(server)
    cleared = tool_registry.normalize_string_tool_output_schemas(server)
    result = await server.call_tool("text_tool", {})

    assert cleared == 1
    assert result.structured_content is None
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert result.content[0].text == "hello"


@pytest.mark.asyncio
async def test_oauth_mode_pipeline_keeps_filtering_and_returns_clean_text(monkeypatch):
    _set_mode_flags(monkeypatch, oauth21=True, read_only=False, permissions=False)
    server = FastMCP("test-tool-registry-oauth")

    @server.tool(name="start_google_auth")
    def legacy_auth_entrypoint() -> str:
        return "legacy"

    @server.tool()
    def text_tool() -> str:
        return "hello"

    tool_registry.filter_server_tools(server)
    cleared = tool_registry.normalize_string_tool_output_schemas(server)
    result = await server.call_tool("text_tool", {})
    remaining = tool_registry.get_tool_components(server)

    assert "start_google_auth" not in remaining
    assert cleared == 1
    assert result.structured_content is None
    assert result.content[0].text == "hello"
