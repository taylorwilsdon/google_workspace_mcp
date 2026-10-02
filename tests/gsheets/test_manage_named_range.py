"""Tests for Google Sheets manage_named_range tool."""

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


def _create_mock_service(sheets=None, named_ranges=None, add_named_range_reply=None):
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
            {"properties": {"sheetId": 12345, "title": "Expenses"}},
        ]
    if named_ranges is not None:
        mock_metadata["namedRanges"] = named_ranges

    spreadsheets_mock.get.return_value.execute.return_value = mock_metadata

    batch_response = {}
    if add_named_range_reply:
        batch_response = {
            "replies": [{"addNamedRange": {"namedRange": add_named_range_reply}}]
        }
    spreadsheets_mock.batchUpdate.return_value.execute.return_value = batch_response

    return service


# ===========================================================================
# Action Validation
# ===========================================================================


@pytest.mark.asyncio
async def test_invalid_action_raises_error():
    """Test that an invalid action raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="Invalid action 'invalid'"):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="invalid",
        )


# ===========================================================================
# Action: List
# ===========================================================================


@pytest.mark.asyncio
async def test_list_named_ranges_when_empty():
    """Test listing named ranges when none exist in the spreadsheet."""
    service = _create_mock_service(named_ranges=[])

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="list",
    )

    assert "No named ranges found in spreadsheet 'sheet123'" in result


@pytest.mark.asyncio
async def test_list_named_ranges_with_data():
    """Test listing named ranges returns formatted details."""
    named_ranges = [
        {
            "namedRangeId": "nr_1",
            "name": "TotalRevenue",
            "range": {
                "sheetId": 0,
                "startRowIndex": 0,
                "endRowIndex": 10,
                "startColumnIndex": 0,
                "endColumnIndex": 5,
            },
        },
        {
            "namedRangeId": "nr_2",
            "name": "Q1Expenses",
            "range": {
                "sheetId": 12345,
                "startRowIndex": 5,
                "endRowIndex": 15,
                "startColumnIndex": 1,
                "endColumnIndex": 3,
            },
        },
    ]
    service = _create_mock_service(named_ranges=named_ranges)

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="list",
    )

    assert "Found 2 named range(s)" in result
    assert "TotalRevenue" in result
    assert "Sheet1!A1:E10" in result
    assert "nr_1" in result
    assert "Q1Expenses" in result
    assert "Expenses!B6:C15" in result
    assert "nr_2" in result


@pytest.mark.asyncio
async def test_list_named_ranges_omitted_sheet_id_defaults_to_zero():
    """Test listing named ranges when sheetId is omitted in API response."""
    named_ranges = [
        {
            "namedRangeId": "nr_zero",
            "name": "FirstSheetData",
            "range": {
                # sheetId is omitted by Google Sheets API protobuf when 0
                "startRowIndex": 0,
                "endRowIndex": 5,
                "startColumnIndex": 0,
                "endColumnIndex": 2,
            },
        },
    ]
    service = _create_mock_service(named_ranges=named_ranges)

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="list",
    )

    assert "FirstSheetData" in result
    assert "Sheet1!A1:B5" in result


@pytest.mark.asyncio
async def test_list_named_ranges_whole_column_and_row():
    """Test that column-only and row-only named ranges render as valid A1."""
    named_ranges = [
        {
            "namedRangeId": "nr_col",
            "name": "WholeColumn",
            # Column A only: no row bounds.
            "range": {"sheetId": 0, "startColumnIndex": 0, "endColumnIndex": 1},
        },
        {
            "namedRangeId": "nr_row",
            "name": "WholeRow",
            # Row 1 only: no column bounds.
            "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
        },
    ]
    service = _create_mock_service(named_ranges=named_ranges)

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="list",
    )

    assert "Sheet1!A:A" in result
    assert "Sheet1!1:1" in result


# ===========================================================================
# Action: Create
# ===========================================================================


@pytest.mark.asyncio
async def test_create_named_range_success():
    """Test successful creation of a named range with mocked response."""
    service = _create_mock_service(
        add_named_range_reply={"namedRangeId": "generated_nr_id", "name": "TaxRate"}
    )

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="create",
        name="TaxRate",
        range_name="Sheet1!B2:B5",
    )

    assert "Successfully created named range 'TaxRate'" in result
    assert "generated_nr_id" in result
    assert "Sheet1!B2:B5" in result

    # Check the batchUpdate payload
    batch_mock = service.spreadsheets.return_value.batchUpdate
    call_kwargs = batch_mock.call_args.kwargs
    requests = call_kwargs["body"]["requests"]
    assert len(requests) == 1
    add_req = requests[0]["addNamedRange"]["namedRange"]
    assert add_req["name"] == "TaxRate"
    assert add_req["range"]["sheetId"] == 0
    assert add_req["range"]["startRowIndex"] == 1
    assert add_req["range"]["endRowIndex"] == 5
    assert add_req["range"]["startColumnIndex"] == 1
    assert add_req["range"]["endColumnIndex"] == 2


@pytest.mark.asyncio
async def test_create_missing_name_raises_error():
    """Test creating a named range without name raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(UserInputError, match="name is required for action='create'"):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="create",
            range_name="Sheet1!A1:B2",
        )


@pytest.mark.asyncio
async def test_create_missing_range_name_raises_error():
    """Test creating a named range without range_name raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError, match="range_name is required for action='create'"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="create",
            name="MyRange",
        )


# ===========================================================================
# Action: Update
# ===========================================================================


@pytest.mark.asyncio
async def test_update_named_range_by_id_both_fields():
    """Test updating both name and range of a named range by ID."""
    existing_ranges = [
        {"namedRangeId": "nr_100", "name": "OldName", "range": {"sheetId": 0}}
    ]
    service = _create_mock_service(named_ranges=existing_ranges)

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="update",
        named_range_id="nr_100",
        new_name="NewName",
        new_range="Sheet1!C1:C10",
    )

    assert "Successfully updated named range (ID: nr_100)" in result
    assert "renamed from 'OldName' to 'NewName'" in result
    assert "range updated to 'Sheet1!C1:C10'" in result

    batch_mock = service.spreadsheets.return_value.batchUpdate
    requests = batch_mock.call_args.kwargs["body"]["requests"]
    assert len(requests) == 1
    upd_req = requests[0]["updateNamedRange"]
    assert upd_req["fields"] == "name,range"
    assert upd_req["namedRange"]["namedRangeId"] == "nr_100"
    assert upd_req["namedRange"]["name"] == "NewName"
    assert upd_req["namedRange"]["range"]["startColumnIndex"] == 2


@pytest.mark.asyncio
async def test_update_named_range_by_name_resolution():
    """Test updating a named range by resolving name to ID."""
    existing_ranges = [
        {
            "namedRangeId": "nr_auto_resolved",
            "name": "SalesData",
            "range": {"sheetId": 0},
        }
    ]
    service = _create_mock_service(named_ranges=existing_ranges)

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="update",
        name="SalesData",
        new_name="AnnualSales",
    )

    assert "Successfully updated named range (ID: nr_auto_resolved)" in result
    assert "renamed from 'SalesData' to 'AnnualSales'" in result

    batch_mock = service.spreadsheets.return_value.batchUpdate
    requests = batch_mock.call_args.kwargs["body"]["requests"]
    upd_req = requests[0]["updateNamedRange"]
    assert upd_req["fields"] == "name"
    assert upd_req["namedRange"]["namedRangeId"] == "nr_auto_resolved"
    assert upd_req["namedRange"]["name"] == "AnnualSales"


@pytest.mark.asyncio
async def test_update_missing_new_values_raises_error():
    """Test updating without new_name or new_range raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError,
        match="At least one of 'new_name' or 'new_range' must be provided",
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="update",
            named_range_id="nr_1",
        )


@pytest.mark.asyncio
async def test_update_whitespace_new_values_raises_error():
    """Test updating with whitespace-only new values raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError,
        match="At least one of 'new_name' or 'new_range' must be provided",
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="update",
            named_range_id="nr_1",
            new_name="   ",
            new_range="   ",
        )


@pytest.mark.asyncio
async def test_update_missing_identifier_raises_error():
    """Test updating without named_range_id or name raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError, match="Either 'named_range_id' or 'name' is required"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="update",
            new_name="NewName",
        )


@pytest.mark.asyncio
async def test_update_whitespace_identifier_raises_error():
    """Test updating with whitespace-only identifiers raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError, match="Either 'named_range_id' or 'name' is required"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="update",
            named_range_id="   ",
            name="   ",
            new_name="NewName",
        )


@pytest.mark.asyncio
async def test_update_not_found_raises_error():
    """Test updating a non-existent named range raises UserInputError."""
    service = _create_mock_service(named_ranges=[])
    with pytest.raises(
        UserInputError, match="Named range with name 'Unknown' not found"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="update",
            name="Unknown",
            new_name="NewName",
        )


# ===========================================================================
# Action: Delete
# ===========================================================================


@pytest.mark.asyncio
async def test_delete_named_range_by_id():
    """Test deleting a named range by ID."""
    service = _create_mock_service()

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="delete",
        named_range_id="nr_to_delete",
    )

    assert "Successfully deleted named range (ID: nr_to_delete)" in result

    batch_mock = service.spreadsheets.return_value.batchUpdate
    requests = batch_mock.call_args.kwargs["body"]["requests"]
    assert len(requests) == 1
    del_req = requests[0]["deleteNamedRange"]
    assert del_req["namedRangeId"] == "nr_to_delete"


@pytest.mark.asyncio
async def test_delete_named_range_by_name_resolution():
    """Test deleting a named range by resolving name to ID."""
    existing_ranges = [{"namedRangeId": "nr_del_id", "name": "TempData"}]
    service = _create_mock_service(named_ranges=existing_ranges)

    result = await _unwrap(sheets_tools.manage_named_range)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        action="delete",
        name="TempData",
    )

    assert "Successfully deleted named range 'TempData' (ID: nr_del_id)" in result

    batch_mock = service.spreadsheets.return_value.batchUpdate
    requests = batch_mock.call_args.kwargs["body"]["requests"]
    assert len(requests) == 1
    del_req = requests[0]["deleteNamedRange"]
    assert del_req["namedRangeId"] == "nr_del_id"


@pytest.mark.asyncio
async def test_delete_missing_identifier_raises_error():
    """Test deleting without identifier raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError, match="Either 'named_range_id' or 'name' is required"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="delete",
        )


@pytest.mark.asyncio
async def test_delete_whitespace_identifier_raises_error():
    """Test deleting with whitespace-only identifiers raises UserInputError."""
    service = _create_mock_service()
    with pytest.raises(
        UserInputError, match="Either 'named_range_id' or 'name' is required"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="delete",
            named_range_id="   ",
            name="   ",
        )


@pytest.mark.asyncio
async def test_delete_not_found_raises_error():
    """Test deleting a non-existent named range raises UserInputError."""
    service = _create_mock_service(named_ranges=[])
    with pytest.raises(
        UserInputError, match="Named range with name 'GhostRange' not found"
    ):
        await _unwrap(sheets_tools.manage_named_range)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            action="delete",
            name="GhostRange",
        )
