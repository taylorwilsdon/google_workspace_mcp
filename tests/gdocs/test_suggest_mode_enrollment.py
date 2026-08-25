"""Tests for Google Docs suggested-edit creation, listing, and management."""

from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError

from gdocs import docs_tools


def _unwrap(tool):
    """Strip MCP/auth decorators to get the raw tool implementation."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


DOCUMENT_ID = "a" * 25
USER = "user@example.com"
DOC_LENGTH_STUB = {
    "documentId": DOCUMENT_ID,
    "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
    "body": {"content": [{"endIndex": 100}]},
}
INSERT_TEXT_OPERATIONS = [
    {"type": "insert_text", "end_of_segment": True, "text": "New sentence\n"}
]
FIND_REPLACE_OPERATIONS = [
    {"type": "find_replace", "find_text": "missing", "replace_text": "x"}
]


def _make_service(batch_update_return):
    service = Mock()
    documents = service.documents.return_value
    documents.batchUpdate.return_value.execute.return_value = batch_update_return
    documents.get.return_value.execute.return_value = DOC_LENGTH_STUB
    return service


async def _run_batch(service, operations=INSERT_TEXT_OPERATIONS, suggest_mode=True):
    return await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email=USER,
        document_id=DOCUMENT_ID,
        operations=operations,
        suggest_mode=suggest_mode,
    )


class TestSuggestModeResponseHandling:
    @pytest.mark.asyncio
    async def test_all_saved_is_authoritative_and_uses_one_small_read(self):
        service = _make_service(
            {
                "replies": [{}],
                "commentUpdateState": "ALL_SAVED",
                "suggestionResponses": [
                    {"updatedSummarySuggestionIds": ["suggest.existing"]}
                ],
            }
        )

        result = await _run_batch(service)

        assert "Applied as suggested edits" in result
        assert "updated: 1" in result
        assert "WARNING" not in result
        body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["writeControl"] == {"writeMode": "SUGGEST"}
        # Preview access is verified before mutation, then document length is read.
        assert service.documents.return_value.get.call_count == 2
        preflight_call = service.documents.return_value.get.call_args_list[0]
        assert preflight_call.kwargs["commentsViewMode"] == (
            "COMMENTS_VIEW_MODE_INCLUDED"
        )
        assert preflight_call.kwargs["fields"] == "documentId,commentsViewMode"
        assert service.documents.return_value.get.call_args.kwargs["fields"] == (
            "body/content(endIndex)"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "comment_state",
        [None, "NO_UPDATES_REQUESTED", "ALL_FAILED_UNKNOWN_REASON"],
    )
    async def test_unconfirmed_mutation_always_warns(self, comment_state):
        response = {"replies": [{}]}
        if comment_state is not None:
            response["commentUpdateState"] = comment_state
        service = _make_service(response)

        result = await _run_batch(service)

        assert "WARNING" in result
        assert "may be LIVE" in result
        rendered_state = comment_state or "MISSING"
        assert f"comment_update_state: {rendered_state}" in result

    @pytest.mark.asyncio
    async def test_zero_match_find_replace_is_the_only_benign_no_update(self):
        service = _make_service(
            {
                "replies": [{"replaceAllText": {"occurrencesChanged": 0}}],
                "commentUpdateState": "NO_UPDATES_REQUESTED",
            }
        )

        result = await _run_batch(service, FIND_REPLACE_OPERATIONS)

        assert "WARNING" not in result
        assert "matched zero occurrences" in result

    @pytest.mark.asyncio
    async def test_matched_find_replace_with_no_updates_warns(self):
        service = _make_service(
            {
                "replies": [{"replaceAllText": {"occurrencesChanged": 2}}],
                "commentUpdateState": "NO_UPDATES_REQUESTED",
            }
        )

        result = await _run_batch(service, FIND_REPLACE_OPERATIONS)

        assert "WARNING" in result

    @pytest.mark.asyncio
    async def test_direct_edit_omits_write_control_and_preview_reporting(self):
        service = _make_service({"replies": [{}]})

        result = await _run_batch(service, suggest_mode=False)

        body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert "writeControl" not in body
        assert "comment_update_state" not in result
        assert "WARNING" not in result

    @pytest.mark.asyncio
    async def test_unenrolled_project_is_blocked_before_mutation(self):
        service = _make_service({"replies": [{}]})
        service.documents.return_value.get.return_value.execute.side_effect = HttpError(
            resp=Mock(status=400),
            content=(
                b'{"error":{"message":"Invalid JSON payload received. Unknown '
                b'name \\"comments_view_mode\\": Field \\"comments_view_mode\\" '
                b'could not be found in request message."}}'
            ),
        )

        result = await _run_batch(service)

        assert "suggest_mode is unavailable" in result
        assert "No document changes were made" in result
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_unconfirmed_preview_access_is_blocked_before_mutation(self):
        service = _make_service({"replies": [{}]})
        service.documents.return_value.get.return_value.execute.return_value = {
            "documentId": DOCUMENT_ID
        }

        result = await _run_batch(service)

        assert "did not confirm Developer Preview comments access" in result
        assert "No document changes were made" in result
        service.documents.return_value.batchUpdate.assert_not_called()


class TestSuggestionResponseSummary:
    def test_summarizes_all_authoritative_response_categories(self):
        message = docs_tools._describe_suggestion_responses(
            [
                {
                    "createdSuggestionIds": ["s.1", "s.2"],
                    "updatedSummarySuggestionIds": ["s.3"],
                },
                {
                    "deletedSuggestionIds": ["s.4"],
                    "acceptedSuggestionIds": ["s.5"],
                    "rejectedSuggestionIds": ["s.6"],
                },
            ]
        )

        for expected in (
            "created: 2",
            "updated: 1",
            "deleted: 1",
            "accepted: 1",
            "rejected: 1",
        ):
            assert expected in message

    def test_duplicate_ids_are_counted_once(self):
        message = docs_tools._describe_suggestion_responses(
            [
                {"createdSuggestionIds": ["s.1"]},
                {"createdSuggestionIds": ["s.1"]},
            ]
        )
        assert "created: 1" in message


class TestManageDocSuggestions:
    @pytest.mark.asyncio
    async def test_manage_suggestions_reports_confirmed_save(self):
        service = _make_service(
            {
                "commentUpdateState": "ALL_SAVED",
                "suggestionResponses": [{"acceptedSuggestionIds": ["suggest.abc123"]}],
            }
        )

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id=DOCUMENT_ID,
            action="accept",
            suggestion_ids=["suggest.abc123"],
        )

        assert "Successfully applied 'accept'" in result
        request = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ][0]
        assert request == {"acceptSuggestion": {"suggestionId": "suggest.abc123"}}

    @pytest.mark.asyncio
    async def test_manage_suggestions_flags_unconfirmed_save(self):
        service = _make_service({"commentUpdateState": "ALL_FAILED_UNKNOWN_REASON"})

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id=DOCUMENT_ID,
            action="reject",
            suggestion_ids=["suggest.abc123"],
        )

        assert "Successfully" not in result
        assert "did not confirm" in result

    @pytest.mark.asyncio
    async def test_rejects_empty_suggestion_ids(self):
        service = Mock()

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id=DOCUMENT_ID,
            action="accept",
            suggestion_ids=[],
        )

        assert "Error" in result
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_unenrolled_project_is_blocked_before_management(self):
        service = _make_service({"commentUpdateState": "ALL_SAVED"})
        service.documents.return_value.get.return_value.execute.side_effect = HttpError(
            resp=Mock(status=400),
            content=(
                b'{"error":{"message":"Unknown name \\"comments_view_mode\\": '
                b'Field \\"comments_view_mode\\" could not be found."}}'
            ),
        )

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id=DOCUMENT_ID,
            action="accept",
            suggestion_ids=["suggest.abc123"],
        )

        assert "suggestion management is unavailable" in result
        assert "No suggestion changes were made" in result
        service.documents.return_value.batchUpdate.assert_not_called()


def _thread_payload(status="OPEN"):
    return {
        "suggestions": [
            {
                "suggestionId": "suggest.thread-only",
                "status": status,
                "summaryText": "Replace old wording with new wording",
                "headPost": {
                    "author": {"displayName": "Ada Lovelace"},
                    "createTime": "2026-08-24T12:34:56Z",
                },
            }
        ],
        "body": {"content": []},
        "tabs": [],
    }


class TestSuggestionThreadListing:
    @pytest.mark.asyncio
    async def test_thread_is_source_of_truth_without_inline_marker(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = (
            _thread_payload()
        )

        found = await docs_tools._fetch_doc_suggestions(service, DOCUMENT_ID)

        assert set(found) == {"suggest.thread-only"}
        assert found["suggest.thread-only"]["author"] == "Ada Lovelace"
        assert found["suggest.thread-only"]["create_time"] == ("2026-08-24T12:34:56Z")
        kwargs = service.documents.return_value.get.call_args.kwargs
        assert kwargs["commentsViewMode"] == "COMMENTS_VIEW_MODE_INCLUDED"

    @pytest.mark.asyncio
    async def test_non_open_threads_are_not_pending(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = (
            _thread_payload(status="ACCEPTED")
        )

        found = await docs_tools._fetch_doc_suggestions(service, DOCUMENT_ID)

        assert found == {}

    @pytest.mark.asyncio
    async def test_list_output_includes_thread_metadata(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = (
            _thread_payload()
        )

        result = await _unwrap(docs_tools.list_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id=DOCUMENT_ID,
        )

        assert "suggest.thread-only" in result
        assert "Replace old wording" in result
        assert "author: Ada Lovelace" in result
        assert "created: 2026-08-24T12:34:56Z" in result

    @pytest.mark.asyncio
    async def test_standard_discovery_client_gets_preview_query_parameter(self):
        service = Mock()
        documents = service.documents.return_value
        request = Mock()
        request.uri = f"https://docs.googleapis.com/v1/documents/{DOCUMENT_ID}?alt=json"
        request.execute.return_value = _thread_payload()

        def build_get(**kwargs):
            if "commentsViewMode" in kwargs:
                raise TypeError("unexpected keyword argument commentsViewMode")
            return request

        documents.get.side_effect = build_get

        found = await docs_tools._fetch_doc_suggestions(service, DOCUMENT_ID)

        assert "suggest.thread-only" in found
        assert "commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED" in request.uri

    @pytest.mark.asyncio
    async def test_unenrolled_project_falls_back_to_inline_markers(self):
        service = Mock()
        request = service.documents.return_value.get.return_value
        request.execute.side_effect = [
            HttpError(
                resp=Mock(status=400),
                content=(
                    b'{"error":{"message":"Unknown name '
                    b'\\"comments_view_mode\\": Field '
                    b'\\"comments_view_mode\\" could not be found."}}'
                ),
            ),
            {
                "body": {
                    "content": [
                        {
                            "paragraph": {
                                "elements": [
                                    {
                                        "textRun": {
                                            "content": "Suggested text",
                                            "suggestedInsertionIds": ["suggest.marker"],
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
                "tabs": [],
            },
        ]

        result = await _unwrap(docs_tools.list_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id=DOCUMENT_ID,
        )

        assert "Developer Preview suggestion-thread metadata is unavailable" in result
        assert "suggest.marker" in result
        assert "Suggested text" in result
        assert service.documents.return_value.get.call_count == 2
        fallback_kwargs = service.documents.return_value.get.call_args_list[1].kwargs
        assert "commentsViewMode" not in fallback_kwargs

    @pytest.mark.asyncio
    async def test_marker_fallback_remains_available_without_thread_field(self):
        service = Mock()
        service.documents.return_value.get.return_value.execute.return_value = {
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "Suggested text",
                                        "suggestedInsertionIds": ["suggest.marker"],
                                    }
                                }
                            ]
                        }
                    }
                ]
            },
            "tabs": [],
        }

        found = await docs_tools._fetch_doc_suggestions(service, DOCUMENT_ID)

        assert set(found) == {"suggest.marker"}
        assert found["suggest.marker"]["types"] == {"insertion"}


class TestSuggestionMarkerEnrichment:
    def test_deeply_nested_table_marker_has_no_arbitrary_depth_limit(self):
        content = [
            {
                "paragraph": {
                    "elements": [
                        {
                            "textRun": {
                                "content": "deep",
                                "suggestedInsertionIds": ["suggest.deep"],
                            }
                        }
                    ]
                }
            }
        ]
        for _ in range(8):
            content = [
                {"table": {"tableRows": [{"tableCells": [{"content": content}]}]}}
            ]

        found = {}
        docs_tools._collect_suggestions_from_elements(content, found)

        assert found["suggest.deep"]["text_preview"] == "deep"

    def test_paragraph_and_table_style_markers_are_enriched(self):
        content = [
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "line\n"}}],
                    "suggestedParagraphStyleChanges": {"suggest.p": {}},
                    "suggestedBulletChanges": {"suggest.p": {}},
                }
            },
            {
                "table": {
                    "tableRows": [
                        {
                            "suggestedTableRowStyleChanges": {"suggest.r": {}},
                            "tableCells": [
                                {
                                    "suggestedTableCellStyleChanges": {"suggest.c": {}},
                                    "content": [],
                                }
                            ],
                        }
                    ]
                }
            },
        ]

        found = {}
        docs_tools._collect_suggestions_from_elements(content, found)

        assert found["suggest.p"]["types"] == {"paragraph-style", "bullet"}
        assert found["suggest.r"]["types"] == {"table-row-style"}
        assert found["suggest.c"]["types"] == {"table-cell-style"}


class TestValidateSuggestModeOperations:
    def test_create_header_footer_is_allowed_by_published_contract(self):
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "create_header_footer", "section_type": "header"}]
        )
        assert error is None

    @pytest.mark.parametrize(
        "operation",
        [
            {"type": "insert_doc_tab", "title": "New tab", "index": 0},
            {"type": "delete_named_range", "named_range_id": "range.1"},
            {
                "type": "update_table_column_properties",
                "table_start_index": 1,
                "column_indices": [0],
                "width": 72,
            },
        ],
    )
    def test_published_unsupported_requests_are_rejected(self, operation):
        error = docs_tools._validate_suggest_mode_operations([operation])
        assert error is not None
        assert operation["type"] in error

    def test_unsupported_document_style_field_is_rejected(self):
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "update_document_style", "document_mode": "PAGES"}]
        )
        assert error is not None
        assert "document_mode" in error

    def test_plain_insert_text_is_allowed(self):
        assert (
            docs_tools._validate_suggest_mode_operations(INSERT_TEXT_OPERATIONS) is None
        )
