import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets.sheets_tools import _read_data_validation_impl


def _service_with(rowdata, start_row=1, start_col=2, title="Sheet1"):
    mock_service = Mock()
    mock_service.spreadsheets().get().execute = Mock(
        return_value={
            "sheets": [
                {
                    "properties": {"title": title},
                    "data": [
                        {
                            "startRow": start_row,
                            "startColumn": start_col,
                            "rowData": rowdata,
                        }
                    ],
                }
            ]
        }
    )
    return mock_service


@pytest.mark.asyncio
async def test_reads_list_and_range_rules_with_addresses():
    rowdata = [
        {
            "values": [
                {
                    "dataValidation": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "Open"},
                                {"userEnteredValue": "Won"},
                            ],
                        },
                        "strict": True,
                        "showCustomUi": True,
                    }
                }
            ]
        },
        {"values": [{}]},  # no validation on this cell
        {
            "values": [
                {
                    "dataValidation": {
                        "condition": {
                            "type": "ONE_OF_RANGE",
                            "values": [{"userEnteredValue": "=Lists!A1:A10"}],
                        },
                        "strict": False,
                        "showCustomUi": True,
                    }
                }
            ]
        },
    ]
    service = _service_with(rowdata, start_row=1, start_col=2)  # origin C2
    result = await _read_data_validation_impl(
        service=service, spreadsheet_id="ss_1", range_name="Sheet1!C2:C4"
    )

    cells = result["cells_with_validation"]
    assert result["cells_checked"] == 3
    assert len(cells) == 2

    first = cells[0]
    assert first["address"] == "Sheet1!C2"
    assert first["type"] == "ONE_OF_LIST"
    assert first["values"] == ["Open", "Won"]
    assert first["strict"] is True
    assert first["show_dropdown"] is True

    second = cells[1]
    assert second["address"] == "Sheet1!C4"
    assert second["type"] == "ONE_OF_RANGE"
    assert second["values"] == ["=Lists!A1:A10"]
    assert second["strict"] is False


@pytest.mark.asyncio
async def test_no_validation_returns_empty_list():
    rowdata = [{"values": [{}]}, {"values": [{}]}]
    service = _service_with(rowdata)
    result = await _read_data_validation_impl(
        service=service, spreadsheet_id="ss_1", range_name="Sheet1!C2:C3"
    )
    assert result["cells_with_validation"] == []
    assert result["cells_checked"] == 2
