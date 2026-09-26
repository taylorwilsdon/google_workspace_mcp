"""Tests for Google Sheets manage_sheet_basic_filter tool."""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets import sheets_tools


def _unwrap(tool):
    """Unwrap FastMCP/auth decorator chain to reach the inner function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service(sheets=None):
    """Create a mock Sheets service client with configured get and batchUpdate responses."""
    service = Mock()
    spreadsheets_mock = Mock()
    service.spreadsheets.return_value = spreadsheets_mock

    mock_metadata = {}
    if sheets is not None:
        mock_metadata["sheets"] = sheets
    else:
        mock_metadata["sheets"] = [
            {"properties": {"sheetId": 0, "title": "Sheet1"}},
            {"properties": {"sheetId": 12345, "title": "Data"}},
        ]

    spreadsheets_mock.get.return_value.execute.return_value = mock_metadata
    spreadsheets_mock.batchUpdate.return_value.execute.return_value = {}

    return service


# ===========================================================================
# Validation Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_empty_spreadsheet_id_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="spreadsheet_id is required"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="",
            action="get",
        )


@pytest.mark.asyncio
async def test_empty_action_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="action is required"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="",
        )


@pytest.mark.asyncio
async def test_invalid_action_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="Invalid action 'invalid'"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="invalid",
        )


@pytest.mark.asyncio
async def test_sheet_not_found_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="Sheet 'NonExistent' not found"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="get",
            sheet_name="NonExistent",
        )


@pytest.mark.asyncio
async def test_sheet_in_range_not_found_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="Sheet 'UnknownTab' not found"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="set",
            range_name="'UnknownTab'!A1:D10",
        )


@pytest.mark.asyncio
async def test_set_action_missing_range_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="range_name is required"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="set",
        )


@pytest.mark.asyncio
async def test_invalid_sort_column_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="Invalid sort_column"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="set",
            range_name="A1:E20",
            sort_column="123Invalid!",
        )


@pytest.mark.asyncio
async def test_invalid_sort_order_raises():
    service = _create_mock_service()
    with pytest.raises(
        UserInputError, match="sort_order must be 'ASCENDING' or 'DESCENDING'"
    ):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="set",
            range_name="A1:E20",
            sort_column="A",
            sort_order="BACKWARDS",
        )


@pytest.mark.asyncio
async def test_invalid_hidden_values_type_raises():
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="hidden_values must be a dictionary"):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="set",
            range_name="A1:E20",
            hidden_values=["Not", "A", "Dict"],  # type: ignore
        )


# ===========================================================================
# Action: set / add Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_set_basic_filter_simple_range():
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="set",
        range_name="A1:E50",
    )

    assert (
        "Successfully set basic filter on sheet 'Sheet1' for range 'Sheet1!A1:E50'"
        in result
    )
    service.spreadsheets().batchUpdate.assert_called_once()
    call_args = service.spreadsheets().batchUpdate.call_args[1]
    body = call_args["body"]
    requests = body["requests"]
    assert len(requests) == 1
    req = requests[0]["setBasicFilter"]["filter"]
    assert req["range"]["sheetId"] == 0
    assert req["range"]["startRowIndex"] == 0
    assert req["range"]["endRowIndex"] == 50
    assert req["range"]["startColumnIndex"] == 0
    assert req["range"]["endColumnIndex"] == 5


@pytest.mark.asyncio
async def test_set_basic_filter_with_sort_and_hidden_values():
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="add",
        sheet_name="Data",
        range_name="B2:F100",
        sort_column="C",
        sort_order="DESCENDING",
        hidden_values={"D": ["Inactive", "Archived"], "4": ["Draft"]},
    )

    assert "Successfully set basic filter on sheet 'Data'" in result
    assert "Sort: Column C (DESCENDING)" in result

    call_args = service.spreadsheets().batchUpdate.call_args[1]
    req = call_args["body"]["requests"][0]["setBasicFilter"]["filter"]
    assert req["range"]["sheetId"] == 12345
    assert req["sortSpecs"] == [{"dimensionIndex": 2, "sortOrder": "DESCENDING"}]
    # Column D is index 3
    assert req["criteria"]["3"] == {"hiddenValues": ["Inactive", "Archived"]}
    # Column 4 is index 4 (E)
    assert req["criteria"]["4"] == {"hiddenValues": ["Draft"]}


@pytest.mark.asyncio
async def test_set_basic_filter_with_filter_criteria():
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="set",
        range_name="A1:D20",
        filter_criteria={
            "A": {
                "condition": {
                    "type": "TEXT_CONTAINS",
                    "values": [{"userEnteredValue": "active"}],
                }
            }
        },
    )

    assert "Successfully set basic filter" in result
    call_args = service.spreadsheets().batchUpdate.call_args[1]
    req = call_args["body"]["requests"][0]["setBasicFilter"]["filter"]
    assert req["criteria"]["0"]["condition"]["type"] == "TEXT_CONTAINS"


# ===========================================================================
# Action: clear / remove Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_clear_basic_filter():
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="clear",
        sheet_name="Data",
    )

    assert "Cleared basic filter on sheet 'Data'" in result
    service.spreadsheets().batchUpdate.assert_called_once()
    call_args = service.spreadsheets().batchUpdate.call_args[1]
    requests = call_args["body"]["requests"]
    assert requests == [{"clearBasicFilter": {"sheetId": 12345}}]


# ===========================================================================
# Action: get / inspect Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_get_basic_filter_none():
    service = _create_mock_service()
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="get",
        sheet_name="Sheet1",
    )

    assert "No basic filter found on sheet 'Sheet1'" in result


@pytest.mark.asyncio
async def test_get_basic_filter_active():
    sheets_with_filter = [
        {
            "properties": {"sheetId": 0, "title": "Sheet1"},
            "basicFilter": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": 0,
                    "endRowIndex": 100,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "sortSpecs": [{"dimensionIndex": 1, "sortOrder": "ASCENDING"}],
                "criteria": {"2": {"hiddenValues": ["Pending"]}},
            },
        }
    ]
    service = _create_mock_service(sheets=sheets_with_filter)
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="inspect",
    )

    assert 'Sheet: "Sheet1"' in result
    assert "Status: Active basic filter" in result
    assert "Range: Sheet1!A1:D100" in result
    assert "Sort specifications:" in result
    assert "Column B: ASCENDING" in result
    assert "Column C hidden values: ['Pending']" in result


# ===========================================================================
# Action: modify / update Tests
# ===========================================================================


@pytest.mark.asyncio
async def test_modify_basic_filter_no_existing_raises():
    service = _create_mock_service()
    with pytest.raises(
        UserInputError,
        match="No existing basic filter found on sheet 'Sheet1' to modify",
    ):
        await _unwrap(sheets_tools.manage_sheet_basic_filter)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="modify",
            sort_column="B",
        )


@pytest.mark.asyncio
async def test_modify_basic_filter_updates_range_and_criteria():
    sheets_with_filter = [
        {
            "properties": {"sheetId": 0, "title": "Sheet1"},
            "basicFilter": {
                "range": {
                    "sheetId": 0,
                    "startRowIndex": 0,
                    "endRowIndex": 50,
                    "startColumnIndex": 0,
                    "endColumnIndex": 4,
                },
                "sortSpecs": [{"dimensionIndex": 0, "sortOrder": "ASCENDING"}],
            },
        }
    ]
    service = _create_mock_service(sheets=sheets_with_filter)
    result = await _unwrap(sheets_tools.manage_sheet_basic_filter)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="update",
        range_name="A1:F100",
        hidden_values={"B": ["Discarded"]},
    )

    assert (
        "Successfully updated basic filter on sheet 'Sheet1' for range 'Sheet1!A1:F100'"
        in result
    )
    call_args = service.spreadsheets().batchUpdate.call_args[1]
    req = call_args["body"]["requests"][0]["setBasicFilter"]["filter"]
    assert req["range"]["endRowIndex"] == 100
    assert req["range"]["endColumnIndex"] == 6
    # Original sort preserved
    assert req["sortSpecs"] == [{"dimensionIndex": 0, "sortOrder": "ASCENDING"}]
    # New criteria merged
    assert req["criteria"]["1"] == {"hiddenValues": ["Discarded"]}
