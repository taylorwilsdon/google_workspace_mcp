"""
Unit tests for gdrive.drive_helpers — specifically the resolve_drive_item
short-circuit for the "root" alias.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdrive.drive_helpers import (
    FOLDER_MIME_TYPE,
    resolve_drive_item,
    resolve_folder_id,
)


@pytest.mark.asyncio
async def test_resolve_drive_item_root_short_circuits_without_api_call():
    """For file_id == "root", return synthetic folder metadata without calling
    files.get. This is required so callers under the narrow drive.file scope
    (where files.get(fileId='root') 404s) can still operate on the user's
    My Drive root via the literal 'root' alias accepted by files.create/list.
    """
    service = MagicMock()
    service.files = MagicMock()

    resolved_id, metadata = await resolve_drive_item(service, "root")

    assert resolved_id == "root"
    assert metadata["id"] == "root"
    assert metadata["mimeType"] == FOLDER_MIME_TYPE
    # Critical: the short-circuit must NOT touch the Drive API.
    service.files.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_drive_item_root_ignores_extra_fields():
    """extra_fields is documented as ignored on the 'root' short-circuit path.
    Callers needing fields beyond id/mimeType for the user's root must call
    files.get themselves with a sufficient scope.
    """
    service = MagicMock()
    service.files = MagicMock()

    _, metadata = await resolve_drive_item(
        service, "root", extra_fields="owners, capabilities, driveId"
    )

    assert metadata.keys() == {"id", "mimeType"}
    service.files.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_folder_id_root_short_circuits():
    """resolve_folder_id wraps resolve_drive_item — verify the wrapper also
    benefits from the short-circuit and returns 'root' without API call.
    """
    service = MagicMock()
    service.files = MagicMock()

    resolved_id = await resolve_folder_id(service, "root")

    assert resolved_id == "root"
    service.files.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_drive_item_non_root_still_calls_api():
    """Regression guard: any non-'root' file_id must still go through the
    normal files.get path. The short-circuit must only fire on the literal
    string 'root'.
    """
    service = MagicMock()
    api_response = {
        "id": "real-folder-id",
        "name": "Real Folder",
        "mimeType": FOLDER_MIME_TYPE,
        "parents": ["some-parent"],
    }
    service.files().get().execute = MagicMock(return_value=api_response)

    resolved_id, metadata = await resolve_drive_item(service, "real-folder-id")

    assert resolved_id == "real-folder-id"
    assert metadata == api_response
    # Confirm an API call was made (path is not short-circuited).
    assert service.files().get.called
