"""
Unit tests for the Google Sheets update_cell_note tool.

Covers setting a note, clearing a note, and input validation. The note is
applied via a single repeatCell batchUpdate with fields="note", so it never
touches the cell's value.
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gsheets.sheets_tools import _update_cell_note_impl
from core.utils import UserInputError


def create_mock_service():
    """Create a properly configured mock Google Sheets service."""
    mock_service = Mock()

    mock_metadata = {"sheets": [{"properties": {"sheetId": 0, "title": "Sheet1"}}]}
    mock_service.spreadsheets().get().execute = Mock(return_value=mock_metadata)
    mock_service.spreadsheets().batchUpdate().execute = Mock(return_value={})

    return mock_service


@pytest.mark.asyncio
async def test_set_note_builds_repeat_cell_request():
    """Setting a note issues a repeatCell request with the note and fields=note."""
    mock_service = create_mock_service()

    result = await _update_cell_note_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="Sheet1!B3",
        note="see PR #2470",
    )

    assert result["spreadsheet_id"] == "test_spreadsheet_123"
    assert result["range_name"] == "Sheet1!B3"

    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request = call_args[1]["body"]["requests"][0]["repeatCell"]
    assert request["cell"]["note"] == "see PR #2470"
    assert request["fields"] == "note"


@pytest.mark.asyncio
async def test_clear_note_sets_empty_note():
    """clear_note=True writes an empty note so the annotation is removed."""
    mock_service = create_mock_service()

    result = await _update_cell_note_impl(
        service=mock_service,
        spreadsheet_id="test_spreadsheet_123",
        range_name="A1",
        clear_note=True,
    )

    assert result["summary"] == "cleared note"
    call_args = mock_service.spreadsheets().batchUpdate.call_args
    request = call_args[1]["body"]["requests"][0]["repeatCell"]
    assert request["cell"]["note"] == ""
    assert request["fields"] == "note"


@pytest.mark.asyncio
async def test_missing_note_raises():
    """Neither a note nor clear_note is a user input error, no API call made."""
    mock_service = create_mock_service()

    with pytest.raises(UserInputError):
        await _update_cell_note_impl(
            service=mock_service,
            spreadsheet_id="test_spreadsheet_123",
            range_name="A1",
        )

    mock_service.spreadsheets().batchUpdate().execute.assert_not_called()
