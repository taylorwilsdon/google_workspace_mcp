"""
Tests for the suggest_mode Developer Preview enrollment self-check.

Google exposes no API to query Workspace Developer Preview Program
enrollment, and an unenrolled project may silently ignore
writeControl.writeMode and apply changes directly instead of erroring. These
tests verify batch_update_doc detects that silent fallthrough by diffing
suggestion IDs before/after, caches the result, and gates suggest_mode /
manage_doc_suggestions for the rest of the process once a failure is seen.
"""

import pytest
from unittest.mock import Mock

from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


NO_SUGGESTIONS_DOC = {
    "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}]},
    "tabs": [],
}

WITH_SUGGESTION_DOC = {
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": "Hello\n"}},
                        {
                            "textRun": {
                                "content": "New sentence\n",
                                "suggestedInsertionIds": ["suggest.abc123"],
                            }
                        },
                    ]
                }
            }
        ]
    },
    "tabs": [],
}

NO_SUGGESTIONS_DOC_WITH_HEADER = {
    "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}]},
    "headers": {
        "header1": {
            "content": [
                {"paragraph": {"elements": [{"textRun": {"content": "Header text\n"}}]}}
            ]
        }
    },
    "tabs": [],
}

WITH_HEADER_SUGGESTION_DOC = {
    "body": {"content": [{"paragraph": {"elements": [{"textRun": {"content": "Hello\n"}}]}}]},
    "headers": {
        "header1": {
            "content": [
                {
                    "paragraph": {
                        "elements": [
                            {"textRun": {"content": "Header text\n"}},
                            {
                                "textRun": {
                                    "content": "Suggested header addition\n",
                                    "suggestedInsertionIds": ["suggest.header1"],
                                }
                            },
                        ]
                    }
                }
            ]
        }
    },
    "tabs": [],
}

DOC_LENGTH_STUB = {"body": {"content": [{"endIndex": 100}]}}

INSERT_TEXT_OPERATIONS = [
    {"type": "insert_text", "end_of_segment": True, "text": "New sentence\n"}
]

HEADER_INSERT_OPERATIONS = [
    {
        "type": "insert_text",
        "end_of_segment": True,
        "text": "Suggested header addition\n",
        "segment_id": "header1",
    }
]


@pytest.fixture(autouse=True)
def _reset_enrollment_cache():
    """Isolate the process-wide enrollment cache between tests."""
    docs_tools._SUGGEST_MODE_ENROLLED = None
    yield
    docs_tools._SUGGEST_MODE_ENROLLED = None


def _make_service(get_side_effect):
    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(
        side_effect=get_side_effect
    )
    service.documents.return_value.batchUpdate.return_value.execute.return_value = {
        "replies": [{}],
        "commentUpdateState": "ALL_SAVED",
    }
    return service


class TestSuggestModeEnrollmentVerification:
    @pytest.mark.asyncio
    async def test_suggest_mode_confirmed_when_suggestion_appears(self):
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, WITH_SUGGESTION_DOC]
        )

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            operations=INSERT_TEXT_OPERATIONS,
            suggest_mode=True,
        )

        assert "Error" not in result
        assert "Applied as suggested edits" in result
        assert docs_tools._SUGGEST_MODE_ENROLLED is True

        body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["writeControl"] == {"writeMode": "SUGGEST"}

    @pytest.mark.asyncio
    async def test_suggest_mode_flagged_when_no_new_suggestion_appears(self):
        # Same document before and after: the API silently applied the edit
        # directly instead of creating a suggestion.
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC]
        )

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            operations=INSERT_TEXT_OPERATIONS,
            suggest_mode=True,
        )

        assert "Error" in result
        assert "not enrolled" in result or "Developer Preview" in result
        assert docs_tools._SUGGEST_MODE_ENROLLED is False

    @pytest.mark.asyncio
    async def test_subsequent_suggest_mode_call_short_circuits_without_api_call(self):
        docs_tools._SUGGEST_MODE_ENROLLED = False
        service = _make_service([])  # any get() call would raise StopIteration

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            operations=INSERT_TEXT_OPERATIONS,
            suggest_mode=True,
        )

        assert "Error" in result
        assert "Developer Preview" in result
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_doc_suggestions_short_circuits_when_not_enrolled(self):
        docs_tools._SUGGEST_MODE_ENROLLED = False
        service = Mock()

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            action="accept",
            suggestion_ids=["suggest.abc123"],
        )

        assert "Error" in result
        assert "Developer Preview" in result
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_manage_doc_suggestions_success_marks_enrolled(self):
        service = Mock()
        service.documents.return_value.batchUpdate.return_value.execute.return_value = {
            "commentUpdateState": "ALL_SAVED"
        }

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            action="accept",
            suggestion_ids=["suggest.abc123"],
        )

        assert "Successfully" in result
        assert docs_tools._SUGGEST_MODE_ENROLLED is True

    @pytest.mark.asyncio
    async def test_direct_edit_batch_not_verified(self):
        """suggest_mode=false should never touch the verification machinery."""
        service = _make_service([DOC_LENGTH_STUB])

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            operations=INSERT_TEXT_OPERATIONS,
            suggest_mode=False,
        )

        assert "Error" not in result
        assert docs_tools._SUGGEST_MODE_ENROLLED is None

        body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert "writeControl" not in body

    @pytest.mark.asyncio
    async def test_header_suggestion_is_detected_by_verifier(self):
        """Regression test: a suggestion inside a header must not be missed
        by the enrollment verifier. Previously _fetch_doc_suggestions only
        walked body/tabs, so a real suggestion placed in a header would look
        identical before/after and falsely disable suggest_mode for the rest
        of the process."""
        service = _make_service(
            [
                NO_SUGGESTIONS_DOC_WITH_HEADER,
                DOC_LENGTH_STUB,
                WITH_HEADER_SUGGESTION_DOC,
            ]
        )

        result = await _unwrap(docs_tools.batch_update_doc)(
            service=service,
            user_google_email="user@example.com",
            document_id="a" * 25,
            operations=HEADER_INSERT_OPERATIONS,
            suggest_mode=True,
        )

        assert "Error" not in result
        assert docs_tools._SUGGEST_MODE_ENROLLED is True


class TestValidateSuggestModeOperations:
    def test_create_header_footer_rejected(self):
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "create_header_footer", "section_type": "header"}]
        )
        assert error is not None
        assert "CreateHeader/CreateFooter" in error

    def test_plain_insert_text_allowed(self):
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "insert_text", "end_of_segment": True, "text": "hi\n"}]
        )
        assert error is None
