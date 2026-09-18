"""
Guard test: every Sheets tool must be listed in core/tool_tiers.yaml.

Tools are registered with @server.tool, but a server started with --tool-tier
filters the registry down to the names listed in tool_tiers.yaml. A tool that
is registered but never tiered is silently unreachable for every tiered
deployment, with no error at startup. This test makes that failure loud.

The tool list is read from the module's AST rather than from the live server so
the result does not depend on which other tool modules a test session imported.
"""

import ast
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

REPO_ROOT = Path(__file__).resolve().parents[2]
SHEETS_TOOLS_PATH = REPO_ROOT / "gsheets" / "sheets_tools.py"
TOOL_TIERS_PATH = REPO_ROOT / "core" / "tool_tiers.yaml"


def _is_server_tool_decorator(decorator: ast.expr) -> bool:
    """Match @server.tool and @server.tool(...)."""
    node = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(node, ast.Attribute) and node.attr == "tool"


def registered_sheets_tool_names() -> set[str]:
    """Names of every function in sheets_tools.py decorated with @server.tool."""
    tree = ast.parse(SHEETS_TOOLS_PATH.read_text())

    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(_is_server_tool_decorator(d) for d in node.decorator_list)
    }


def tiered_sheets_tool_names() -> set[str]:
    """Every tool name listed under the `sheets` service in tool_tiers.yaml."""
    config = yaml.safe_load(TOOL_TIERS_PATH.read_text())
    sheets_config = config.get("sheets") or {}

    names: set[str] = set()
    for tier_tools in sheets_config.values():
        if tier_tools:
            names.update(tier_tools)
    return names


def test_ast_scan_finds_the_known_sheets_tools():
    """Sanity-check the scanner itself, so a silent zero-match cannot pass."""
    found = registered_sheets_tool_names()

    assert "read_sheet_values" in found
    assert "manage_conditional_formatting" in found
    assert len(found) > 5


def test_every_sheets_tool_is_assigned_to_a_tier():
    """
    A registered-but-untiered tool never reaches a --tool-tier deployment.
    """
    registered = registered_sheets_tool_names()
    tiered = tiered_sheets_tool_names()

    untiered = registered - tiered
    assert not untiered, (
        f"these Sheets tools are registered but missing from {TOOL_TIERS_PATH.name}, "
        f"so --tool-tier deployments cannot see them: {sorted(untiered)}"
    )


def test_tier_entries_all_refer_to_real_tools():
    """
    Catch the reverse drift: a tier entry naming a tool that no longer exists.
    Comment tools are attached separately by create_comment_tools.
    """
    registered = registered_sheets_tool_names()
    tiered = tiered_sheets_tool_names()
    dynamically_registered = {"list_spreadsheet_comments", "manage_spreadsheet_comment"}

    dangling = tiered - registered - dynamically_registered
    assert not dangling, (
        f"tool_tiers.yaml lists Sheets tools that do not exist: {sorted(dangling)}"
    )


@pytest.mark.parametrize("tool_name", ["manage_sheet_table"])
def test_specific_tools_are_tiered(tool_name):
    """Explicit pin for tools whose tier entry is easy to forget."""
    assert tool_name in registered_sheets_tool_names()
    assert tool_name in tiered_sheets_tool_names()
