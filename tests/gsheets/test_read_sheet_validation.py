"""Tests for reading data validation rules back with read_sheet_values."""

from typing import Any, Callable, Optional
from unittest.mock import Mock

import pytest

from gsheets.sheets_helpers import (
    _describe_data_validation,
    _extract_cell_validation_from_grid,
    _format_sheet_validation_section,
)
from gsheets.sheets_tools import read_sheet_values


def _unwrap(tool: Any) -> Callable[..., Any]:
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


def _grid(
    rows: list[list[Optional[dict[str, Any]]]],
    start_row: int = 0,
    start_col: int = 0,
    title: str = "Sheet1",
) -> dict[str, Any]:
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


def _service(values_response: dict[str, Any], grid_response: dict[str, Any]) -> Mock:
    service = Mock()
    service.spreadsheets().values().get().execute = Mock(return_value=values_response)
    service.spreadsheets().get().execute = Mock(return_value=grid_response)
    return service


async def _read(service: Mock, **overrides: Any) -> str:
    kwargs = {
        "service": service,
        "user_google_email": "user@example.com",
        "spreadsheet_id": "spreadsheet-123",
        "range_name": "Sheet1!A1:B3",
        **overrides,
    }
    return await _unwrap(read_sheet_values)(**kwargs)


@pytest.mark.asyncio
async def test_include_validation_lists_rules_for_the_range() -> None:
    service = _service(
        {
            "range": "Sheet1!A1:B3",
            "values": [["Task", "Done"], ["a", "Yes"], ["b", "No"]],
        },
        _grid([[None, None], [None, YES_NO], [None, YES_NO]]),
    )

    result = await _read(service, include_validation=True)

    assert "Data validation in range 'Sheet1!A1:B3':" in result
    assert '- Sheet1!B2:B3: ONE_OF_LIST: "Yes", "No" (strict)' in result
    fields = service.spreadsheets().get.call_args.kwargs["fields"]
    assert "dataValidation" in fields


@pytest.mark.asyncio
async def test_validation_is_not_fetched_by_default() -> None:
    service = _service(
        {"range": "Sheet1!A1:B3", "values": [["Task", "Done"]]},
        _grid([[None, YES_NO]]),
    )

    result = await _read(service)

    assert "Data validation" not in result
    service.spreadsheets().get().execute.assert_not_called()


def test_contiguous_cells_with_the_same_rule_are_grouped() -> None:
    grid = _grid(
        [[YES_NO, CHECKBOX], [YES_NO, CHECKBOX], [None, YES_NO], [YES_NO, None]],
        start_row=1,
    )

    section = _format_sheet_validation_section(
        rules=_extract_cell_validation_from_grid(grid), range_label="Sheet1!A2:B5"
    )

    assert '- Sheet1!A2:A3: ONE_OF_LIST: "Yes", "No" (strict)' in section
    assert "- Sheet1!B2:B3: BOOLEAN" in section
    assert '- Sheet1!B4: ONE_OF_LIST: "Yes", "No" (strict)' in section
    assert '- Sheet1!A5: ONE_OF_LIST: "Yes", "No" (strict)' in section


def test_rule_description_includes_range_source_and_help_text() -> None:
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

    assert '- Sheet1!A1: ONE_OF_RANGE: "=Lists!A1:A5" (help: "Pick a team")' in section


def test_no_rules_gives_no_section() -> None:
    assert (
        _format_sheet_validation_section(
            rules=_extract_cell_validation_from_grid(_grid([[None, None]])),
            range_label="Sheet1!A1:B1",
        )
        == ""
    )


@pytest.mark.asyncio
async def test_validation_on_a_blank_range_is_still_reported() -> None:
    service = _service(
        {"range": "Sheet1!B2:B3"},
        _grid([[YES_NO], [YES_NO]], start_row=1, start_col=1),
    )

    result = await _read(service, range_name="Sheet1!B2:B3", include_validation=True)

    assert "No data found" in result
    assert '- Sheet1!B2:B3: ONE_OF_LIST: "Yes", "No" (strict)' in result
    assert service.spreadsheets().get.call_args.kwargs["ranges"] == ["Sheet1!B2:B3"]


@pytest.mark.asyncio
async def test_validation_covers_a_blank_tail_of_the_range() -> None:
    service = _service(
        {"range": "Sheet1!B2:B3", "values": [["Yes"]]},
        _grid([[YES_NO], [YES_NO]], start_row=1, start_col=1),
    )

    result = await _read(service, range_name="Sheet1!B2:B3", include_validation=True)

    assert service.spreadsheets().get.call_args.kwargs["ranges"] == ["Sheet1!B2:B3"]
    assert '- Sheet1!B2:B3: ONE_OF_LIST: "Yes", "No" (strict)' in result


def test_lists_that_read_alike_are_not_merged() -> None:
    one_item = {
        "condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": "A, B"}]}
    }
    two_items = {
        "condition": {
            "type": "ONE_OF_LIST",
            "values": [{"userEnteredValue": "A"}, {"userEnteredValue": "B"}],
        }
    }

    rules = _extract_cell_validation_from_grid(_grid([[one_item], [two_items]]))

    assert rules == [
        {"range": "Sheet1!A1", "rule": 'ONE_OF_LIST: "A, B"'},
        {"range": "Sheet1!A2", "rule": 'ONE_OF_LIST: "A", "B"'},
    ]


def test_rules_that_differ_only_in_hidden_fields_are_not_merged() -> None:
    shown = {**YES_NO, "showCustomUi": True}
    hidden = {**YES_NO, "showCustomUi": False}

    rules = _extract_cell_validation_from_grid(_grid([[shown], [hidden]]))

    assert [rule["range"] for rule in rules] == ["Sheet1!A1", "Sheet1!A2"]


@pytest.mark.asyncio
async def test_validation_is_kept_when_only_formulas_are_found() -> None:
    service = Mock()
    service.spreadsheets().values().get().execute = Mock(
        side_effect=[
            {"range": "Sheet1!A1:A1"},
            {"range": "Sheet1!A1:A1", "values": [['=IF(TRUE, "", "")']]},
        ]
    )
    service.spreadsheets().get().execute = Mock(return_value=_grid([[YES_NO]]))

    result = await _read(
        service,
        range_name="Sheet1!A1:A1",
        include_formulas=True,
        include_validation=True,
    )

    assert "The range contains formula cells." in result
    assert '- Sheet1!A1: ONE_OF_LIST: "Yes", "No" (strict)' in result


@pytest.mark.asyncio
async def test_a_failed_validation_read_is_reported_with_the_values() -> None:
    service = Mock()
    service.spreadsheets().values().get().execute = Mock(
        return_value={"range": "Sheet1!A1:B2", "values": [["Task", "Done"]]}
    )
    service.spreadsheets().get().execute = Mock(side_effect=OSError("ssl reset"))

    result = await _read(service, range_name="Sheet1!A1:B2", include_validation=True)

    assert "Task" in result
    assert "Data validation could not be read for range 'Sheet1!A1:B2'" in result


@pytest.mark.asyncio
async def test_an_oversized_request_falls_back_to_the_populated_range() -> None:
    service = _service(
        {"range": "Sheet1!A1:Z1000", "values": [["Task", "Done"]]},
        _grid([[None, YES_NO]]),
    )

    result = await _read(service, range_name="A1:Z1000", include_validation=True)

    assert service.spreadsheets().get.call_args.kwargs["ranges"] == ["Sheet1!A1:B1"]
    assert '- Sheet1!B1: ONE_OF_LIST: "Yes", "No" (strict)' in result


@pytest.mark.asyncio
async def test_a_skipped_validation_read_is_reported() -> None:
    service = _service({"range": "Sheet1!A1:Z1000"}, _grid([[YES_NO]]))

    result = await _read(service, range_name="A1:Z1000", include_validation=True)

    assert "Data validation was not read for range 'Sheet1!A1:Z1000'" in result
    service.spreadsheets().get().execute.assert_not_called()


def test_rule_text_keeps_unicode_and_escapes_help_text() -> None:
    rule = {
        "condition": {
            "type": "ONE_OF_LIST",
            "values": [{"userEnteredValue": "Sí"}, {"userEnteredValue": "完了"}],
        },
        "inputMessage": 'Say "hi"\n- Sheet1!Z9: BOOLEAN',
    }

    assert _describe_data_validation(rule) == (
        'ONE_OF_LIST: "Sí", "完了" (help: "Say \\"hi\\"\\n- Sheet1!Z9: BOOLEAN")'
    )


def test_relative_date_values_are_described() -> None:
    rule = {
        "condition": {"type": "DATE_AFTER", "values": [{"relativeDate": "PAST_WEEK"}]}
    }

    assert _describe_data_validation(rule) == 'DATE_AFTER: "PAST_WEEK"'


def test_validation_section_is_capped() -> None:
    rules = [{"range": f"Sheet1!A{i}", "rule": "BOOLEAN"} for i in range(1, 28)]

    section = _format_sheet_validation_section(
        rules=rules, range_label="Sheet1!A1:A27", max_details=25
    )

    assert "- Sheet1!A25: BOOLEAN" in section
    assert "Sheet1!A26" not in section
    assert section.endswith("... and 2 more validation ranges")


def test_boolean_values_keep_their_positions() -> None:
    rule = {
        "condition": {
            "type": "BOOLEAN",
            "values": [{"userEnteredValue": ""}, {"userEnteredValue": "No"}],
        }
    }

    assert _describe_data_validation(rule) == 'BOOLEAN: "", "No"'
