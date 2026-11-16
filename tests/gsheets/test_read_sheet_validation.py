"""Tests for reading data validation rules back with read_sheet_values."""

from unittest.mock import Mock

import pytest

from gsheets.sheets_helpers import (
    _extract_cell_validation_from_grid,
    _format_sheet_validation_section,
)
from gsheets.sheets_tools import read_sheet_values


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


YES_NO = {
    "condition": {
        "type": "ONE_OF_LIST",
        "values": [{"userEnteredValue": "Yes"}, {"userEnteredValue": "No"}],
    },
    "strict": True,
    "showCustomUi": True,
}
CHECKBOX = {"condition": {"type": "BOOLEAN"}}


def _grid(rows, start_row=0, start_col=0, title="Sheet1"):
    return {
        "sheets": [
            {
                "properties": {"title": title},
                "data": [
                    {
                        "startRow": start_row,
                        "startColumn": start_col,
                        "rowData": [
                            {
                                "values": [
                                    {"dataValidation": r} if r else {} for r in row
                                ]
                            }
                            for row in rows
                        ],
                    }
                ],
            }
        ]
    }


def _service(values_response, grid_response):
    service = Mock()
    service.spreadsheets().values().get().execute = Mock(return_value=values_response)
    service.spreadsheets().get().execute = Mock(return_value=grid_response)
    return service


async def _read(service, **overrides):
    kwargs = {
        "service": service,
        "user_google_email": "user@example.com",
        "spreadsheet_id": "spreadsheet-123",
        "range_name": "Sheet1!A1:B3",
        **overrides,
    }
    return await _unwrap(read_sheet_values)(**kwargs)


@pytest.mark.asyncio
async def test_include_validation_lists_rules_for_the_range():
    service = _service(
        {
            "range": "Sheet1!A1:B3",
            "values": [["Task", "Done"], ["a", "Yes"], ["b", "No"]],
        },
        _grid([[None, None], [None, YES_NO], [None, YES_NO]]),
    )

    result = await _read(service, include_validation=True)

    assert "Data validation in range 'Sheet1!A1:B3':" in result
    assert "- Sheet1!B2:B3: ONE_OF_LIST: Yes, No (strict)" in result
    fields = service.spreadsheets().get.call_args.kwargs["fields"]
    assert "dataValidation" in fields


@pytest.mark.asyncio
async def test_validation_is_not_fetched_by_default():
    service = _service(
        {"range": "Sheet1!A1:B3", "values": [["Task", "Done"]]},
        _grid([[None, YES_NO]]),
    )

    result = await _read(service)

    assert "Data validation" not in result
    service.spreadsheets().get().execute.assert_not_called()


def test_contiguous_cells_with_the_same_rule_are_grouped():
    grid = _grid(
        [[YES_NO, CHECKBOX], [YES_NO, CHECKBOX], [None, YES_NO], [YES_NO, None]],
        start_row=1,
    )

    section = _format_sheet_validation_section(
        rules=_extract_cell_validation_from_grid(grid), range_label="Sheet1!A2:B5"
    )

    assert "- Sheet1!A2:A3: ONE_OF_LIST: Yes, No (strict)" in section
    assert "- Sheet1!B2:B3: BOOLEAN" in section
    assert "- Sheet1!B4: ONE_OF_LIST: Yes, No (strict)" in section
    assert "- Sheet1!A5: ONE_OF_LIST: Yes, No (strict)" in section


def test_rule_description_includes_range_source_and_help_text():
    rule = {
        "condition": {
            "type": "ONE_OF_RANGE",
            "values": [{"userEnteredValue": "=Lists!A1:A5"}],
        },
        "inputMessage": "Pick a team",
    }

    section = _format_sheet_validation_section(
        rules=_extract_cell_validation_from_grid(_grid([[rule]])),
        range_label="Sheet1!A1",
    )

    assert '- Sheet1!A1: ONE_OF_RANGE: =Lists!A1:A5 (help: "Pick a team")' in section


def test_no_rules_gives_no_section():
    assert (
        _format_sheet_validation_section(
            rules=_extract_cell_validation_from_grid(_grid([[None, None]])),
            range_label="Sheet1!A1:B1",
        )
        == ""
    )
