"""Tool-level coverage for opt-in semantic context and compatibility."""

from unittest.mock import AsyncMock, Mock

import pytest

from gdocs import docs_tools


@pytest.fixture(autouse=True)
def _no_ambient_office_xml_limit(monkeypatch):
    """The expansion limit is read from the environment on every extraction."""
    monkeypatch.delenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", raising=False)


def _unwrap(tool):
    """Unwrap the MCP tool and decorators to exercise its implementation directly."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _paragraph(text, link=None):
    """Build a single-run paragraph with optional link metadata."""
    return {
        "paragraph": {
            "elements": [
                {"textRun": {"content": text, "textStyle": {"link": link or {}}}}
            ]
        }
    }


def _services(doc, mime_type="application/vnd.google-apps.document"):
    """Mock Drive metadata and Docs content without contacting Google services."""
    drive = Mock()
    drive.files.return_value.get.return_value.execute.return_value = {
        "name": "Notes",
        "mimeType": mime_type,
        "webViewLink": "https://example.com/doc",
    }
    docs = Mock()
    docs.documents.return_value.get.return_value.execute.return_value = doc
    return drive, docs


async def _read(doc, **kwargs):
    """Read a mocked document through the tool with the requested options."""
    drive, docs = _services(doc)
    return await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=docs,
        user_google_email="reader@example.com",
        document_id="doc-1",
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve_context", [False, True])
async def test_link_context_is_opt_in(preserve_context):
    """Readable destinations appear only when semantic context is requested."""
    doc = {
        "body": {
            "content": [_paragraph("Reference\n", {"url": "https://example.com/ref"})]
        }
    }
    result = await _read(doc, preserve_context=preserve_context)
    content = result.split("--- CONTENT ---\n", 1)[1]
    if preserve_context:
        assert "Reference" in content
        assert "https://example.com/ref" in content
    else:
        assert content == "Reference\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{}, {"preserve_context": True}])
async def test_ordinary_text_and_metadata_are_compatible(kwargs):
    """Both reader modes retain the metadata header and ordinary paragraph spacing."""
    result = await _read(
        {
            "body": {
                "content": [
                    _paragraph("Ordinary text\n"),
                    _paragraph("\n"),
                    _paragraph("Second paragraph\n"),
                ]
            }
        },
        **kwargs,
    )
    assert result == (
        'File: "Notes" (ID: doc-1, Type: application/vnd.google-apps.document)\n'
        "Link: https://example.com/doc\n\n--- CONTENT ---\n"
        "Ordinary text\n\nSecond paragraph\n"
    )


@pytest.mark.asyncio
async def test_context_for_nested_selected_tab_includes_segments_and_local_targets():
    """Selecting a nested tab retains its local targets and excludes parent content."""
    selected = {
        "tabProperties": {"tabId": "t.child", "title": "Child"},
        "documentTab": {
            "body": {"content": [_paragraph("Jump\n", {"headingId": "h.local"})]},
            "headers": {"header-1": {"content": [_paragraph("Header context\n")]}},
        },
    }
    doc = {
        "tabs": [
            {
                "tabProperties": {"tabId": "t.parent", "title": "Parent"},
                "documentTab": {"body": {"content": [_paragraph("Parent text\n")]}},
                "childTabs": [selected],
            }
        ]
    }
    result = await _read(doc, preserve_context=True, tab_id="t.child")
    assert "[tab: Child]" in result
    assert "h.local" in result and "t.child" in result
    assert "Header context" in result
    assert "Parent text" not in result
    assert "--- TAB:" not in result


@pytest.mark.asyncio
async def test_context_reports_missing_tab():
    """Context mode returns the existing error for an unknown tab ID."""
    assert "not found" in await _read({}, preserve_context=True, tab_id="missing")


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve_context", [False, True])
async def test_office_extraction_is_unaffected(monkeypatch, preserve_context):
    """Office downloads use the same extraction path with either context setting."""
    drive, docs = _services(
        {}, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    download = AsyncMock(return_value=b"office bytes")
    monkeypatch.setattr(docs_tools, "download_media_bytes", download)
    extract = Mock(return_value="Office text")
    monkeypatch.setattr(docs_tools, "extract_office_xml_text", extract)
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=docs,
        user_google_email="reader@example.com",
        document_id="file-1",
        preserve_context=preserve_context,
    )
    assert result.endswith("--- CONTENT ---\nOffice text")
    download.assert_awaited_once()
    extract.assert_called_once()
    docs.documents.assert_not_called()


@pytest.mark.asyncio
async def test_office_file_over_the_expansion_limit_is_reported_as_such(monkeypatch):
    drive, docs = _services(
        {}, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    monkeypatch.setattr(
        docs_tools, "download_media_bytes", AsyncMock(return_value=b"office bytes")
    )
    monkeypatch.setattr(
        docs_tools,
        "extract_office_xml_text",
        Mock(side_effect=docs_tools.OfficeXmlTooLargeError("part expands beyond")),
    )
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive,
        docs_service=docs,
        user_google_email="reader@example.com",
        document_id="file-1",
    )
    assert "part expands beyond" in result
    assert "appears damaged" not in result
    assert "office bytes" not in result
