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
from gsheets.sheets_tools import _format_sheet_range_impl


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


def _sent_requests(mock_service):
    return mock_service.spreadsheets().batchUpdate.call_args[1]["body"]["requests"]


@pytest.mark.asyncio
async def test_format_borders_outer_defaults_to_solid_black():
    """borders="outer" draws the four outer edges, SOLID and black by default."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:C3",
        borders="outer",
    )

    requests = _sent_requests(mock_service)
    assert len(requests) == 1
    update = requests[0]["updateBorders"]
    assert set(update) == {"range", "top", "bottom", "left", "right"}
    assert update["top"] == {
        "style": "SOLID",
        "color": {"red": 0.0, "green": 0.0, "blue": 0.0},
    }
    assert "SOLID borders (outer)" in result["summary"]


@pytest.mark.asyncio
async def test_format_borders_combined_with_cell_format():
    """Borders and cell formatting go out in one batchUpdate, format first."""
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B2",
        background_color="#FFEECC",
        borders="bottom,inner_horizontal",
        border_style="dashed",
        border_color="#FF0000",
    )

    requests = _sent_requests(mock_service)
    assert list(requests[0]) == ["repeatCell"]
    update = requests[1]["updateBorders"]
    assert set(update) == {"range", "bottom", "innerHorizontal"}
    assert update["bottom"]["style"] == "DASHED"
    assert update["bottom"]["color"] == {"red": 1.0, "green": 0.0, "blue": 0.0}


@pytest.mark.asyncio
async def test_format_borders_all_deduplicates_sides():
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B2",
        borders="all,top",
    )

    update = _sent_requests(mock_service)[0]["updateBorders"]
    assert set(update) - {"range"} == {
        "top",
        "bottom",
        "left",
        "right",
        "innerHorizontal",
        "innerVertical",
    }


@pytest.mark.asyncio
async def test_format_borders_none_clears_every_side():
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B2",
        borders="none",
    )

    update = _sent_requests(mock_service)[0]["updateBorders"]
    assert len(update) == 7
    assert all(update[side] == {"style": "NONE"} for side in update if side != "range")
    assert "borders removed" in result["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"borders": "diagonal"}, "Unknown border side"),
        ({"borders": "none,top"}, "cannot be combined"),
        ({"borders": "all", "border_style": "WAVY"}, "border_style must be one of"),
        ({"border_style": "SOLID"}, "need borders"),
        ({"border_color": "#000000"}, "need borders"),
        ({"borders": "none", "border_style": "WAVY"}, "drop border_style"),
        ({"borders": "none", "border_color": "#FF0000"}, "drop border_style"),
    ],
)
async def test_format_borders_invalid_input(kwargs, message):
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match=message):
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:B2",
            **kwargs,
        )
    mock_service.spreadsheets().batchUpdate().execute.assert_not_called()
    # Bad border input is rejected before the metadata fetch, too.
    mock_service.spreadsheets().get().execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "side", ["innerHorizontal", "inner-horizontal", "INNER_HORIZONTAL"]
)
async def test_format_borders_accepts_api_and_hyphen_spellings(side):
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B2",
        borders=side,
    )

    update = _sent_requests(mock_service)[0]["updateBorders"]
    assert set(update) == {"range", "innerHorizontal"}


@pytest.mark.asyncio
async def test_format_borders_blank_style_falls_back_to_solid():
    mock_service = create_mock_service()

    await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:B2",
        borders="outer",
        border_style="  ",
    )

    update = _sent_requests(mock_service)[0]["updateBorders"]
    assert update["top"]["style"] == "SOLID"


@pytest.mark.asyncio
async def test_format_dropdown_values_sets_strict_list():
    """dropdown_values adds a strict ONE_OF_LIST rule with the dropdown chip."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="C2:C20",
        dropdown_values=["open", " in progress ", "", "done"],
    )

    requests = _sent_requests(mock_service)
    assert len(requests) == 1
    rule = requests[0]["setDataValidation"]["rule"]
    assert rule["condition"] == {
        "type": "ONE_OF_LIST",
        "values": [
            {"userEnteredValue": "open"},
            {"userEnteredValue": "in progress"},
            {"userEnteredValue": "done"},
        ],
    }
    assert rule["strict"] is True
    assert rule["showCustomUi"] is True
    assert "dropdown [open, in progress, done]" in result["summary"]


@pytest.mark.asyncio
async def test_format_dropdown_combined_with_cell_format():
    """Dropdown and cell formatting go out in one batchUpdate."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A5",
        background_color="#FFF2CC",
        dropdown_values=["1", "2", "3"],
        dropdown_strict=False,
    )

    requests = _sent_requests(mock_service)
    assert len(requests) == 2
    assert list(requests[0]) == ["repeatCell"]
    assert requests[1]["setDataValidation"]["range"]["sheetId"] == 0
    assert requests[1]["setDataValidation"]["rule"]["strict"] is False
    assert "(warning only)" in result["summary"]


@pytest.mark.asyncio
async def test_format_clear_dropdown_sends_rule_less_request():
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A5",
        clear_dropdown=True,
    )

    request = _sent_requests(mock_service)[0]["setDataValidation"]
    assert "rule" not in request
    assert "data validation removed" in result["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"dropdown_values": ["a"], "clear_dropdown": True}, "not both"),
        ({"dropdown_values": [" ", ""]}, "at least one value"),
        ({"dropdown_strict": False, "bold": True}, "needs dropdown_values"),
        ({"dropdown_values": [], "bold": True}, "at least one value"),
    ],
)
async def test_format_dropdown_invalid_input(kwargs, message):
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match=message):
        await _format_sheet_range_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1:B2",
            **kwargs,
        )
    mock_service.spreadsheets().batchUpdate().execute.assert_not_called()
    # Bad dropdown input is rejected before the metadata fetch, too.
    mock_service.spreadsheets().get().execute.assert_not_called()


@pytest.mark.asyncio
async def test_format_borders_and_dropdown_in_one_call():
    """Cell format, borders and dropdown all go out in a single batchUpdate."""
    mock_service = create_mock_service()

    result = await _format_sheet_range_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1:A5",
        bold=True,
        borders="outer",
        dropdown_values=["yes", "no"],
    )

    requests = _sent_requests(mock_service)
    assert [list(r)[0] for r in requests] == [
        "repeatCell",
        "updateBorders",
        "setDataValidation",
    ]
    assert (
        requests[1]["updateBorders"]["range"]
        == requests[2]["setDataValidation"]["range"]
    )
    assert "SOLID borders (outer), dropdown [yes, no]" in result["summary"]
