"""
Tests for copy_doc_as_snapshot — Drive files.copy wrapper used as a
safety-net snapshot before agent edits to a Google Doc.

Mocking convention: unittest.mock + per-file _unwrap helper, matching
tests/gdocs/test_advanced_doc_formatting.py:21-26.
"""

from unittest.mock import Mock, patch

import pytest

from core.utils import UserInputError
from gdrive import drive_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _doc_metadata(name="My Doc", parents=("folder-abc",), webview="https://docs/x"):
    return (
        "doc-123",
        {
            "name": name,
            "webViewLink": webview,
            "mimeType": "application/vnd.google-apps.document",
            "parents": list(parents),
        },
    )


class TestCopyDocAsSnapshot:
    @pytest.mark.asyncio
    async def test_creates_snapshot_when_no_existing(self):
        fn = _unwrap(drive_tools.copy_doc_as_snapshot)
        mock_service = Mock()

        # files.list returns empty (no existing snapshot).
        mock_service.files().list.return_value = Mock(
            execute=Mock(return_value={"files": []})
        )
        # files.copy returns the new file.
        mock_service.files().copy.return_value = Mock(
            execute=Mock(
                return_value={
                    "id": "snap-001",
                    "name": "My Doc.snapshot.20260612T194401Z",
                    "webViewLink": "https://docs/snap",
                    "mimeType": "application/vnd.google-apps.document",
                    "parents": ["folder-abc"],
                }
            )
        )

        with (
            patch(
                "gdrive.drive_tools.resolve_drive_item",
                return_value=_doc_metadata(),
            ),
            patch(
                "gdrive.drive_tools.resolve_folder_id",
                side_effect=lambda _svc, fid: fid,
            ),
        ):
            result = await fn(
                mock_service,
                "user@example.com",
                "doc-123",
                timestamp="20260612T194401Z",
            )

        # files.copy was called with the right body.
        copy_args = mock_service.files().copy.call_args
        assert copy_args.kwargs["fileId"] == "doc-123"
        assert copy_args.kwargs["supportsAllDrives"] is True
        body = copy_args.kwargs["body"]
        assert body["name"] == "My Doc.snapshot.20260612T194401Z"
        assert body["parents"] == ["folder-abc"]

        assert "Successfully created snapshot" in result
        assert "snap-001" in result
        assert "Snapshot before agent edit" in result

    @pytest.mark.asyncio
    async def test_returns_existing_snapshot_without_creating(self):
        fn = _unwrap(drive_tools.copy_doc_as_snapshot)
        mock_service = Mock()

        # files.list returns an existing file with the target name.
        mock_service.files().list.return_value = Mock(
            execute=Mock(
                return_value={
                    "files": [
                        {
                            "id": "snap-existing",
                            "name": "My Doc.snapshot.20260612T194401Z",
                            "webViewLink": "https://docs/existing",
                            "mimeType": "application/vnd.google-apps.document",
                        }
                    ]
                }
            )
        )

        with (
            patch(
                "gdrive.drive_tools.resolve_drive_item",
                return_value=_doc_metadata(),
            ),
            patch(
                "gdrive.drive_tools.resolve_folder_id",
                side_effect=lambda _svc, fid: fid,
            ),
        ):
            result = await fn(
                mock_service,
                "user@example.com",
                "doc-123",
                timestamp="20260612T194401Z",
            )

        # files.copy must NOT be called.
        mock_service.files().copy.assert_not_called()
        assert "already exists" in result
        assert "snap-existing" in result

    @pytest.mark.asyncio
    async def test_requires_name_or_timestamp(self):
        fn = _unwrap(drive_tools.copy_doc_as_snapshot)
        mock_service = Mock()
        with (
            patch(
                "gdrive.drive_tools.resolve_drive_item",
                return_value=_doc_metadata(),
            ),
            patch(
                "gdrive.drive_tools.resolve_folder_id",
                side_effect=lambda _svc, fid: fid,
            ),
            pytest.raises(UserInputError, match="name.*or.*timestamp"),
        ):
            await fn(mock_service, "user@example.com", "doc-123")

    @pytest.mark.asyncio
    async def test_custom_name_used_verbatim(self):
        fn = _unwrap(drive_tools.copy_doc_as_snapshot)
        mock_service = Mock()
        mock_service.files().list.return_value = Mock(
            execute=Mock(return_value={"files": []})
        )
        mock_service.files().copy.return_value = Mock(
            execute=Mock(
                return_value={
                    "id": "snap-custom",
                    "name": "manual-name",
                    "webViewLink": "https://docs/custom",
                }
            )
        )

        with (
            patch(
                "gdrive.drive_tools.resolve_drive_item",
                return_value=_doc_metadata(),
            ),
            patch(
                "gdrive.drive_tools.resolve_folder_id",
                side_effect=lambda _svc, fid: fid,
            ),
        ):
            await fn(
                mock_service,
                "user@example.com",
                "doc-123",
                name="manual-name",
            )

        body = mock_service.files().copy.call_args.kwargs["body"]
        assert body["name"] == "manual-name"
