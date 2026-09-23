"""Tests for Drive revision listing and per-revision export.

Drive lists metadata for revisions whose content it no longer keeps. For binary
files only the head revision and revisions pinned with ``keepForever`` can be
downloaded, so the tools must say so instead of surfacing a backend error.
"""

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from gdrive.drive_tools import (
    export_file_revision,
    list_file_revisions,
    revision_is_downloadable,
)

NATIVE_SHEET = "application/vnd.google-apps.spreadsheet"
BINARY = "application/pdf"


def _unwrap(tool):
    """Peel FastMCP/auth wrappers so unit tests can pass a mock service."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _patch_resolve(mime_type, *, head_revision_id=None, name="Report"):
    metadata = {
        "name": name,
        "mimeType": mime_type,
        "webViewLink": "https://drive.example/view",
        "headRevisionId": head_revision_id,
    }
    return patch(
        "gdrive.drive_tools.resolve_drive_item",
        AsyncMock(return_value=("file-123", metadata)),
    )


# ------------------------------------------------------------------ downloadability


@pytest.mark.parametrize(
    "revision,is_native,head,expected",
    [
        ({"id": "3"}, True, None, True),  # native: always exportable
        ({"id": "3"}, False, "9", False),  # binary, pruned
        ({"id": "9"}, False, "9", True),  # binary, head revision
        ({"id": "3", "keepForever": True}, False, "9", True),  # binary, pinned
        ({"id": "3"}, False, None, False),  # binary, head unknown
    ],
)
def test_revision_is_downloadable(revision, is_native, head, expected):
    assert revision_is_downloadable(revision, is_native, head) is expected


# ------------------------------------------------------------------------- listing


@pytest.mark.asyncio
async def test_list_marks_non_downloadable_binary_revisions():
    service = Mock()
    service.revisions().list().execute = Mock(
        return_value={
            "revisions": [
                {"id": "1", "modifiedTime": "2024-01-01T00:00:00Z"},
                {
                    "id": "2",
                    "modifiedTime": "2024-01-02T00:00:00Z",
                    "keepForever": True,
                },
                {"id": "3", "modifiedTime": "2024-01-03T00:00:00Z"},
            ]
        }
    )

    with _patch_resolve(BINARY, head_revision_id="3"):
        result = await _unwrap(list_file_revisions)(
            service=service, user_google_email="user@example.com", file_id="file-123"
        )

    assert (
        "- revision 1 | 2024-01-01T00:00:00Z | by unknown  [not downloadable]" in result
    )
    assert "- revision 2 | 2024-01-02T00:00:00Z | by unknown\n" in result
    assert "- revision 3 | 2024-01-03T00:00:00Z | by unknown\n" in result
    assert "Drive-visible revision(s)" in result
    assert "not necessarily the file's complete edit history" in result


@pytest.mark.asyncio
async def test_list_does_not_mark_native_revisions():
    service = Mock()
    service.revisions().list().execute = Mock(
        return_value={
            "revisions": [{"id": "1", "modifiedTime": "2024-01-01T00:00:00Z"}]
        }
    )

    with _patch_resolve(NATIVE_SHEET):
        result = await _unwrap(list_file_revisions)(
            service=service, user_google_email="user@example.com", file_id="file-123"
        )

    assert "[not downloadable]" not in result
    assert "keepForever" not in result


@pytest.mark.asyncio
async def test_list_requests_keep_forever_field():
    service = Mock()
    service.revisions().list().execute = Mock(return_value={"revisions": []})

    with _patch_resolve(BINARY, head_revision_id="1"):
        await _unwrap(list_file_revisions)(
            service=service, user_google_email="user@example.com", file_id="file-123"
        )

    fields = service.revisions().list.call_args.kwargs["fields"]
    assert "keepForever" in fields


# -------------------------------------------------------------------------- export


@pytest.mark.asyncio
async def test_export_rejects_unknown_format():
    service = Mock()

    with _patch_resolve(NATIVE_SHEET):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="7",
            export_format="csvv",
        )

    assert "unknown export_format 'csvv'" in result
    assert "xlsx" in result
    # The bad request must not reach the API at all.
    service.revisions().get.assert_not_called()


@pytest.mark.asyncio
async def test_export_normalizes_format_case_and_dot():
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={
            "id": "7",
            "modifiedTime": "2024-01-01T00:00:00Z",
            "exportLinks": {},
        }
    )

    with _patch_resolve(NATIVE_SHEET):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="7",
            export_format=".CSV",
        )

    # Accepted, then fails later on the missing export link rather than on format.
    assert "unknown export_format" not in result
    assert "cannot be exported as 'csv'" in result


@pytest.mark.asyncio
async def test_export_fails_fast_for_pruned_binary_revision():
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={"id": "3", "modifiedTime": "2024-01-03T00:00:00Z", "size": "10"}
    )

    with _patch_resolve(BINARY, head_revision_id="9"):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="3",
        )

    assert "cannot be downloaded" in result
    assert "keepForever" in result
    # No media request must be issued for a revision Drive cannot serve.
    service.revisions().get_media.assert_not_called()


@pytest.mark.asyncio
async def test_export_rejects_oversized_revision_before_download(monkeypatch):
    """The declared revision size, not the file's, must be checked."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "100")
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={
            "id": "9",
            "modifiedTime": "2024-01-09T00:00:00Z",
            "size": "500",
        }
    )

    with _patch_resolve(BINARY, head_revision_id="9"):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="9",
        )

    assert result.startswith("Error:")
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in result
    service.revisions().get_media.assert_not_called()


@pytest.mark.asyncio
async def test_export_accepts_revision_within_limit(monkeypatch):
    """Counterpart: a revision under the cap must not be rejected."""
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "1000")
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={
            "id": "9",
            "modifiedTime": "2024-01-09T00:00:00Z",
            "size": "500",
        }
    )
    tmp = Mock()
    tmp.stat.return_value = Mock(st_size=500)

    with (
        _patch_resolve(BINARY, head_revision_id="9"),
        patch(
            "gdrive.drive_tools._download_revision_to_temp",
            AsyncMock(return_value=tmp),
        ) as to_temp,
        patch("gdrive.drive_tools.is_stateless_mode", return_value=True),
    ):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="9",
        )

    to_temp.assert_awaited_once()
    assert not result.startswith("Error:")


# ------------------------------------------------------- native type consistency


@pytest.mark.parametrize(
    "mime_type,expected",
    [
        ("application/vnd.google-apps.spreadsheet", True),
        ("application/vnd.google-apps.drawing", True),
        ("application/vnd.google-apps.form", True),
        ("application/pdf", False),
        ("", False),
    ],
)
def test_is_native_google_type(mime_type, expected):
    from gdrive.drive_tools import is_native_google_type

    assert is_native_google_type(mime_type) is expected


@pytest.mark.asyncio
async def test_export_treats_any_workspace_type_as_native():
    """A Drawing must not fall into the binary path and hit the keepForever error."""
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={
            "id": "3",
            "modifiedTime": "2024-01-03T00:00:00Z",
            "exportLinks": {"image/png": "https://export.example/png"},
        }
    )

    with _patch_resolve(
        "application/vnd.google-apps.drawing", head_revision_id="9", name="Sketch"
    ):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="3",
        )

    # No default export format for Drawings, so it asks instead of failing binary.
    assert "keepForever" not in result
    assert "no default export format" in result
    assert "image/png" in result
    service.revisions().get_media.assert_not_called()


@pytest.mark.asyncio
async def test_export_drawing_with_explicit_format_uses_export_link():
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={
            "id": "3",
            "modifiedTime": "2024-01-03T00:00:00Z",
            "exportLinks": {"image/png": "https://export.example/png"},
        }
    )
    saved = Mock(path="/tmp/Sketch_rev3.png", file_id="att-2")

    with (
        _patch_resolve("application/vnd.google-apps.drawing", name="Sketch"),
        patch(
            "gdrive.drive_tools.download_http_url_bytes",
            AsyncMock(return_value=b"pngdata"),
        ),
        patch("gdrive.drive_tools.is_stateless_mode", return_value=False),
        patch("gdrive.drive_tools.get_transport_mode", return_value="stdio"),
        patch("gdrive.drive_tools.get_attachment_storage") as storage,
    ):
        # The real save_attachment_from_path moves the temp file; the mock must
        # consume it too, otherwise every run leaks a file into the temp dir.
        def _consume(src_path, filename=None, mime_type=None):
            Path(src_path).unlink(missing_ok=True)
            return saved

        storage.return_value.save_attachment_from_path = Mock(side_effect=_consume)
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="3",
            export_format="png",
        )
        src = storage.return_value.save_attachment_from_path.call_args.kwargs[
            "src_path"
        ]
        assert not Path(src).exists()

    assert "MIME Type: image/png" in result
    kwargs = storage.return_value.save_attachment_from_path.call_args.kwargs
    assert kwargs["filename"] == "Sketch_rev3.png"
    assert kwargs["mime_type"] == "image/png"


@pytest.mark.asyncio
async def test_export_streams_binary_revision_to_disk():
    """The binary path must not buffer the whole revision in memory."""
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={
            "id": "9",
            "modifiedTime": "2024-01-09T00:00:00Z",
            "mimeType": "image/png",
            "originalFilename": "diagram.png",
            "size": "12",
        }
    )
    saved = Mock(path="/tmp/diagram_rev9.png", file_id="att-3")
    tmp = Mock()
    tmp.stat.return_value = Mock(st_size=7)

    with (
        _patch_resolve(BINARY, head_revision_id="9", name="renamed.pdf"),
        patch(
            "gdrive.drive_tools._download_revision_to_temp",
            AsyncMock(return_value=tmp),
        ) as to_temp,
        patch("gdrive.drive_tools.download_media_bytes") as in_memory,
        patch("gdrive.drive_tools.is_stateless_mode", return_value=False),
        patch("gdrive.drive_tools.get_transport_mode", return_value="stdio"),
        patch("gdrive.drive_tools.get_attachment_storage") as storage,
    ):
        storage.return_value.save_attachment_from_path = Mock(return_value=saved)
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="9",
        )

    to_temp.assert_awaited_once()
    in_memory.assert_not_called()
    assert "MIME Type: image/png" in result
    kwargs = storage.return_value.save_attachment_from_path.call_args.kwargs
    assert kwargs["filename"] == "diagram_rev9.png"


@pytest.mark.asyncio
async def test_export_removes_temp_file_in_stateless_mode():
    service = Mock()
    service.revisions().get().execute = Mock(
        return_value={"id": "9", "modifiedTime": "2024-01-09T00:00:00Z", "size": "12"}
    )
    tmp = Mock()
    tmp.stat.return_value = Mock(st_size=7)

    with (
        _patch_resolve(BINARY, head_revision_id="9"),
        patch(
            "gdrive.drive_tools._download_revision_to_temp",
            AsyncMock(return_value=tmp),
        ),
        patch("gdrive.drive_tools.is_stateless_mode", return_value=True),
    ):
        result = await _unwrap(export_file_revision)(
            service=service,
            user_google_email="user@example.com",
            file_id="file-123",
            revision_id="9",
        )

    assert "Stateless mode" in result
    tmp.unlink.assert_called_once_with(missing_ok=True)
