"""Tests for create_doc folder placement (folder_id parameter)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from auth.scopes import DOCS_WRITE_SCOPE, DRIVE_FILE_SCOPE, DRIVE_SCOPE
from core.server import server
from core.tool_registry import get_tool_components
from gdocs.docs_tools import create_doc


def _docs_mock(document_id="doc-123"):
    """Docs service mock for documents().create() and documents().batchUpdate()."""
    service = Mock()
    service.documents().create().execute = Mock(
        return_value={"documentId": document_id}
    )
    service.documents().batchUpdate().execute = Mock(return_value={})
    return service


def _patch_placement(**mock_kwargs):
    """Stand in for the on-demand Drive move; its mechanics are tested separately."""
    return patch(
        "gdrive.drive_helpers.place_created_file_in_folder",
        new=AsyncMock(**mock_kwargs),
    )


async def _call_create_doc(docs_service, **overrides):
    """Call the undecorated implementation to keep auth out of unit tests."""
    impl = create_doc.__wrapped__.__wrapped__
    defaults = {
        "service": docs_service,
        "user_google_email": "user@example.com",
        "title": "My Doc",
    }
    defaults.update(overrides)
    return await impl(**defaults)


def test_create_doc_schema_exposes_optional_folder_id():
    components = get_tool_components(server)
    parameters = components["create_doc"].parameters

    assert "folder_id" not in parameters["required"]
    assert parameters["properties"]["folder_id"]["default"] == "root"


def test_create_doc_requires_no_drive_scope():
    """
    Drive is acquired on demand inside the tool, never by its decorator.

    Declaring it on the decorator would authenticate Drive before the function
    runs, so the folder_id="root" default would fail for a Docs-only grant, and
    the permission filter would drop the tool from registries lacking the scope.
    """
    required = create_doc._required_google_scopes

    assert required == [DOCS_WRITE_SCOPE]
    assert DRIVE_FILE_SCOPE not in required
    assert DRIVE_SCOPE not in required


@pytest.mark.asyncio
async def test_create_doc_defaults_to_root_and_skips_drive():
    docs_service = _docs_mock()

    with _patch_placement() as place:
        result = await _call_create_doc(docs_service)

    place.assert_not_awaited()
    assert "Placed in folder" not in result
    assert "Created Google Doc 'My Doc' (ID: doc-123)" in result
    assert "https://docs.google.com/document/d/doc-123/edit" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("folder_id", ["root", "", None])
async def test_create_doc_skips_the_move_for_root_folder_ids(folder_id):
    """No Drive authentication or round-trip when no real folder is requested."""
    docs_service = _docs_mock()

    with _patch_placement() as place:
        result = await _call_create_doc(docs_service, folder_id=folder_id)

    place.assert_not_awaited()
    assert "Placed in folder" not in result


@pytest.mark.asyncio
async def test_create_doc_moves_new_doc_into_requested_folder():
    docs_service = _docs_mock()

    with _patch_placement(return_value="resolved-folder") as place:
        result = await _call_create_doc(docs_service, folder_id="folder-abc")

    place.assert_awaited_once_with(
        user_google_email="user@example.com",
        file_id="doc-123",
        folder_id="folder-abc",
        tool_name="create_doc",
    )
    assert "Placed in folder 'folder-abc'." in result


@pytest.mark.asyncio
async def test_create_doc_still_creates_doc_with_plain_title_body():
    """The Docs create call must stay title-only; the folder is a Drive concern."""
    docs_service = _docs_mock()

    with _patch_placement():
        await _call_create_doc(docs_service, folder_id="folder-abc")

    assert docs_service.documents().create.call_args.kwargs["body"] == {
        "title": "My Doc"
    }


@pytest.mark.asyncio
async def test_create_doc_inserts_content_after_moving():
    docs_service = _docs_mock()

    with _patch_placement():
        result = await _call_create_doc(
            docs_service, content="Hello", folder_id="folder-abc"
        )

    batch_kwargs = docs_service.documents().batchUpdate.call_args.kwargs
    assert batch_kwargs["documentId"] == "doc-123"
    assert batch_kwargs["body"]["requests"] == [
        {"insertText": {"location": {"index": 1}, "text": "Hello"}}
    ]
    assert "Initial content: 5 characters inserted." in result
    assert "Placed in folder 'folder-abc'." in result


@pytest.mark.asyncio
async def test_create_doc_without_content_skips_batch_update():
    docs_service = _docs_mock()
    docs_service.documents().batchUpdate.reset_mock()

    with _patch_placement():
        result = await _call_create_doc(docs_service, folder_id="folder-abc")

    docs_service.documents().batchUpdate.assert_not_called()
    assert "Document is empty" in result


@pytest.mark.asyncio
async def test_create_doc_reports_invalid_folder_without_orphaning_the_doc():
    """A failed move must still surface the doc ID, or the new doc is unreachable."""
    docs_service = _docs_mock()

    with _patch_placement(side_effect=Exception("is not a folder")):
        result = await _call_create_doc(docs_service, folder_id="not-a-folder")

    assert "doc-123" in result
    assert "is not a folder" in result
    assert "My Drive root" in result
    assert "Placed in folder" not in result


@pytest.mark.asyncio
async def test_create_doc_survives_missing_drive_authorization():
    """
    A Docs-only grant still creates the doc; only the move is reported as failed.

    The on-demand Drive service raises at authentication time for a caller
    without drive.file, which must not cost the caller the document.
    """
    docs_service = _docs_mock()

    with _patch_placement(side_effect=Exception("credentials lack required scopes")):
        result = await _call_create_doc(docs_service, folder_id="folder-abc")

    assert "doc-123" in result
    assert "credentials lack required scopes" in result
    assert "Placed in folder 'folder-abc'." not in result
