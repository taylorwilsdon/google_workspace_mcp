"""
Unit tests for Google Sheets resize_sheet_dimensions tool.

Tests column/row resizing, auto-resize, freeze, and hide/unhide operations.
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets.sheets_tools import _resize_sheet_dimensions_impl


def create_mock_service():
    """Create a properly configured mock Google Sheets service."""
    mock_service = Mock()

    mock_metadata = {"sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]}
    mock_service.spreadsheets().get().execute = Mock(return_value=mock_metadata)
    mock_service.spreadsheets().batchUpdate().execute = Mock(return_value={})

    return mock_service


@pytest.mark.asyncio
async def test_resize_columns():
    """Test setting explicit column widths."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        column_sizes={"A": 200, "C": 300},
    )

    assert result["spreadsheet_id"] == "test_123"
    assert "A=200px" in result["summary"]
    assert "C=300px" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 2

    # Column A (index 0)
    req_a = requests[0]["updateDimensionProperties"]
    assert req_a["range"]["startIndex"] == 0
    assert req_a["range"]["endIndex"] == 1
    assert req_a["range"]["dimension"] == "COLUMNS"
    assert req_a["properties"]["pixelSize"] == 200

    # Column C (index 2)
    req_c = requests[1]["updateDimensionProperties"]
    assert req_c["range"]["startIndex"] == 2
    assert req_c["range"]["endIndex"] == 3
    assert req_c["properties"]["pixelSize"] == 300


@pytest.mark.asyncio
async def test_resize_rows():
    """Test setting explicit row heights."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        row_sizes={"1": 40, "3": 60},
    )

    assert "1=40px" in result["summary"]
    assert "3=60px" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 2

    req_1 = requests[0]["updateDimensionProperties"]
    assert req_1["range"]["startIndex"] == 0
    assert req_1["range"]["endIndex"] == 1
    assert req_1["range"]["dimension"] == "ROWS"
    assert req_1["properties"]["pixelSize"] == 40


@pytest.mark.asyncio
async def test_auto_resize_columns():
    """Test auto-resizing columns to fit content."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        auto_resize_columns=["A", "B"],
    )

    assert "auto-resized columns" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 2
    assert "autoResizeDimensions" in requests[0]
    assert requests[0]["autoResizeDimensions"]["dimensions"]["dimension"] == "COLUMNS"


@pytest.mark.asyncio
async def test_auto_resize_rows():
    """Test auto-resizing rows to fit content."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        auto_resize_rows=[1, 2],
    )

    assert "auto-resized rows" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 2
    assert "autoResizeDimensions" in requests[0]
    assert requests[0]["autoResizeDimensions"]["dimensions"]["dimension"] == "ROWS"


@pytest.mark.asyncio
async def test_freeze_rows():
    """Test freezing rows."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        frozen_row_count=1,
    )

    assert "froze 1 row(s)" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 1
    props = requests[0]["updateSheetProperties"]
    assert props["properties"]["gridProperties"]["frozenRowCount"] == 1
    assert "gridProperties.frozenRowCount" in props["fields"]


@pytest.mark.asyncio
async def test_freeze_columns():
    """Test freezing columns."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        frozen_column_count=2,
    )

    assert "froze 2 column(s)" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    props = requests[0]["updateSheetProperties"]
    assert props["properties"]["gridProperties"]["frozenColumnCount"] == 2


@pytest.mark.asyncio
async def test_unfreeze_rows():
    """Test unfreezing rows with frozen_row_count=0."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        frozen_row_count=0,
    )

    assert "unfroze rows" in result["summary"]


@pytest.mark.asyncio
async def test_hide_columns():
    """Test hiding columns."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        hide_columns=["B", "C"],
    )

    assert "hid columns" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 2
    req = requests[0]["updateDimensionProperties"]
    assert req["properties"]["hiddenByUser"] is True
    assert req["fields"] == "hiddenByUser"


@pytest.mark.asyncio
async def test_unhide_columns():
    """Test unhiding columns."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        unhide_columns=["B"],
    )

    assert "unhid columns" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    req = request_body["requests"][0]["updateDimensionProperties"]
    assert req["properties"]["hiddenByUser"] is False


@pytest.mark.asyncio
async def test_hide_rows():
    """Test hiding rows."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        hide_rows=[2, 3],
    )

    assert "hid rows" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 2
    req = requests[0]["updateDimensionProperties"]
    assert req["range"]["dimension"] == "ROWS"
    assert req["properties"]["hiddenByUser"] is True


@pytest.mark.asyncio
async def test_json_string_column_sizes():
    """Test column_sizes accepts JSON string input."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        column_sizes='{"A": 200}',
    )

    assert "A=200px" in result["summary"]


@pytest.mark.asyncio
async def test_combined_operations():
    """Test combining resize + freeze + hide in one call."""
    mock_service = create_mock_service()

    result = await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        column_sizes={"A": 200},
        frozen_row_count=1,
        hide_columns=["D"],
    )

    assert "A=200px" in result["summary"]
    assert "froze 1 row(s)" in result["summary"]
    assert "hid columns" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    # 1 column resize + 1 freeze + 1 hide = 3 requests
    assert len(requests) == 3


@pytest.mark.asyncio
async def test_sheet_name_targeting():
    """Test targeting a specific sheet by name."""
    mock_service = Mock()

    mock_metadata = {
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Sheet1"}},
            {"properties": {"sheetId": 123, "title": "Data"}},
        ]
    }
    mock_service.spreadsheets().get().execute = Mock(return_value=mock_metadata)
    mock_service.spreadsheets().batchUpdate().execute = Mock(return_value={})

    await _resize_sheet_dimensions_impl(
        service=mock_service,
        spreadsheet_id="test_123",
        sheet_name="Data",
        column_sizes={"A": 150},
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    req = request_body["requests"][0]["updateDimensionProperties"]
    assert req["range"]["sheetId"] == 123


@pytest.mark.asyncio
async def test_no_params_raises_error():
    """Test that calling with no dimension params raises UserInputError."""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError):
        await _resize_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_123",
        )


@pytest.mark.asyncio
async def test_invalid_column_letter_raises_error():
    """Test that invalid column letter raises UserInputError."""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError):
        await _resize_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_123",
            column_sizes={"": 200},
        )


@pytest.mark.asyncio
async def test_negative_pixel_size_raises_error():
    """Test that negative pixel size raises UserInputError."""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError):
        await _resize_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_123",
            column_sizes={"A": -100},
        )


@pytest.mark.asyncio
async def test_sheet_not_found_raises_error():
    """Test that targeting a non-existent sheet raises UserInputError."""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError, match="not found"):
        await _resize_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_123",
            sheet_name="NonExistent",
            column_sizes={"A": 200},
        )


@pytest.mark.asyncio
async def test_negative_frozen_row_count_raises_error():
    """Test that negative frozen_row_count raises UserInputError."""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError):
        await _resize_sheet_dimensions_impl(
            service=mock_service,
            spreadsheet_id="test_123",
            frozen_row_count=-1,
        )
