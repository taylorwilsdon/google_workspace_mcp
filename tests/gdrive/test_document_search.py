"""Drive search scope and incomplete-result handling for Docs and Sheets."""

import inspect
from unittest.mock import Mock

import pytest

from core.server import server
from core.tool_registry import get_tool_components
from gdocs.docs_tools import list_docs_in_folder, search_docs
from gdrive.drive_helpers import INCOMPLETE_SEARCH_WARNING
from gsheets.sheets_tools import list_spreadsheets


@pytest.fixture(params=[search_docs, list_spreadsheets], ids=["docs", "sheets"])
def search_tool(request):
    tool = request.param
    kwargs = {"user_google_email": "user@example.com"}
    if tool is search_docs:
        kwargs["query"] = "Quarterly"
    return inspect.unwrap(tool), kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope, expected_corpora, expected_drive_id",
    [
        ({}, "allDrives", None),
        ({"corpora": None, "drive_id": None}, "allDrives", None),
        ({"corpora": "user"}, "user", None),
        ({"corpora": "domain"}, "domain", None),
        ({"drive_id": "shared-drive"}, "drive", "shared-drive"),
        (
            {"corpora": "drive", "drive_id": "shared-drive"},
            "drive",
            "shared-drive",
        ),
    ],
)
async def test_search_scope(search_tool, scope, expected_corpora, expected_drive_id):
    tool, kwargs = search_tool
    service = Mock()
    service.files().list().execute.return_value = {"files": []}

    await tool(service=service, **kwargs, **scope)

    params = service.files().list.call_args.kwargs
    assert params["corpora"] == expected_corpora
    assert params.get("driveId") == expected_drive_id
    assert params["supportsAllDrives"] is True
    assert params["includeItemsFromAllDrives"] is True
    assert "incompleteSearch" in params["fields"]
    if tool.__name__ == "search_docs":
        assert params["pageSize"] == 10
        assert params["q"] == (
            "name contains 'Quarterly' and "
            "mimeType='application/vnd.google-apps.document' and trashed=false"
        )
    else:
        assert params["pageSize"] == 25
        assert params["orderBy"] == "modifiedTime desc"
        assert params["q"] == "mimeType='application/vnd.google-apps.spreadsheet'"


@pytest.mark.asyncio
@pytest.mark.parametrize("incomplete", [None, False, True])
@pytest.mark.parametrize("files", [[], [{"id": "f1", "name": "Quarterly"}]])
async def test_incomplete_results(search_tool, incomplete, files):
    tool, kwargs = search_tool
    service = Mock()
    response = {"files": files}
    if incomplete is not None:
        response["incompleteSearch"] = incomplete
    service.files().list().execute.return_value = response

    result = await tool(service=service, **kwargs)

    assert (INCOMPLETE_SEARCH_WARNING in result) is (incomplete is True)
    if files:
        assert "ID: f1" in result
    else:
        assert "No " in result


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [[], [{"id": "f1", "name": "Quarterly"}]])
async def test_single_page_with_token(search_tool, files):
    """Each call fetches one page and surfaces its token, even when empty."""
    tool, kwargs = search_tool
    service = Mock()
    service.files().list().execute.return_value = {
        "files": files,
        "nextPageToken": "next-page",
    }
    service.files().list.reset_mock()

    result = await tool(service=service, **kwargs, page_token="this-page")

    service.files().list.assert_called_once()
    params = service.files().list.call_args.kwargs
    assert params["pageToken"] == "this-page"
    assert "nextPageToken" in params["fields"]
    assert result.endswith("nextPageToken: next-page")
    assert f"{len(files)} " in result
    assert not result.startswith("No ")


@pytest.mark.asyncio
@pytest.mark.parametrize("files", [[], [{"id": "f1", "name": "Quarterly"}]])
async def test_list_docs_in_folder_single_page_with_token(files):
    service = Mock()
    service.files().list().execute.return_value = {
        "files": files,
        "nextPageToken": "next-page",
    }
    service.files().list.reset_mock()

    result = await inspect.unwrap(list_docs_in_folder)(
        service=service,
        user_google_email="user@example.com",
        page_token="this-page",
    )

    service.files().list.assert_called_once()
    params = service.files().list.call_args.kwargs
    assert params["pageToken"] == "this-page"
    assert "nextPageToken" in params["fields"]
    assert result.endswith("nextPageToken: next-page")
    assert result.startswith(f"Found {len(files)} Docs")


@pytest.mark.parametrize("name", ["search_docs", "list_spreadsheets"])
def test_scope_parameters_are_optional_in_tool_schema(name):
    parameters = get_tool_components(server)[name].parameters
    for param in ("page_token", "corpora", "drive_id"):
        assert param not in parameters.get("required", [])
        assert parameters["properties"][param]["default"] is None
