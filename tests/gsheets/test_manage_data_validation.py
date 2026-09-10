import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets.sheets_tools import _manage_data_validation_impl
from core.utils import UserInputError


def create_mock_service():
    mock_service = Mock()
    mock_service.spreadsheets().get().execute = Mock(
        return_value={
            "sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]
        }
    )
    mock_service.spreadsheets().batchUpdate().execute = Mock(return_value={})
    return mock_service


def _batch_body(mock_service):
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    return call_args[1]["body"]


@pytest.mark.asyncio
async def test_set_list_dropdown_builds_one_of_list():
    mock_service = create_mock_service()
    result = await _manage_data_validation_impl(
        service=mock_service,
        spreadsheet_id="ss_123",
        range_name="Sheet1!C2:C100",
        values=["Open", "Won", "Lost"],
    )
    assert result["spreadsheet_id"] == "ss_123"
    dv = _batch_body(mock_service)["requests"][0]["setDataValidation"]
    assert dv["range"]["sheetId"] == 0
    assert dv["rule"]["condition"]["type"] == "ONE_OF_LIST"
    assert dv["rule"]["condition"]["values"] == [
        {"userEnteredValue": "Open"},
        {"userEnteredValue": "Won"},
        {"userEnteredValue": "Lost"},
    ]
    assert dv["rule"]["showCustomUi"] is True
    assert dv["rule"]["strict"] is True


@pytest.mark.asyncio
async def test_set_range_dropdown_adds_equals_prefix():
    mock_service = create_mock_service()
    await _manage_data_validation_impl(
        service=mock_service,
        spreadsheet_id="ss_123",
        range_name="A1:A10",
        source_range="Lists!A1:A20",
    )
    dv = _batch_body(mock_service)["requests"][0]["setDataValidation"]
    assert dv["rule"]["condition"]["type"] == "ONE_OF_RANGE"
    assert dv["rule"]["condition"]["values"] == [
        {"userEnteredValue": "=Lists!A1:A20"}
    ]


@pytest.mark.asyncio
async def test_clear_omits_rule():
    mock_service = create_mock_service()
    await _manage_data_validation_impl(
        service=mock_service,
        spreadsheet_id="ss_123",
        range_name="Sheet1!C2:C100",
        action="clear",
    )
    dv = _batch_body(mock_service)["requests"][0]["setDataValidation"]
    assert "rule" not in dv
    assert dv["range"]["sheetId"] == 0


@pytest.mark.asyncio
async def test_set_requires_exactly_one_source():
    mock_service = create_mock_service()
    # neither values nor source_range
    with pytest.raises(UserInputError):
        await _manage_data_validation_impl(
            service=mock_service,
            spreadsheet_id="ss_123",
            range_name="A1:A10",
        )
    # both provided
    with pytest.raises(UserInputError):
        await _manage_data_validation_impl(
            service=mock_service,
            spreadsheet_id="ss_123",
            range_name="A1:A10",
            values=["X"],
            source_range="Lists!A1:A5",
        )


@pytest.mark.asyncio
async def test_invalid_action_rejected():
    mock_service = create_mock_service()
    with pytest.raises(UserInputError):
        await _manage_data_validation_impl(
            service=mock_service,
            spreadsheet_id="ss_123",
            range_name="A1:A10",
            action="delete",
        )
