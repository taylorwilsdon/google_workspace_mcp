"""Regression: modify_sheet_values accepts numbers, booleans and blanks as cells.

The published schema typed every cell as a string, so a client that sent a native
list of numbers failed argument validation before the tool ran; only a
JSON-encoded string worked. The Sheets API accepts string, number, boolean and
null cell values, so the tool must too.
"""

from typing import Any, Callable
from unittest.mock import Mock

import jsonschema
import pytest

import gsheets.sheets_tools  # noqa: F401
from core.server import server
from gsheets.sheets_tools import modify_sheet_values


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service() -> Mock:
    service = Mock()
    values_api = service.spreadsheets.return_value.values.return_value
    values_api.update.return_value.execute.return_value = {
        "updatedCells": 4,
        "updatedRows": 2,
        "updatedColumns": 2,
    }
    return service


@pytest.mark.asyncio
async def test_published_schema_accepts_non_string_cells() -> None:
    tool = await server.get_tool("modify_sheet_values")

    jsonschema.validate(
        {
            "user_google_email": "user@example.com",
            "spreadsheet_id": "sheet123",
            "range_name": "Data!A1:B2",
            "values": [["name", "score"], ["Ada", 42], [True, None]],
        },
        tool.parameters,
    )


@pytest.mark.asyncio
async def test_writes_numeric_boolean_and_blank_cells() -> None:
    service = _service()

    await _unwrap(modify_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        range_name="Data!A1:B3",
        values=[["name", "score"], ["Ada", 42], [True, None]],
    )

    update = service.spreadsheets.return_value.values.return_value.update
    update.assert_called_once()
    assert update.call_args.kwargs["body"] == {
        "values": [["name", "score"], ["Ada", 42], [True, None]]
    }
