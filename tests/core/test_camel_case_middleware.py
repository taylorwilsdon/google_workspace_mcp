"""Tests for CamelCaseArgumentsMiddleware (issue #918)."""

import json
from typing import Optional

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from core.camel_case_middleware import CamelCaseArgumentsMiddleware, to_snake_case


def test_to_snake_case_conversions():
    assert to_snake_case("calendarId") == "calendar_id"
    assert to_snake_case("timeMin") == "time_min"
    assert to_snake_case("timeMax") == "time_max"
    assert to_snake_case("maxResults") == "max_results"
    assert to_snake_case("singleEvents") == "single_events"
    assert to_snake_case("orderBy") == "order_by"
    # Acronym runs split from the word that follows them
    assert to_snake_case("fooURLValue") == "foo_url_value"
    assert to_snake_case("iCalUID") == "i_cal_uid"
    assert to_snake_case("htmlURL") == "html_url"
    # Already snake_case or single-word names are untouched
    assert to_snake_case("calendar_id") == "calendar_id"
    assert to_snake_case("query") == "query"


def _build_server() -> FastMCP:
    server = FastMCP("camel-case-test")
    server.add_middleware(CamelCaseArgumentsMiddleware())

    @server.tool
    def get_events(
        calendar_id: str = "primary",
        time_min: Optional[str] = None,
        time_max: Optional[str] = None,
        max_results: int = 25,
    ) -> str:
        return json.dumps(
            {
                "calendar_id": calendar_id,
                "time_min": time_min,
                "time_max": time_max,
                "max_results": max_results,
            }
        )

    return server


@pytest.mark.asyncio
async def test_camel_case_arguments_are_accepted():
    """The exact failure mode from #918: camelCase keys on a snake_case tool."""
    server = _build_server()
    async with Client(server) as client:
        result = await client.call_tool(
            "get_events",
            {
                "calendarId": "primary",
                "timeMin": "2026-07-12T00:00:00Z",
                "timeMax": "2026-07-13T00:00:00Z",
                "maxResults": 10,
            },
        )
    payload = json.loads(result.content[0].text)
    assert payload == {
        "calendar_id": "primary",
        "time_min": "2026-07-12T00:00:00Z",
        "time_max": "2026-07-13T00:00:00Z",
        "max_results": 10,
    }


@pytest.mark.asyncio
async def test_snake_case_arguments_pass_through_unchanged():
    server = _build_server()
    async with Client(server) as client:
        result = await client.call_tool(
            "get_events",
            {"calendar_id": "team", "time_min": "2026-07-12T00:00:00Z"},
        )
    payload = json.loads(result.content[0].text)
    assert payload["calendar_id"] == "team"
    assert payload["time_min"] == "2026-07-12T00:00:00Z"


@pytest.mark.asyncio
async def test_explicit_snake_case_key_is_never_overridden():
    """If a caller sends both spellings, the snake_case value wins and the
    camelCase duplicate is still rejected by schema validation."""
    server = _build_server()
    async with Client(server) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "get_events",
                {"calendar_id": "team", "calendarId": "primary"},
            )


@pytest.mark.asyncio
async def test_unknown_arguments_are_still_rejected():
    """Arguments with no snake_case equivalent keep failing validation."""
    server = _build_server()
    async with Client(server) as client:
        with pytest.raises(Exception):
            await client.call_tool("get_events", {"notARealParam": "x"})


@pytest.mark.asyncio
async def test_declared_camel_case_parameter_is_untouched():
    """A tool that genuinely declares a camelCase parameter keeps working."""
    server = FastMCP("camel-case-edge")
    server.add_middleware(CamelCaseArgumentsMiddleware())

    @server.tool
    def echo(calendarId: str) -> str:  # noqa: N803 - intentional camelCase
        return calendarId

    async with Client(server) as client:
        result = await client.call_tool("echo", {"calendarId": "kept"})
    assert result.content[0].text == "kept"


@pytest.mark.asyncio
async def test_unknown_tool_error_still_propagates():
    server = _build_server()
    async with Client(server) as client:
        with pytest.raises(ToolError, match="Unknown tool: 'does_not_exist'"):
            await client.call_tool("does_not_exist", {"calendarId": "primary"})


@pytest.mark.asyncio
async def test_colliding_camel_case_keys_are_rejected():
    """Two spellings that normalize to the same parameter stay invalid rather
    than one silently overwriting the other."""
    server = FastMCP("camel-case-collision")
    server.add_middleware(CamelCaseArgumentsMiddleware())

    @server.tool
    def find_event(i_cal_uid: str = "") -> str:
        return i_cal_uid

    async with Client(server) as client:
        with pytest.raises(Exception):
            await client.call_tool("find_event", {"iCalUid": "a", "iCalUID": "b"})
        # A single unambiguous spelling still works
        result = await client.call_tool("find_event", {"iCalUid": "ok"})
    assert result.content[0].text == "ok"
