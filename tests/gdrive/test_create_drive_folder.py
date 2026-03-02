"""
Unit tests for create_drive_folder tool.
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdrive.drive_tools import _create_drive_folder_impl as _raw_create_drive_folder


def _make_service(created_response):
    """Build a mock Drive service whose files().create().execute returns *created_response*."""
    execute = MagicMock(return_value=created_response)
    create = MagicMock()
    create.return_value.execute = execute
    files = MagicMock()
    files.return_value.create = create
    service = MagicMock()
    service.files = files
    return service


@pytest.mark.asyncio
async def test_create_folder_root_skips_resolve():
    """Parent 'root' should pass through resolve_folder_id and produce correct output."""
    api_response = {
        "id": "new-folder-id",
        "name": "My Folder",
        "webViewLink": "https://drive.google.com/drive/folders/new-folder-id",
    }
    service = _make_service(api_response)

    with patch(
        "gdrive.drive_tools.resolve_folder_id",
        new_callable=AsyncMock,
        return_value="root",
    ):
        result = await _raw_create_drive_folder(
            service,
            user_google_email="user@example.com",
            folder_name="My Folder",
            parent_folder_id="root",
        )

    assert "new-folder-id" in result
    assert "My Folder" in result
    assert "https://drive.google.com/drive/folders/new-folder-id" in result


@pytest.mark.asyncio
async def test_create_folder_custom_parent_resolves():
    """A non-root parent_folder_id should go through resolve_folder_id."""
    api_response = {
        "id": "new-folder-id",
        "name": "Sub Folder",
        "webViewLink": "https://drive.google.com/drive/folders/new-folder-id",
    }
    service = _make_service(api_response)

    with patch(
        "gdrive.drive_tools.resolve_folder_id",
        new_callable=AsyncMock,
        return_value="resolved-parent-id",
    ) as mock_resolve:
        result = await _raw_create_drive_folder(
            service,
            user_google_email="user@example.com",
            folder_name="Sub Folder",
            parent_folder_id="shortcut-id",
        )

    mock_resolve.assert_awaited_once_with(service, "shortcut-id")
    # The output message uses the original parent_folder_id, not the resolved one
    assert "shortcut-id" in result
    # But the API call should use the resolved ID
    service.files().create.assert_called_once_with(
        body={
            "name": "Sub Folder",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["resolved-parent-id"],
        },
        fields="id, name, webViewLink",
        supportsAllDrives=True,
    )


@pytest.mark.asyncio
async def test_create_folder_passes_correct_metadata():
    """Verify the metadata dict sent to the Drive API is correct."""
    api_response = {
        "id": "abc123",
        "name": "Test",
        "webViewLink": "https://drive.google.com/drive/folders/abc123",
    }
    service = _make_service(api_response)

    with patch(
        "gdrive.drive_tools.resolve_folder_id",
        new_callable=AsyncMock,
        return_value="resolved-id",
    ):
        await _raw_create_drive_folder(
            service,
            user_google_email="user@example.com",
            folder_name="Test",
            parent_folder_id="some-parent",
        )

    service.files().create.assert_called_once_with(
        body={
            "name": "Test",
            "mimeType": "application/vnd.google-apps.folder",
            "parents": ["resolved-id"],
        },
        fields="id, name, webViewLink",
        supportsAllDrives=True,
    )


@pytest.mark.asyncio
async def test_create_folder_missing_webviewlink():
    """When the API omits webViewLink, the result should have an empty link."""
    api_response = {
        "id": "abc123",
        "name": "NoLink",
    }
    service = _make_service(api_response)

    with patch(
        "gdrive.drive_tools.resolve_folder_id",
        new_callable=AsyncMock,
        return_value="root",
    ):
        result = await _raw_create_drive_folder(
            service,
            user_google_email="user@example.com",
            folder_name="NoLink",
            parent_folder_id="root",
        )

    assert "abc123" in result
    assert "NoLink" in result
