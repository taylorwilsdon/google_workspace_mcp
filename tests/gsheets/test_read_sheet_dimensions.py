"""
Unit tests for Google Sheets read_sheet_dimensions tool.

Tests column width reading, row height reading, hidden status, default dimensions,
multi-letter columns (AA, AB...), sheet name resolution, row truncation, and JSON output validity.
"""

import json
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_tools import _read_sheet_dimensions_impl, read_sheet_dimensions


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def create_mock_service(
    sheets_metadata=None,
    grid_data=None,
):
    """Create a properly configured mock Google Sheets service."""
    mock_service = Mock()

    if sheets_metadata is None:
        sheets_metadata = {
            "sheets": [
                {
                    "properties": {
                        "sheetId": 0,
                        "title": "Sheet1",
                        "gridProperties": {"rowCount": 1000, "columnCount": 26},
                    }
                },
                {
                    "properties": {
                        "sheetId": 12345,
                        "title": "Feuille",
                        "gridProperties": {"rowCount": 500, "columnCount": 6},
                    }
                },
            ]
        }

    if grid_data is None:
        grid_data = {
            "sheets": [
                {
                    "properties": {
                        "sheetId": 12345,
                        "title": "Feuille",
                        "gridProperties": {"rowCount": 500, "columnCount": 6},
                    },
                    "data": [
                        {
                            "columnMetadata": [
                                {"pixelSize": 140},
                                {"pixelSize": 100},
                                {"pixelSize": 260},
                                {"pixelSize": 240},
                                {"pixelSize": 380},
                                {"pixelSize": 90, "hiddenByUser": True},
                            ],
                            "rowMetadata": [
                                {"pixelSize": 30},
                                {"pixelSize": 21},
                            ],
                        }
                    ],
                }
            ]
        }

    def get_side_effect(spreadsheetId, fields=None, ranges=None, includeGridData=False):
        mock_req = Mock()
        if ranges or includeGridData:
            mock_req.execute = Mock(return_value=grid_data)
        else:
            mock_req.execute = Mock(return_value=sheets_metadata)
        return mock_req

    mock_service.spreadsheets().get.side_effect = get_side_effect
    return mock_service


@pytest.mark.asyncio
async def test_read_sheet_dimensions_impl_explicit_metadata():
    """Test reading explicit column widths, row heights, and hidden flags."""
    mock_service = create_mock_service()

    result = await _read_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_sheet_123",
        sheet_name="Feuille",
        include_rows=True,
    )

    assert result["spreadsheet_id"] == "test_sheet_123"
    assert result["sheet_name"] == "Feuille"
    assert result["sheet_id"] == 12345
    assert result["row_count"] == 500
    assert result["column_count"] == 6

    # Verify column widths
    cols = result["column_sizes"]
    assert cols["A"] == 140
    assert cols["B"] == 100
    assert cols["C"] == 260
    assert cols["D"] == 240
    assert cols["E"] == 380
    assert cols["F"] == 90
    assert result["hidden_columns"] == ["F"]

    # Verify row heights
    rows = result["row_sizes"]
    assert rows[1] == 30
    assert rows[2] == 21
    assert result["hidden_rows"] == []


@pytest.mark.asyncio
async def test_read_sheet_dimensions_formatted_output():
    """Test formatted string output of read_sheet_dimensions."""
    mock_service = create_mock_service()

    output = await _unwrap(read_sheet_dimensions)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_123",
        sheet_name="Feuille",
        include_rows=False,
    )

    assert 'Sheet: "Feuille"' in output
    assert "Grid size: 500 rows x 6 columns" in output
    assert "Column A: 140px" in output
    assert "Column F: 90px (hidden)" in output
    assert "column_sizes JSON for resize_sheet_dimensions:" in output
    assert '"A": 140' in output


@pytest.mark.asyncio
async def test_read_sheet_dimensions_not_found_raises():
    """Test error when requested sheet does not exist."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError) as exc_info:
        await _read_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_sheet_123",
            sheet_name="NonexistentSheet",
        )

    assert "Sheet 'NonexistentSheet' not found" in str(exc_info.value)
    assert "Feuille" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_sheet_dimensions_empty_spreadsheet_raises():
    """Test error when spreadsheet has no sheets."""
    mock_service = create_mock_service(sheets_metadata={"sheets": []})

    with pytest.raises(UserInputError) as exc_info:
        await _read_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_sheet_empty",
        )

    assert "No sheets found in spreadsheet" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_sheet_dimensions_defaults_to_first_sheet():
    """Test omitting sheet_name resolves to the first sheet."""
    grid_data = {
        "sheets": [
            {
                "properties": {"sheetId": 0, "title": "Sheet1"},
                "data": [
                    {
                        "columnMetadata": [{"pixelSize": 180}],
                    }
                ],
            }
        ]
    }
    mock_service = create_mock_service(grid_data=grid_data)

    result = await _read_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        sheet_name=None,
    )

    assert result["sheet_name"] == "Sheet1"
    assert result["sheet_id"] == 0
    assert result["column_sizes"]["A"] == 180


@pytest.mark.asyncio
async def test_read_sheet_dimensions_default_unmodified_dimensions():
    """Test default values when sheet has no explicit column or row metadata."""
    grid_data = {
        "sheets": [
            {
                "properties": {
                    "sheetId": 0,
                    "title": "Sheet1",
                    "gridProperties": {"rowCount": 100, "columnCount": 2},
                },
                "data": [{"columnMetadata": [], "rowMetadata": []}],
            }
        ]
    }
    mock_service = create_mock_service(grid_data=grid_data)

    output = await _unwrap(read_sheet_dimensions)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_123",
        sheet_name="Sheet1",
        include_rows=True,
    )

    assert "All columns are at default width (100px)" in output
    assert "All rows are at default height (21px)" in output


@pytest.mark.asyncio
async def test_read_sheet_dimensions_multi_letter_columns():
    """Test column conversion beyond column Z (e.g. 26 -> AA, 27 -> AB)."""
    # 28 columns (0=A ... 25=Z, 26=AA, 27=AB)
    col_metadata = [{"pixelSize": 100} for _ in range(26)]
    col_metadata.append({"pixelSize": 250})  # AA
    col_metadata.append({"pixelSize": 300})  # AB

    grid_data = {
        "sheets": [
            {
                "properties": {"sheetId": 0, "title": "Sheet1"},
                "data": [{"columnMetadata": col_metadata}],
            }
        ]
    }
    mock_service = create_mock_service(grid_data=grid_data)

    result = await _read_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        sheet_name="Sheet1",
    )

    assert result["column_sizes"]["AA"] == 250
    assert result["column_sizes"]["AB"] == 300


@pytest.mark.asyncio
async def test_read_sheet_dimensions_hidden_rows():
    """Test hidden status for rows when include_rows is True."""
    row_metadata = [
        {"pixelSize": 25, "hiddenByUser": True},
        {"pixelSize": 40},
    ]
    grid_data = {
        "sheets": [
            {
                "properties": {"sheetId": 0, "title": "Sheet1"},
                "data": [{"rowMetadata": row_metadata}],
            }
        ]
    }
    mock_service = create_mock_service(grid_data=grid_data)

    output = await _unwrap(read_sheet_dimensions)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_123",
        sheet_name="Sheet1",
        include_rows=True,
    )

    assert "Row 1: 25px (hidden)" in output
    assert "Row 2: 40px" in output


@pytest.mark.asyncio
async def test_read_sheet_dimensions_more_than_50_rows_truncates():
    """Test that row list is truncated after 50 items with a summary indicator."""
    row_metadata = [{"pixelSize": 22} for _ in range(75)]
    grid_data = {
        "sheets": [
            {
                "properties": {"sheetId": 0, "title": "Sheet1"},
                "data": [{"rowMetadata": row_metadata}],
            }
        ]
    }
    mock_service = create_mock_service(grid_data=grid_data)

    output = await _unwrap(read_sheet_dimensions)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_123",
        sheet_name="Sheet1",
        include_rows=True,
    )

    assert "Row 50: 22px" in output
    assert "Row 51:" not in output
    assert "... and 25 more rows" in output


@pytest.mark.asyncio
async def test_read_sheet_dimensions_json_validity():
    """Test that the embedded JSON block can be parsed and matches column_sizes."""
    mock_service = create_mock_service()

    output = await _unwrap(read_sheet_dimensions)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_123",
        sheet_name="Feuille",
    )

    # Extract JSON line
    json_marker = "column_sizes JSON for resize_sheet_dimensions:\n"
    assert json_marker in output
    json_str = output.split(json_marker)[1].split("\n\n")[0].strip()

    parsed = json.loads(json_str)
    assert parsed["A"] == 140
    assert parsed["B"] == 100
    assert parsed["C"] == 260
    assert parsed["D"] == 240
    assert parsed["E"] == 380
    assert parsed["F"] == 90


@pytest.mark.asyncio
async def test_read_sheet_dimensions_quoted_sheet_name():
    """Test sheet name containing special characters and single quotes."""
    special_title = "L'école et Courriels"
    sheets_metadata = {
        "sheets": [
            {
                "properties": {"sheetId": 999, "title": special_title},
            }
        ]
    }
    grid_data = {
        "sheets": [
            {
                "properties": {"sheetId": 999, "title": special_title},
                "data": [
                    {
                        "columnMetadata": [{"pixelSize": 175}],
                    }
                ],
            }
        ]
    }
    mock_service = create_mock_service(
        sheets_metadata=sheets_metadata,
        grid_data=grid_data,
    )

    result = await _read_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_special",
        sheet_name=special_title,
    )

    assert result["sheet_name"] == special_title
    assert result["column_sizes"]["A"] == 175

    # Check that ranges query properly escaped the single quote
    call_args = mock_service.spreadsheets().get.call_args_list[-1]
    assert call_args[1]["ranges"] == ["'L''école et Courriels'"]
