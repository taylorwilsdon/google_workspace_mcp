"""Tests for reading a single tab and for clearing inherited list formatting."""

import sys
import os
import json
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


DOC_URL = "https://docs.google.com/document/d/doc123/edit"


@pytest.mark.asyncio
@pytest.mark.parametrize("tab_value", ["t.week2", "t%2Eweek%32"])
async def test_get_doc_content_extracts_the_id_and_tab_from_a_url(tab_value):
    drive = _drive_service()
    docs = _docs_service(TABBED_DOC)

    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=docs,
        user_google_email="user@example.com",
        document_id=f"{DOC_URL}?tab={tab_value}",
    )

    assert drive.files.return_value.get.call_args.kwargs["fileId"] == "doc123"
    assert docs.documents.return_value.get.call_args.kwargs["documentId"] == "doc123"
    header, content = result.split("--- CONTENT ---\n", 1)
    # The notice sits in the header so the content stays index-aligned.
    assert content == "Second week notes\n"
    assert "Showing only tab 'Week 2' (t.week2)" in header


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["", "#?tab=t.week2", "#heading=h.x&tab=t.week2"])
async def test_get_doc_content_reads_every_tab_of_a_url_without_tab(suffix):
    drive = _drive_service()

    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=_docs_service(TABBED_DOC),
        user_google_email="user@example.com",
        document_id=f"{DOC_URL}{suffix}",
    )

    assert drive.files.return_value.get.call_args.kwargs["fileId"] == "doc123"
    assert "First week notes" in result
    assert "Second week notes" in result
    assert "Showing only tab" not in result


async def _get_doc_as_markdown(doc, comments=None, **kwargs):
    drive = Mock()
    drive.comments.return_value.list.return_value.execute = Mock(
        return_value={"comments": comments or []}
    )
    docs = _docs_service(doc)
    result = await _unwrap(docs_tools.get_doc_as_markdown)(
        drive_service=drive,
        docs_service=docs,
        user_google_email="user@example.com",
        **kwargs,
    )
    assert docs.documents.return_value.get.call_args.kwargs["documentId"] == "doc123"
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("tab_value", ["t.week2", "t%2Eweek%32"])
async def test_get_doc_as_markdown_reads_the_tab_named_in_the_url(tab_value):
    result = await _get_doc_as_markdown(
        TABBED_DOC,
        document_id=f"{DOC_URL}?usp=sharing&tab={tab_value}#heading=h.x",
        include_comments=False,
    )

    assert "Second week notes" in result
    assert "First week notes" not in result
    # Browsers add ?tab= to every URL, so the narrowed read must say so.
    assert "Showing only tab 'Week 2' (t.week2)" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment", ["?tab=t.week2", "heading=h.x&tab=t.week2"])
async def test_get_doc_as_markdown_ignores_tab_parameters_in_the_fragment(fragment):
    result = await _get_doc_as_markdown(
        TABBED_DOC,
        document_id=f"{DOC_URL}#{fragment}",
        include_comments=False,
    )

    assert "First week notes" in result
    assert "Second week notes" in result
    assert "Showing only tab" not in result


@pytest.mark.asyncio
async def test_get_doc_as_markdown_explicit_tab_id_overrides_the_url():
    result = await _get_doc_as_markdown(
        TABBED_DOC,
        document_id=f"{DOC_URL}?tab=t.week2",
        tab_id="t.week1",
        include_comments=False,
    )

    assert result == "First week notes\n"


@pytest.mark.asyncio
async def test_get_doc_as_markdown_empty_tab_id_reads_every_tab_of_a_url():
    result = await _get_doc_as_markdown(
        TABBED_DOC,
        document_id=f"{DOC_URL}?tab=t.week2",
        tab_id="",
        include_comments=False,
    )

    assert "First week notes" in result
    assert "Second week notes" in result


@pytest.mark.asyncio
async def test_get_doc_as_markdown_url_tab_of_a_single_tab_doc_adds_no_notice():
    result = await _get_doc_as_markdown(
        {"tabs": [_tab("t.0", "Tab 1", "Only tab\n")]},
        document_id=f"{DOC_URL}?tab=t.0",
        include_comments=False,
    )

    assert result == "Only tab\n"


@pytest.mark.asyncio
async def test_get_doc_as_markdown_selected_parent_excludes_child_tabs():
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

    result = await _get_doc_as_markdown(
        doc, document_id="doc123", tab_id="t.parent", include_comments=False
    )

    assert result == "Parent text\n"


@pytest.mark.asyncio
async def test_get_doc_as_markdown_keeps_comments_document_wide_for_one_tab():
    comment = {
        "content": "Belongs to another tab",
        "author": {"displayName": "Ann"},
        "quotedFileContent": {"value": "week notes"},
    }

    result = await _get_doc_as_markdown(
        TABBED_DOC, comments=[comment], document_id="doc123", tab_id="t.week1"
    )

    # Drive comments have no tab association, so the anchor text matching
    # this tab must not pin the comment inline.
    assert "[^c1]" not in result
    assert "## Comments (entire document)" in result
    assert "Belongs to another tab" in result


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
async def test_inspect_doc_structure_field_mask_omits_legacy_top_level_content():
    """Regression test for #1108.

    documents.get() rejects a field mask that names top-level body/headers/
    footers alongside tabs(...) once includeTabsContent=True is set, with
    "Field mask may not contain legacy text-level Document resource fields
    while requesting tabs content". Since the call always sets
    includeTabsContent=True and Google leaves those legacy fields empty
    anyway in that mode, the mask must not request them at the top level,
    only within the tabs(...) branch. This includes documentStyle and
    namedRanges, which are also legacy fields at the document level.
    """
    service = _docs_service(TABBED_DOC)

    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=service,
        user_google_email="user@example.com",
        document_id="doc123",
    )

    call_kwargs = service.documents.return_value.get.call_args.kwargs
    assert call_kwargs.get("includeTabsContent") is True

    # Pin the whole request independently of the production constant. Checking
    # only the prefix before tabs(...) misses legacy fields appended after it.
    assert call_kwargs["fields"] == (
        "title,tabs(tabProperties,childTabs,documentTab("
        "documentStyle,namedRanges,headers,footers,body(content("
        "startIndex,endIndex,"
        "paragraph(elements(startIndex,endIndex,textRun/content),paragraphStyle,"
        "bullet,positionedObjectIds,suggestedPositionedObjectIds),"
        "table(tableRows/tableCells(startIndex,endIndex,content),tableStyle),"
        "sectionBreak/sectionStyle,tableOfContents))))"
    )
    data = json.loads(result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0])
    assert data["total_elements"] == 1
    assert data["total_length"] == 1 + len("First week notes\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("detailed", [False, True])
@pytest.mark.parametrize("populated", [False, True])
@pytest.mark.parametrize("tab_id", [None, "t.child"])
async def test_inspection_scopes_segment_style_ids_to_tab(detailed, populated, tab_id):
    def document_tab(prefix):
        content = {
            "documentStyle": {
                "defaultHeaderId": f"{prefix}.header",
                "defaultFooterId": f"{prefix}.footer",
            },
            "body": {
                "content": [
                    {
                        "startIndex": 0,
                        "endIndex": 1,
                        "sectionBreak": {
                            "sectionStyle": {
                                "firstPageHeaderId": f"{prefix}.section_header",
                                "firstPageFooterId": f"{prefix}.section_footer",
                            }
                        },
                    }
                ]
            },
        }
        if populated:
            for kind in ("header", "footer"):
                content[f"{kind}s"] = {f"{prefix}.content_{kind}": {"content": []}}
        return content

    doc = {
        **document_tab("legacy"),
        "tabs": [
            {
                "tabProperties": {"tabId": "t.parent"},
                "documentTab": document_tab("parent"),
                "childTabs": [
                    {
                        "tabProperties": {"tabId": "t.child"},
                        "documentTab": document_tab("child"),
                    }
                ],
            }
        ],
    }
    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=_docs_service(doc),
        user_google_email="user@example.com",
        document_id="doc123",
        tab_id=tab_id,
        detailed=detailed,
    )
    data = json.loads(result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0])
    prefix = "child" if tab_id else "parent"
    for kind in ("header", "footer"):
        expected_ids = {f"{prefix}.{kind}", f"{prefix}.section_{kind}"}
        if populated:
            expected_ids.add(f"{prefix}.content_{kind}")
        assert {entry["segment_id"] for entry in data[f"{kind}s"]} == expected_ids


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
