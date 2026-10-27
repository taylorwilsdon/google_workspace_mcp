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
    assert "\n1\t1" in result
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
    lines = result.split("\n")
    for i in range(1, 121):
        assert f"{i}\t{i}" in lines
    assert len([line for line in lines if "\t" in line]) == 120
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


@pytest.mark.asyncio
async def test_read_sheet_values_numbers_rows_from_the_sheet():
    service = _create_mock_service(
        {
            "range": "Sheet1!C41:G700",
            "values": [["Name", "Score", ""], [], ["Ann", "", "3"]],
        }
    )

    result = await _call_read_sheet_values(service, range_name="Sheet1!C41:G700")

    assert result.split("\n")[1:] == [
        "Each line: sheet row number, then tab-separated cells from column C.",
        "41\tName\tScore",
        "42",
        "43\tAnn\t\t3",
    ]
    assert "Row " not in result
    assert "['" not in result


@pytest.mark.asyncio
async def test_read_sheet_values_escapes_tabs_and_line_breaks_in_cells():
    service = _create_mock_service(
        {"range": "'My Sheet'!A1:B1", "values": [["two\nlines", "a\tb\r\nc"]]}
    )

    result = await _call_read_sheet_values(service)

    legend, row = result.split("\n")[1:]
    assert legend == (
        "Each line: sheet row number, then tab-separated cells from column A."
        " Tabs and line breaks inside a cell are shown as \\t and \\n."
    )
    assert row == "1\ttwo\\nlines\ta\\tb\\r\\nc"


@pytest.mark.asyncio
async def test_read_sheet_values_falls_back_to_range_relative_numbers():
    service = _create_mock_service({"values": [["x"], ["y"]]})

    result = await _call_read_sheet_values(service, range_name="Totals")

    assert result.split("\n")[1:] == [
        "Each line: row number within the range, then tab-separated cells.",
        "1\tx",
        "2\ty",
    ]
