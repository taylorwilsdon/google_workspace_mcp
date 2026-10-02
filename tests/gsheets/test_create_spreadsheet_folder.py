"""Tests for create_spreadsheet folder placement (folder_id parameter)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from auth.scopes import DRIVE_FILE_SCOPE, DRIVE_SCOPE, SHEETS_WRITE_SCOPE
from core.server import server
from core.tool_registry import get_tool_components
from gsheets.sheets_tools import create_spreadsheet


def _sheets_mock(spreadsheet_id="sheet-123"):
    """Sheets service mock for spreadsheets().create()."""
    service = Mock()
    service.spreadsheets().create().execute = Mock(
        return_value={
            "spreadsheetId": spreadsheet_id,
            "spreadsheetUrl": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "properties": {"title": "My Sheet", "locale": "en_US"},
        }
    )
    return service


def _patch_placement(**mock_kwargs):
    """Stand in for the on-demand Drive move; its mechanics are tested separately."""
    return patch(
        "gdrive.drive_helpers.place_created_file_in_folder",
        new=AsyncMock(**mock_kwargs),
    )


async def _call_create_spreadsheet(sheets_service, **overrides):
    """Call the undecorated implementation to keep auth out of unit tests."""
    impl = create_spreadsheet.__wrapped__.__wrapped__
    defaults = {
        "service": sheets_service,
        "user_google_email": "user@example.com",
        "title": "My Sheet",
    }
    defaults.update(overrides)
    return await impl(**defaults)


def test_create_spreadsheet_schema_exposes_optional_folder_id():
    components = get_tool_components(server)
    parameters = components["create_spreadsheet"].parameters

    assert "folder_id" not in parameters["required"]
    assert parameters["properties"]["folder_id"]["default"] == "root"


def test_create_spreadsheet_requires_no_drive_scope():
    """
    Drive is acquired on demand inside the tool, never by its decorator.

    Declaring it on the decorator would authenticate Drive before the function
    runs, so the folder_id="root" default would fail for a Sheets-only grant,
    and the permission filter would drop the tool from registries lacking it.
    """
    required = create_spreadsheet._required_google_scopes

    assert required == [SHEETS_WRITE_SCOPE]
    assert DRIVE_FILE_SCOPE not in required
    assert DRIVE_SCOPE not in required


@pytest.mark.asyncio
async def test_create_spreadsheet_defaults_to_root_and_skips_drive():
    sheets_service = _sheets_mock()

    with _patch_placement() as place:
        result = await _call_create_spreadsheet(sheets_service)

    place.assert_not_awaited()
    assert "Placed in folder" not in result
    assert "Successfully created spreadsheet 'My Sheet' for user@example.com." in result
    assert "ID: sheet-123" in result
    assert "Locale: en_US" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("folder_id", ["root", "", None])
async def test_create_spreadsheet_skips_the_move_for_root_folder_ids(folder_id):
    """No Drive authentication or round-trip when no real folder is requested."""
    sheets_service = _sheets_mock()

    with _patch_placement() as place:
        result = await _call_create_spreadsheet(sheets_service, folder_id=folder_id)

    place.assert_not_awaited()
    assert "Placed in folder" not in result


@pytest.mark.asyncio
async def test_create_spreadsheet_moves_new_file_into_requested_folder():
    sheets_service = _sheets_mock()

    with _patch_placement(return_value="resolved-folder") as place:
        result = await _call_create_spreadsheet(sheets_service, folder_id="folder-abc")

    place.assert_awaited_once_with(
        user_google_email="user@example.com",
        file_id="sheet-123",
        folder_id="folder-abc",
        tool_name="create_spreadsheet",
    )
    assert "Placed in folder 'folder-abc'." in result


@pytest.mark.asyncio
async def test_create_spreadsheet_keeps_sheet_names_in_create_body():
    sheets_service = _sheets_mock()

    with _patch_placement():
        await _call_create_spreadsheet(
            sheets_service,
            sheet_names=["Q1", "Q2"],
            folder_id="folder-abc",
        )

    body = sheets_service.spreadsheets().create.call_args.kwargs["body"]
    assert body["properties"] == {"title": "My Sheet"}
    assert body["sheets"] == [
        {"properties": {"title": "Q1"}},
        {"properties": {"title": "Q2"}},
    ]


@pytest.mark.asyncio
async def test_create_spreadsheet_omits_sheets_key_without_sheet_names():
    sheets_service = _sheets_mock()

    with _patch_placement():
        await _call_create_spreadsheet(sheets_service)

    assert "sheets" not in sheets_service.spreadsheets().create.call_args.kwargs["body"]


@pytest.mark.asyncio
async def test_create_spreadsheet_reports_invalid_folder_without_orphaning_the_file():
    """A failed move must still surface the spreadsheet ID and URL."""
    sheets_service = _sheets_mock()

    with _patch_placement(side_effect=Exception("is not a folder")):
        result = await _call_create_spreadsheet(
            sheets_service, folder_id="not-a-folder"
        )

    assert "sheet-123" in result
    assert "is not a folder" in result
    assert "My Drive root" in result
    assert "Placed in folder" not in result


@pytest.mark.asyncio
async def test_create_spreadsheet_survives_missing_drive_authorization():
    """
    A Sheets-only grant still creates the file; only the move is reported failed.

    The on-demand Drive service raises at authentication time for a caller
    without drive.file, which must not cost the caller the spreadsheet.
    """
    sheets_service = _sheets_mock()

    with _patch_placement(side_effect=Exception("credentials lack required scopes")):
        result = await _call_create_spreadsheet(sheets_service, folder_id="folder-abc")

    assert "sheet-123" in result
    assert "credentials lack required scopes" in result
    assert "Placed in folder 'folder-abc'." not in result
