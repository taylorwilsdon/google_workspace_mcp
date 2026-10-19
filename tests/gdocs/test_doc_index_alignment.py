"""Tests that read tools stay aligned with real Google Docs indices."""

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


def _paragraph(start_index, *elements):
    """Build a body paragraph from strings (text runs) and dicts (other elements)."""
    index = start_index
    para_elements = []
    for element in elements:
        if isinstance(element, str):
            span = len(element)
            element = {"textRun": {"content": element}}
        else:
            # Every non-text paragraph element occupies exactly one index.
            span = 1
        para_elements.append({"startIndex": index, "endIndex": index + span, **element})
        index += span
    return {
        "startIndex": start_index,
        "endIndex": index,
        "paragraph": {"elements": para_elements},
    }


# "Alpha\n", "\n", "Bravo\n", "\n" plus a page break: body spans indices 1-16.
DOC_BODY = [
    _paragraph(1, "Alpha\n"),
    _paragraph(7, "\n"),
    _paragraph(8, "Bravo\n"),
    _paragraph(14, {"pageBreak": {}}, "\n"),
]


def _docs_service(doc):
    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(return_value=doc)
    return service


def _drive_service():
    service = Mock()
    service.files.return_value.get.return_value.execute = Mock(
        return_value={
            "id": "doc123",
            "name": "Test Doc",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/doc123/edit",
        }
    )
    return service


@pytest.mark.asyncio
async def test_get_doc_content_is_index_aligned_with_the_document():
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=_drive_service(),
        docs_service=_docs_service({"body": {"content": DOC_BODY}}),
        user_google_email="user@example.com",
        document_id="doc123",
    )

    content = result.split("--- CONTENT ---\n", 1)[1]

    # Body spans indices 1-16, so the text is 15 characters long.
    assert content == "Alpha\n\nBravo\n￼\n"
    assert len(content) == DOC_BODY[-1]["endIndex"] - 1

    # An offset found in the text maps to a document index by adding 1.
    assert content.index("Bravo") + 1 == 8


@pytest.mark.asyncio
async def test_inspect_doc_structure_truncates_preview_by_default():
    long_text = "x" * 250 + "\n"
    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=_docs_service({"body": {"content": [_paragraph(1, long_text)]}}),
        user_google_email="user@example.com",
        document_id="doc123",
        detailed=True,
    )

    structure = json.loads(result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0])
    assert structure["elements"][0]["text_preview"] == "x" * 100


@pytest.mark.asyncio
@pytest.mark.parametrize("preview_chars", [0, None, -1])
async def test_inspect_doc_structure_preview_chars_zero_returns_full_text(
    preview_chars,
):
    long_text = "x" * 250 + "\n"
    result = await _unwrap(docs_tools.inspect_doc_structure)(
        service=_docs_service({"body": {"content": [_paragraph(1, long_text)]}}),
        user_google_email="user@example.com",
        document_id="doc123",
        detailed=True,
        preview_chars=preview_chars,
    )

    structure = json.loads(result.split("\n\n", 1)[1].rsplit("\n\nLink:", 1)[0])
    assert structure["elements"][0]["text_preview"] == long_text
