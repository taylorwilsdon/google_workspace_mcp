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
    kwargs = {
        "service": service,
        "user_google_email": "user@example.com",
        "spreadsheet_id": "spreadsheet-123",
        "range_name": "Sheet1!A1:A1",
        **overrides,
    }
    return await _unwrap(read_sheet_values)(**kwargs)


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
    assert "Row  1 (sheet row 1): ['1']" in result
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
        assert f"Row {i:2d} (sheet row {i}): ['{i}']" in result
    assert result.count("Row ") == 120
    assert "more rows" not in result


@pytest.mark.asyncio
async def test_read_sheet_values_labels_rows_with_sheet_row_numbers():
    service = _create_mock_service(
        {"range": "Sheet1!A13:B14", "values": [["a", "b"], ["c", "d"]]}
    )

    result = await _call_read_sheet_values(service, range_name="Sheet1!A13:B14")

    assert "Row  1 (sheet row 13): ['a', 'b']" in result
    assert "Row  2 (sheet row 14): ['c', 'd']" in result


@pytest.mark.asyncio
async def test_read_sheet_values_returns_every_hyperlink():
    rows = [[f"SOP {r}", "x"] for r in range(13, 43)]
    service = _create_mock_service({"range": "Sheet1!A13:B42", "values": rows})
    service.spreadsheets().get().execute = Mock(
        return_value={
            "sheets": [
                {
                    "properties": {"title": "Sheet1"},
                    "data": [
                        {
                            "startRow": 12,
                            "startColumn": 0,
                            "rowData": [
                                {
                                    "values": [
                                        {},
                                        {"hyperlink": f"https://example.com/{r}"},
                                    ]
                                }
                                for r in range(13, 43)
                            ],
                        }
                    ],
                }
            ]
        }
    )

    result = await _call_read_sheet_values(
        service, range_name="Sheet1!A13:B42", include_hyperlinks=True
    )

    for r in range(13, 43):
        assert f"Row {r - 12:2d} (sheet row {r}): ['SOP {r}', 'x']" in result
        assert f"- Sheet1!B{r}: https://example.com/{r}" in result
    assert "more hyperlinks" not in result


@pytest.mark.asyncio
async def test_read_sheet_values_reports_skipped_hyperlinks_for_large_ranges():
    rows = [[str(c) for c in range(10)] for _ in range(600)]
    service = _create_mock_service({"range": "Sheet1!A1:J600", "values": rows})
    grid_get = Mock()
    service.spreadsheets().get = grid_get

    result = await _call_read_sheet_values(
        service, range_name="Sheet1!A1:J600", include_hyperlinks=True
    )

    grid_get.assert_not_called()
    assert (
        "Hyperlinks not fetched for range 'Sheet1!A1:J600': 6000 cells exceeds "
        "the 5000-cell limit. Read a smaller range."
    ) in result


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
