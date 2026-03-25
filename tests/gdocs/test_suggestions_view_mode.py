"""Tests for suggestions_view_mode support in Google Docs tools."""

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


@pytest.mark.asyncio
async def test_get_doc_content_passes_suggestions_view_mode_to_docs_api():
    drive_service = Mock()
    drive_service.files.return_value.get.return_value.execute = Mock(
        return_value={
            "id": "doc123",
            "name": "Test Doc",
            "mimeType": "application/vnd.google-apps.document",
            "webViewLink": "https://docs.google.com/document/d/doc123/edit",
        }
    )

    docs_service = Mock()
    docs_service.documents.return_value.get.return_value.execute = Mock(
        return_value={
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Hello world\n"}}]
                        }
                    }
                ]
            }
        }
    )

    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=drive_service,
        docs_service=docs_service,
        user_google_email="user@example.com",
        document_id="doc123",
        suggestions_view_mode="SUGGESTIONS_INLINE",
    )

    assert "Hello world" in result

    call_kwargs = docs_service.documents.return_value.get.call_args.kwargs
    assert call_kwargs["documentId"] == "doc123"
    assert call_kwargs["includeTabsContent"] is True
    assert call_kwargs["suggestionsViewMode"] == "SUGGESTIONS_INLINE"


@pytest.mark.asyncio
async def test_get_doc_content_rejects_invalid_suggestions_view_mode():
    result = await _unwrap(docs_tools.get_doc_content)(
        drive_service=Mock(),
        docs_service=Mock(),
        user_google_email="user@example.com",
        document_id="doc123",
        suggestions_view_mode="INVALID_MODE",
    )

    assert "suggestions_view_mode must be one of" in result


@pytest.mark.asyncio
async def test_get_doc_as_markdown_passes_suggestions_view_mode_to_docs_api(
    monkeypatch,
):
    docs_service = Mock()
    docs_service.documents.return_value.get.return_value.execute = Mock(
        return_value={"body": {"content": []}}
    )
    monkeypatch.setattr(docs_tools, "convert_doc_to_markdown", lambda doc: "# Doc")

    result = await _unwrap(docs_tools.get_doc_as_markdown)(
        drive_service=Mock(),
        docs_service=docs_service,
        user_google_email="user@example.com",
        document_id="doc123",
        include_comments=False,
        suggestions_view_mode="PREVIEW_SUGGESTIONS_ACCEPTED",
    )

    assert result == "# Doc"

    call_kwargs = docs_service.documents.return_value.get.call_args.kwargs
    assert call_kwargs["documentId"] == "doc123"
    assert call_kwargs["suggestionsViewMode"] == "PREVIEW_SUGGESTIONS_ACCEPTED"


@pytest.mark.asyncio
async def test_get_doc_as_markdown_rejects_invalid_suggestions_view_mode():
    result = await _unwrap(docs_tools.get_doc_as_markdown)(
        drive_service=Mock(),
        docs_service=Mock(),
        user_google_email="user@example.com",
        document_id="doc123",
        suggestions_view_mode="INVALID_MODE",
    )

    assert "suggestions_view_mode must be one of" in result
