"""Tests for reading a single tab and for clearing inherited list formatting."""

import sys
import os
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gdocs import docs_tools


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _tab(tab_id, title, text, child_tabs=None):
    return {
        "tabProperties": {"tabId": tab_id, "title": title},
        "documentTab": {
            "body": {
                "content": [
                    {
                        "startIndex": 1,
                        "endIndex": 1 + len(text),
                        "paragraph": {
                            "elements": [{"textRun": {"content": text}}],
                        },
                    }
                ]
            }
        },
        "childTabs": child_tabs or [],
    }


TABBED_DOC = {
    "tabs": [
        _tab("t.week1", "Week 1", "First week notes\n"),
        _tab("t.week2", "Week 2", "Second week notes\n"),
    ]
}


def _drive_service():
    service = Mock()
    service.files.return_value.get.return_value.execute = Mock(
        return_value={
            "id": "doc123",
            "name": "Log",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/doc123/edit",
        }
    )
    return service


def _docs_service(doc):
    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(return_value=doc)
    return service


@pytest.mark.asyncio
async def test_get_doc_content_returns_only_the_named_tab():
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=_drive_service(),
        docs_service=_docs_service(TABBED_DOC),
        user_google_email="user@example.com",
        document_id="doc123",
        tab_id="t.week2",
    )

    content = result.split("--- CONTENT ---\n", 1)[1]
    assert content == "Second week notes\n"
    # No tab separator, so the content stays index-aligned with the tab.
    assert "--- TAB:" not in content
    assert "[tab: Week 2]" in result


@pytest.mark.asyncio
async def test_get_doc_content_without_tab_id_returns_every_tab():
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=_drive_service(),
        docs_service=_docs_service(TABBED_DOC),
        user_google_email="user@example.com",
        document_id="doc123",
    )

    assert "First week notes" in result
    assert "Second week notes" in result


@pytest.mark.asyncio
async def test_get_doc_content_reports_an_unknown_tab():
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=_drive_service(),
        docs_service=_docs_service(TABBED_DOC),
        user_google_email="user@example.com",
        document_id="doc123",
        tab_id="t.missing",
    )

    assert "not found" in result


@pytest.mark.asyncio
async def test_get_doc_content_finds_a_nested_child_tab():
    doc = {
        "tabs": [
            _tab(
                "t.parent",
                "Parent",
                "Parent text\n",
                [_tab("t.child", "Child", "Child text\n")],
            )
        ]
    }

    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=_drive_service(),
        docs_service=_docs_service(doc),
        user_google_email="user@example.com",
        document_id="doc123",
        tab_id="t.child",
    )

    assert result.split("--- CONTENT ---\n", 1)[1] == "Child text\n"


@pytest.mark.asyncio
async def test_inspect_doc_structure_requests_a_field_mask():
    service = _docs_service({"body": {"content": []}})

    await _unwrap(docs_tools.inspect_doc_structure)(
        service=service,
        user_google_email="user@example.com",
        document_id="doc123",
    )

    call_kwargs = service.documents.return_value.get.call_args.kwargs
    assert "fields" in call_kwargs
    # The mask must still name everything the structure parser reads.
    for field in ("startIndex", "paragraph", "table", "sectionBreak", "tabs"):
        assert field in call_kwargs["fields"]


@pytest.mark.asyncio
async def test_update_paragraph_style_none_removes_bullets():
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute = Mock(
        return_value={}
    )

    result = await _unwrap(docs_tools.update_paragraph_style)(
        service=service,
        user_google_email="user@example.com",
        document_id="doc123",
        start_index=10,
        end_index=40,
        list_type="NONE",
    )

    requests = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]
    assert any("deleteParagraphBullets" in request for request in requests)
    assert not any("createParagraphBullets" in request for request in requests)
    assert "Error" not in result


@pytest.mark.asyncio
async def test_update_paragraph_style_rejects_nesting_with_none():
    result = await _unwrap(docs_tools.update_paragraph_style)(
        service=Mock(),
        user_google_email="user@example.com",
        document_id="doc123",
        start_index=10,
        end_index=40,
        list_type="NONE",
        list_nesting_level=1,
    )

    assert "cannot be used with list_type='NONE'" in result
