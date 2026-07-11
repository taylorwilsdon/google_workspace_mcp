"""Tests for Developer Preview write controls in Google Docs batches."""

from unittest.mock import Mock

import pytest

from gdocs import docs_tools
from gdocs.managers.batch_operation_manager import BatchOperationManager


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_manager_sends_suggest_write_mode_and_required_revision():
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}],
        "suggestionResponses": [{"createdSuggestionIds": ["suggestion-1"]}],
        "commentUpdateState": "ALL_SAVED",
    }
    service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 8}]}
    }

    success, _, metadata = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc-1",
        [{"type": "insert_text", "index": 1, "text": "Proposed text"}],
        write_mode="SUGGEST",
        required_revision_id="revision-1",
    )

    assert success
    body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    assert body["writeControl"] == {
        "writeMode": "SUGGEST",
        "requiredRevisionId": "revision-1",
    }
    assert metadata["write_mode"] == "SUGGEST"
    assert metadata["created_suggestion_ids"] == ["suggestion-1"]
    assert metadata["comment_update_state"] == "ALL_SAVED"


@pytest.mark.asyncio
async def test_manager_omits_write_control_for_default_edit_behavior():
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}]
    }
    service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 8}]}
    }

    success, _, _ = await BatchOperationManager(service).execute_batch_operations(
        "doc-1", [{"type": "insert_text", "index": 1, "text": "Direct edit"}]
    )

    assert success
    body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    assert body == {
        "requests": [{"insertText": {"text": "Direct edit", "location": {"index": 1}}}]
    }


@pytest.mark.asyncio
async def test_manager_rejects_both_revision_controls():
    service = Mock()

    success, message, _ = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc-1",
        [{"type": "insert_text", "index": 1, "text": "Text"}],
        required_revision_id="required",
        target_revision_id="target",
    )

    assert not success
    assert "required_revision_id" in message
    assert "target_revision_id" in message
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_manager_rejects_operations_unsupported_in_suggest_mode():
    service = Mock()

    success, message, _ = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc-1",
        [{"type": "insert_doc_tab", "title": "New tab", "index": 0}],
        write_mode="SUGGEST",
    )

    assert not success
    assert "insert_doc_tab" in message
    assert "not supported" in message
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_manager_reports_comment_thread_partial_failure_without_hiding_edit():
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}],
        "commentUpdateState": "ALL_FAILED_UNKNOWN_REASON",
    }
    service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 8}]}
    }

    success, message, metadata = await BatchOperationManager(
        service
    ).execute_batch_operations(
        "doc-1",
        [{"type": "insert_text", "index": 1, "text": "Suggested text"}],
        write_mode="SUGGEST",
    )

    assert success
    assert "WARNING" in message
    assert metadata["partial_failure"] is True
    assert metadata["comment_update_state"] == "ALL_FAILED_UNKNOWN_REASON"


@pytest.mark.asyncio
async def test_public_tool_exposes_write_mode_and_revision_control():
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}],
        "suggestionResponses": [{"createdSuggestionIds": ["suggestion-1"]}],
    }
    service.documents.return_value.get.return_value.execute.return_value = {
        "body": {"content": [{"endIndex": 8}]}
    }

    result = await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        operations=[{"type": "insert_text", "index": 1, "text": "Suggestion"}],
        write_mode="SUGGEST",
        target_revision_id="revision-1",
    )

    assert "SUGGEST" in result
    assert "suggestion-1" in result
    body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
    assert body["writeControl"] == {
        "writeMode": "SUGGEST",
        "targetRevisionId": "revision-1",
    }
