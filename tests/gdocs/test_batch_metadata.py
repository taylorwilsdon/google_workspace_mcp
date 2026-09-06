"""Batch verification must use each operation's tab and segment indices."""

from unittest.mock import Mock

import pytest

from gdocs.managers.batch_operation_manager import BatchOperationManager


def _segment(text, style="NORMAL_TEXT"):
    return {
        "content": [
            {
                "startIndex": 1,
                "endIndex": 1 + len(text),
                "paragraph": {
                    "elements": [{"textRun": {"content": text}}],
                    "paragraphStyle": {"namedStyleType": style},
                },
            }
        ]
    }


def _service():
    service = Mock()
    service.documents.return_value.get.return_value.execute.return_value = {
        "revisionId": "rev2",
        "body": _segment("Wrong legacy body\n"),
        "tabs": [
            {
                "tabProperties": {"tabId": "first"},
                "documentTab": {"body": _segment("First tab body\n")},
                "childTabs": [
                    {
                        "tabProperties": {"tabId": "child"},
                        "documentTab": {
                            "body": _segment("Child\n", "HEADING_1"),
                            "headers": {"h1": _segment("Header\n")},
                            "footers": {"f1": _segment("Footer text\n")},
                            "footnotes": {"n1": _segment("Note\n")},
                        },
                    }
                ],
            }
        ],
    }
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}]
    }
    return service


@pytest.mark.asyncio
async def test_batch_metadata_is_independent_for_each_tab_and_segment():
    service = _service()
    targets = [
        ("first", None),
        ("child", None),
        ("child", "h1"),
        ("child", "f1"),
        ("child", "n1"),
    ]
    operations = [
        {
            "type": "format_text",
            "tab_id": tab_id,
            "segment_id": segment_id,
            "start_index": 1,
            "end_index": 3,
            "bold": True,
        }
        for tab_id, segment_id in targets
    ]
    success, message, metadata = await BatchOperationManager(
        service
    ).execute_batch_operations("doc123", operations)
    assert success, message
    assert metadata["revision_after"] == "rev2"
    assert "document_length" not in metadata
    assert "affected_range" not in metadata
    scopes = metadata["target_ranges"]
    assert [(scope["tab_id"], scope["segment_id"]) for scope in scopes] == targets
    assert [scope["document_length"] for scope in scopes] == [16, 7, 8, 13, 6]
    assert [scope["affected_range"][0]["text_preview"] for scope in scopes] == [
        "First tab body\n",
        "Child\n",
        "Header\n",
        "Footer text\n",
        "Note\n",
    ]
    assert scopes[1]["affected_range"][0]["named_style"] == "HEADING_1"
    assert (
        service.documents.return_value.get.call_args.kwargs["includeTabsContent"]
        is True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tab_id, expected_text", [(None, "First tab body\n"), ("child", "Child\n")]
)
async def test_single_target_retains_existing_metadata_fields(tab_id, expected_text):
    success, message, metadata = await BatchOperationManager(
        _service()
    ).execute_batch_operations(
        "doc123",
        [
            {
                "type": "format_text",
                "tab_id": tab_id,
                "start_index": 1,
                "end_index": 3,
                "bold": True,
            }
        ],
    )
    assert success, message
    assert metadata["document_length"] == len(expected_text) + 1
    assert metadata["affected_range"][0]["text_preview"] == expected_text


@pytest.mark.asyncio
async def test_segment_anchor_resolves_within_selected_tab_header():
    service = _service()
    success, message, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc123",
        [
            {
                "type": "insert_text",
                "tab_id": "child",
                "segment_id": "h1",
                "anchor_text": "Header",
                "text": "!",
            }
        ],
    )
    assert success, message
    request = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ][0]
    assert request["insertText"]["location"] == {
        "tabId": "child",
        "segmentId": "h1",
        "index": 7,
    }
