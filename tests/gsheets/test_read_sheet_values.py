"""Tests for formula-aware sheet reads."""

from unittest.mock import Mock

import pytest

from gsheets.sheets_tools import read_sheet_values


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service(*responses_or_errors):
    """Create a Sheets service mock for sequential values.get responses."""
    mock_service = Mock()
    mock_service.spreadsheets().values().get().execute = Mock(
        side_effect=list(responses_or_errors)
    )
    return mock_service


async def _call_read_sheet_values(service, **overrides):
    """Call the undecorated implementation to keep auth out of unit tests."""
    return await _unwrap(read_sheet_values)(
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


@pytest.mark.asyncio
async def test_read_sheet_values_renders_all_fetched_rows():
    # Regression: the formatter used to cap the display at 50 rows, silently
    # discarding data the caller fetched via range_name.
    rows = [[str(i)] for i in range(1, 121)]
    service = _create_mock_service({"range": "Sheet1!A1:A120", "values": rows})

    result = await _call_read_sheet_values(service)

    assert "Successfully read 120 rows" in result
    # Every fetched row must be rendered, not just the endpoints.
    for i in range(1, 121):
        assert f"Row {i:2d}: ['{i}']" in result
    assert result.count("Row ") == 120
    assert "more rows" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("requested_range", "expected_range"),
    [
        ("A:Z", "A1:Z1000"),
        ("Sheet1!A:B", "Sheet1!A1:B1000"),
        ("Sheet1!A1:A5000", "Sheet1!A1:A1000"),
        ("'My Custom Sheet'", "'My Custom Sheet'!1:1000"),
    ],
)
async def test_read_sheet_values_clamps_range_before_api_call(
    requested_range, expected_range
):
    rows = [[str(i)] for i in range(1, 51)]
    get_mock = Mock(
        return_value=Mock(
            execute=Mock(return_value={"range": expected_range, "values": rows})
        )
    )
    service = Mock()
    service.spreadsheets().values().get = get_mock

    result = await _unwrap(read_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="spreadsheet-123",
        range_name=requested_range,
    )

    get_mock.assert_called_once_with(
        spreadsheetId="spreadsheet-123", range=expected_range
    )
    assert "Successfully read 50 rows" in result
    assert f"clamped to '{expected_range}'" in result
    assert "max 1000 rows" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("range_name", ["MyNamedRange", "Sheet1"])
async def test_read_sheet_values_preserves_ambiguous_bare_range(range_name):
    get_mock = Mock(
        return_value=Mock(
            execute=Mock(return_value={"range": "Sheet1!A1:A1", "values": [["value"]]})
        )
    )
    service = Mock()
    service.spreadsheets().values().get = get_mock

    result = await _unwrap(read_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="spreadsheet-123",
        range_name=range_name,
    )

    get_mock.assert_called_once_with(spreadsheetId="spreadsheet-123", range=range_name)
    assert "Successfully read 1 rows" in result
    assert "was clamped" not in result
