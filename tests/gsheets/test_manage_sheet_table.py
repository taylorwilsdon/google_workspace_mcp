"""
Unit tests for the Google Sheets manage_sheet_table tool.

Covers create/update/delete of native Sheets tables (addTable / updateTable /
deleteTable), column typing, dropdown column validation, and input validation.
"""

import os
import sys

import pytest
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_tools import _manage_sheet_table_impl


def create_mock_service(tables=None):
    """Mock Sheets service exposing one sheet, plus optional existing tables."""
    mock_service = Mock()

    sheet = {"properties": {"sheetId": 0, "title": "Sheet1"}}
    if tables is not None:
        sheet["tables"] = tables

    mock_service.spreadsheets().get().execute = Mock(return_value={"sheets": [sheet]})
    mock_service.spreadsheets().batchUpdate().execute = Mock(
        return_value={
            "replies": [{"addTable": {"table": {"tableId": "tbl_generated"}}}]
        }
    )
    return mock_service


def get_requests(mock_service):
    """Pull the requests list out of the last batchUpdate call."""
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    return call_args[1]["body"]["requests"]


# --------------------------------------------------------------------------
# create
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_table_sends_add_table_with_grid_range():
    """A minimal create maps the A1 range onto a GridRange and names the table."""
    mock_service = create_mock_service()

    result = await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="Sheet1!A1:C10",
    )

    requests = get_requests(mock_service)
    assert len(requests) == 1

    table = requests[0]["addTable"]["table"]
    assert table["name"] == "Pipeline"
    assert table["range"] == {
        "sheetId": 0,
        "startRowIndex": 0,
        "endRowIndex": 10,
        "startColumnIndex": 0,
        "endColumnIndex": 3,
    }
    assert "tableId" not in table, "tableId must be left for Sheets to generate"
    assert result["table_id"] == "tbl_generated"
    assert result["action"] == "create"


@pytest.mark.asyncio
async def test_create_table_assigns_sequential_column_indexes():
    """columnIndex is table-relative and inferred from position when omitted."""
    mock_service = create_mock_service()

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="Sheet1!A1:C10",
        column_properties=[
            {"columnName": "Client", "columnType": "TEXT"},
            {"columnName": "Fee", "columnType": "CURRENCY"},
            {"columnName": "Signed", "columnType": "DATE"},
        ],
    )

    cols = get_requests(mock_service)[0]["addTable"]["table"]["columnProperties"]
    assert [c["columnIndex"] for c in cols] == [0, 1, 2]
    assert [c["columnName"] for c in cols] == ["Client", "Fee", "Signed"]
    assert [c["columnType"] for c in cols] == ["TEXT", "CURRENCY", "DATE"]


@pytest.mark.asyncio
async def test_create_table_honours_explicit_column_index():
    """An explicit columnIndex overrides the positional default."""
    mock_service = create_mock_service()

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="Sheet1!A1:C10",
        column_properties=[
            {"columnIndex": 2, "columnName": "Third", "columnType": "TEXT"}
        ],
    )

    cols = get_requests(mock_service)[0]["addTable"]["table"]["columnProperties"]
    assert cols[0]["columnIndex"] == 2


@pytest.mark.asyncio
async def test_create_dropdown_column_builds_one_of_list_validation():
    """A DROPDOWN column with values becomes a ONE_OF_LIST validation rule."""
    mock_service = create_mock_service()

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="Sheet1!A1:C10",
        column_properties=[
            {
                "columnName": "Status",
                "columnType": "DROPDOWN",
                "values": ["Open", "Won", "Lost"],
            }
        ],
    )

    col = get_requests(mock_service)[0]["addTable"]["table"]["columnProperties"][0]
    condition = col["dataValidationRule"]["condition"]
    assert condition["type"] == "ONE_OF_LIST"
    assert condition["values"] == [
        {"userEnteredValue": "Open"},
        {"userEnteredValue": "Won"},
        {"userEnteredValue": "Lost"},
    ]


@pytest.mark.asyncio
async def test_create_accepts_json_encoded_column_properties():
    """column_properties may arrive as a JSON string from an MCP client."""
    mock_service = create_mock_service()

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="Sheet1!A1:B10",
        column_properties='[{"columnName": "A", "columnType": "TEXT"}]',
    )

    cols = get_requests(mock_service)[0]["addTable"]["table"]["columnProperties"]
    assert cols[0]["columnName"] == "A"


@pytest.mark.asyncio
async def test_create_applies_banding_colors():
    """Header/footer/band colors are converted to rowsProperties color styles."""
    mock_service = create_mock_service()

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="Sheet1!A1:C10",
        header_color="#4285f4",
        first_band_color="#ffffff",
        second_band_color="#f1f3f4",
    )

    rows_props = get_requests(mock_service)[0]["addTable"]["table"]["rowsProperties"]
    assert "headerColorStyle" in rows_props
    assert "firstBandColorStyle" in rows_props
    assert "secondBandColorStyle" in rows_props
    assert rows_props["headerColorStyle"]["rgbColor"]["red"] == pytest.approx(
        0x42 / 255, abs=1e-3
    )
    assert "footerColorStyle" not in rows_props


@pytest.mark.asyncio
async def test_create_without_sheet_prefix_uses_first_sheet():
    """A bare A1 range resolves against the first sheet."""
    mock_service = create_mock_service()

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="create",
        table_name="Pipeline",
        range_name="A1:C10",
    )

    table = get_requests(mock_service)[0]["addTable"]["table"]
    assert table["range"]["sheetId"] == 0


# --------------------------------------------------------------------------
# update
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_renames_table_with_narrow_field_mask():
    """Updating only the name must not send a wildcard field mask."""
    mock_service = create_mock_service(tables=[{"tableId": "tbl_1", "name": "Old"}])

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="update",
        table_id="tbl_1",
        table_name="New",
    )

    req = get_requests(mock_service)[0]["updateTable"]
    assert req["table"]["tableId"] == "tbl_1"
    assert req["table"]["name"] == "New"
    assert req["fields"] == "name"


@pytest.mark.asyncio
async def test_update_range_and_columns_builds_combined_mask():
    """Each supplied field appears in the mask, comma separated and sorted."""
    mock_service = create_mock_service(tables=[{"tableId": "tbl_1", "name": "Old"}])

    await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="update",
        table_id="tbl_1",
        range_name="Sheet1!A1:D20",
        column_properties=[{"columnName": "A", "columnType": "TEXT"}],
    )

    req = get_requests(mock_service)[0]["updateTable"]
    assert set(req["fields"].split(",")) == {"range", "columnProperties"}
    assert req["table"]["range"]["endColumnIndex"] == 4


@pytest.mark.asyncio
async def test_update_requires_at_least_one_field():
    """An update that changes nothing is a caller error, not a no-op API call."""
    mock_service = create_mock_service(tables=[{"tableId": "tbl_1", "name": "Old"}])
    mock_service.spreadsheets().batchUpdate.reset_mock()

    with pytest.raises(UserInputError, match="at least one"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="update",
            table_id="tbl_1",
        )

    mock_service.spreadsheets().batchUpdate.assert_not_called()


# --------------------------------------------------------------------------
# delete
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_table_sends_delete_request():
    mock_service = create_mock_service(tables=[{"tableId": "tbl_1", "name": "Old"}])

    result = await _manage_sheet_table_impl(
        service=mock_service,
        spreadsheet_id="ss_1",
        action="delete",
        table_id="tbl_1",
    )

    requests = get_requests(mock_service)
    assert requests == [{"deleteTable": {"tableId": "tbl_1"}}]
    assert result["action"] == "delete"


# --------------------------------------------------------------------------
# input validation
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_action_rejected():
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="action"):
        await _manage_sheet_table_impl(
            service=mock_service, spreadsheet_id="ss_1", action="frobnicate"
        )


@pytest.mark.asyncio
async def test_create_requires_name_and_range():
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="table_name"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            range_name="Sheet1!A1:C10",
        )

    with pytest.raises(UserInputError, match="range_name"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            table_name="Pipeline",
        )


@pytest.mark.asyncio
async def test_delete_and_update_require_table_id():
    mock_service = create_mock_service()

    for action in ("update", "delete"):
        with pytest.raises(UserInputError, match="table_id"):
            await _manage_sheet_table_impl(
                service=mock_service, spreadsheet_id="ss_1", action=action
            )


@pytest.mark.asyncio
async def test_invalid_column_type_rejected():
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="columnType"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            table_name="Pipeline",
            range_name="Sheet1!A1:C10",
            column_properties=[{"columnName": "X", "columnType": "NOT_A_TYPE"}],
        )


@pytest.mark.asyncio
async def test_values_on_non_dropdown_column_rejected():
    """The API only allows column validation on DROPDOWN columns."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="DROPDOWN"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            table_name="Pipeline",
            range_name="Sheet1!A1:C10",
            column_properties=[
                {"columnName": "X", "columnType": "TEXT", "values": ["a", "b"]}
            ],
        )


@pytest.mark.asyncio
async def test_dropdown_column_requires_values():
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="values"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            table_name="Pipeline",
            range_name="Sheet1!A1:C10",
            column_properties=[{"columnName": "X", "columnType": "DROPDOWN"}],
        )


@pytest.mark.asyncio
async def test_non_integer_column_index_rejected():
    """A bad columnIndex must error loudly, not silently fall back to position."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="columnIndex"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            table_name="Pipeline",
            range_name="Sheet1!A1:C10",
            column_properties=[
                {"columnIndex": "second", "columnName": "X", "columnType": "TEXT"}
            ],
        )


@pytest.mark.asyncio
async def test_invalid_create_args_do_not_hit_the_api():
    """Argument validation must happen before any network round trip."""
    mock_service = create_mock_service()
    mock_service.spreadsheets().get.reset_mock()

    with pytest.raises(UserInputError):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            range_name="Sheet1!A1:C10",  # table_name missing
        )

    mock_service.spreadsheets().get.assert_not_called()


@pytest.mark.asyncio
async def test_malformed_json_column_properties_rejected():
    mock_service = create_mock_service()

    with pytest.raises(UserInputError, match="column_properties"):
        await _manage_sheet_table_impl(
            service=mock_service,
            spreadsheet_id="ss_1",
            action="create",
            table_name="Pipeline",
            range_name="Sheet1!A1:C10",
            column_properties="{not json",
        )
