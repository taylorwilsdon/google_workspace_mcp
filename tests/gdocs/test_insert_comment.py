"""Tests for the insert_comment batch_update_doc operation."""

from unittest.mock import Mock

import pytest
from pydantic import TypeAdapter, ValidationError

from gdocs.docs_helpers import validate_operation
from gdocs.managers.batch_operation_manager import BatchOperationManager
from gdocs.operation_schemas import BatchDocOperation

THREAD = {"commentId": "c1", "anchorId": "a1", "plainTextQuote": "went up"}


def _paragraph(start_index, text):
    return {
        "startIndex": start_index,
        "endIndex": start_index + len(text),
        "paragraph": {
            "elements": [
                {
                    "startIndex": start_index,
                    "endIndex": start_index + len(text),
                    "textRun": {"content": text},
                }
            ]
        },
    }


# "Intro\n" 1-7, "Numbers went up.\n" 7-24
DOC = {
    "body": {"content": [_paragraph(1, "Intro\n"), _paragraph(7, "Numbers went up.\n")]}
}


async def _execute(operations):
    service = Mock()
    docs = service.documents.return_value
    docs.get.return_value.execute.return_value = DOC
    docs.batchUpdate.return_value.execute.return_value = {
        "replies": [{"insertComment": {"commentThread": THREAD}}],
        "commentUpdateState": "ALL_SAVED",
    }
    result = await BatchOperationManager(service).execute_batch_operations(
        "doc123", operations
    )
    return result, docs


@pytest.mark.asyncio
async def test_insert_comment_builds_range_request_and_returns_thread():
    operation = TypeAdapter(BatchDocOperation).validate_python(
        {
            "type": "insert_comment",
            "content": "Source?",
            "start_index": 15,
            "end_index": 22,
            "assignee_email": "matt@example.com",
            "tab_id": "t.1",
        }
    )
    (success, message, metadata), docs = await _execute(
        [operation.model_dump(exclude_none=True)]
    )
    assert success, message
    assert docs.batchUpdate.call_args.kwargs["body"]["requests"] == [
        {
            "insertComment": {
                "content": "Source?",
                "range": {"startIndex": 15, "endIndex": 22, "tabId": "t.1"},
                "assigneeEmailAddress": "matt@example.com",
            }
        }
    ]
    assert metadata["created_comments"] == [THREAD]
    assert metadata["comment_update_state"] == "ALL_SAVED"


@pytest.mark.asyncio
async def test_quoted_text_resolves_to_live_range():
    (success, message, _), docs = await _execute(
        [{"type": "insert_comment", "content": "Source?", "quoted_text": "went up"}]
    )
    assert success, message
    request = docs.batchUpdate.call_args.kwargs["body"]["requests"][0]
    assert request["insertComment"]["range"] == {"startIndex": 15, "endIndex": 22}


@pytest.mark.asyncio
async def test_quoted_text_must_be_unique():
    (success, message, _), docs = await _execute(
        [{"type": "insert_comment", "content": "?", "quoted_text": "n"}]
    )
    assert not success
    assert "matches" in message
    docs.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_quoted_text_cannot_mix_with_mutations():
    (success, message, _), docs = await _execute(
        [
            {"type": "insert_text", "index": 1, "text": "X"},
            {"type": "insert_comment", "content": "?", "quoted_text": "went up"},
        ]
    )
    assert not success
    assert "cannot be mixed" in message
    docs.batchUpdate.assert_not_called()


@pytest.mark.parametrize(
    "location",
    [
        {},
        {"start_index": 1},
        {"start_index": 1, "end_index": 5, "quoted_text": "Intro"},
    ],
)
def test_insert_comment_requires_exactly_one_range(location):
    operation = {"type": "insert_comment", "content": "?", **location}
    with pytest.raises(ValidationError, match="exactly one"):
        TypeAdapter(BatchDocOperation).validate_python(operation)
    valid, message = validate_operation(operation)
    assert not valid
    assert "exactly one" in message


def test_insert_comment_rejects_empty_content():
    with pytest.raises(ValidationError, match="at least 1 character"):
        TypeAdapter(BatchDocOperation).validate_python(
            {"type": "insert_comment", "content": "", "start_index": 1, "end_index": 2}
        )
