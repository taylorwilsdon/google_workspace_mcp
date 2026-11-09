"""Tests for the replace_doc_section tool."""

from unittest.mock import Mock

import pytest

from core.utils import UserInputError
from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _para(start, text, style="NORMAL_TEXT", footnote=False):
    """Build a minimal body paragraph element. text must end with a newline."""
    elements = [
        {
            "startIndex": start,
            "endIndex": start + len(text),
            "textRun": {"content": text},
        }
    ]
    if footnote:
        elements.insert(
            0, {"startIndex": start, "endIndex": start + 1, "footnoteReference": {}}
        )
    return {
        "startIndex": start,
        "endIndex": start + len(text),
        "paragraph": {
            "paragraphStyle": {"namedStyleType": style},
            "elements": elements,
        },
    }


# Indices: 1-7 "Intro\n" H1, 7-15 "Results\n" H2, 15-25 "Old body.\n", 25-32 "Other\n" H2
def _doc(revision="rev-1"):
    return {
        "revisionId": revision,
        "body": {
            "content": [
                {"endIndex": 1, "sectionBreak": {}},
                _para(1, "Intro\n", "HEADING_1"),
                _para(7, "Results\n", "HEADING_2"),
                _para(15, "Old body.\n"),
                _para(25, "Other\n", "HEADING_2"),
            ]
        },
    }


def _make_service(doc):
    service = Mock()
    service.documents.return_value.get.return_value.execute.return_value = doc
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}]
    }
    return service


@pytest.mark.asyncio
async def test_replace_doc_section_calls_batchupdate():
    service = _make_service(_doc())
    result = await _unwrap(docs_tools.replace_doc_section)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        heading="Results",
        markdown_text="New content.\n",
    )
    assert "Error" not in result
    service.documents.return_value.batchUpdate.assert_called_once()


@pytest.mark.asyncio
async def test_replace_doc_section_missing_heading_returns_error():
    service = _make_service(_doc())
    with pytest.raises(UserInputError, match="No heading matching"):
        await _unwrap(docs_tools.replace_doc_section)(
            service=service,
            user_google_email="user@example.com",
            document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
            heading="Nonexistent Section",
            markdown_text="Content.\n",
        )
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_replace_doc_section_empty_heading_returns_error():
    service = _make_service(_doc())
    result = await _unwrap(docs_tools.replace_doc_section)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        heading="   ",
        markdown_text="Content.\n",
    )
    assert "Error" in result
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_replace_doc_section_empty_markdown_returns_error():
    service = _make_service(_doc())
    result = await _unwrap(docs_tools.replace_doc_section)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        heading="Results",
        markdown_text="",
    )
    assert "Error" in result
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_replace_doc_section_refuses_footnotes_on_replace():
    doc = _doc()
    # Add a footnote reference in the Results section body.
    doc["body"]["content"][3] = _para(15, "Old body.\n", footnote=True)
    service = _make_service(doc)
    with pytest.raises(UserInputError, match="footnote"):
        await _unwrap(docs_tools.replace_doc_section)(
            service=service,
            user_google_email="user@example.com",
            document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
            heading="Results",
            markdown_text="Replacement.\n",
        )
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_replace_doc_section_refuses_table_cell_footnotes():
    doc = _doc()
    doc["body"]["content"][3] = {
        "startIndex": 15,
        "endIndex": 25,
        "table": {
            "tableRows": [
                {
                    "tableCells": [
                        {
                            "content": [
                                {
                                    "startIndex": 16,
                                    "endIndex": 24,
                                    "paragraph": {
                                        "elements": [
                                            {
                                                "startIndex": 16,
                                                "endIndex": 17,
                                                "footnoteReference": {},
                                            },
                                            {
                                                "startIndex": 17,
                                                "endIndex": 24,
                                                "textRun": {"content": "Cell.\n"},
                                            },
                                        ]
                                    },
                                }
                            ]
                        }
                    ]
                }
            ]
        },
    }
    service = _make_service(doc)
    with pytest.raises(UserInputError, match="footnote"):
        await _unwrap(docs_tools.replace_doc_section)(
            service=service,
            user_google_email="user@example.com",
            document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
            heading="Results",
            markdown_text="Replacement.\n",
        )
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_replace_doc_section_append_skips_footnote_guard():
    doc = _doc()
    doc["body"]["content"][3] = _para(15, "Old body.\n", footnote=True)
    service = _make_service(doc)
    result = await _unwrap(docs_tools.replace_doc_section)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        heading="Results",
        markdown_text="Appended.\n",
        append=True,
    )
    assert "Error" not in result
    service.documents.return_value.batchUpdate.assert_called_once()


@pytest.mark.asyncio
async def test_replace_doc_section_uses_revision_id_in_write_control():
    service = _make_service(_doc(revision="abc123"))
    await _unwrap(docs_tools.replace_doc_section)(
        service=service,
        user_google_email="user@example.com",
        document_id="1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",
        heading="Results",
        markdown_text="Content.\n",
    )
    call_body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    assert call_body.get("writeControl", {}).get("requiredRevisionId") == "abc123"
