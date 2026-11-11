"""Risk annotations advertised for tools with side effects (#1248)."""

import pytest

import gdocs.docs_tools  # noqa: F401
import gmail.gmail_tools  # noqa: F401
import gsheets.sheets_tools  # noqa: F401
import gslides.slides_tools  # noqa: F401
from core.server import server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    [
        "send_gmail_message",
        "manage_document_comment",
        "manage_spreadsheet_comment",
        "manage_presentation_comment",
    ],
)
async def test_tool_is_destructive(name):
    tool = await server.get_tool(name)
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is True
