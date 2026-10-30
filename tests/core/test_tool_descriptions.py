"""Tool descriptions carry a docstring's whole guidance text, not its first section."""

import inspect

import pytest
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool

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
from core.server import SecureFastMCP, server
from core.tool_descriptions import docstring_description


async def description_of(app: FastMCP, name: str) -> str:
    tool = await app.get_tool(name)
    assert tool is not None and tool.description is not None, name
    return tool.description


def documented():
    """Summary line.

    Important:
        Guidance under a heading.

    Actions:
        - create: Make one.

    Args:
        x: A parameter.

    Returns:
        str: Something.

    Examples:
        documented(x=1)
    """


def test_description_keeps_headed_guidance_and_stops_at_args():
    assert docstring_description(documented) == (
        "Summary line.\n\n"
        "Important:\n    Guidance under a heading.\n\n"
        "Actions:\n    - create: Make one."
    )


def test_description_stops_at_examples_and_returns():
    def examples_first():
        """Summary.

        Examples:
            examples_first()
        """

    def returns_only():
        """Summary.

        Returns:
            str: Something.
        """

    assert docstring_description(examples_first) == "Summary."
    assert docstring_description(returns_only) == "Summary."


@pytest.mark.parametrize(
    "heading", ["Arguments", "Parameters", "Params", "Return", "Yields", "Example"]
)
def test_description_stops_at_google_style_aliases(heading):
    def aliased():
        pass

    aliased.__doc__ = f"Summary.\n\n{heading}:\n    x: A parameter.\n"

    assert docstring_description(aliased) == "Summary."


def test_description_is_none_without_a_docstring():
    def undocumented():
        pass

    assert docstring_description(undocumented) is None


@pytest.mark.asyncio
async def test_every_registration_form_publishes_the_guidance():
    app = SecureFastMCP(name="descriptions")

    @app.tool
    def bare():
        """Bare.

        Important:
            Bare guidance.
        """

    @app.tool()
    def called():
        """Called.

        Important:
            Called guidance.
        """

    @app.tool("renamed")
    def named():
        """Named.

        Important:
            Named guidance.
        """

    def direct():
        """Direct.

        Important:
            Direct guidance.
        """

    app.tool(direct, title="Direct")

    @app.tool(description=None)
    def none_decorated():
        """None decorated.

        Important:
            None decorated guidance.
        """

    def none_direct():
        """None direct.

        Important:
            None direct guidance.
        """

    app.tool(none_direct, description=None)

    @app.tool(description="Explicit.")
    def explicit():
        """Ignored.

        Important:
            Ignored guidance.
        """

    for name, guidance in [
        ("bare", "Bare guidance."),
        ("called", "Called guidance."),
        ("renamed", "Named guidance."),
        ("direct", "Direct guidance."),
        ("none_decorated", "None decorated guidance."),
        ("none_direct", "None direct guidance."),
    ]:
        assert guidance in await description_of(app, name)
    assert await description_of(app, "explicit") == "Explicit."


@pytest.mark.asyncio
async def test_registered_tools_publish_every_line_above_args():
    tools = await server.list_tools(run_middleware=False)
    assert tools
    for tool in tools:
        assert isinstance(tool, FunctionTool), tool.name
        doc = inspect.getdoc(tool.fn) or ""
        description = tool.description or ""
        assert description == docstring_description(tool.fn), tool.name
        assert "\nArgs:" not in description, tool.name
        assert "\nReturns:" not in description, tool.name
        if "\nArgs:" in doc:
            above_args = doc.split("\nArgs:")[0].strip()
            assert description == above_args, tool.name


@pytest.mark.asyncio
async def test_batch_update_presentation_publishes_its_write_rules():
    description = await description_of(server, "batch_update_presentation")
    assert "insertText.objectId must be a text-capable shape" in description
    assert "include_speaker_notes=True" in description
    assert "The notes page ID is not a\n    valid target." in description


@pytest.mark.asyncio
async def test_render_page_to_image_is_the_way_to_see_a_slide():
    description = await description_of(server, "render_page_to_image")
    assert "return it as image content" in description
    assert "instead of exporting the\npresentation to PPTX or PDF" in description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["get_presentation", "get_page", "create_presentation"],
)
async def test_slides_read_descriptions_say_what_they_return(name):
    description = await description_of(server, name)
    assert "object ID" in description or "ID and URL" in description


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "manage_document_comment",
        "manage_spreadsheet_comment",
        "manage_presentation_comment",
    ],
)
async def test_comment_tools_publish_their_actions(name):
    description = await description_of(server, name)
    for action in ("create:", "reply:", "resolve:", "update:", "delete:"):
        assert action in description
