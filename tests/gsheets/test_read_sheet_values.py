"""Tests for formula-aware sheet reads."""

from unittest.mock import Mock

import pytest

from gsheets.sheets_tools import read_sheet_values


def _create_mock_service(*responses_or_errors):
    """Create a Sheets service mock for sequential values.get responses."""
    mock_service = Mock()
    mock_service.spreadsheets().values().get().execute = Mock(
        side_effect=list(responses_or_errors)
    )
    return mock_service


async def _call_read_sheet_values(service, **overrides):
    """Call the undecorated implementation to keep auth out of unit tests."""
    impl = read_sheet_values.__wrapped__.__wrapped__
    return await impl(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="spreadsheet-123",
        range_name="Sheet1!A1:A1",
        **overrides,
    )


@pytest.mark.asyncio
async def test_read_sheet_values_surfaces_formulas_when_display_values_are_blank():
    service = _create_mock_service(
        {"range": "Sheet1!A1:A1", "values": []},
        {"range": "Sheet1!A1:A1", "values": [['=IF(TRUE, "", "")']]},
    )

    result = await _call_read_sheet_values(service, include_formulas=True)

    assert "No data found" not in result
    assert "The range contains formula cells." in result
    assert "Formula cells in range 'Sheet1!A1:A1':" in result
    assert '- Sheet1!A1: =IF(TRUE, "", "")' in result


@pytest.mark.asyncio
async def test_read_sheet_values_tolerates_formula_fetch_failures():
    service = _create_mock_service(
        {"range": "Sheet1!A1:A1", "values": [["1"]]},
        RuntimeError("formula fetch failed"),
    )

    result = await _call_read_sheet_values(service, include_formulas=True)

    assert "Successfully read 1 rows" in result
    assert "Row  1: ['1']" in result
    assert "Formula cells in range" not in result
