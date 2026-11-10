"""Tests for copying a range with modify_sheet_values(source_range=...)."""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gsheets.sheets_tools import modify_sheet_values


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _service():
    service = Mock()
    spreadsheets = service.spreadsheets.return_value
    spreadsheets.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Daily"}},
            {"properties": {"sheetId": 42, "title": "Données 2026"}},
        ]
    }
    spreadsheets.batchUpdate.return_value.execute.return_value = {}
    return service


async def _copy(service, **kwargs):
    return await _unwrap(modify_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        **kwargs,
    )


def _sent_requests(service):
    batch_update = service.spreadsheets.return_value.batchUpdate
    batch_update.assert_called_once()
    assert batch_update.call_args.kwargs["spreadsheetId"] == "sheet123"
    return batch_update.call_args.kwargs["body"]["requests"]


@pytest.mark.asyncio
async def test_copies_row_with_formulas_and_formatting():
    service = _service()

    result = await _copy(service, range_name="Daily!A30", source_range="Daily!A29:AO29")

    assert _sent_requests(service) == [
        {
            "copyPaste": {
                "source": {
                    "sheetId": 0,
                    "startRowIndex": 28,
                    "endRowIndex": 29,
                    "startColumnIndex": 0,
                    "endColumnIndex": 41,
                },
                "destination": {
                    "sheetId": 0,
                    "startRowIndex": 29,
                    "endRowIndex": 30,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "pasteType": "PASTE_NORMAL",
            }
        }
    ]
    service.spreadsheets.return_value.values.assert_not_called()
    assert "Daily!A29:AO29" in result and "Daily!A30" in result


@pytest.mark.asyncio
async def test_copies_across_sheets_with_paste_type():
    service = _service()

    result = await _copy(
        service,
        range_name="'Données 2026'!3:3",
        source_range="Daily!2:2",
        paste_type=" paste_format ",
    )

    copy_paste = _sent_requests(service)[0]["copyPaste"]
    assert copy_paste["source"] == {"sheetId": 0, "startRowIndex": 1, "endRowIndex": 2}
    assert copy_paste["destination"] == {
        "sheetId": 42,
        "startRowIndex": 2,
        "endRowIndex": 3,
    }
    assert copy_paste["pasteType"] == "PASTE_FORMAT"
    assert "PASTE_FORMAT" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"values": [["x"]]},
        {"clear_values": True},
        {"chips": "user@example.com"},
    ],
    ids=["values", "clear", "chips"],
)
async def test_rejects_source_range_with_other_modes(extra):
    service = _service()

    with pytest.raises(UserInputError, match="only one of"):
        await _copy(service, range_name="Daily!A30", source_range="Daily!A29", **extra)
    service.spreadsheets.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"source_range": "Daily!A29", "paste_type": "PASTE_ALL"}, "paste_type"),
        ({"source_range": "Missing!A1"}, "not found"),
        ({"source_range": ""}, "must not be empty"),
    ],
    ids=["bad-paste-type", "unknown-sheet", "empty-source"],
)
async def test_rejects_invalid_copy_input(kwargs, message):
    service = _service()

    with pytest.raises(UserInputError, match=message):
        await _copy(service, range_name="Daily!A30", **kwargs)
    service.spreadsheets.return_value.batchUpdate.assert_not_called()
