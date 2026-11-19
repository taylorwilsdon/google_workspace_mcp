"""Tests for writing a local CSV/TSV file with modify_sheet_values(file_path=...)."""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gsheets.sheets_tools import modify_sheet_values


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.fixture
def allowed_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
    monkeypatch.setattr("core.utils.is_stateless_mode", lambda: False)
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
    return tmp_path


def _service():
    service = Mock()
    values_api = service.spreadsheets.return_value.values.return_value
    values_api.update.return_value.execute.return_value = {
        "updatedCells": 6,
        "updatedRows": 3,
        "updatedColumns": 2,
    }
    return service


async def _write(service, **kwargs):
    return await _unwrap(modify_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        **kwargs,
    )


def _sent_update(service):
    update = service.spreadsheets.return_value.values.return_value.update
    update.assert_called_once()
    return update.call_args.kwargs


@pytest.mark.asyncio
async def test_writes_csv_rows_from_file(allowed_dir):
    src = allowed_dir / "rows.csv"
    # BOM from Excel's "CSV UTF-8" export, a quoted delimiter, an embedded newline.
    src.write_bytes(
        '﻿name,notes\r\nAda,"likes, commas"\r\nBob,"two\nlines"\r\n'.encode("utf-8")
    )
    service = _service()

    result = await _write(service, range_name="Data!A2", file_path=str(src))

    sent = _sent_update(service)
    assert sent["spreadsheetId"] == "sheet123"
    assert sent["range"] == "Data!A2"
    assert sent["valueInputOption"] == "USER_ENTERED"
    assert sent["body"] == {
        "values": [
            ["name", "notes"],
            ["Ada", "likes, commas"],
            ["Bob", "two\nlines"],
        ]
    }
    assert "Updated: 6 cells, 3 rows, 2 columns" in result


@pytest.mark.asyncio
async def test_tsv_uses_tab_delimiter_and_value_input_option(allowed_dir):
    src = allowed_dir / "rows.TSV"
    src.write_text("a,1\t=SUM(1,2)\nb\t2\n", encoding="utf-8")
    service = _service()

    await _write(
        service,
        range_name="A1",
        file_path=str(src),
        value_input_option="RAW",
    )

    sent = _sent_update(service)
    assert sent["valueInputOption"] == "RAW"
    assert sent["body"] == {"values": [["a,1", "=SUM(1,2)"], ["b", "2"]]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"values": [["x"]]},
        {"clear_values": True},
        {"chips": "user@example.com"},
        {"source_range": "Sheet1!A1"},
    ],
)
async def test_rejects_file_path_with_other_sources(allowed_dir, extra):
    src = allowed_dir / "rows.csv"
    src.write_text("a,b\n", encoding="utf-8")
    service = _service()

    with pytest.raises(UserInputError, match="only one of"):
        await _write(service, range_name="A1", file_path=str(src), **extra)

    service.spreadsheets.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_name", "data", "message"),
    [
        ("rows.xlsx", b"PK\x03\x04", "must be a .csv or .tsv file"),
        ("empty.csv", b"", "has no rows"),
        ("blank_lines.csv", b"\r\n\r\n", "has no rows"),
        ("latin1.csv", "café,1\n".encode("latin-1"), "must be UTF-8"),
        # One field past the csv module's default 128 KiB field limit.
        ("huge.csv", b"x" * (256 * 1024), "Could not parse"),
    ],
)
async def test_rejects_unusable_files(allowed_dir, file_name, data, message):
    src = allowed_dir / file_name
    src.write_bytes(data)
    service = _service()

    with pytest.raises(UserInputError, match=message):
        await _write(service, range_name="A1", file_path=str(src))

    service.spreadsheets.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_directory(allowed_dir):
    folder = allowed_dir / "export.csv"
    folder.mkdir()

    with pytest.raises(UserInputError, match="is not a file"):
        await _write(_service(), range_name="A1", file_path=str(folder))


@pytest.mark.asyncio
async def test_rejects_path_outside_allowed_dirs(allowed_dir, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "rows.csv"
    outside.write_text("a,b\n", encoding="utf-8")
    service = _service()

    with pytest.raises(ValueError, match="outside permitted directories"):
        await _write(service, range_name="A1", file_path=str(outside))

    service.spreadsheets.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_file_path_when_local_files_disabled(allowed_dir, monkeypatch):
    src = allowed_dir / "rows.csv"
    src.write_text("a,b\n", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "true")

    with pytest.raises(UserInputError, match="Local file access is disabled"):
        await _write(_service(), range_name="A1", file_path=str(src))
