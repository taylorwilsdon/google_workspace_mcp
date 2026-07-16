"""
Regression tests for the 2026-07-16 live Sheets-workflow failures:

1. `import_to_google_sheets(content=..., file_name="No Extension")` refused every inline-CSV
   call ("Detected source MIME type 'text/plain' is not supported") because a sheet NAME has
   no extension — the primary documented path never worked.
2. A `file_url` that returns an HTML page (Google's sign-in page for an anonymously-fetched
   Drive share link) was importable as data, producing a garbage 148x6362 "spreadsheet".
3. `create_drive_file(content=..., mime_type="application/vnd.google-apps.spreadsheet")`
   uploaded media AS the native type and 400'd — instead of uploading a convertible source
   with the native target in the metadata.
"""

import io
import os
import sys
from tempfile import SpooledTemporaryFile
from unittest.mock import AsyncMock, Mock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdrive.drive_helpers import (
    GOOGLE_SHEETS_IMPORT_FORMATS,
    GOOGLE_SLIDES_IMPORT_FORMATS,
    _resolve_import_media,
)
from gdrive.drive_tools import create_drive_file, import_to_google_sheets


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# ---------------------------------------------------------------------------
# 1. Inline content with no extension defaults to the tool's own text format
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inline_csv_content_without_extension_defaults_to_csv():
    media, mime, closeable = await _resolve_import_media(
        tool_name="import_to_google_sheets",
        file_name="DifferentDog Spend Summary",  # no extension — the live failure shape
        content="Platform,Spend\nFacebook,21273.12\nGoogle Ads,15903.03",
        file_path=None,
        file_url=None,
        source_format=None,
        format_map=GOOGLE_SHEETS_IMPORT_FORMATS,
    )
    assert mime == "text/csv"
    assert closeable is None


@pytest.mark.asyncio
async def test_inline_tab_content_without_extension_defaults_to_tsv():
    media, mime, _ = await _resolve_import_media(
        tool_name="import_to_google_sheets",
        file_name="Tabbed Data",
        content="Platform\tSpend\nFacebook\t21273.12",
        file_path=None,
        file_url=None,
        source_format=None,
        format_map=GOOGLE_SHEETS_IMPORT_FORMATS,
    )
    assert mime == "text/tab-separated-values"


@pytest.mark.asyncio
async def test_explicit_source_format_still_wins():
    _, mime, _ = await _resolve_import_media(
        tool_name="import_to_google_sheets",
        file_name="whatever",
        content="a,b\n1,2",
        file_path=None,
        file_url=None,
        source_format="csv",
        format_map=GOOGLE_SHEETS_IMPORT_FORMATS,
    )
    assert mime == "text/csv"


@pytest.mark.asyncio
async def test_binary_only_tools_still_refuse_inline_content():
    # Slides accepts no text source format — inline content must still be rejected.
    with pytest.raises(ValueError):
        await _resolve_import_media(
            tool_name="import_to_google_slides",
            file_name="Deck",
            content="not a pptx",
            file_path=None,
            file_url=None,
            source_format=None,
            format_map=GOOGLE_SLIDES_IMPORT_FORMATS,
        )


# ---------------------------------------------------------------------------
# 2. HTML responses from file_url are rejected, not imported as data
# ---------------------------------------------------------------------------


def _spool_with(data: bytes):
    spool = SpooledTemporaryFile(max_size=1 << 20)
    spool.write(data)
    spool.seek(0)
    return spool


@pytest.mark.asyncio
async def test_file_url_returning_html_is_rejected():
    html = b'<!doctype html><html lang="en-US"><head><base href="https://accounts.google.com/">'
    with patch(
        "gdrive.drive_helpers._download_url_to_bytes",
        new=AsyncMock(return_value=(_spool_with(html), "text/html; charset=utf-8")),
    ):
        with pytest.raises(ValueError) as exc:
            await _resolve_import_media(
                tool_name="import_to_google_sheets",
                file_name="Remote Sheet",
                content=None,
                file_path=None,
                file_url="https://drive.google.com/uc?id=abc&export=download",
                source_format="csv",  # even an explicit hint must not let HTML through
                format_map=GOOGLE_SHEETS_IMPORT_FORMATS,
            )
        assert "HTML page" in str(exc.value)


@pytest.mark.asyncio
async def test_file_url_with_real_csv_still_works():
    csv_bytes = b"a,b\n1,2\n"
    with patch(
        "gdrive.drive_helpers._download_url_to_bytes",
        new=AsyncMock(return_value=(_spool_with(csv_bytes), "text/csv")),
    ):
        _, mime, closeable = await _resolve_import_media(
            tool_name="import_to_google_sheets",
            file_name="Remote Sheet",
            content=None,
            file_path=None,
            file_url="https://example.com/data.csv",
            source_format=None,
            format_map=GOOGLE_SHEETS_IMPORT_FORMATS,
        )
        assert mime == "text/csv"
        closeable.close()


# ---------------------------------------------------------------------------
# 3. create_drive_file converts text content into native Google types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_create_drive_file_native_sheet_uploads_csv_media(mock_resolve_folder):
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().create().execute.return_value = {
        "id": "sheet123",
        "name": "Spend",
        "webViewLink": "https://docs.google.com/spreadsheets/d/sheet123",
    }
    mock_service.files().create.reset_mock()

    fn = _unwrap(create_drive_file)
    result = await fn(
        mock_service,
        user_google_email="u@example.com",
        file_name="Spend",
        content="a,b\n1,2",
        mime_type="application/vnd.google-apps.spreadsheet",
    )
    assert "sheet123" in result

    _, kwargs = mock_service.files().create.call_args
    assert kwargs["body"]["mimeType"] == "application/vnd.google-apps.spreadsheet"
    assert kwargs["media_body"].mimetype() == "text/csv"  # media = SOURCE format


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_create_drive_file_native_slides_from_text_is_a_clear_error(mock_resolve_folder):
    mock_resolve_folder.return_value = "root"
    fn = _unwrap(create_drive_file)
    with pytest.raises(ValueError) as exc:
        await fn(
            Mock(),
            user_google_email="u@example.com",
            file_name="Deck",
            content="text",
            mime_type="application/vnd.google-apps.presentation",
        )
    assert "import_to_google_" in str(exc.value)


# ---------------------------------------------------------------------------
# 4. The end-to-end tool path the live agent used now succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_import_to_google_sheets_inline_content_no_extension_succeeds(
    mock_resolve_folder,
):
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().create().execute.return_value = {
        "id": "s1",
        "name": "DifferentDog Spend Summary",
        "webViewLink": "https://docs.google.com/spreadsheets/d/s1",
    }

    fn = _unwrap(import_to_google_sheets)
    result = await fn(
        mock_service,
        user_google_email="u@example.com",
        file_name="DifferentDog Spend Summary",
        content="Platform,Spend\nFacebook,21273.12",
    )
    assert "s1" in result
