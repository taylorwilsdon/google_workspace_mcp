"""Tests that Google Docs write helpers measure text ranges in UTF-16 code units.

Google Docs API indexes count UTF-16 code units (2 per non-BMP character such as
most emoji), not Python code points. Using len() produces ranges that under-format
trailing characters, corrupt replacement deletions, or split surrogate pairs.
"""

from unittest.mock import Mock

import pytest

from gdocs import docs_tools
from gdocs.docs_helpers import utf16_length
from gdocs.docs_tables import build_table_population_requests
from gdocs.managers.table_operation_manager import TableOperationManager

DOC_ID = "1" * 24


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _mock_docs_service():
    service = Mock()
    captured = []

    def _batch_update(documentId, body):
        captured.append(body)
        req = Mock()
        req.execute.return_value = {"replies": []}
        return req

    service.documents.return_value.batchUpdate.side_effect = _batch_update
    return service, captured


def test_utf16_length_counts_non_bmp_and_lone_surrogate():
    assert utf16_length("abc") == 3
    assert utf16_length("Launch 🚀") == 9
    assert utf16_length("\ud800") == 1


@pytest.mark.asyncio
async def test_modify_doc_text_replace_at_start_uses_utf16_for_delete_and_format():
    service, captured = _mock_docs_service()
    modify_doc_text = _unwrap(docs_tools.modify_doc_text)

    # "Launch 🚀" has len=8 but utf16_length=9.
    await modify_doc_text(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=0,
        end_index=6,
        text="Launch 🚀",
        bold=True,
    )

    requests = captured[0]["requests"]
    assert requests[0]["insertText"] == {
        "location": {"index": 1},
        "text": "Launch 🚀",
    }
    assert requests[1]["deleteContentRange"]["range"] == {
        "startIndex": 10,
        "endIndex": 15,
    }
    assert requests[2]["updateTextStyle"]["range"] == {
        "startIndex": 1,
        "endIndex": 10,
    }


@pytest.mark.asyncio
async def test_modify_doc_text_replace_at_start_ascii_formats_full_text():
    service, captured = _mock_docs_service()
    modify_doc_text = _unwrap(docs_tools.modify_doc_text)

    await modify_doc_text(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=0,
        end_index=5,
        text="Hi",
        bold=True,
    )

    requests = captured[0]["requests"]
    assert requests[0]["insertText"] == {"location": {"index": 1}, "text": "Hi"}
    assert requests[1]["deleteContentRange"]["range"] == {
        "startIndex": 3,
        "endIndex": 7,
    }
    assert requests[2]["updateTextStyle"]["range"] == {
        "startIndex": 1,
        "endIndex": 3,
    }


@pytest.mark.asyncio
async def test_modify_doc_text_replace_nonzero_uses_utf16_for_format():
    service, captured = _mock_docs_service()
    modify_doc_text = _unwrap(docs_tools.modify_doc_text)

    await modify_doc_text(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=5,
        end_index=10,
        text="🚀 Go 🎉",
        bold=True,
    )

    requests = captured[0]["requests"]
    # "🚀 Go 🎉" has len=6, utf16_length=8 -> [5, 13)
    assert requests[2]["updateTextStyle"]["range"] == {
        "startIndex": 5,
        "endIndex": 13,
    }


@pytest.mark.asyncio
async def test_modify_doc_text_replace_in_segment_at_zero_uses_utf16():
    service, captured = _mock_docs_service()
    modify_doc_text = _unwrap(docs_tools.modify_doc_text)

    # In a header/footer segment, index 0 is valid and is not shifted to 1.
    await modify_doc_text(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=0,
        end_index=4,
        text="Note 🚀",
        bold=True,
        segment_id="kix.header1",
    )

    requests = captured[0]["requests"]
    assert requests[1]["insertText"] == {
        "location": {"index": 0, "segmentId": "kix.header1"},
        "text": "Note 🚀",
    }
    assert requests[2]["updateTextStyle"]["range"] == {
        "startIndex": 0,
        "endIndex": 7,
        "segmentId": "kix.header1",
    }


@pytest.mark.asyncio
async def test_insert_doc_elements_list_flattens_requests_and_uses_utf16_range():
    service, captured = _mock_docs_service()
    insert_doc_elements = _unwrap(docs_tools.insert_doc_elements)

    # "🚀 First\n🎉" has len=9, utf16_length=11 -> [5, 16)
    await insert_doc_elements(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        element_type="list",
        index=5,
        list_type="UNORDERED",
        text="🚀 First\n🎉",
    )

    requests = captured[0]["requests"]
    assert all(isinstance(req, dict) for req in requests)
    assert requests[1]["createParagraphBullets"]["range"] == {
        "startIndex": 5,
        "endIndex": 16,
    }


def test_build_table_population_requests_uses_utf16_header_bold_ranges():
    table_info = {
        "cells": [
            [
                {
                    "start_index": 4,
                    "end_index": 6,
                    "insertion_index": 5,
                    "content": "\n",
                },
                {
                    "start_index": 10,
                    "end_index": 16,
                    "insertion_index": 11,
                    "content": "Old",
                },
            ]
        ]
    }
    requests = build_table_population_requests(
        table_info,
        [["Metric 📊", " + 🚀"]],
        bold_headers=True,
    )

    style_ranges = [
        req["updateTextStyle"]["range"] for req in requests if "updateTextStyle" in req
    ]
    # Empty cell at 5 with "Metric 📊" (utf16=9) -> [5, 14)
    # Non-empty cell appends at end_index-1=15 with " + 🚀" (utf16=5) -> [15, 20)
    assert style_ranges == [
        {"startIndex": 5, "endIndex": 14},
        {"startIndex": 15, "endIndex": 20},
    ]


@pytest.mark.asyncio
async def test_table_operation_manager_uses_utf16_header_bold_range():
    service, captured = _mock_docs_service()
    manager = TableOperationManager(service)
    table = {
        "start_index": 2,
        "cells": [
            [
                {
                    "start_index": 4,
                    "end_index": 6,
                    "insertion_index": 5,
                    "content": "\n",
                }
            ]
        ],
    }

    count = await manager._populate_table_cells_batch(
        document_id=DOC_ID,
        table=table,
        table_data=[["Status 🚀"]],
        bold_headers=True,
        header_rows=0,
    )

    assert count == 1
    requests = captured[0]["requests"]
    style_req = next(req for req in requests if "updateTextStyle" in req)
    # "Status 🚀" has len=8, utf16_length=9 -> [5, 14)
    assert style_req["updateTextStyle"]["range"] == {
        "startIndex": 5,
        "endIndex": 14,
    }
