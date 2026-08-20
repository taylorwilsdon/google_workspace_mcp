"""Tests for bounded, read-only Drive XLSX inspection."""

import asyncio
import io
import json
import re
import struct
import threading
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from openpyxl import Workbook

from gdrive.drive_tools import inspect_drive_excel, read_drive_excel_range


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _workbook_bytes() -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Summary"
    summary["A1"] = "Fund"
    summary["B1"] = "TVPI"
    summary["A2"] = "Fund 6"
    summary["B2"] = 1.25
    summary["B2"].number_format = "0.0x"
    summary["C2"] = None
    summary["D2"] = "=SUM(1,2)"
    workbook.create_sheet("Co-invest")["A1"] = "Company"
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()

    source = zipfile.ZipFile(io.BytesIO(output.getvalue()))
    rewritten = io.BytesIO()
    with source, zipfile.ZipFile(rewritten, "w") as target:
        for entry in source.infolist():
            content = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                text = content.decode("utf-8")
                text, count = re.subn(
                    r'(<c r="D2"[^>]*><f>SUM\(1,2\)</f>)<v\s*/>',
                    r"\g<1><v>3</v>",
                    text,
                )
                assert count == 1
                content = text.encode("utf-8")
            target.writestr(entry, content)
    return rewritten.getvalue()


def _encrypted_zip_bytes(content: bytes) -> bytes:
    encrypted = bytearray(content)
    for signature, flags_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        offset = 0
        while True:
            offset = encrypted.find(signature, offset)
            if offset < 0:
                break
            flags = struct.unpack_from("<H", encrypted, offset + flags_offset)[0]
            struct.pack_into("<H", encrypted, offset + flags_offset, flags | 0x1)
            offset += 4
    return bytes(encrypted)


class _FakeDownloader:
    handles = []
    data = b""

    def __init__(self, handle, _request, chunksize=None):
        type(self).handles.append(handle)
        self.handle = handle

    def next_chunk(self):
        self.handle.write(type(self).data)
        return None, True


def _drive_file(
    content: bytes, *, mime_type: str | None = None, size: int | None = None
):
    metadata = {
        "id": "xlsx123",
        "name": "NAV Pack.xlsx",
        "webViewLink": "https://drive.google.com/file/d/xlsx123/view",
        "mimeType": mime_type
        or "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "size": str(len(content) if size is None else size),
        "capabilities": {"canDownload": True},
    }
    service = Mock()
    service.files().get().execute.return_value = metadata
    service.files().get_media.return_value = "request"
    _FakeDownloader.data = content
    _FakeDownloader.handles = []
    return service


@pytest.mark.asyncio
async def test_inspect_drive_excel_lists_sheets_and_cleans_up_temp_file():
    service = _drive_file(_workbook_bytes())

    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        result = await _unwrap(inspect_drive_excel)(
            service=service,
            user_google_email="user@example.com",
            file_id="xlsx123",
        )

    payload = json.loads(result)
    assert payload == {
        "fileId": "xlsx123",
        "filename": "NAV Pack.xlsx",
        "webViewLink": "https://drive.google.com/file/d/xlsx123/view",
        "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "sheets": [
            {"name": "Summary", "declaredRange": "A1:D2"},
            {"name": "Co-invest", "declaredRange": "A1:A1"},
        ],
    }
    assert not Path(_FakeDownloader.handles[0].name).exists()
    assert [call[0] for call in service.files.return_value.method_calls] == [
        "get",
        "get",
        "get_media",
    ]


@pytest.mark.asyncio
async def test_inspect_drive_excel_parser_owns_temp_file_after_cancellation():
    service = _drive_file(_workbook_bytes())
    started = threading.Event()
    release = threading.Event()
    parser_path = None

    def blocking_parser(path, _metadata):
        nonlocal parser_path
        parser_path = path
        started.set()
        release.wait(timeout=2)
        return "{}"

    with (
        patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader),
        patch("gdrive.drive_tools._inspect_excel_workbook", blocking_parser),
    ):
        task = asyncio.create_task(
            _unwrap(inspect_drive_excel)(
                service=service,
                user_google_email="user@example.com",
                file_id="xlsx123",
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert parser_path is not None
        assert parser_path.exists()
        release.set()
        for _ in range(100):
            if not parser_path.exists():
                break
            await asyncio.sleep(0.01)
        assert not parser_path.exists()


@pytest.mark.asyncio
async def test_read_drive_excel_range_returns_cached_values_formulas_formats_and_blanks():
    service = _drive_file(_workbook_bytes())

    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        result = await _unwrap(read_drive_excel_range)(
            service=service,
            user_google_email="user@example.com",
            file_id="xlsx123",
            sheet_name="Summary",
            cell_range="A1:D2",
        )

    payload = json.loads(result)
    assert payload["fileId"] == "xlsx123"
    assert payload["filename"] == "NAV Pack.xlsx"
    assert payload["webViewLink"] == "https://drive.google.com/file/d/xlsx123/view"
    assert payload["sheet"] == "Summary"
    assert payload["range"] == "A1:D2"
    assert payload["values"] == [
        ["Fund", "TVPI", None, None],
        ["Fund 6", 1.25, None, 3],
    ]
    assert payload["formulas"] == [
        [None, None, None, None],
        [None, None, None, "=SUM(1,2)"],
    ]
    assert payload["numberFormats"] == [
        [None, None, None, None],
        [None, "0.0x", None, None],
    ]
    assert not Path(_FakeDownloader.handles[0].name).exists()


@pytest.mark.asyncio
async def test_read_drive_excel_range_rejects_oversized_range_before_drive_access():
    service = Mock()

    with pytest.raises(ValueError, match="at most 500 cells"):
        await _unwrap(read_drive_excel_range)(
            service=service,
            user_google_email="user@example.com",
            file_id="xlsx123",
            sheet_name="Summary",
            cell_range="A1:A501",
        )

    service.files.assert_not_called()


@pytest.mark.asyncio
async def test_read_drive_excel_range_rejects_sheet_qualified_or_open_ended_ranges():
    service = Mock()

    for cell_range in ("Summary!A1:B2", "A:B", "D2:A1"):
        with pytest.raises(
            ValueError, match="worksheet-local|columns and rows|worksheet bounds"
        ):
            await _unwrap(read_drive_excel_range)(
                service=service,
                user_google_email="user@example.com",
                file_id="xlsx123",
                sheet_name="Summary",
                cell_range=cell_range,
            )

    service.files.assert_not_called()


@pytest.mark.asyncio
async def test_drive_excel_tools_reject_wrong_mime_type_before_download():
    service = _drive_file(_workbook_bytes(), mime_type="application/pdf")

    with pytest.raises(ValueError, match="Drive-hosted .xlsx"):
        await _unwrap(inspect_drive_excel)(
            service=service,
            user_google_email="user@example.com",
            file_id="xlsx123",
        )

    service.files.return_value.get_media.assert_not_called()


@pytest.mark.asyncio
async def test_drive_excel_tools_reject_metadata_and_stream_size_limits(monkeypatch):
    content = _workbook_bytes()
    metadata_oversized = _drive_file(content, size=26 * 1024 * 1024)

    with pytest.raises(ValueError, match="safety limit"):
        await _unwrap(inspect_drive_excel)(
            service=metadata_oversized,
            user_google_email="user@example.com",
            file_id="xlsx123",
        )
    metadata_oversized.files.return_value.get_media.assert_not_called()

    stream_oversized = _drive_file(content, size=1)
    monkeypatch.setattr("gdrive.drive_tools.MAX_EXCEL_DOWNLOAD_BYTES", 1)
    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        with pytest.raises(ValueError, match="safety limit"):
            await _unwrap(inspect_drive_excel)(
                service=stream_oversized,
                user_google_email="user@example.com",
                file_id="xlsx123",
            )
    assert not Path(_FakeDownloader.handles[0].name).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"not a zip", "valid XLSX"),
        (_encrypted_zip_bytes(_workbook_bytes()), "Encrypted Excel"),
    ],
)
async def test_drive_excel_tools_reject_corrupt_and_encrypted_workbooks(
    content, message
):
    service = _drive_file(content)

    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        with pytest.raises(ValueError, match=message):
            await _unwrap(inspect_drive_excel)(
                service=service,
                user_google_email="user@example.com",
                file_id="xlsx123",
            )
    assert not Path(_FakeDownloader.handles[0].name).exists()


@pytest.mark.asyncio
async def test_drive_excel_tools_reject_archive_expansion_and_large_response(
    monkeypatch,
):
    content = _workbook_bytes()
    service = _drive_file(content)
    monkeypatch.setattr("gdrive.drive_tools.MAX_EXCEL_ARCHIVE_UNCOMPRESSED_BYTES", 1)

    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        with pytest.raises(ValueError, match="expands beyond"):
            await _unwrap(inspect_drive_excel)(
                service=service,
                user_google_email="user@example.com",
                file_id="xlsx123",
            )

    response_service = _drive_file(content)
    monkeypatch.setattr(
        "gdrive.drive_tools.MAX_EXCEL_ARCHIVE_UNCOMPRESSED_BYTES", 200 * 1024 * 1024
    )
    monkeypatch.setattr("gdrive.drive_tools.MAX_EXCEL_RESPONSE_CHARACTERS", 1)
    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        with pytest.raises(ValueError, match="output safety limit"):
            await _unwrap(read_drive_excel_range)(
                service=response_service,
                user_google_email="user@example.com",
                file_id="xlsx123",
                sheet_name="Summary",
                cell_range="A1:D2",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("limit_name", "message"),
    [
        ("MAX_EXCEL_ARCHIVE_ENTRIES", "too many archive entries"),
        ("MAX_EXCEL_ARCHIVE_ENTRY_BYTES", "oversized archive entry"),
    ],
)
async def test_drive_excel_tools_reject_archive_entry_limits(
    monkeypatch, limit_name, message
):
    content = _workbook_bytes()
    service = _drive_file(content)
    monkeypatch.setattr(f"gdrive.drive_tools.{limit_name}", 1)

    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        with pytest.raises(ValueError, match=message):
            await _unwrap(inspect_drive_excel)(
                service=service,
                user_google_email="user@example.com",
                file_id="xlsx123",
            )


@pytest.mark.asyncio
async def test_read_drive_excel_range_rejects_unknown_sheet_and_download_restriction():
    content = _workbook_bytes()
    unknown_sheet = _drive_file(content)
    with patch("gdrive.drive_tools.MediaIoBaseDownload", _FakeDownloader):
        with pytest.raises(ValueError, match="does not exist"):
            await _unwrap(read_drive_excel_range)(
                service=unknown_sheet,
                user_google_email="user@example.com",
                file_id="xlsx123",
                sheet_name="Missing",
                cell_range="A1:B2",
            )

    restricted = _drive_file(content)
    restricted.files().get().execute.return_value["capabilities"] = {
        "canDownload": False
    }
    with pytest.raises(ValueError, match="not permitted"):
        await _unwrap(inspect_drive_excel)(
            service=restricted,
            user_google_email="user@example.com",
            file_id="xlsx123",
        )
    restricted.files.return_value.get_media.assert_not_called()
