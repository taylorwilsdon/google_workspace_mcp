"""Unit tests for manage_conditional_formatting tool."""

import sys
import os
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets import sheets_tools
from core.utils import UserInputError


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service(sheets=None):
    """Create a mock Sheets service client with configured sheet metadata."""
    if sheets is None:
        sheets = [
            {
                "properties": {"sheetId": 0, "title": "Sheet1", "index": 0},
                "conditionalFormats": [],
            }
        ]
    service = Mock()
    service.spreadsheets.return_value.get.return_value.execute = Mock(
        return_value={"sheets": sheets}
    )
    service.spreadsheets.return_value.batchUpdate.return_value.execute = Mock(
        return_value={}
    )
    return service


@pytest.mark.asyncio
async def test_add_missing_condition_type_and_gradient_raises_error():
    """Test adding conditional format rule without condition_type or gradient_points raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError,
        match="condition_type \\(or gradient_points\\) is required for action 'add'",
    ):
        await _unwrap(sheets_tools.manage_conditional_formatting)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="add",
            range_name="Sheet1!A1:B10",
        )


@pytest.mark.asyncio
async def test_add_valid_boolean_rule_success():
    """Test successfully adding a boolean conditional format rule."""
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_conditional_formatting)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="add",
        range_name="Sheet1!A1:B10",
        condition_type="NUMBER_GREATER",
        condition_values=["10"],
        background_color="#FF0000",
    )
    assert "Added conditional format" in result
    assert "NUMBER_GREATER" in result


@pytest.mark.asyncio
async def test_add_valid_gradient_rule_success():
    """Test successfully adding a gradient color scale conditional format rule."""
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_conditional_formatting)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="add",
        range_name="Sheet1!A1:B10",
        gradient_points=[
            {"type": "MIN", "color": "#FFFFFF"},
            {"type": "MAX", "color": "#FF0000"},
        ],
    )
    assert "Added conditional format" in result
    assert "gradient" in result
