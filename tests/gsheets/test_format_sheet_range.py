"""
Unit tests for Google Sheets format_sheet_range tool enhancements

Tests the enhanced formatting parameters: wrap_strategy, horizontal_alignment,
vertical_alignment, bold, italic, and font_size.
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_tools import _format_sheet_range_impl, format_sheet_range


def _unwrap(fn):
    """Unwrap decorated function to access the underlying coroutine."""
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def create_mock_service():
    """Create a properly configured mock Google Sheets service."""
    mock_service = Mock()

    mock_metadata = {"sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]}
    mock_service.spreadsheets().get().execute = Mock(return_value=mock_metadata)
    mock_service.spreadsheets().batchUpdate().execute = Mock(return_value={})

    return mock_service


@pytest.mark.asyncio
async def test_format_wrap_strategy_wrap():
    """Test wrap_strategy=WRAP applies text wrapping"""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C10",
        wrap_strategy="WRAP",
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    assert result["range_name"] == "A1:C10"

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["wrapStrategy"] == "WRAP"


@pytest.mark.asyncio
async def test_format_wrap_strategy_clip():
    """Test wrap_strategy=CLIP clips text at cell boundary"""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B5",
        wrap_strategy="CLIP",
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["wrapStrategy"] == "CLIP"


@pytest.mark.asyncio
async def test_format_wrap_strategy_overflow():
    """Test wrap_strategy=OVERFLOW_CELL allows text overflow"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A1",
        wrap_strategy="OVERFLOW_CELL",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["wrapStrategy"] == "OVERFLOW_CELL"


@pytest.mark.asyncio
async def test_format_horizontal_alignment_center():
    """Test horizontal_alignment=CENTER centers text"""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:D10",
        horizontal_alignment="CENTER",
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["horizontalAlignment"] == "CENTER"


@pytest.mark.asyncio
async def test_format_horizontal_alignment_left():
    """Test horizontal_alignment=LEFT aligns text left"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A10",
        horizontal_alignment="LEFT",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["horizontalAlignment"] == "LEFT"


@pytest.mark.asyncio
async def test_format_horizontal_alignment_right():
    """Test horizontal_alignment=RIGHT aligns text right"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="B1:B10",
        horizontal_alignment="RIGHT",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["horizontalAlignment"] == "RIGHT"


@pytest.mark.asyncio
async def test_format_vertical_alignment_top():
    """Test vertical_alignment=TOP aligns text to top"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C5",
        vertical_alignment="TOP",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["verticalAlignment"] == "TOP"


@pytest.mark.asyncio
async def test_format_vertical_alignment_middle():
    """Test vertical_alignment=MIDDLE centers text vertically"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C5",
        vertical_alignment="MIDDLE",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["verticalAlignment"] == "MIDDLE"


@pytest.mark.asyncio
async def test_format_vertical_alignment_bottom():
    """Test vertical_alignment=BOTTOM aligns text to bottom"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C5",
        vertical_alignment="BOTTOM",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["verticalAlignment"] == "BOTTOM"


@pytest.mark.asyncio
async def test_format_bold_true():
    """Test bold=True applies bold text formatting"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A1",
        bold=True,
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["textFormat"]["bold"] is True


@pytest.mark.asyncio
async def test_format_italic_true():
    """Test italic=True applies italic text formatting"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A1",
        italic=True,
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["textFormat"]["italic"] is True


@pytest.mark.asyncio
async def test_format_font_size():
    """Test font_size applies specified font size"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:D5",
        font_size=14,
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["textFormat"]["fontSize"] == 14


@pytest.mark.asyncio
async def test_format_combined_text_formatting():
    """Test combining bold, italic, and font_size"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A1",
        bold=True,
        italic=True,
        font_size=16,
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    text_format = cell_format["textFormat"]
    assert text_format["bold"] is True
    assert text_format["italic"] is True
    assert text_format["fontSize"] == 16


@pytest.mark.asyncio
async def test_format_combined_alignment_and_wrap():
    """Test combining wrap_strategy with alignments"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C10",
        wrap_strategy="WRAP",
        horizontal_alignment="CENTER",
        vertical_alignment="TOP",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["wrapStrategy"] == "WRAP"
    assert cell_format["horizontalAlignment"] == "CENTER"
    assert cell_format["verticalAlignment"] == "TOP"


@pytest.mark.asyncio
async def test_format_all_new_params_with_existing():
    """Test combining new params with existing color params"""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:D10",
        background_color="#FFFFFF",
        text_color="#000000",
        wrap_strategy="WRAP",
        horizontal_alignment="LEFT",
        vertical_alignment="MIDDLE",
        bold=True,
        font_size=12,
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]

    assert cell_format["wrapStrategy"] == "WRAP"
    assert cell_format["horizontalAlignment"] == "LEFT"
    assert cell_format["verticalAlignment"] == "MIDDLE"
    assert cell_format["textFormat"]["bold"] is True
    assert cell_format["textFormat"]["fontSize"] == 12
    assert "backgroundColor" in cell_format


@pytest.mark.asyncio
async def test_format_invalid_wrap_strategy():
    """Test invalid wrap_strategy raises error"""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError) as exc_info:
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:A1",
            wrap_strategy="INVALID",
        )

    error_msg = str(exc_info.value).lower()
    assert "wrap_strategy" in error_msg or "wrap" in error_msg


@pytest.mark.asyncio
async def test_format_invalid_horizontal_alignment():
    """Test invalid horizontal_alignment raises error"""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError) as exc_info:
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:A1",
            horizontal_alignment="INVALID",
        )

    error_msg = str(exc_info.value).lower()
    assert "horizontal" in error_msg or "left" in error_msg


@pytest.mark.asyncio
async def test_format_invalid_vertical_alignment():
    """Test invalid vertical_alignment raises error"""
    mock_service = create_mock_service()

    from core.utils import UserInputError

    with pytest.raises(UserInputError) as exc_info:
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:A1",
            vertical_alignment="INVALID",
        )

    error_msg = str(exc_info.value).lower()
    assert "vertical" in error_msg or "top" in error_msg


@pytest.mark.asyncio
async def test_format_case_insensitive_wrap_strategy():
    """Test wrap_strategy accepts lowercase input"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A1",
        wrap_strategy="wrap",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["wrapStrategy"] == "WRAP"


@pytest.mark.asyncio
async def test_format_case_insensitive_alignment():
    """Test alignments accept lowercase input"""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A1",
        horizontal_alignment="center",
        vertical_alignment="middle",
    )

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    cell_format = request_body["requests"][0]["repeatCell"]["cell"]["userEnteredFormat"]
    assert cell_format["horizontalAlignment"] == "CENTER"
    assert cell_format["verticalAlignment"] == "MIDDLE"


@pytest.mark.asyncio
async def test_format_confirmation_message_includes_new_params():
    """Test confirmation message mentions new formatting applied"""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C10",
        wrap_strategy="WRAP",
        bold=True,
        font_size=14,
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    assert result["range_name"] == "A1:C10"


@pytest.mark.asyncio
async def test_format_merge_cells_default_all():
    """Test merge_cells=True defaults to MERGE_ALL."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="J3:L3",
        merge_cells=True,
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    assert result["range_name"] == "J3:L3"
    assert "merged cells (MERGE_ALL)" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 1
    assert "mergeCells" in requests[0]
    assert requests[0]["mergeCells"]["mergeType"] == "MERGE_ALL"
    assert requests[0]["mergeCells"]["range"]["startColumnIndex"] == 9
    assert requests[0]["mergeCells"]["range"]["endColumnIndex"] == 12
    assert requests[0]["mergeCells"]["range"]["startRowIndex"] == 2
    assert requests[0]["mergeCells"]["range"]["endRowIndex"] == 3


@pytest.mark.asyncio
async def test_format_merge_cells_columns():
    """Test merge_cells=True with merge_type='MERGE_COLUMNS'."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B5",
        merge_cells=True,
        merge_type="MERGE_COLUMNS",
    )

    assert "merged cells (MERGE_COLUMNS)" in result["summary"]
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    assert request_body["requests"][0]["mergeCells"]["mergeType"] == "MERGE_COLUMNS"


@pytest.mark.asyncio
async def test_format_merge_cells_rows():
    """Test merge_cells=True with merge_type='MERGE_ROWS'."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C2",
        merge_cells=True,
        merge_type="MERGE_ROWS",
    )

    assert "merged cells (MERGE_ROWS)" in result["summary"]
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    assert request_body["requests"][0]["mergeCells"]["mergeType"] == "MERGE_ROWS"


@pytest.mark.asyncio
async def test_format_merge_type_only():
    """Test specifying merge_type without merge_cells automatically triggers merge."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="J3:L3",
        merge_type="MERGE_ALL",
    )

    assert "merged cells (MERGE_ALL)" in result["summary"]
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    assert "mergeCells" in call_args[1]["body"]["requests"][0]


@pytest.mark.asyncio
async def test_format_unmerge_cells():
    """Test merge_cells=False creates unmergeCells request."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="J3:L3",
        merge_cells=False,
    )

    assert "unmerged cells" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    requests = request_body["requests"]
    assert len(requests) == 1
    assert "unmergeCells" in requests[0]
    assert requests[0]["unmergeCells"]["range"]["startColumnIndex"] == 9
    assert requests[0]["unmergeCells"]["range"]["endColumnIndex"] == 12


@pytest.mark.asyncio
async def test_format_combined_formatting_and_merge():
    """Test applying text formatting and merging together in a single request."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="J3:L3",
        bold=True,
        horizontal_alignment="CENTER",
        vertical_alignment="MIDDLE",
        merge_cells=True,
    )

    assert "bold" in result["summary"]
    assert "horizontal align CENTER" in result["summary"]
    assert "vertical align MIDDLE" in result["summary"]
    assert "merged cells (MERGE_ALL)" in result["summary"]

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    requests = call_args[1]["body"]["requests"]
    assert len(requests) == 2
    assert "repeatCell" in requests[0]
    assert "mergeCells" in requests[1]


@pytest.mark.asyncio
async def test_format_merge_single_cell_raises():
    """Test attempting to merge a single cell raises UserInputError."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError) as exc_info:
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:A1",
            merge_cells=True,
        )

    assert "Cannot merge single cell" in str(exc_info.value)
    assert "spanning at least 2 cells" in str(exc_info.value)


@pytest.mark.asyncio
async def test_format_invalid_merge_type_raises():
    """Test invalid merge_type raises UserInputError."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError) as exc_info:
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:B2",
            merge_cells=True,
            merge_type="INVALID_TYPE",
        )

    assert "merge_type must be one of" in str(exc_info.value)


@pytest.mark.asyncio
async def test_format_unmerge_with_merge_type_raises():
    """Test specifying merge_type when unmerging raises UserInputError."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError) as exc_info:
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:B2",
            merge_cells=False,
            merge_type="MERGE_ALL",
        )

    assert "Cannot specify merge_type when merge_cells is False" in str(exc_info.value)


@pytest.mark.asyncio
async def test_format_merge_case_insensitive():
    """Test merge_type is case-insensitive."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B2",
        merge_cells=True,
        merge_type="merge_rows",
    )

    assert "merged cells (MERGE_ROWS)" in result["summary"]
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request_body = call_args[1]["body"]
    assert request_body["requests"][0]["mergeCells"]["mergeType"] == "MERGE_ROWS"


@pytest.mark.asyncio
async def test_format_sheet_range_tool_wrapper_merge():
    """Test format_sheet_range server tool wrapper with merge."""
    mock_service = create_mock_service()

    output = await _unwrap(format_sheet_range)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_spreadsheet_123",
        range_name="J3:L3",
        bold=True,
        merge_cells=True,
    )

    assert "Applied formatting to range 'J3:L3'" in output
    assert "merged cells (MERGE_ALL)" in output
    assert "user@example.com" in output
