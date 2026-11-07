from unittest.mock import AsyncMock, Mock, patch

import pytest
from googleapiclient.discovery import build

from gdrive.drive_tools import copy_drive_file


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


async def _copy(**kwargs):
    service = Mock()
    service.files().copy().execute = Mock(
        return_value={
            "id": "copy-1",
            "name": "Copy of Doc",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/copy-1/edit",
        }
    )
    with (
        patch(
            "gdrive.drive_tools.resolve_drive_item",
            new=AsyncMock(return_value=("file-1", {"name": "Doc"})),
        ),
        patch(
            "gdrive.drive_tools.resolve_folder_id",
            new=AsyncMock(return_value="root"),
        ),
    ):
        await _unwrap(copy_drive_file)(
            service, user_google_email="you@gmail.com", file_id="file-1", **kwargs
        )
    return service.files().copy.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, expected", [({"copy_comments": True}, True), ({}, False)]
)
async def test_copy_comments_reaches_files_copy(kwargs, expected):
    assert (await _copy(**kwargs))["copyComments"] is expected


def test_drive_discovery_accepts_copy_comments():
    service = build("drive", "v3", developerKey="test", static_discovery=True)
    request = service.files().copy(fileId="file-1", body={}, copyComments=True)
    assert "copyComments=true" in request.uri
