"""Tests for formula-aware sheet reads."""

from unittest.mock import Mock

import pytest

from gsheets.sheets_tools import read_sheet_values, read_sheet_values_structured


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


# read_sheet_values_structured (studyops fork addition) — returns dict for
# programmatic consumers. See docs/security/google-workspace-mcp.md § L6 / L1
# for context on why this tool is added on the studyops side.


async def _call_read_sheet_values_structured(service, **overrides):
    """Call the undecorated implementation (skip auth wrapper)."""
    impl = read_sheet_values_structured.__wrapped__.__wrapped__
    return await impl(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="spreadsheet-123",
        range_name="members!A:Z",
        **overrides,
    )


@pytest.mark.asyncio
async def test_read_sheet_values_structured_returns_dict_with_values():
    service = _create_mock_service(
        {
            "range": "members!A1:C2",
            "values": [
                ["member_id", "name", "email"],
                ["mem_a1ce", "Alice", "alice@example.com"],
            ],
        }
    )

    result = await _call_read_sheet_values_structured(service)

    assert isinstance(result, dict)
    assert result["values"] == [
        ["member_id", "name", "email"],
        ["mem_a1ce", "Alice", "alice@example.com"],
    ]
    assert result["range"] == "members!A1:C2"
    assert result["row_count"] == 2


@pytest.mark.asyncio
async def test_read_sheet_values_structured_handles_empty_range():
    service = _create_mock_service({"range": "members!A1:Z1"})

    result = await _call_read_sheet_values_structured(service)

    assert result["values"] == []
    assert result["row_count"] == 0
    assert result["range"] == "members!A1:Z1"


@pytest.mark.asyncio
async def test_read_sheet_values_structured_falls_back_to_input_range():
    """API が ``range`` を返さない場合は引数の ``range_name`` をそのまま使う。"""
    service = _create_mock_service({"values": [["x"]]})

    result = await _call_read_sheet_values_structured(service)

    assert result["range"] == "members!A:Z"
    assert result["values"] == [["x"]]
    assert result["row_count"] == 1
