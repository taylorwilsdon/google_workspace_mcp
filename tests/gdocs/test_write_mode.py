"""Tests for write_mode (suggested edits) support in Google Docs edit tools."""

from unittest.mock import Mock

import pytest

from gdocs import docs_tools
from gdocs.docs_helpers import (
    build_batch_update_body,
    suggestion_save_error,
    validate_write_mode,
)
from gdocs.managers.batch_operation_manager import BatchOperationManager

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEf"
SAVED = {"commentUpdateState": "ALL_SAVED"}
FAILED = {"commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"}


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _docs_service(batch_reply=None):
    service = Mock()
    service.documents.return_value.batchUpdate.return_value.execute = Mock(
        return_value=batch_reply or {"replies": [{}]}
    )
    service.documents.return_value.get.return_value.execute = Mock(
        return_value={"revisionId": "rev1", "body": {"content": []}}
    )
    return service


def _batch_body(service):
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"]


class TestBuildBatchUpdateBody:
    def test_edit_mode_keeps_api_default_body(self):
        requests = [{"insertText": {"location": {"index": 1}, "text": "Hi"}}]
        assert build_batch_update_body(requests) == {"requests": requests}
        assert build_batch_update_body(requests, "EDIT") == {"requests": requests}

    def test_suggest_mode_sets_write_control(self):
        requests = [{"insertText": {"location": {"index": 1}, "text": "Hi"}}]
        assert build_batch_update_body(requests, "SUGGEST") == {
            "requests": requests,
            "writeControl": {"writeMode": "SUGGEST"},
        }


class TestValidateWriteMode:
    @pytest.mark.parametrize("mode", ["EDIT", "SUGGEST"])
    def test_accepts_supported_modes(self, mode):
        assert validate_write_mode(mode) is None

    @pytest.mark.parametrize("mode", ["suggest", "COMMENT", ""])
    def test_rejects_other_values(self, mode):
        error = validate_write_mode(mode)
        assert error.startswith("Error: write_mode must be one of EDIT, SUGGEST")


class TestSuggestionSaveError:
    def test_reports_failed_suggestion_save(self):
        error = suggestion_save_error(FAILED, "SUGGEST")
        assert "did not save these changes as suggestions" in error
        assert "version history" in error

    @pytest.mark.parametrize(
        "result",
        [
            SAVED,
            {"commentUpdateState": "NO_UPDATES_REQUESTED"},
            {"commentUpdateState": "COMMENT_UPDATE_STATE_UNSPECIFIED"},
            {},
        ],
    )
    def test_other_or_missing_states_are_not_failures(self, result):
        assert suggestion_save_error(result, "SUGGEST") is None

    def test_edit_mode_ignores_comment_update_state(self):
        assert suggestion_save_error(FAILED, "EDIT") is None


@pytest.mark.asyncio
async def test_modify_doc_text_submits_suggestion_when_requested():
    service = _docs_service({"replies": [{}, {}], **SAVED})

    result = await _unwrap(docs_tools.modify_doc_text)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=5,
        end_index=10,
        text="world",
        write_mode="SUGGEST",
    )

    body = _batch_body(service)
    assert body["writeControl"] == {"writeMode": "SUGGEST"}
    assert [next(iter(request)) for request in body["requests"]] == [
        "deleteContentRange",
        "insertText",
    ]
    assert result.startswith("Suggested: Replaced text from index 5 to 10")


@pytest.mark.asyncio
async def test_modify_doc_text_edits_directly_by_default():
    service = _docs_service()

    result = await _unwrap(docs_tools.modify_doc_text)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=1,
        text="Hello",
    )

    assert "writeControl" not in _batch_body(service)
    assert result.startswith("Inserted text at index 1")


@pytest.mark.asyncio
async def test_modify_doc_text_rejects_invalid_write_mode_without_calling_api():
    service = _docs_service()

    result = await _unwrap(docs_tools.modify_doc_text)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=1,
        text="Hello",
        write_mode="DRAFT",
    )

    assert "write_mode must be one of" in result
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_find_and_replace_doc_submits_suggestions_when_requested():
    service = _docs_service(
        {"replies": [{"replaceAllText": {"occurrencesChanged": 2}}], **SAVED}
    )

    result = await _unwrap(docs_tools.find_and_replace_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        find_text="colour",
        replace_text="color",
        write_mode="SUGGEST",
    )

    body = _batch_body(service)
    assert body["writeControl"] == {"writeMode": "SUGGEST"}
    assert "replaceAllText" in body["requests"][0]
    assert result.startswith("Suggested replacing 2 occurrence(s) of 'colour'")


@pytest.mark.asyncio
async def test_find_and_replace_doc_rejects_invalid_write_mode_without_calling_api():
    service = _docs_service()

    result = await _unwrap(docs_tools.find_and_replace_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        find_text="a",
        replace_text="b",
        write_mode="suggestion",
    )

    assert "write_mode must be one of" in result
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_batch_update_doc_submits_whole_batch_as_suggestions():
    service = _docs_service({"replies": [{}, {}], **SAVED})

    result = await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        operations=[
            {"type": "insert_text", "index": 1, "text": "Intro\n"},
            {"type": "delete_text", "start_index": 10, "end_index": 20},
        ],
        write_mode="SUGGEST",
    )

    body = _batch_body(service)
    assert body["writeControl"] == {"writeMode": "SUGGEST"}
    assert len(body["requests"]) == 2
    assert "as suggested edits" in result


@pytest.mark.asyncio
async def test_batch_update_doc_rejects_invalid_write_mode_without_calling_api():
    service = _docs_service()

    result = await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        operations=[{"type": "insert_text", "index": 1, "text": "Intro\n"}],
        write_mode="REVIEW",
    )

    assert "write_mode must be one of" in result
    service.documents.return_value.batchUpdate.assert_not_called()


@pytest.mark.asyncio
async def test_batch_manager_defaults_to_direct_edits():
    service = _docs_service()
    manager = BatchOperationManager(service)

    success, message, _ = await manager.execute_batch_operations(
        DOC_ID, [{"type": "insert_text", "index": 1, "text": "Intro\n"}]
    )

    assert success
    assert "writeControl" not in _batch_body(service)
    assert "suggested" not in message


@pytest.mark.asyncio
async def test_modify_doc_text_reports_unsaved_suggestions():
    service = _docs_service({"replies": [{}], **FAILED})

    result = await _unwrap(docs_tools.modify_doc_text)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        start_index=1,
        text="Hello",
        write_mode="SUGGEST",
    )

    assert result.startswith("Error: Google Docs did not save these changes")
    assert "Suggested" not in result


@pytest.mark.asyncio
async def test_find_and_replace_doc_reports_unsaved_suggestions():
    service = _docs_service(
        {"replies": [{"replaceAllText": {"occurrencesChanged": 1}}], **FAILED}
    )

    result = await _unwrap(docs_tools.find_and_replace_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        find_text="colour",
        replace_text="color",
        write_mode="SUGGEST",
    )

    assert result.startswith("Error: Google Docs did not save these changes")


@pytest.mark.asyncio
async def test_batch_update_doc_reports_unsaved_suggestions():
    service = _docs_service({"replies": [{}], **FAILED})

    result = await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email="user@example.com",
        document_id=DOC_ID,
        operations=[{"type": "insert_text", "index": 1, "text": "Intro\n"}],
        write_mode="SUGGEST",
    )

    assert result.startswith("Error: Google Docs did not save these changes")
    assert "as suggested edits" not in result
