"""
Unit tests for Google Sheets Smart Chips (Jetons intelligents) support.

Tests insertion and extraction of Google Drive and People smart chips.
"""

import os
import sys
from unittest.mock import Mock

import pytest
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gsheets.sheets_helpers import (
    _create_chip_cell_data,
    _extract_cell_smart_chips_from_grid,
    _normalize_chips_input,
)
from gsheets.sheets_tools import (
    MAX_DRIVE_CHIPS_PER_BATCH,
    _write_smart_chips,
    modify_sheet_values,
    read_sheet_values,
)


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def create_mock_sheets_service(sheets_metadata=None):
    """Create a mock Sheets service for testing."""
    mock_service = Mock()
    if sheets_metadata is None:
        sheets_metadata = {
            "sheets": [
                {"properties": {"sheetId": 0, "title": "Sheet1"}},
                {"properties": {"sheetId": 12345, "title": "Elections"}},
            ]
        }
    spreadsheets_mock = Mock()
    mock_service.spreadsheets.return_value = spreadsheets_mock
    spreadsheets_mock.get.return_value.execute.return_value = sheets_metadata
    spreadsheets_mock.batchUpdate.return_value.execute.return_value = {}
    return mock_service


# ---------------------------------------------------------------------------
# Tests for _create_chip_cell_data
# ---------------------------------------------------------------------------


def test_create_chip_cell_data_drive_url():
    """Drive URL creates a richLinkProperties chip with stringValue '@'."""
    url = "https://drive.google.com/drive/folders/0Bz_I3qW-b3gTRGdwQzhHTUFoWUk"
    cell = _create_chip_cell_data(url)
    assert cell is not None
    assert cell["userEnteredValue"] == {"stringValue": "@"}
    assert len(cell["chipRuns"]) == 1
    assert cell["chipRuns"][0]["startIndex"] == 0
    assert cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"] == url


def test_create_chip_cell_data_person_email():
    """Email string creates a personProperties chip."""
    email = "antoine.beaubien@gmail.com"
    cell = _create_chip_cell_data(email)
    assert cell is not None
    assert cell["userEnteredValue"] == {"stringValue": "@"}
    assert len(cell["chipRuns"]) == 1
    assert cell["chipRuns"][0]["chip"]["personProperties"]["email"] == email


def test_create_chip_cell_data_drive_id():
    """Raw Drive ID is converted to open?id= URL."""
    drive_id = "0Bz_I3qW-b3gTRGdwQzhHTUFoWUk"
    cell = _create_chip_cell_data(drive_id)
    assert cell is not None
    assert (
        cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"]
        == f"https://drive.google.com/open?id={drive_id}"
    )


def test_create_chip_cell_data_dict_folder_id():
    """Dictionary with folder_id creates a Drive folder URL."""
    folder_id = "folder12345"
    cell = _create_chip_cell_data({"folder_id": folder_id})
    assert cell is not None
    assert (
        cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"]
        == f"https://drive.google.com/drive/folders/{folder_id}"
    )


def test_create_chip_cell_data_dict_person():
    """Dictionary with type=person and email."""
    cell = _create_chip_cell_data({"type": "person", "email": "test@example.com"})
    assert cell is not None
    assert (
        cell["chipRuns"][0]["chip"]["personProperties"]["email"] == "test@example.com"
    )


def test_create_chip_cell_data_multi_chips():
    """List of chips in a single cell creates multiple chipRuns with '@ @' stringValue."""
    chips = [
        "https://drive.google.com/folder1",
        "antoine@example.com",
    ]
    cell = _create_chip_cell_data(chips)
    assert cell is not None
    assert cell["userEnteredValue"] == {"stringValue": "@ @"}
    assert len(cell["chipRuns"]) == 2
    assert cell["chipRuns"][0]["startIndex"] == 0
    assert (
        cell["chipRuns"][0]["chip"]["richLinkProperties"]["uri"]
        == "https://drive.google.com/folder1"
    )
    assert cell["chipRuns"][1]["startIndex"] == 2
    assert (
        cell["chipRuns"][1]["chip"]["personProperties"]["email"]
        == "antoine@example.com"
    )


def test_create_chip_cell_data_empty():
    """Empty or None items return None (skipped)."""
    assert _create_chip_cell_data(None) is None
    assert _create_chip_cell_data("") is None


def test_create_chip_cell_data_invalid_type():
    """Invalid chip_type raises UserInputError."""
    with pytest.raises(UserInputError, match="Unknown chip_type 'calendar'"):
        _create_chip_cell_data("something", default_type="calendar")


def test_create_chip_cell_data_uninferrable_string_raises():
    """A string that is not a URL, email, or Drive ID is rejected, not sent as a URI."""
    with pytest.raises(UserInputError, match="Cannot infer chip type"):
        _create_chip_cell_data("hello")


def test_create_chip_cell_data_missing_uri():
    """Drive chip dict without uri or id raises UserInputError."""
    with pytest.raises(UserInputError, match="Drive chip requires a URI"):
        _create_chip_cell_data({"type": "drive"})


def test_create_chip_cell_data_missing_email():
    """Person chip dict without email raises UserInputError."""
    with pytest.raises(UserInputError, match="Person chip requires an email"):
        _create_chip_cell_data({"type": "person"})


# ---------------------------------------------------------------------------
# Tests for _normalize_chips_input
# ---------------------------------------------------------------------------


def test_normalize_chips_input_single_cell():
    """Single cell target with single URL."""
    url = "https://drive.google.com/file/d/123/view"
    updates = _normalize_chips_input(
        chips=url,
        start_row=2,
        end_row=2,
        start_col=5,
        end_col=5,
    )
    assert len(updates) == 1
    r, c, data = updates[0]
    assert r == 2
    assert c == 5
    assert data["chipRuns"][0]["chip"]["richLinkProperties"]["uri"] == url


def test_normalize_chips_input_column_range():
    """1D list for a column range F3:F5 maps vertically."""
    urls = [
        "https://drive.google.com/folder1",
        "https://drive.google.com/folder2",
        "https://drive.google.com/folder3",
    ]
    updates = _normalize_chips_input(
        chips=urls,
        start_row=2,
        end_row=4,
        start_col=5,
        end_col=5,
    )
    assert len(updates) == 3
    assert [u[0] for u in updates] == [2, 3, 4]
    assert [u[1] for u in updates] == [5, 5, 5]


def test_normalize_chips_input_row_range():
    """1D list for a row range A1:C1 maps horizontally."""
    emails = ["a@example.com", "b@example.com", "c@example.com"]
    updates = _normalize_chips_input(
        chips=emails,
        start_row=0,
        end_row=0,
        start_col=0,
        end_col=2,
    )
    assert len(updates) == 3
    assert [u[0] for u in updates] == [0, 0, 0]
    assert [u[1] for u in updates] == [0, 1, 2]


def test_normalize_chips_input_json_string():
    """JSON string is automatically parsed."""
    json_str = '["https://drive.google.com/1", "https://drive.google.com/2"]'
    updates = _normalize_chips_input(
        chips=json_str,
        start_row=0,
        end_row=1,
        start_col=0,
        end_col=0,
    )
    assert len(updates) == 2


def test_normalize_chips_input_json_scalar_string():
    """A JSON-encoded single string is decoded rather than used verbatim."""
    updates = _normalize_chips_input(
        chips='"a@example.com"',
        start_row=0,
        end_row=0,
        start_col=0,
        end_col=0,
    )
    assert (
        updates[0][2]["chipRuns"][0]["chip"]["personProperties"]["email"]
        == "a@example.com"
    )


def test_normalize_chips_single_cell_multiple_chips():
    """Providing multiple chips to a single-cell target combines them into one cell."""
    updates = _normalize_chips_input(
        chips=["https://drive.google.com/1", "https://drive.google.com/2"],
        start_row=2,
        end_row=2,
        start_col=5,
        end_col=5,
    )
    assert len(updates) == 1
    r, c, cell_data = updates[0]
    assert r == 2
    assert c == 5
    assert cell_data["userEnteredValue"]["stringValue"] == "@ @"
    assert len(cell_data["chipRuns"]) == 2
    assert cell_data["chipRuns"][0]["startIndex"] == 0
    assert (
        cell_data["chipRuns"][0]["chip"]["richLinkProperties"]["uri"]
        == "https://drive.google.com/1"
    )
    assert cell_data["chipRuns"][1]["startIndex"] == 2
    assert (
        cell_data["chipRuns"][1]["chip"]["richLinkProperties"]["uri"]
        == "https://drive.google.com/2"
    )


def test_normalize_chips_range_with_multi_chips_per_cell():
    """Providing a list of chip lists to a column range creates multi-chip cells."""
    chips = [
        ["https://drive.google.com/1", "https://drive.google.com/2"],
        ["https://drive.google.com/3"],
    ]
    updates = _normalize_chips_input(
        chips=chips,
        start_row=2,
        end_row=3,
        start_col=5,
        end_col=5,
    )
    assert len(updates) == 2
    assert updates[0][0] == 2 and updates[0][1] == 5
    assert updates[0][2]["userEnteredValue"]["stringValue"] == "@ @"
    assert len(updates[0][2]["chipRuns"]) == 2
    assert updates[1][0] == 3 and updates[1][1] == 5
    assert updates[1][2]["userEnteredValue"]["stringValue"] == "@"
    assert len(updates[1][2]["chipRuns"]) == 1


def test_normalize_chips_vertical_overflow_raises_error():
    """Providing more chips than vertical capacity raises UserInputError."""
    with pytest.raises(UserInputError, match="exceeds vertical range capacity"):
        _normalize_chips_input(
            chips=["https://1", "https://2", "https://3", "https://4"],
            start_row=2,
            end_row=4,  # capacity 3
            start_col=5,
            end_col=5,
        )


def test_normalize_chips_horizontal_overflow_raises_error():
    """Providing more chips than horizontal capacity raises UserInputError."""
    with pytest.raises(UserInputError, match="exceeds horizontal range capacity"):
        _normalize_chips_input(
            chips=["https://1", "https://2", "https://3", "https://4"],
            start_row=0,
            end_row=0,
            start_col=0,
            end_col=2,  # capacity 3
        )


def test_normalize_chips_2d_overflow_raises_error():
    """Providing 2D chips exceeding row or col bounds raises UserInputError."""
    with pytest.raises(UserInputError, match="2D chips row count"):
        _normalize_chips_input(
            chips=[["https://1"], ["https://2"], ["https://3"]],
            start_row=0,
            end_row=1,  # 2 rows max
            start_col=0,
            end_col=1,
        )


def test_normalize_chips_2d_flat_overflow_raises_error():
    """Providing more chips than 2D grid capacity raises UserInputError."""
    with pytest.raises(UserInputError, match="exceeds 2D range capacity"):
        _normalize_chips_input(
            chips=["https://1", "https://2", "https://3", "https://4", "https://5"],
            start_row=0,
            end_row=1,
            start_col=0,
            end_col=1,
        )


# ---------------------------------------------------------------------------
# Tests for _write_smart_chips (Batching & Execution)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("chip_count", [3, 4])
async def test_write_smart_chips_row_only_range_capacity(chip_count):
    service = create_mock_sheets_service()
    kwargs = dict(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Sheet1!3:5",
        chips=[f"https://drive.google.com/file_{i}" for i in range(chip_count)],
    )

    if chip_count > 3:
        with pytest.raises(UserInputError, match="exceeds 2D range capacity"):
            await _write_smart_chips(**kwargs)
        service.spreadsheets().batchUpdate.assert_not_called()
    else:
        await _write_smart_chips(**kwargs)
        requests = service.spreadsheets().batchUpdate.call_args.kwargs["body"][
            "requests"
        ]
        assert [r["updateCells"]["range"]["startRowIndex"] for r in requests] == [
            2,
            3,
            4,
        ]
        assert all(r["updateCells"]["range"]["startColumnIndex"] == 0 for r in requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("preceding_cells", [0, 9])
@pytest.mark.parametrize("extra_chips", [0, 1])
async def test_write_smart_chips_per_cell_limit(preceding_cells, extra_chips):
    service = create_mock_sheets_service()
    cell_chips = [
        f"https://drive.google.com/file_{i}"
        for i in range(MAX_DRIVE_CHIPS_PER_BATCH + extra_chips)
    ]
    kwargs = dict(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name=f"Sheet1!A1:A{preceding_cells + 1}",
        chips=[["https://drive.google.com/first"]] * preceding_cells + [cell_chips],
    )

    if extra_chips:
        with pytest.raises(UserInputError, match="exceeds.*per-batch limit"):
            await _write_smart_chips(**kwargs)
        service.spreadsheets().batchUpdate.assert_not_called()
    else:
        result = await _write_smart_chips(**kwargs)
        assert (
            f"Successfully inserted {preceding_cells + len(cell_chips)} smart chip(s)"
            in result
        )
        for call in service.spreadsheets().batchUpdate.call_args_list:
            chip_count = sum(
                len(request["updateCells"]["rows"][0]["values"][0]["chipRuns"])
                for request in call.kwargs["body"]["requests"]
            )
            assert chip_count <= MAX_DRIVE_CHIPS_PER_BATCH


@pytest.mark.asyncio
async def test_write_smart_chips_single():
    """Test inserting a single chip into a cell."""
    service = create_mock_sheets_service()
    url = "https://drive.google.com/drive/folders/0Bz_I3qW-b3gTRGdwQzhHTUFoWUk"

    result = await _write_smart_chips(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Elections!F3",
        chips=url,
    )

    assert "Successfully inserted 1 smart chip(s)" in result
    assert "Elections!F3" in result

    # Check batchUpdate was called
    service.spreadsheets().batchUpdate.assert_called_once()
    call_args = service.spreadsheets().batchUpdate.call_args
    assert call_args[1]["spreadsheetId"] == "test_sheet_id"
    requests = call_args[1]["body"]["requests"]
    assert len(requests) == 1
    assert requests[0]["updateCells"]["range"]["sheetId"] == 12345
    assert requests[0]["updateCells"]["range"]["startRowIndex"] == 2
    assert requests[0]["updateCells"]["range"]["startColumnIndex"] == 5


@pytest.mark.asyncio
async def test_write_smart_chips_single_dict():
    """Test inserting a single dict chip."""
    service = create_mock_sheets_service()
    chip_dict = {"type": "drive", "uri": "https://drive.google.com/folders/0Bz_I3qW"}

    result = await _write_smart_chips(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Elections!F3",
        chips=chip_dict,
    )

    assert "Successfully inserted 1 smart chip(s)" in result
    assert "Elections!F3" in result


@pytest.mark.asyncio
async def test_write_smart_chips_batches_over_limit():
    """Test inserting 19 chips chunks them into batches of <= 8."""
    service = create_mock_sheets_service()
    urls = [f"https://drive.google.com/folder_{i}" for i in range(19)]

    result = await _write_smart_chips(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Sheet1!F3:F21",
        chips=urls,
    )

    assert "Successfully inserted 19 smart chip(s)" in result

    # 19 chips with BATCH_SIZE=8 -> 3 batchUpdate calls: 8 + 8 + 3
    batch_calls = service.spreadsheets().batchUpdate.call_args_list
    assert len(batch_calls) == 3
    assert len(batch_calls[0][1]["body"]["requests"]) == 8
    assert len(batch_calls[1][1]["body"]["requests"]) == 8
    assert len(batch_calls[2][1]["body"]["requests"]) == 3


@pytest.mark.asyncio
async def test_write_smart_chips_reports_partial_write_on_later_batch_failure():
    """A failure after a committed batch reports how many cells were already written."""
    service = create_mock_sheets_service()
    service.spreadsheets().batchUpdate.return_value.execute.side_effect = [
        {},
        HttpError(Mock(status=400), b"bad chip"),
    ]
    urls = [f"https://drive.google.com/folder_{i}" for i in range(19)]

    with pytest.raises(ToolError, match="Wrote smart chips to 8 of 19 cells"):
        await _write_smart_chips(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="test_sheet_id",
            range_name="Sheet1!F3:F21",
            chips=urls,
        )


@pytest.mark.asyncio
async def test_write_smart_chips_first_batch_failure_propagates_http_error():
    """With nothing written yet, the HttpError reaches handle_http_errors unchanged."""
    service = create_mock_sheets_service()
    service.spreadsheets().batchUpdate.return_value.execute.side_effect = HttpError(
        Mock(status=403), b"forbidden"
    )

    with pytest.raises(HttpError):
        await _write_smart_chips(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="test_sheet_id",
            range_name="Sheet1!F3",
            chips="https://drive.google.com/1",
        )


@pytest.mark.asyncio
async def test_write_smart_chips_unknown_sheet():
    """Test error when sheet name is not found."""
    service = create_mock_sheets_service()
    with pytest.raises(UserInputError, match="Sheet 'NonExistent' not found"):
        await _write_smart_chips(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="test_sheet_id",
            range_name="NonExistent!A1",
            chips="https://drive.google.com/1",
        )


@pytest.mark.asyncio
async def test_write_smart_chips_overflow_raises_error():
    """Test that inserting more chips than range capacity raises UserInputError."""
    service = create_mock_sheets_service()
    with pytest.raises(UserInputError, match="exceeds vertical range capacity"):
        await _write_smart_chips(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="test_sheet_id",
            range_name="Sheet1!F3:F4",
            chips=["https://1", "https://2", "https://3"],
        )


# ---------------------------------------------------------------------------
# Tests for read_sheet_values with include_smart_chips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_sheet_values_with_smart_chips():
    """Test read_sheet_values extracts and formats smart chips."""
    mock_service = Mock()
    # Mock values().get
    mock_service.spreadsheets().values().get().execute = Mock(
        return_value={
            "range": "Sheet1!F3:F4",
            "values": [["@"], ["@"]],
        }
    )
    # Mock spreadsheets().get with includeGridData=True
    grid_response = {
        "sheets": [
            {
                "properties": {"title": "Sheet1"},
                "data": [
                    {
                        "startRow": 2,
                        "startColumn": 5,
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "chipRuns": [
                                            {
                                                "startIndex": 0,
                                                "chip": {
                                                    "richLinkProperties": {
                                                        "uri": "https://drive.google.com/folders/111",
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                ]
                            },
                            {
                                "values": [
                                    {
                                        "chipRuns": [
                                            {
                                                "startIndex": 0,
                                                "chip": {
                                                    "personProperties": {
                                                        "email": "antoine@example.com",
                                                    }
                                                },
                                            }
                                        ],
                                    }
                                ]
                            },
                        ],
                    }
                ],
            }
        ]
    }
    mock_service.spreadsheets().get().execute = Mock(return_value=grid_response)

    result = await _unwrap(read_sheet_values)(
        service=mock_service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Sheet1!F3:F4",
        include_smart_chips=True,
    )

    assert "Smart Chips in range 'Sheet1!F3:F4':" in result
    assert "- Sheet1!F3: [Drive Chip] https://drive.google.com/folders/111" in result
    assert "- Sheet1!F4: [Person Chip] antoine@example.com" in result


def test_extract_smart_chips_skips_plain_text_runs():
    """Plain-text runs come back with an empty chip and must not be reported."""
    grid = {
        "sheets": [
            {
                "properties": {"title": "Sheet1"},
                "data": [
                    {
                        "rowData": [
                            {
                                "values": [
                                    {
                                        "chipRuns": [
                                            {"startIndex": 0},
                                            {
                                                "startIndex": 7,
                                                "chip": {
                                                    "personProperties": {
                                                        "email": "a@example.com"
                                                    }
                                                },
                                            },
                                            {"startIndex": 8, "chip": {}},
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ],
            }
        ]
    }
    assert _extract_cell_smart_chips_from_grid(grid) == [
        {"cell": "Sheet1!A1", "type": "person", "value": "a@example.com"}
    ]


@pytest.mark.asyncio
async def test_write_smart_chips_multi_chips_single_cell():
    """Test inserting multiple smart chips into a single cell."""
    service = create_mock_sheets_service()
    chips = [
        "https://drive.google.com/folder1",
        "antoine@example.com",
    ]

    result = await _write_smart_chips(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Elections!F3",
        chips=chips,
    )

    assert "Successfully inserted 2 smart chip(s)" in result
    assert "Elections!F3" in result

    service.spreadsheets().batchUpdate.assert_called_once()
    call_args = service.spreadsheets().batchUpdate.call_args
    requests = call_args[1]["body"]["requests"]
    assert len(requests) == 1
    cell_data = requests[0]["updateCells"]["rows"][0]["values"][0]
    assert cell_data["userEnteredValue"] == {"stringValue": "@ @"}
    assert len(cell_data["chipRuns"]) == 2
    assert cell_data["chipRuns"][0]["startIndex"] == 0
    assert cell_data["chipRuns"][1]["startIndex"] == 2


# ---------------------------------------------------------------------------
# Tests for modify_sheet_values chips mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_modify_sheet_values_writes_chips():
    """modify_sheet_values routes chips to batchUpdate instead of values.update."""
    service = create_mock_sheets_service()

    result = await _unwrap(modify_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="test_sheet_id",
        range_name="Elections!F3",
        chips="antoine@example.com",
    )

    assert "Successfully inserted 1 smart chip(s)" in result
    service.spreadsheets().batchUpdate.assert_called_once()
    service.spreadsheets().values.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra", [{"values": [["x"]]}, {"clear_values": True}], ids=["values", "clear"]
)
async def test_modify_sheet_values_rejects_chips_with_other_modes(extra):
    service = create_mock_sheets_service()

    with pytest.raises(UserInputError, match="only one of"):
        await _unwrap(modify_sheet_values)(
            service=service,
            user_google_email="user@example.com",
            spreadsheet_id="test_sheet_id",
            range_name="Elections!F3",
            chips="antoine@example.com",
            **extra,
        )
    service.spreadsheets().batchUpdate.assert_not_called()
