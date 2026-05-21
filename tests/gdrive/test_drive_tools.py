"""
Unit tests for Google Drive MCP tools.

Tests create_drive_folder with mocked API responses, plus coverage for
`search_drive_files` and `list_drive_items` pagination, `detailed` output,
and `file_type` filtering behaviors.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdrive.drive_helpers import build_drive_list_params
from gdrive.drive_tools import (
    get_drive_file_permissions,
    import_to_google_doc,
    list_drive_items,
    search_drive_files,
)


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original async function.

    Handles both older FastMCP (FunctionTool with .fn) and newer FastMCP
    (server.tool() returns the function directly).
    """
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


# ---------------------------------------------------------------------------
# get_drive_file_permissions — owners
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_drive_item", new_callable=AsyncMock)
async def test_get_drive_file_permissions_includes_owner_details(mock_resolve_item):
    """Owner metadata requested from Drive is included in the output."""
    mock_resolve_item.return_value = ("file123", None)
    mock_service = Mock()
    mock_service.files().get().execute.return_value = {
        "id": "file123",
        "name": "Budget",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "parents": ["folder1"],
        "owners": [
            {
                "displayName": "Ada Lovelace",
                "emailAddress": "ada@example.com",
            },
            {
                "name": "Legacy Owner",
                "email": "legacy@example.com",
            },
        ],
        "permissions": [],
    }

    result = await _unwrap(get_drive_file_permissions)(
        service=mock_service,
        user_google_email="user@example.com",
        file_id="file123",
    )

    fields = mock_service.files.return_value.get.call_args.kwargs["fields"]
    assert "owners(displayName,emailAddress)" in fields
    assert (
        "Owners: Ada Lovelace (ada@example.com), Legacy Owner (legacy@example.com)"
        in result
    )


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_drive_item", new_callable=AsyncMock)
async def test_get_drive_file_permissions_handles_missing_owners(mock_resolve_item):
    """Missing owner metadata is represented explicitly."""
    mock_resolve_item.return_value = ("file123", None)
    mock_service = Mock()
    mock_service.files().get().execute.return_value = {
        "id": "file123",
        "name": "Budget",
        "mimeType": "application/vnd.google-apps.spreadsheet",
        "permissions": [],
    }

    result = await _unwrap(get_drive_file_permissions)(
        service=mock_service,
        user_google_email="user@example.com",
        file_id="file123",
    )

    assert "Owners: None available" in result


# ---------------------------------------------------------------------------
# search_drive_files — page_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_drive_files_page_token_passed_to_api():
    """page_token is forwarded to the Drive API as pageToken."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "f1",
                "name": "Report.pdf",
                "mimeType": "application/pdf",
                "webViewLink": "https://drive.google.com/file/f1",
                "modifiedTime": "2024-01-01T00:00:00Z",
            }
        ]
    }

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="budget",
        page_token="tok_abc123",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert call_kwargs.get("pageToken") == "tok_abc123"


@pytest.mark.asyncio
async def test_search_drive_files_next_page_token_in_output():
    """nextPageToken from the API response is appended at the end of the output."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "f2",
                "name": "Notes.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "webViewLink": "https://drive.google.com/file/f2",
                "modifiedTime": "2024-02-01T00:00:00Z",
            }
        ],
        "nextPageToken": "next_tok_xyz",
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="notes",
    )

    assert result.endswith("nextPageToken: next_tok_xyz")


@pytest.mark.asyncio
async def test_search_drive_files_no_next_page_token_when_absent():
    """nextPageToken does not appear in output when the API has no more pages."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "f3",
                "name": "Summary.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive.google.com/file/f3",
                "modifiedTime": "2024-03-01T00:00:00Z",
            }
        ]
        # no nextPageToken key
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="summary",
    )

    assert "nextPageToken" not in result


# ---------------------------------------------------------------------------
# list_drive_items — page_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_drive_items_page_token_passed_to_api(mock_resolve_folder):
    """page_token is forwarded to the Drive API as pageToken."""
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "folder1",
                "name": "Archive",
                "mimeType": "application/vnd.google-apps.folder",
                "webViewLink": "https://drive.google.com/drive/folders/folder1",
                "modifiedTime": "2024-01-15T00:00:00Z",
            }
        ]
    }

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        page_token="tok_page2",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert call_kwargs.get("pageToken") == "tok_page2"


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_drive_items_next_page_token_in_output(mock_resolve_folder):
    """nextPageToken from the API response is appended at the end of the output."""
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "file99",
                "name": "data.csv",
                "mimeType": "text/csv",
                "webViewLink": "https://drive.google.com/file/file99",
                "modifiedTime": "2024-04-01T00:00:00Z",
            }
        ],
        "nextPageToken": "next_list_tok",
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert result.endswith("nextPageToken: next_list_tok")


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_drive_items_no_next_page_token_when_absent(mock_resolve_folder):
    """nextPageToken does not appear in output when the API has no more pages."""
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "file100",
                "name": "readme.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive.google.com/file/file100",
                "modifiedTime": "2024-05-01T00:00:00Z",
            }
        ]
        # no nextPageToken key
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
    )

    assert "nextPageToken" not in result


# ---------------------------------------------------------------------------
# search_drive_files — order_by
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_drive_files_order_by_passed_to_api():
    """order_by is forwarded to the Drive API as orderBy."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "f1",
                "name": "Recent.pdf",
                "mimeType": "application/pdf",
                "webViewLink": "https://drive.google.com/file/f1",
                "modifiedTime": "2024-06-01T00:00:00Z",
            }
        ]
    }

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="test",
        order_by="modifiedTime desc",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert call_kwargs.get("orderBy") == "modifiedTime desc"


@pytest.mark.asyncio
async def test_search_drive_files_order_by_not_set_when_none():
    """orderBy is not included in API call when order_by is None."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "f2",
                "name": "File.txt",
                "mimeType": "text/plain",
                "webViewLink": "https://drive.google.com/file/f2",
                "modifiedTime": "2024-06-02T00:00:00Z",
            }
        ]
    }

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="test",
        # order_by not specified (defaults to None)
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "orderBy" not in call_kwargs


# ---------------------------------------------------------------------------
# list_drive_items — order_by
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_drive_items_order_by_passed_to_api(mock_resolve_folder):
    """order_by is forwarded to the Drive API as orderBy."""
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "folder1",
                "name": "Archive",
                "mimeType": "application/vnd.google-apps.folder",
                "webViewLink": "https://drive.google.com/drive/folders/folder1",
                "modifiedTime": "2024-06-01T00:00:00Z",
            }
        ]
    }

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        order_by="folder,modifiedTime desc",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert call_kwargs.get("orderBy") == "folder,modifiedTime desc"


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_drive_items_order_by_not_set_when_none(mock_resolve_folder):
    """orderBy is not included in API call when order_by is None."""
    mock_resolve_folder.return_value = "root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            {
                "id": "file1",
                "name": "Document.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "webViewLink": "https://drive.google.com/file/file1",
                "modifiedTime": "2024-06-02T00:00:00Z",
            }
        ]
    }

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        # order_by not specified (defaults to None)
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "orderBy" not in call_kwargs


# Helpers
# ---------------------------------------------------------------------------


def _make_file(
    file_id: str,
    name: str,
    mime_type: str,
    link: str = "http://link",
    modified: str = "2024-01-01T00:00:00Z",
    size: str | None = None,
    drive_id: str | None = None,
    created: str | None = None,
    last_modifying_user: dict | None = None,
    permissions: list | None = None,
) -> dict:
    item = {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "webViewLink": link,
        "modifiedTime": modified,
    }
    if size is not None:
        item["size"] = size
    if drive_id is not None:
        item["driveId"] = drive_id
    if created is not None:
        item["createdTime"] = created
    if last_modifying_user is not None:
        item["lastModifyingUser"] = last_modifying_user
    if permissions is not None:
        item["permissions"] = permissions
    return item


# ---------------------------------------------------------------------------
# create_drive_folder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_drive_folder():
    """Test create_drive_folder returns success message with folder id, name, and link."""
    from gdrive.drive_tools import _create_drive_folder_impl

    mock_service = Mock()
    mock_response = {
        "id": "folder123",
        "name": "My Folder",
        "webViewLink": "https://drive.google.com/drive/folders/folder123",
    }
    mock_request = Mock()
    mock_request.execute.return_value = mock_response
    mock_service.files.return_value.create.return_value = mock_request

    with patch(
        "gdrive.drive_tools.resolve_folder_id",
        new_callable=AsyncMock,
        return_value="root",
    ):
        result = await _create_drive_folder_impl(
            service=mock_service,
            user_google_email="user@example.com",
            folder_name="My Folder",
            parent_folder_id="root",
        )

    assert "Successfully created folder" in result
    assert "My Folder" in result
    assert "folder123" in result
    assert "user@example.com" in result
    assert "https://drive.google.com/drive/folders/folder123" in result


# ---------------------------------------------------------------------------
# build_drive_list_params — detailed flag (pure unit tests, no I/O)
# ---------------------------------------------------------------------------


def test_build_params_detailed_true_includes_extra_fields():
    """detailed=True requests metadata fields but omits ACLs by default."""
    params = build_drive_list_params(query="name='x'", page_size=10, detailed=True)
    assert "modifiedTime" in params["fields"]
    assert "webViewLink" in params["fields"]
    assert "size" in params["fields"]
    assert "driveId" in params["fields"]
    assert "createdTime" in params["fields"]
    assert "lastModifyingUser" in params["fields"]
    assert "permissions" not in params["fields"]


def test_build_params_include_permissions_requests_acl_fields():
    """ACL fields are requested only when explicitly needed by the caller."""
    params = build_drive_list_params(
        query="name='x'", page_size=10, detailed=True, include_permissions=True
    )
    assert "permissions" in params["fields"]


def test_build_params_detailed_false_omits_extra_fields():
    """detailed=False omits detailed metadata from the API request."""
    params = build_drive_list_params(query="name='x'", page_size=10, detailed=False)
    assert "modifiedTime" not in params["fields"]
    assert "webViewLink" not in params["fields"]
    assert "size" not in params["fields"]
    assert "driveId" not in params["fields"]
    assert "createdTime" not in params["fields"]
    assert "lastModifyingUser" not in params["fields"]
    assert "permissions" not in params["fields"]


def test_build_params_detailed_false_keeps_core_fields():
    """detailed=False still requests id, name, and mimeType."""
    params = build_drive_list_params(query="name='x'", page_size=10, detailed=False)
    assert "id" in params["fields"]
    assert "name" in params["fields"]
    assert "mimeType" in params["fields"]


def test_build_params_default_is_detailed():
    """Omitting detailed behaves identically to detailed=True."""
    params_default = build_drive_list_params(query="q", page_size=5)
    params_true = build_drive_list_params(query="q", page_size=5, detailed=True)
    assert params_default["fields"] == params_true["fields"]


def test_build_params_order_by_trims_surrounding_whitespace():
    """order_by is normalized before being sent to the Drive API."""
    params = build_drive_list_params(
        query="q", page_size=5, order_by="  modifiedTime desc  "
    )
    assert params["orderBy"] == "modifiedTime desc"


def test_build_params_order_by_omits_whitespace_only_values():
    """Whitespace-only order_by values are omitted to avoid invalid API requests."""
    params = build_drive_list_params(query="q", page_size=5, order_by="   ")
    assert "orderBy" not in params


# ---------------------------------------------------------------------------
# import_to_google_doc — upload retries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_import_to_google_doc_upload_uses_google_api_retries(mock_resolve_folder):
    """Drive uploads use googleapiclient's built-in retry handling."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().create().execute.return_value = {
        "id": "doc123",
        "name": "My Doc",
        "webViewLink": "https://docs.google.com/document/d/doc123",
        "mimeType": "application/vnd.google-apps.document",
    }

    result = await _unwrap(import_to_google_doc)(
        service=mock_service,
        user_google_email="user@example.com",
        file_name="My Doc.md",
        content="# Title",
        source_format="md",
        folder_id="root",
    )

    assert "Successfully imported" in result
    execute_kwargs = (
        mock_service.files.return_value.create.return_value.execute.call_args.kwargs
    )
    assert execute_kwargs["num_retries"] == 3


# ---------------------------------------------------------------------------
# search_drive_files — detailed flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_detailed_true_output_includes_metadata():
    """detailed=True (default) includes modified time and link in output."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "My Doc",
                "application/vnd.google-apps.document",
                modified="2024-06-01T12:00:00Z",
                link="http://link/f1",
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="my doc",
        detailed=True,
    )

    assert "My Doc" in result
    assert "2024-06-01T12:00:00Z" in result
    assert "http://link/f1" in result


@pytest.mark.asyncio
async def test_search_detailed_false_output_excludes_metadata():
    """detailed=False omits modified time and link from output."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "My Doc",
                "application/vnd.google-apps.document",
                modified="2024-06-01T12:00:00Z",
                link="http://link/f1",
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="my doc",
        detailed=False,
    )

    assert "My Doc" in result
    assert "f1" in result
    assert "2024-06-01T12:00:00Z" not in result
    assert "http://link/f1" not in result


@pytest.mark.asyncio
async def test_search_detailed_true_with_size():
    """When the item has a size field, detailed=True includes it in output."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file("f2", "Big File", "application/pdf", size="102400"),
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="big",
        detailed=True,
    )

    assert "102400" in result


@pytest.mark.asyncio
async def test_search_detailed_shows_created_time():
    """detailed=True shows createdTime when present."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "Doc",
                "application/vnd.google-apps.document",
                created="2024-05-15T09:00:00Z",
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="doc",
        detailed=True,
    )

    assert "Created: 2024-05-15T09:00:00Z" in result


@pytest.mark.asyncio
async def test_search_detailed_shows_last_modifying_user():
    """detailed=True shows lastModifyingUser displayName and email."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "Doc",
                "application/vnd.google-apps.document",
                last_modifying_user={
                    "displayName": "Alice Smith",
                    "emailAddress": "alice@example.com",
                },
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="doc",
        detailed=True,
    )

    assert "Last Edited By: Alice Smith <alice@example.com>" in result


@pytest.mark.asyncio
async def test_search_detailed_shows_anyone_permission_role():
    """detailed=True shows the role on an anyone-with-link permission."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "Public Doc",
                "application/vnd.google-apps.document",
                permissions=[
                    {"id": "anyoneWithLink", "type": "anyone", "role": "writer"},
                ],
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="public",
        detailed=True,
    )

    assert "Anyone with link: writer" in result


@pytest.mark.asyncio
async def test_search_detailed_no_anyone_permission():
    """When no anyone permission exists, the anyone-with-link field is absent."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "Private Doc",
                "application/vnd.google-apps.document",
                permissions=[
                    {"id": "user123", "type": "user", "role": "writer"},
                ],
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="private",
        detailed=True,
    )

    assert "Anyone with link" not in result


@pytest.mark.asyncio
async def test_search_detailed_all_new_fields_together():
    """All new metadata fields appear together in output."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "f1",
                "Full Doc",
                "application/vnd.google-apps.document",
                created="2024-03-01T10:00:00Z",
                last_modifying_user={
                    "displayName": "Bob",
                    "emailAddress": "bob@example.com",
                },
                permissions=[
                    {"id": "anyoneWithLink", "type": "anyone", "role": "reader"},
                ],
            )
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="full",
        detailed=True,
    )

    assert "Created: 2024-03-01T10:00:00Z" in result
    assert "Last Edited By: Bob <bob@example.com>" in result
    assert "Anyone with link: reader" in result
    # Existing fields still present
    assert "Modified:" in result
    assert "Link:" in result


@pytest.mark.asyncio
async def test_search_detailed_true_requests_extra_api_fields():
    """detailed=True passes full fields string to the Drive API."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="anything",
        detailed=True,
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "modifiedTime" in call_kwargs["fields"]
    assert "webViewLink" in call_kwargs["fields"]
    assert "size" in call_kwargs["fields"]
    assert "createdTime" in call_kwargs["fields"]
    assert "lastModifyingUser" in call_kwargs["fields"]
    assert "permissions" in call_kwargs["fields"]


@pytest.mark.asyncio
async def test_search_detailed_false_requests_compact_api_fields():
    """detailed=False passes compact fields string to the Drive API."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="anything",
        detailed=False,
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "modifiedTime" not in call_kwargs["fields"]
    assert "webViewLink" not in call_kwargs["fields"]
    assert "size" not in call_kwargs["fields"]
    assert "createdTime" not in call_kwargs["fields"]
    assert "lastModifyingUser" not in call_kwargs["fields"]
    assert "permissions" not in call_kwargs["fields"]


@pytest.mark.asyncio
async def test_search_default_detailed_matches_detailed_true():
    """Omitting detailed produces the same output as detailed=True."""
    file = _make_file(
        "f1",
        "Doc",
        "application/vnd.google-apps.document",
        modified="2024-01-01T00:00:00Z",
        link="http://l",
    )

    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": [file]}
    result_default = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="doc",
    )

    mock_service.files().list().execute.return_value = {"files": [file]}
    result_true = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="doc",
        detailed=True,
    )

    assert result_default == result_true


# ---------------------------------------------------------------------------
# list_drive_items — detailed flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_true_output_includes_metadata(mock_resolve_folder):
    """detailed=True (default) includes modified time and link in output."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "id1",
                "Report",
                "application/vnd.google-apps.document",
                modified="2024-03-15T08:00:00Z",
                link="http://link/id1",
            )
        ]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=True,
    )

    assert "Report" in result
    assert "2024-03-15T08:00:00Z" in result
    assert "http://link/id1" in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_false_output_excludes_metadata(mock_resolve_folder):
    """detailed=False omits modified time and link from output."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "id1",
                "Report",
                "application/vnd.google-apps.document",
                modified="2024-03-15T08:00:00Z",
                link="http://link/id1",
            )
        ]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=False,
    )

    assert "Report" in result
    assert "id1" in result
    assert "2024-03-15T08:00:00Z" not in result
    assert "http://link/id1" not in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_true_with_size(mock_resolve_folder):
    """When item has a size field, detailed=True includes it in output."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file("id2", "Big File", "application/pdf", size="204800"),
        ]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=True,
    )

    assert "204800" in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_true_with_drive_id(mock_resolve_folder):
    """When item has a driveId field, detailed=True includes it in output."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "id3",
                "Shared File",
                "application/pdf",
                drive_id="shared-drive-123",
            ),
        ]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=True,
    )

    assert "Drive ID: shared-drive-123" in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_true_shows_created_and_last_editor(mock_resolve_folder):
    """detailed=True shows createdTime and lastModifyingUser in list output."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file(
                "id4",
                "Report",
                "application/vnd.google-apps.document",
                created="2024-06-01T12:00:00Z",
                last_modifying_user={
                    "displayName": "Carol",
                    "emailAddress": "carol@example.com",
                },
            ),
        ]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=True,
    )

    assert "Created: 2024-06-01T12:00:00Z" in result
    assert "Last Edited By: Carol <carol@example.com>" in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_true_requests_extra_api_fields(mock_resolve_folder):
    """detailed=True passes full fields string to the Drive API."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=True,
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "modifiedTime" in call_kwargs["fields"]
    assert "webViewLink" in call_kwargs["fields"]
    assert "size" in call_kwargs["fields"]
    assert "driveId" in call_kwargs["fields"]
    assert "permissions" not in call_kwargs["fields"]


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_detailed_false_requests_compact_api_fields(mock_resolve_folder):
    """detailed=False passes compact fields string to the Drive API."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        detailed=False,
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "modifiedTime" not in call_kwargs["fields"]
    assert "webViewLink" not in call_kwargs["fields"]
    assert "size" not in call_kwargs["fields"]


# ---------------------------------------------------------------------------
# Existing behavior coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_free_text_returns_results():
    """Free-text query is wrapped in fullText contains and results are formatted."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file("f1", "My Doc", "application/vnd.google-apps.document"),
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="my doc",
    )

    assert "Found 1 files" in result
    assert "My Doc" in result
    assert "f1" in result


@pytest.mark.asyncio
async def test_search_no_results():
    """No results returns a clear message."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="nothing here",
    )

    assert "No files found" in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_basic(mock_resolve_folder):
    """Basic listing without filters returns all items."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file("id1", "Folder A", "application/vnd.google-apps.folder"),
            _make_file("id2", "Doc B", "application/vnd.google-apps.document"),
        ]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
    )

    assert "Found 2 items" in result
    assert "Folder A" in result
    assert "Doc B" in result


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_no_results(mock_resolve_folder):
    """Empty folder returns a clear message."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
    )

    assert "No items found" in result


# ---------------------------------------------------------------------------
# file_type filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_file_type_folder_adds_mime_filter():
    """file_type='folder' appends the folder MIME type to the query."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [
            _make_file("fold1", "My Folder", "application/vnd.google-apps.folder")
        ]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="my",
        file_type="folder",
    )

    assert "Found 1 files" in result
    assert "My Folder" in result

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/vnd.google-apps.folder'" in call_kwargs["q"]


@pytest.mark.asyncio
async def test_search_file_type_document_alias():
    """Alias 'doc' resolves to the Google Docs MIME type."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="report",
        file_type="doc",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/vnd.google-apps.document'" in call_kwargs["q"]


@pytest.mark.asyncio
async def test_search_file_type_plural_alias():
    """Plural aliases are resolved for friendlier natural-language usage."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="project",
        file_type="folders",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/vnd.google-apps.folder'" in call_kwargs["q"]


@pytest.mark.asyncio
async def test_search_file_type_sheet_alias():
    """Alias 'sheet' resolves to the Google Sheets MIME type."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="budget",
        file_type="sheet",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/vnd.google-apps.spreadsheet'" in call_kwargs["q"]


@pytest.mark.asyncio
async def test_search_file_type_raw_mime():
    """A raw MIME type string is passed through unchanged."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [_make_file("p1", "Report.pdf", "application/pdf")]
    }

    result = await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="report",
        file_type="application/pdf",
    )

    assert "Report.pdf" in result
    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/pdf'" in call_kwargs["q"]


@pytest.mark.asyncio
async def test_search_file_type_none_no_mime_filter():
    """When file_type is None no mimeType clause is added to the query."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="anything",
        file_type=None,
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType" not in call_kwargs["q"]


@pytest.mark.asyncio
async def test_search_file_type_structured_query_combined():
    """file_type filter is appended even when the query is already structured."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="name contains 'budget'",
        file_type="spreadsheet",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    q = call_kwargs["q"]
    assert "name contains 'budget'" in q
    assert "mimeType = 'application/vnd.google-apps.spreadsheet'" in q


@pytest.mark.asyncio
async def test_search_file_type_unknown_raises_value_error():
    """An unrecognised friendly type name raises ValueError immediately."""
    mock_service = Mock()

    with pytest.raises(ValueError, match="Unknown file_type"):
        await _unwrap(search_drive_files)(
            service=mock_service,
            user_google_email="user@example.com",
            query="something",
            file_type="notatype",
        )


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_file_type_folder_adds_mime_filter(mock_resolve_folder):
    """file_type='folder' appends the folder MIME clause to the query."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {
        "files": [_make_file("sub1", "SubFolder", "application/vnd.google-apps.folder")]
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        file_type="folder",
    )

    assert "Found 1 items" in result
    assert "SubFolder" in result

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    q = call_kwargs["q"]
    assert "'resolved_root' in parents" in q
    assert "trashed=false" in q
    assert "mimeType = 'application/vnd.google-apps.folder'" in q


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_file_type_spreadsheet(mock_resolve_folder):
    """file_type='spreadsheet' appends the Sheets MIME clause."""
    mock_resolve_folder.return_value = "folder_xyz"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="folder_xyz",
        file_type="spreadsheet",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/vnd.google-apps.spreadsheet'" in call_kwargs["q"]


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_file_type_raw_mime(mock_resolve_folder):
    """A raw MIME type string is passed through unchanged."""
    mock_resolve_folder.return_value = "folder_abc"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="folder_abc",
        file_type="application/pdf",
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType = 'application/pdf'" in call_kwargs["q"]


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_file_type_none_no_mime_filter(mock_resolve_folder):
    """When file_type is None no mimeType clause is added."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        folder_id="root",
        file_type=None,
    )

    call_kwargs = mock_service.files.return_value.list.call_args.kwargs
    assert "mimeType" not in call_kwargs["q"]


@pytest.mark.asyncio
@patch("gdrive.drive_tools.resolve_folder_id", new_callable=AsyncMock)
async def test_list_items_file_type_unknown_raises(mock_resolve_folder):
    """An unrecognised friendly type name raises ValueError."""
    mock_resolve_folder.return_value = "resolved_root"
    mock_service = Mock()

    with pytest.raises(ValueError, match="Unknown file_type"):
        await _unwrap(list_drive_items)(
            service=mock_service,
            user_google_email="user@example.com",
            folder_id="root",
            file_type="unknowntype",
        )


# ---------------------------------------------------------------------------
# list_drive_items — shared drive containers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_drive_items_can_list_shared_drives():
    """resource_type='shared_drives' uses the shared drive list endpoint."""
    mock_service = Mock()
    mock_service.drives().list().execute.return_value = {
        "drives": [
            {
                "id": "drive1",
                "name": "Engineering",
                "createdTime": "2024-01-01T00:00:00Z",
                "hidden": False,
                "capabilities": {"canManageMembers": True, "canEdit": True},
                "restrictions": {"adminManagedRestrictions": False},
            }
        ],
        "nextPageToken": "shared_next",
    }

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        resource_type="shared_drives",
        page_size=250,
        page_token="shared_page",
        query="name contains 'Engineering'",
    )

    call_kwargs = mock_service.drives.return_value.list.call_args.kwargs
    assert call_kwargs["pageSize"] == 100
    assert call_kwargs["pageToken"] == "shared_page"
    assert call_kwargs["q"] == "name contains 'Engineering'"
    assert "Found 1 shared drives" in result
    assert 'Name: "Engineering" (ID: drive1' in result
    assert result.endswith("nextPageToken: shared_next")
    mock_service.files.assert_not_called()


@pytest.mark.asyncio
async def test_list_drive_items_shared_drives_can_include_organizers():
    """include_organizers adds organizer principals to shared drive output."""
    mock_service = Mock()
    mock_service.drives().list().execute.return_value = {
        "drives": [
            {
                "id": "drive1",
                "name": "Engineering",
                "capabilities": {},
                "restrictions": {},
            }
        ]
    }
    mock_service.permissions.return_value.list.return_value.execute.side_effect = [
        {
            "permissions": [
                {
                    "emailAddress": "lead@example.com",
                    "displayName": "Eng Lead",
                    "role": "organizer",
                    "type": "user",
                },
                {
                    "emailAddress": "reader@example.com",
                    "role": "reader",
                    "type": "user",
                },
            ],
            "nextPageToken": "permission_page_2",
        },
        {
            "permissions": [
                {
                    "emailAddress": "second-lead@example.com",
                    "displayName": "Second Eng Lead",
                    "role": "organizer",
                    "type": "user",
                }
            ]
        },
    ]

    result = await _unwrap(list_drive_items)(
        service=mock_service,
        user_google_email="user@example.com",
        resource_type="shared_drives",
        include_organizers=True,
    )

    permissions_calls = mock_service.permissions.return_value.list.call_args_list
    assert permissions_calls[0].kwargs["fileId"] == "drive1"
    assert permissions_calls[0].kwargs["supportsAllDrives"] is True
    assert "nextPageToken" in permissions_calls[0].kwargs["fields"]
    assert "pageToken" not in permissions_calls[0].kwargs
    assert "nextPageToken" in permissions_calls[1].kwargs["fields"]
    assert permissions_calls[1].kwargs["pageToken"] == "permission_page_2"
    assert "Organizer (user): lead@example.com" in result
    assert "Organizer (user): second-lead@example.com" in result
    assert "reader@example.com" not in result


@pytest.mark.asyncio
async def test_list_drive_items_invalid_resource_type_raises():
    """Unknown resource types are rejected before calling Drive APIs."""
    mock_service = Mock()

    with pytest.raises(ValueError, match="resource_type"):
        await _unwrap(list_drive_items)(
            service=mock_service,
            user_google_email="user@example.com",
            resource_type="calendars",
        )

    mock_service.files.assert_not_called()
    mock_service.drives.assert_not_called()


# ---------------------------------------------------------------------------
# OR-precedence grouping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_or_query_is_grouped_before_mime_filter():
    """An OR structured query is wrapped in parentheses so MIME filter precedence is correct."""
    mock_service = Mock()
    mock_service.files().list().execute.return_value = {"files": []}

    await _unwrap(search_drive_files)(
        service=mock_service,
        user_google_email="user@example.com",
        query="name contains 'a' or name contains 'b'",
        file_type="document",
    )

    q = mock_service.files.return_value.list.call_args.kwargs["q"]
    assert q.startswith("(")
    assert "name contains 'a' or name contains 'b'" in q
    assert ") and mimeType = 'application/vnd.google-apps.document'" in q


# ---------------------------------------------------------------------------
# MIME type validation
# ---------------------------------------------------------------------------


def test_resolve_file_type_mime_invalid_mime_raises():
    """A raw string with '/' but containing quotes raises ValueError."""
    from gdrive.drive_helpers import resolve_file_type_mime

    with pytest.raises(ValueError, match="Invalid MIME type"):
        resolve_file_type_mime("application/pdf' or '1'='1")


def test_resolve_file_type_mime_strips_whitespace():
    """Leading/trailing whitespace is stripped from raw MIME strings."""
    from gdrive.drive_helpers import resolve_file_type_mime

    assert resolve_file_type_mime("  application/pdf  ") == "application/pdf"


def test_resolve_file_type_mime_normalizes_case():
    """Raw MIME types are normalized to lowercase for Drive query consistency."""
    from gdrive.drive_helpers import resolve_file_type_mime

    assert resolve_file_type_mime("Application/PDF") == "application/pdf"


def test_resolve_file_type_mime_empty_raises():
    """Blank values are rejected with a clear validation error."""
    from gdrive.drive_helpers import resolve_file_type_mime

    with pytest.raises(ValueError, match="cannot be empty"):
        resolve_file_type_mime("   ")
