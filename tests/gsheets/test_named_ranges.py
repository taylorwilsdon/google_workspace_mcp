import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets.sheets_tools import (
    _list_named_ranges_impl,
    _manage_named_range_impl,
)
from core.utils import UserInputError


def _service(named=None, sheets=None, batch_reply=None):
    m = Mock()
    m.spreadsheets().get().execute = Mock(
        return_value={
            "namedRanges": named or [],
            "sheets": sheets or [{"properties": {"sheetId": 0, "title": "Sheet1"}}],
        }
    )
    m.spreadsheets().batchUpdate().execute = Mock(return_value=batch_reply or {})
    return m


def _batch_request(service):
    return service.spreadsheets().batchUpdate.call_args[1]["body"]["requests"][0]


@pytest.mark.asyncio
async def test_list_named_ranges_renders_a1():
    named = [
        {
            "name": "ref_DeviceSKU",
            "namedRangeId": "nr123",
            "range": {
                "sheetId": 0,
                "startRowIndex": 90,
                "endRowIndex": 145,
                "startColumnIndex": 2,
                "endColumnIndex": 3,
            },
        }
    ]
    service = _service(named=named, sheets=[{"properties": {"sheetId": 0, "title": "DEV REF"}}])
    result = await _list_named_ranges_impl(service=service, spreadsheet_id="ss_1")
    assert len(result["named_ranges"]) == 1
    nr = result["named_ranges"][0]
    assert nr["name"] == "ref_DeviceSKU"
    assert nr["named_range_id"] == "nr123"
    assert "C91:C145" in nr["range"]


@pytest.mark.asyncio
async def test_create_named_range():
    service = _service(
        batch_reply={"replies": [{"addNamedRange": {"namedRange": {"namedRangeId": "newid1"}}}]}
    )
    result = await _manage_named_range_impl(
        service=service,
        spreadsheet_id="ss_1",
        action="create",
        name="ref_X",
        range_name="Sheet1!A1:A10",
    )
    assert result["named_range_id"] == "newid1"
    req = _batch_request(service)["addNamedRange"]["namedRange"]
    assert req["name"] == "ref_X"
    assert req["range"]["sheetId"] == 0


@pytest.mark.asyncio
async def test_delete_named_range_by_name():
    service = _service(named=[{"name": "ref_X", "namedRangeId": "id9"}])
    await _manage_named_range_impl(
        service=service, spreadsheet_id="ss_1", action="delete", name="ref_X"
    )
    assert _batch_request(service)["deleteNamedRange"]["namedRangeId"] == "id9"


@pytest.mark.asyncio
async def test_update_repoint_named_range():
    service = _service(named=[{"name": "ref_X", "namedRangeId": "id9"}])
    await _manage_named_range_impl(
        service=service,
        spreadsheet_id="ss_1",
        action="update",
        name="ref_X",
        range_name="Sheet1!B1:B5",
    )
    upd = _batch_request(service)["updateNamedRange"]
    assert upd["namedRange"]["namedRangeId"] == "id9"
    assert "range" in upd["fields"]


@pytest.mark.asyncio
async def test_create_requires_range():
    service = _service()
    with pytest.raises(UserInputError):
        await _manage_named_range_impl(
            service=service, spreadsheet_id="ss_1", action="create", name="ref_X"
        )


@pytest.mark.asyncio
async def test_delete_unknown_name_errors():
    service = _service(named=[])
    with pytest.raises(UserInputError):
        await _manage_named_range_impl(
            service=service, spreadsheet_id="ss_1", action="delete", name="missing"
        )
