"""Keep the bundled Claude skill in step with the registered toolset.

Every tool the server registers must be routed in SKILL.md and documented, with
its full parameter list, in a ``### <tool>`` section under references/. Adding,
renaming, or removing a tool or parameter fails here until the skill follows.
"""

import asyncio
import re
from pathlib import Path

import pytest

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
from core.server import server
from core.tool_tier_loader import get_tools_for_tier

SKILL_DIR = Path(__file__).parent.parent / "skills" / "managing-google-workspace"

# Advertised only in some server modes: user_google_email is dropped under
# OAuth 2.1, and core.utils.hide_local_file_args / hide_remote_only_args swap
# file_path for return_upload_url when local file access is off. The skill
# documents both modes, so these may appear in a table without being live.
MODE_DEPENDENT_PARAMS = {"user_google_email", "file_path", "return_upload_url"}

TOOL_HEADING = re.compile(r"### `?([a-z][a-z0-9_]*)`?")
HEADING_SPLIT = re.compile(r"^(#{1,6} .*)$", re.M)
TABLE = re.compile(r"(?:^\|.*\n?)+", re.M)
BACKTICKED = re.compile(r"`([a-z][a-z0-9_]*)`")
PARENTHESIZED = re.compile(r"\(.*?\)")


def registered_tools():
    """Map each registered tool to its advertised parameter names.

    Runs on a private loop because asyncio.run would clear the thread's current
    loop, breaking later tests that call asyncio.get_event_loop().
    """
    loop = asyncio.new_event_loop()
    try:
        tools = loop.run_until_complete(server.list_tools())
    finally:
        loop.close()
    return {tool.name: set(tool.parameters.get("properties", {})) for tool in tools}


def routed_tools():
    """Tool names in the last column of SKILL.md's routing tables.

    Parameter hints are parenthesized, e.g. `modify_sheet_values` (`chips`).
    """
    cells = (
        row.rstrip(" |").rsplit("|", 1)[-1]
        for row in (SKILL_DIR / "SKILL.md").read_text().splitlines()
        if row.startswith("|")
    )
    return {
        name
        for cell in cells
        for name in BACKTICKED.findall(PARENTHESIZED.sub("", cell))
    }


def documented_tools():
    """Map each ``### <tool>`` section to (file, params in its first table)."""
    sections = {}
    for path in sorted((SKILL_DIR / "references").glob("*.md")):
        chunks = HEADING_SPLIT.split(path.read_text())
        for heading, body in zip(chunks[1::2], chunks[2::2]):
            match = TOOL_HEADING.fullmatch(heading.strip())
            if not match:
                continue
            table = TABLE.search(body)
            rows = table[0].splitlines()[2:] if table else []
            params = {row.split("|")[1].strip(" `") for row in rows}
            sections[match[1]] = (path.name, params)
    return sections


TOOLS = registered_tools()
ROUTED = routed_tools()
SECTIONS = documented_tools()


def test_every_tiered_tool_is_registered():
    unregistered = set(get_tools_for_tier("complete")) - set(TOOLS)
    assert not unregistered, (
        f"tool_tiers.yaml names tools this test never registered: {sorted(unregistered)}. "
        "Import the service module that defines them above."
    )


def test_no_stale_tool_names():
    stale = (ROUTED | set(SECTIONS)) - set(TOOLS)
    assert not stale, (
        f"Skill names tools the server no longer registers: {sorted(stale)}"
    )


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tool_is_routed(tool):
    assert tool in ROUTED, f"Add a `{tool}` row to a routing table in SKILL.md"


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tool_parameters_are_documented(tool):
    assert tool in SECTIONS, (
        f"Add a `### {tool}` section with a parameter table under references/"
    )
    filename, documented = SECTIONS[tool]
    undocumented = TOOLS[tool] - documented
    unknown = documented - TOOLS[tool] - MODE_DEPENDENT_PARAMS
    assert not undocumented and not unknown, (
        f"references/{filename} `{tool}`: "
        f"undocumented={sorted(undocumented)} unknown={sorted(unknown)}"
    )
