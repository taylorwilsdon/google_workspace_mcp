"""Tests for the sheet tab lifecycle tool."""

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


def _service(sheets):
    service = Mock()
    service.spreadsheets.return_value.get.return_value.execute = Mock(
        return_value={"sheets": sheets}
    )
    service.spreadsheets.return_value.batchUpdate.return_value.execute = Mock(
        return_value={}
    )
    return service


TWO_SHEETS = [
    {"properties": {"sheetId": 0, "title": "January", "index": 0}},
    {"properties": {"sheetId": 7, "title": "February", "index": 1}},
]


def _sent_request(service):
    body = service.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
    assert len(body["requests"]) == 1
    return body["requests"][0]


@pytest.mark.asyncio
async def test_rename_updates_title_only():
    service = _service(TWO_SHEETS)

    await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="January",
        action="rename",
        new_name="January 2026",
    )

    request = _sent_request(service)["updateSheetProperties"]
    assert request["fields"] == "title"
    assert request["properties"] == {"sheetId": 0, "title": "January 2026"}


@pytest.mark.asyncio
async def test_hide_and_unhide_set_the_hidden_flag():
    for action, expected in (("hide", True), ("unhide", False)):
        service = _service(TWO_SHEETS)
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="January",
            action=action,
        )
        request = _sent_request(service)["updateSheetProperties"]
        assert request["fields"] == "hidden"
        assert request["properties"]["hidden"] is expected


@pytest.mark.asyncio
async def test_reorder_sets_index():
    service = _service(TWO_SHEETS)

    await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="February",
        action="reorder",
        new_index=0,
    )

    request = _sent_request(service)["updateSheetProperties"]
    assert request["fields"] == "index"
    assert request["properties"] == {"sheetId": 7, "index": 0}


@pytest.mark.asyncio
async def test_delete_removes_the_sheet():
    service = _service(TWO_SHEETS)

    await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="February",
        action="delete",
    )

    assert _sent_request(service) == {"deleteSheet": {"sheetId": 7}}


@pytest.mark.asyncio
async def test_deleting_the_last_sheet_is_refused():
    service = _service([{"properties": {"sheetId": 0, "title": "Only"}}])

    with pytest.raises(UserInputError, match="at least one sheet"):
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="Only",
            action="delete",
        )

    service.spreadsheets.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_rename_without_new_name_is_refused():
    with pytest.raises(UserInputError, match="new_name is required"):
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=_service(TWO_SHEETS),
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="January",
            action="rename",
        )


@pytest.mark.asyncio
async def test_unknown_action_is_refused():
    with pytest.raises(UserInputError, match="Invalid action"):
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=_service(TWO_SHEETS),
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="January",
            action="archive",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("new_index, api_index", [(1, 2), (0, 0)])
async def test_reorder_january_uses_pre_move_api_index(new_index, api_index):
    service = _service(TWO_SHEETS)
    await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="January",
        action="reorder",
        new_index=new_index,
    )
    assert _sent_request(service)["updateSheetProperties"]["properties"] == {
        "sheetId": 0,
        "index": api_index,
    }


@pytest.mark.asyncio
async def test_reorder_rejects_out_of_bounds_final_index():
    service = _service(TWO_SHEETS)
    with pytest.raises(UserInputError, match="less than the number of sheets"):
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="January",
            action="reorder",
            new_index=2,
        )
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_style_sets_tab_color_and_hides_gridlines():
    service = _service(TWO_SHEETS)

    result = await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="February",
        action="style",
        tab_color="#FF0000",
        show_gridlines=False,
    )

    request = _sent_request(service)["updateSheetProperties"]
    assert request["fields"] == "tabColorStyle,gridProperties.hideGridlines"
    assert request["properties"] == {
        "sheetId": 7,
        "tabColorStyle": {"rgbColor": {"red": 1.0, "green": 0.0, "blue": 0.0}},
        "gridProperties": {"hideGridlines": True},
    }
    assert "tab color #FF0000, gridlines hidden" in result


@pytest.mark.asyncio
async def test_style_tab_color_none_clears_it():
    service = _service(TWO_SHEETS)

    await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="January",
        action="style",
        tab_color="none",
    )

    request = _sent_request(service)["updateSheetProperties"]
    assert request["fields"] == "tabColorStyle"
    assert request["properties"] == {"sheetId": 0, "tabColorStyle": {}}


@pytest.mark.asyncio
async def test_style_shows_gridlines_only():
    service = _service(TWO_SHEETS)

    await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="January",
        action="style",
        show_gridlines=True,
    )

    request = _sent_request(service)["updateSheetProperties"]
    assert request["fields"] == "gridProperties.hideGridlines"
    assert request["properties"]["gridProperties"] == {"hideGridlines": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"action": "style"}, "needs tab_color and/or show_gridlines"),
        ({"action": "style", "tab_color": "red"}, "#RRGGBB"),
        ({"action": "style", "tab_color": ""}, "#RRGGBB"),
        ({"action": "style", "tab_color": "   "}, "#RRGGBB"),
        ({"action": "rename", "new_name": "X", "tab_color": "#000000"}, "only apply"),
    ],
)
async def test_style_invalid_input(kwargs, message):
    service = _service(TWO_SHEETS)

    with pytest.raises(UserInputError, match=message):
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="January",
            **kwargs,
        )
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_style_summary_normalizes_tab_color():
    service = _service(TWO_SHEETS)

    result = await _unwrap(sheets_tools.manage_sheet_tab)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        sheet_name="January",
        action="style",
        tab_color=" ff0000 ",
    )

    assert "tab color #FF0000" in result


@pytest.mark.asyncio
async def test_style_gridlines_rejected_on_object_sheet():
    service = _service(
        [
            {"properties": {"sheetId": 0, "title": "January", "index": 0}},
            {
                "properties": {
                    "sheetId": 9,
                    "title": "Chart",
                    "index": 1,
                    "sheetType": "OBJECT",
                }
            },
        ]
    )

    with pytest.raises(UserInputError, match="has no gridlines"):
        await _unwrap(sheets_tools.manage_sheet_tab)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet123",
            sheet_name="Chart",
            action="style",
            show_gridlines=False,
        )
    service.spreadsheets.return_value.batchUpdate.assert_not_called()
