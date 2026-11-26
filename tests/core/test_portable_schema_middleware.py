"""Tests for PortableSchemaMiddleware (issue #1099)."""

import json
from typing import Literal, Optional

import pytest
from fastmcp import Client, FastMCP
from pydantic import TypeAdapter, ValidationError

# Importing the tool modules registers their tools on the shared server.
import gappsscript.apps_script_tools  # noqa: F401
import gcalendar.calendar_tools  # noqa: F401
import gchat.chat_tools  # noqa: F401
import gcontacts.contacts_tools  # noqa: F401
import gdocs.docs_tools  # noqa: F401
import gdrive.drive_tools  # noqa: F401
import gforms.forms_tools  # noqa: F401
import gmail.gmail_tools  # noqa: F401
import gsearch.search_tools  # noqa: F401
import gsheets.sheets_tools  # noqa: F401
import gslides.slides_tools  # noqa: F401
import gtasks.tasks_tools  # noqa: F401
from core.portable_schema_middleware import PortableSchemaMiddleware, portable_schema
from core.server import server


def _unportable_keywords(node, path="$"):
    """Yield the path of every null union, list-valued type, or const."""
    if isinstance(node, dict):
        if "const" in node:
            yield f"{path}.const"
        if isinstance(node.get("type"), list):
            yield f"{path}.type"
        for key in ("anyOf", "oneOf"):
            if {"type": "null"} in node.get(key, ()):
                yield f"{path}.{key}"
        for key, value in node.items():
            yield from _unportable_keywords(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _unportable_keywords(value, f"{path}[{index}]")


def test_optional_collapses_to_concrete_type():
    schema = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "description": "Page token.",
    }
    assert portable_schema(schema) == {
        "type": "string",
        "default": None,
        "description": "Page token.",
    }


def test_optional_literal_becomes_enum():
    schema = {
        "anyOf": [{"const": "image", "type": "string"}, {"type": "null"}],
        "default": None,
    }
    assert portable_schema(schema) == {
        "type": "string",
        "enum": ["image"],
        "default": None,
    }


def test_multi_variant_union_keeps_non_null_branches():
    schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "array", "items": {"type": "string"}},
            {"type": "null"},
        ]
    }
    assert portable_schema(schema) == {
        "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}]
    }


def test_list_valued_type_drops_null():
    assert portable_schema({"type": ["integer", "null"]}) == {"type": "integer"}


def test_nested_definitions_are_rewritten_without_mutating_input():
    schema = {
        "type": "object",
        "properties": {"ops": {"type": "array", "items": {"$ref": "#/$defs/Op"}}},
        "$defs": {
            "Op": {
                "type": "object",
                "properties": {
                    "type": {"const": "insert_text", "type": "string"},
                    "tab_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            }
        },
    }
    original = json.loads(json.dumps(schema))

    result = portable_schema(schema)

    assert result["$defs"]["Op"]["properties"] == {
        "type": {"type": "string", "enum": ["insert_text"]},
        "tab_id": {"type": "string"},
    }
    assert schema == original


def test_parameter_names_that_look_like_keywords_are_untouched():
    schema = {"type": "object", "properties": {"const": {"type": "string"}}}
    assert portable_schema(schema) == schema


@pytest.mark.asyncio
async def test_listed_schema_is_portable_and_optional_args_still_work():
    server = FastMCP("portable-schema-test")
    server.add_middleware(PortableSchemaMiddleware())

    @server.tool
    def search(
        query: str,
        page_token: Optional[str] = None,
        search_type: Optional[Literal["image"]] = None,
    ) -> str:
        return json.dumps([query, page_token, search_type])

    async with Client(server) as client:
        (tool,) = await client.list_tools()
        omitted = await client.call_tool("search", {"query": "q"})
        explicit_null = await client.call_tool(
            "search", {"query": "q", "page_token": None}
        )

    properties = tool.input_schema["properties"]
    assert properties["page_token"] == {"type": "string", "default": None}
    assert properties["search_type"] == {
        "type": "string",
        "enum": ["image"],
        "default": None,
    }
    assert tool.input_schema["required"] == ["query"]
    assert json.loads(omitted.content[0].text) == ["q", None, None]
    assert json.loads(explicit_null.content[0].text) == ["q", None, None]


@pytest.mark.asyncio
async def test_registered_catalog_has_no_unportable_keywords():
    offenders = {
        tool.name: next(_unportable_keywords(tool.parameters), None)
        for tool in await server.list_tools()
    }

    assert {name: path for name, path in offenders.items() if path} == {}


@pytest.mark.asyncio
async def test_registered_null_default_args_accept_explicit_null():
    rejected = []
    for tool in await server.list_tools():
        # Its default is USER_GOOGLE_EMAIL, which is None only when that is unset.
        if tool.name == "start_google_auth":
            continue
        for name, prop in tool.parameters.get("properties", {}).items():
            if "default" not in prop or prop["default"] is not None:
                continue
            try:
                TypeAdapter(tool.fn.__annotations__[name]).validate_python(None)
            except ValidationError:
                rejected.append(f"{tool.name}.{name}")
    assert rejected == []
