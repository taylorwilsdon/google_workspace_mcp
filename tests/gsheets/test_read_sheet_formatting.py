"""Tests for read_sheet_formatting tool."""

from unittest.mock import Mock

import pytest

from gsheets.sheets_tools import read_sheet_formatting


def _create_mock_service(response):
    """Create a Sheets service mock returning the given get() response."""
    mock_service = Mock()
    mock_service.spreadsheets().get().execute = Mock(return_value=response)
    return mock_service


async def _call_read_sheet_formatting(service, **overrides):
    """Call the undecorated implementation to keep auth out of unit tests."""
    impl = read_sheet_formatting.__wrapped__.__wrapped__
    defaults = {
        "service": service,
        "user_google_email": "user@example.com",
        "spreadsheet_id": "spreadsheet-123",
        "range_name": "Sheet1!A1:C3",
    }
    defaults.update(overrides)
    return await impl(**defaults)


def _make_cell(**format_overrides):
    """Build a cell dict with the given effectiveFormat overrides."""
    return {"effectiveFormat": format_overrides}


def _make_response(rows, start_row=0, start_col=0, sheet_title="Sheet1"):
    """Build a spreadsheets.get response with the given row data."""
    return {
        "sheets": [
            {
                "properties": {"title": sheet_title},
                "data": [
                    {
                        "startRow": start_row,
                        "startColumn": start_col,
                        "rowData": [{"values": cells} for cells in rows],
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_reports_background_color_as_hex():
    service = _create_mock_service(
        _make_response(
            [[_make_cell(backgroundColor={"red": 0.957, "green": 0.8, "blue": 0.8})]]
        )
    )

    result = await _call_read_sheet_formatting(service)

    assert "A1: bg=#F4CCCC" in result


@pytest.mark.asyncio
async def test_skips_default_cells():
    service = _create_mock_service(
        _make_response(
            [
                [
                    _make_cell(
                        backgroundColor={"red": 1.0, "green": 1.0, "blue": 1.0},
                        textFormat={
                            "foregroundColor": {"red": 0, "green": 0, "blue": 0},
                            "bold": False,
                            "italic": False,
                        },
                    ),
                    _make_cell(
                        backgroundColor={"red": 0.957, "green": 0.8, "blue": 0.8}
                    ),
                ]
            ]
        )
    )

    result = await _call_read_sheet_formatting(service)

    assert "  A1:" not in result
    assert "B1: bg=#F4CCCC" in result


@pytest.mark.asyncio
async def test_reports_text_styles():
    service = _create_mock_service(
        _make_response(
            [
                [
                    _make_cell(
                        textFormat={
                            "foregroundColor": {
                                "red": 1.0,
                                "green": 0,
                                "blue": 0,
                            },
                            "bold": True,
                            "italic": True,
                            "fontSize": 14,
                        }
                    )
                ]
            ]
        )
    )

    result = await _call_read_sheet_formatting(service)

    assert "text_color=#FF0000" in result
    assert "bold" in result
    assert "italic" in result
    assert "font_size=14" in result


@pytest.mark.asyncio
async def test_reports_alignment_wrap_and_number_format():
    service = _create_mock_service(
        _make_response(
            [
                [
                    _make_cell(
                        horizontalAlignment="CENTER",
                        verticalAlignment="MIDDLE",
                        wrapStrategy="WRAP",
                        numberFormat={"type": "CURRENCY"},
                    )
                ]
            ]
        )
    )

    result = await _call_read_sheet_formatting(service)

    assert "h_align=CENTER" in result
    assert "v_align=MIDDLE" in result
    assert "wrap=WRAP" in result
    assert "number_format=CURRENCY" in result


@pytest.mark.asyncio
async def test_cell_references_account_for_start_offsets():
    service = _create_mock_service(
        _make_response(
            [[_make_cell(backgroundColor={"red": 0.85, "green": 0.92, "blue": 0.83})]],
            start_row=4,
            start_col=2,
        )
    )

    result = await _call_read_sheet_formatting(service, range_name="Sheet1!C5")

    assert "C5: bg=#D8EAD3" in result


@pytest.mark.asyncio
async def test_double_letter_columns_are_built_correctly():
    cells_in_row = [None] * 27
    cells_in_row[26] = _make_cell(
        backgroundColor={"red": 0.957, "green": 0.8, "blue": 0.8}
    )
    cells_in_row = [c if c is not None else _make_cell() for c in cells_in_row]
    service = _create_mock_service(_make_response([cells_in_row]))

    result = await _call_read_sheet_formatting(service)

    assert "AA1: bg=#F4CCCC" in result


@pytest.mark.asyncio
async def test_returns_friendly_message_when_all_cells_are_default():
    service = _create_mock_service(
        _make_response(
            [[_make_cell(backgroundColor={"red": 1.0, "green": 1.0, "blue": 1.0})]]
        )
    )

    result = await _call_read_sheet_formatting(service)

    assert "default formatting" in result


@pytest.mark.asyncio
async def test_returns_friendly_message_when_no_sheets_returned():
    service = _create_mock_service({"sheets": []})

    result = await _call_read_sheet_formatting(service)

    assert "No data found" in result


@pytest.mark.asyncio
async def test_handles_missing_effective_format_gracefully():
    service = _create_mock_service(
        _make_response(
            [
                [
                    {},
                    _make_cell(
                        backgroundColor={"red": 0.957, "green": 0.8, "blue": 0.8}
                    ),
                ]
            ]
        )
    )

    result = await _call_read_sheet_formatting(service)

    assert "B1: bg=#F4CCCC" in result
