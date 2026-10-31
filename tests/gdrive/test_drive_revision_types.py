"""Revision types remain authoritative when the current file changes format."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from core.utils import UserInputError
import gdrive.drive_helpers as helpers
import gdrive.drive_tools as tools

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
DRAWING = "application/vnd.google-apps.drawing"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _unwrap(tool):
    """Reach the implementation without introducing authentication or network I/O."""
    function = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(function, "__wrapped__"):
        function = function.__wrapped__
    return function


@pytest.fixture
def revision_case(tmp_path, monkeypatch):
    """Exercise real revision routing with a fake transport and controlled limits."""
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_MAX_CONTENT_CHARS", "40000")
    monkeypatch.setattr(tools, "is_stateless_mode", lambda: True)
    monkeypatch.setattr(tools, "gcs_files_enabled", lambda: False)
    monkeypatch.setattr(tools, "get_stateless_inline_max_bytes", lambda: 1024)

    def setup(current_mime, revision, payload=b"historical bytes"):
        """Supply current and historical metadata independently for one read."""
        metadata = {
            "name": "Current",
            "mimeType": current_mime,
            "headRevisionId": None
            if current_mime.startswith("application/vnd.google-apps.")
            else "head",
        }
        resolved = AsyncMock(return_value=("file123", metadata))
        monkeypatch.setattr(helpers, "resolve_drive_item", resolved)
        monkeypatch.setattr(tools, "resolve_drive_item", resolved)
        revision = {
            "id": "old",
            "modifiedTime": "2026-08-28T13:00:00Z",
            **revision,
        }
        service = Mock()
        service._http = Mock()
        service.revisions().get().execute.return_value = revision
        path = tmp_path / "historical-download"

        async def transfer(request, **kwargs):
            """Materialize bytes only if the selected revision reaches acquisition."""
            path.write_bytes(payload)
            return path

        acquisition = AsyncMock(side_effect=transfer)
        monkeypatch.setattr(helpers, "_download_http_request_to_temp", acquisition)
        return SimpleNamespace(
            service=service,
            path=path,
            payload=payload,
            acquisition=acquisition,
        )

    return setup


@pytest.mark.asyncio
@pytest.mark.parametrize("current_mime", [DOC, DRAWING])
async def test_listing_downloadability_uses_each_revision_type(
    revision_case, current_mime
):
    """Current native type cannot make old blobs or unsupported types exportable."""
    case = revision_case(current_mime, {})
    revisions = [
        {"id": "blob-kept", "mimeType": "text/csv", "keepForever": True},
        {"id": "blob-purgeable", "mimeType": "text/csv", "keepForever": False},
        {"id": "native-doc", "mimeType": DOC},
        {"id": "native-drawing", "mimeType": DRAWING},
    ]
    case.service.revisions().list().execute.return_value = {"revisions": revisions}
    result = await helpers.list_drive_file_revisions_data(case.service, "file123")
    assert [r["download_available"] for r in result["revisions"]] == [
        True,
        False,
        True,
        False,
    ]
    assert [r["mime_type"] for r in result["revisions"]] == [
        "text/csv",
        "text/csv",
        DOC,
        DRAWING,
    ]
    case.acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_old_native_revision_exports_when_current_file_is_a_blob(revision_case):
    """An old Google Doc must use its export links rather than the blob route."""
    case = revision_case(
        "text/plain",
        {
            "mimeType": DOC,
            "exportLinks": {"application/pdf": "https://example.invalid/old.pdf"},
        },
    )
    result = await helpers.download_drive_revision_to_temp(
        case.service, "file123", "old"
    )
    assert case.acquisition.await_args.args[0].uri == "https://example.invalid/old.pdf"
    assert result["output_mime_type"] == "application/pdf"
    assert result["export_mime_type"] == "application/pdf"
    assert result["path"].read_bytes() == case.payload
    case.service.revisions().get_media.assert_not_called()
    result["path"].unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool",
    [tools.get_drive_file_content, tools.get_drive_file_download_url],
    ids=["content", "download"],
)
async def test_old_blob_is_read_without_current_native_export_validation(
    revision_case, tool
):
    """A retained CSV stays CSV even when the current file is a Google Doc."""
    case = revision_case(
        DOC,
        {
            "mimeType": "text/csv",
            "keepForever": True,
            "originalFilename": "historic.csv",
            "size": "8",
        },
        payload=b"a,b\n1,2\n",
    )
    arguments = (
        {"export_format": "csv"} if tool is tools.get_drive_file_download_url else {}
    )
    result = await _unwrap(tool)(
        service=case.service,
        user_google_email="user@example.com",
        file_id="file123",
        revision_id="old",
        **arguments,
    )
    case.service.revisions().get_media.assert_called_once_with(
        fileId="file123", revisionId="old"
    )
    case.service.files().export_media.assert_not_called()
    if isinstance(result, str):
        assert "Type: text/csv)" in result
        assert case.payload.decode() in result
    else:
        summary = result.content[0].text
        resource = result.content[1].resource
        assert "MIME Type: text/csv" in summary
        assert "Google native file exported" not in summary
        assert resource.mime_type == "text/csv"
        assert base64.b64decode(resource.blob) == case.payload
    assert not case.path.exists()


@pytest.mark.asyncio
async def test_content_export_format_follows_old_native_type(
    revision_case, monkeypatch
):
    """An old Sheet uses XLSX for extraction even when the current file is a Doc."""
    case = revision_case(
        DOC,
        {"mimeType": SHEET, "exportLinks": {XLSX: "https://example.invalid/old.xlsx"}},
    )
    extract = Mock(return_value="Historical cells")
    monkeypatch.setattr(tools, "extract_office_xml_text", extract)
    result = await _unwrap(tools.get_drive_file_content)(
        service=case.service,
        user_google_email="user@example.com",
        file_id="file123",
        revision_id="old",
    )
    assert case.acquisition.await_args.args[0].uri == "https://example.invalid/old.xlsx"
    extract.assert_called_once_with(case.payload, XLSX)
    assert f"Type: {XLSX})" in result
    assert "Historical cells" in result
    case.service.revisions().get_media.assert_not_called()
    assert not case.path.exists()


@pytest.mark.asyncio
async def test_unsupported_native_history_is_not_treated_as_an_uploaded_file(
    revision_case,
):
    """Historical Drawing export remains unsupported after a current-type change."""
    case = revision_case("text/plain", {"mimeType": DRAWING, "keepForever": True})
    with pytest.raises(UserInputError, match="Historical export is not supported"):
        await helpers.download_drive_revision_to_temp(case.service, "file123", "old")
    case.service.revisions().get_media.assert_not_called()
    case.acquisition.assert_not_awaited()
    assert not case.path.exists()
