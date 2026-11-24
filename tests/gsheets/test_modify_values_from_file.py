"""Tests for writing a local CSV/TSV file with modify_sheet_values(file_path=...)."""

from pathlib import Path
from typing import Any, Callable
from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gsheets.sheets_tools import modify_sheet_values


def _unwrap(tool: Any) -> Callable[..., Any]:
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.fixture
def allowed_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", raising=False)
    monkeypatch.setattr("core.utils.is_stateless_mode", lambda: False)
    monkeypatch.setenv("ALLOWED_FILE_DIRS", str(tmp_path))
    return tmp_path


def _service() -> Mock:
    service = Mock()
    values_api = service.spreadsheets.return_value.values.return_value
    values_api.update.return_value.execute.return_value = {
        "updatedCells": 6,
        "updatedRows": 3,
        "updatedColumns": 2,
    }
    return service


async def _write(service: Mock, **kwargs: Any) -> str:
    return await _unwrap(modify_sheet_values)(
        service=service,
        user_google_email="user@example.com",
        spreadsheet_id="sheet123",
        **kwargs,
    )


def _sent_update(service: Mock) -> dict[str, Any]:
    update = service.spreadsheets.return_value.values.return_value.update
    update.assert_called_once()
    return update.call_args.kwargs


@pytest.mark.asyncio
async def test_writes_csv_rows_from_file(allowed_dir: Path) -> None:
    src = allowed_dir / "rows.csv"
    src.write_bytes(
        '\ufeffname,notes\r\nAda,"likes, commas"\r\nBob,"two\r\nlines"\r\n'.encode(
            "utf-8"
        )
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
            ["Bob", "two\r\nlines"],
        ]
    }
    assert "Updated: 6 cells, 3 rows, 2 columns" in result


@pytest.mark.asyncio
async def test_tsv_uses_tab_delimiter_and_value_input_option(allowed_dir: Path) -> None:
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
async def test_rejects_file_path_with_other_sources(
    allowed_dir: Path, extra: dict[str, Any]
) -> None:
    src = allowed_dir / "rows.csv"
    src.write_text("a,b\n", encoding="utf-8")
    service = _service()

    with pytest.raises(UserInputError, match="only one of 'values', 'file_path'"):
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
        ("unterminated.csv", b'a,b\n1,"open\n2,next\n', "Could not parse"),
    ],
)
async def test_rejects_unusable_files(
    allowed_dir: Path, file_name: str, data: bytes, message: str
) -> None:
    src = allowed_dir / file_name
    src.write_bytes(data)
    service = _service()

    with pytest.raises(UserInputError, match=message):
        await _write(service, range_name="A1", file_path=str(src))

    service.spreadsheets.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_directory(allowed_dir: Path) -> None:
    folder = allowed_dir / "export.csv"
    folder.mkdir()

    with pytest.raises(UserInputError, match="is not a file"):
        await _write(_service(), range_name="A1", file_path=str(folder))


@pytest.mark.asyncio
async def test_rejects_path_outside_allowed_dirs(
    allowed_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "rows.csv"
    outside.write_text("a,b\n", encoding="utf-8")
    service = _service()

    with pytest.raises(ValueError, match="outside permitted directories"):
        await _write(service, range_name="A1", file_path=str(outside))

    service.spreadsheets.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_file_path_when_local_files_disabled(
    allowed_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = allowed_dir / "rows.csv"
    src.write_text("a,b\n", encoding="utf-8")
    monkeypatch.setenv("WORKSPACE_MCP_DISABLE_LOCAL_FILES", "true")

    with pytest.raises(UserInputError, match="Local file access is disabled"):
        await _write(_service(), range_name="A1", file_path=str(src))
