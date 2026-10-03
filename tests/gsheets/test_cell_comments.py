"""Tests for cell-anchored spreadsheet comments."""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.scopes import DRIVE_SCOPE, SHEETS_WRITE_SCOPE
from core.comments import create_comment_tools
from core.utils import UserInputError
from gsheets.sheets_helpers import _build_insert_comment_request, _insert_cell_comment

SHEETS = [
    {"properties": {"sheetId": 0, "title": "Sheet1"}},
    {"properties": {"sheetId": 777, "title": "My Data"}},
]


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _mock_sheets_service(comment_id="thread-1"):
    service = Mock()
    spreadsheets = service.spreadsheets.return_value
    spreadsheets.get.return_value.execute.return_value = {"sheets": SHEETS}
    thread = {"commentId": comment_id} if comment_id else {}
    spreadsheets.batchUpdate.return_value.execute.return_value = {
        "replies": [{"insertComment": {"commentThread": thread}}]
    }
    return service


@pytest.fixture
def manage_comment():
    tools = create_comment_tools(
        "spreadsheet", "spreadsheet_id", insert_cell_comment=_insert_cell_comment
    )
    return _unwrap(tools["manage_comment"])


class TestBuildInsertCommentRequest:
    def test_defaults_to_first_sheet(self):
        request = _build_insert_comment_request("B2", SHEETS, "Check this")
        assert request == {
            "insertComment": {
                "content": "Check this",
                "coordinate": {"sheetId": 0, "rowIndex": 1, "columnIndex": 1},
            }
        }

    def test_resolves_quoted_sheet_name(self):
        request = _build_insert_comment_request("'My Data'!$C$5", SHEETS, "x")
        assert request["insertComment"]["coordinate"] == {
            "sheetId": 777,
            "rowIndex": 4,
            "columnIndex": 2,
        }

    @pytest.mark.parametrize("cell", ["A1:B2", "A", "3", "Sheet1!A:A"])
    def test_rejects_non_single_cell(self, cell):
        with pytest.raises(UserInputError, match="single cell"):
            _build_insert_comment_request(cell, SHEETS, "x")

    def test_unknown_sheet(self):
        with pytest.raises(UserInputError, match="not found"):
            _build_insert_comment_request("Nope!A1", SHEETS, "x")


class TestManageSpreadsheetCommentCell:
    @pytest.mark.asyncio
    async def test_create_with_cell_uses_sheets_api(self, manage_comment):
        drive = Mock()
        sheets = _mock_sheets_service("thread-42")

        result = await manage_comment(
            drive,
            sheets,
            "user@example.com",
            "sheet123",
            "create",
            comment_content="Looks off",
            cell="My Data!D10",
        )

        body = sheets.spreadsheets.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["requests"][0]["insertComment"]["coordinate"] == {
            "sheetId": 777,
            "rowIndex": 9,
            "columnIndex": 3,
        }
        assert "Comment ID: thread-42" in result
        assert "My Data!D10" in result
        drive.comments.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_without_cell_uses_drive(self, manage_comment):
        drive = Mock()
        drive.comments.return_value.create.return_value.execute.return_value = {
            "id": "c1",
            "author": {"displayName": "Alice"},
            "createdTime": "2025-01-15T10:00:00Z",
        }
        sheets = Mock()

        result = await manage_comment(
            drive,
            sheets,
            "user@example.com",
            "sheet123",
            "create",
            comment_content="File-level",
        )

        assert "Comment ID: c1" in result
        sheets.spreadsheets.assert_not_called()

    @pytest.mark.asyncio
    async def test_cell_rejected_for_reply(self, manage_comment):
        with pytest.raises(ValueError, match="only supported for the create"):
            await manage_comment(
                Mock(),
                Mock(),
                "user@example.com",
                "sheet123",
                "reply",
                comment_content="hi",
                comment_id="c1",
                cell="A1",
            )

    @pytest.mark.asyncio
    async def test_cell_requires_content(self, manage_comment):
        with pytest.raises(ValueError, match="comment_content is required"):
            await manage_comment(
                Mock(), Mock(), "user@example.com", "sheet123", "create", cell="A1"
            )

    @pytest.mark.asyncio
    async def test_empty_cell_falls_back_to_drive(self, manage_comment):
        drive = Mock()
        drive.comments.return_value.create.return_value.execute.return_value = {
            "id": "c1"
        }
        sheets = Mock()

        result = await manage_comment(
            drive,
            sheets,
            "user@example.com",
            "sheet123",
            "create",
            comment_content="File-level",
            cell="",
        )

        assert "Comment ID: c1" in result
        sheets.spreadsheets.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_comment_id_raises(self, manage_comment):
        with pytest.raises(RuntimeError, match="did not return a comment ID"):
            await manage_comment(
                Mock(),
                _mock_sheets_service(comment_id=None),
                "user@example.com",
                "sheet123",
                "create",
                comment_content="x",
                cell="A1",
            )


def test_manage_comment_requires_drive_and_sheets_write_scopes():
    tools = create_comment_tools(
        "spreadsheet", "spreadsheet_id", insert_cell_comment=_insert_cell_comment
    )
    tool = tools["manage_comment"]
    fn = tool.fn if hasattr(tool, "fn") else tool
    assert set(fn._required_google_scopes) >= {DRIVE_SCOPE, SHEETS_WRITE_SCOPE}
