"""
Tests for suggest_mode's Developer Preview verification behaviour.

Suggested edits are gated behind the Workspace Developer Preview Program, which
exposes no enrollment API. An unenrolled project may silently ignore
writeControl.writeMode and apply "suggested" edits directly, so batch_update_doc
diffs suggestion IDs before/after and warns when the expected suggestion does
not appear.

That signal is a heuristic with known blind spots, so it only ever warns on the
affected response - it must never disable the feature, and must never leak a
negative verdict to another user. These tests pin both the detection and, just
as importantly, the things it is required NOT to do.
"""

import pytest
from unittest.mock import Mock

from gdocs import docs_tools


def _unwrap(tool):
    """Strip MCP/auth decorators to get at the raw tool implementation."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _doc(*paragraph_elements, headers=None):
    """Build a minimal documents.get payload with the given paragraph elements."""
    payload = {
        "body": {"content": [{"paragraph": {"elements": list(paragraph_elements)}}]},
        "tabs": [],
    }
    if headers is not None:
        payload["headers"] = headers
    return payload


PLAIN_RUN = {"textRun": {"content": "Hello\n"}}
SUGGESTED_RUN = {
    "textRun": {
        "content": "New sentence\n",
        "suggestedInsertionIds": ["suggest.abc123"],
    }
}
HEADER_SUGGESTED_RUN = {
    "textRun": {
        "content": "Suggested header addition\n",
        "suggestedInsertionIds": ["suggest.header1"],
    }
}

NO_SUGGESTIONS_DOC = _doc(PLAIN_RUN)
WITH_SUGGESTION_DOC = _doc(PLAIN_RUN, SUGGESTED_RUN)
NO_SUGGESTIONS_DOC_WITH_HEADER = _doc(
    PLAIN_RUN, headers={"header1": {"content": [{"paragraph": {"elements": []}}]}}
)
WITH_HEADER_SUGGESTION_DOC = _doc(
    PLAIN_RUN,
    headers={
        "header1": {"content": [{"paragraph": {"elements": [HEADER_SUGGESTED_RUN]}}]}
    },
)

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
FIND_REPLACE_OPERATIONS = [
    {"type": "find_replace", "find_text": "nonexistent", "replace_text": "x"}
]

USER = "user@example.com"
OTHER_USER = "other@example.com"


@pytest.fixture(autouse=True)
def _reset_verified_users():
    """Isolate the process-wide verified-user cache between tests."""
    docs_tools._SUGGEST_MODE_VERIFIED_USERS.clear()
    yield
    docs_tools._SUGGEST_MODE_VERIFIED_USERS.clear()


def _make_service(get_results, batch_update_return=None):
    """
    Build a mock Docs service.

    get_results is consumed in order by successive documents().get() calls; an
    exception instance is raised instead of returned. Once exhausted the last
    value repeats, so a miscounted test fails on an assertion rather than
    raising StopIteration inside asyncio.to_thread (which hangs the run).
    """
    remaining = list(get_results)
    last = {"value": None}

    def next_get(*args, **kwargs):
        """Return the next queued documents.get result, raising exceptions."""
        value = remaining.pop(0) if remaining else last["value"]
        last["value"] = value
        if isinstance(value, Exception):
            raise value
        return value

    service = Mock()
    service.documents.return_value.get.return_value.execute = Mock(
        side_effect=next_get
    )
    service.documents.return_value.batchUpdate.return_value.execute.return_value = (
        batch_update_return
        if batch_update_return is not None
        else {"replies": [{}], "commentUpdateState": "ALL_SAVED"}
    )
    return service


async def _run_batch(service, operations, suggest_mode=True, user=USER):
    """Invoke batch_update_doc with the standard test arguments."""
    return await _unwrap(docs_tools.batch_update_doc)(
        service=service,
        user_google_email=user,
        document_id="a" * 25,
        operations=operations,
        suggest_mode=suggest_mode,
    )


class TestSuggestModeVerification:
    """Detection of suggest_mode silently falling through to direct edits."""

    @pytest.mark.asyncio
    async def test_confirmed_when_new_suggestion_appears(self):
        """A new suggestion ID confirms suggest mode and sends writeControl."""
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, WITH_SUGGESTION_DOC]
        )

        result = await _run_batch(service, INSERT_TEXT_OPERATIONS)

        assert "Error" not in result
        assert "WARNING" not in result
        assert "Applied as suggested edits" in result
        assert USER in docs_tools._SUGGEST_MODE_VERIFIED_USERS

        body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["writeControl"] == {"writeMode": "SUGGEST"}

    @pytest.mark.asyncio
    async def test_warns_when_no_suggestion_appears(self):
        """No suggestion where one was expected warns without erroring."""
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC]
        )

        result = await _run_batch(service, INSERT_TEXT_OPERATIONS)

        assert "WARNING" in result
        assert "Developer Preview" in result
        assert USER not in docs_tools._SUGGEST_MODE_VERIFIED_USERS

    @pytest.mark.asyncio
    async def test_warning_does_not_disable_later_calls(self):
        """A warning must not latch: the next call still attempts suggest mode.

        An earlier revision disabled suggestions process-wide after one
        negative verdict, turning any heuristic miss into an outage.
        """
        first = _make_service([NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC])
        await _run_batch(first, INSERT_TEXT_OPERATIONS)

        second = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, WITH_SUGGESTION_DOC]
        )
        result = await _run_batch(second, INSERT_TEXT_OPERATIONS)

        assert "Error" not in result
        assert "WARNING" not in result
        body = second.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert body["writeControl"] == {"writeMode": "SUGGEST"}

    @pytest.mark.asyncio
    async def test_verdict_does_not_leak_across_users(self):
        """One user's negative verdict must not affect another user.

        A single process serves many users (OAuth 2.1 multi-user mode) whose
        accounts and projects can differ in Preview enrollment.
        """
        failing = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC]
        )
        warned = await _run_batch(failing, INSERT_TEXT_OPERATIONS, user=USER)
        assert "WARNING" in warned

        working = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, WITH_SUGGESTION_DOC]
        )
        result = await _run_batch(working, INSERT_TEXT_OPERATIONS, user=OTHER_USER)

        assert "WARNING" not in result
        assert OTHER_USER in docs_tools._SUGGEST_MODE_VERIFIED_USERS
        assert USER not in docs_tools._SUGGEST_MODE_VERIFIED_USERS

    @pytest.mark.asyncio
    async def test_verified_user_skips_reverification(self):
        """Once confirmed, later calls skip the two extra document reads."""
        docs_tools._SUGGEST_MODE_VERIFIED_USERS.add(USER)
        service = _make_service([DOC_LENGTH_STUB])

        result = await _run_batch(service, INSERT_TEXT_OPERATIONS)

        assert "Error" not in result
        # Only the batch manager's document-length read.
        assert service.documents.return_value.get.return_value.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_header_suggestion_is_detected(self):
        """A suggestion inside a header counts; the walk covers segments.

        _fetch_doc_suggestions once read only body/tabs, so a real header
        suggestion looked identical before and after.
        """
        service = _make_service(
            [
                NO_SUGGESTIONS_DOC_WITH_HEADER,
                DOC_LENGTH_STUB,
                WITH_HEADER_SUGGESTION_DOC,
            ]
        )

        result = await _run_batch(service, HEADER_INSERT_OPERATIONS)

        assert "WARNING" not in result
        assert USER in docs_tools._SUGGEST_MODE_VERIFIED_USERS


class TestInconclusiveVerification:
    """Cases where the diff proves nothing and must not produce a warning."""

    @pytest.mark.asyncio
    async def test_zero_match_find_replace_does_not_warn(self):
        """A find_replace matching nothing had nothing to suggest."""
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC],
            batch_update_return={
                "replies": [{"replaceAllText": {"occurrencesChanged": 0}}],
                "commentUpdateState": "ALL_SAVED",
            },
        )

        result = await _run_batch(service, FIND_REPLACE_OPERATIONS)

        assert "WARNING" not in result
        assert USER not in docs_tools._SUGGEST_MODE_VERIFIED_USERS

    @pytest.mark.asyncio
    async def test_matched_find_replace_without_suggestion_warns(self):
        """A find_replace that did match but left no suggestion is suspicious."""
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC],
            batch_update_return={
                "replies": [{"replaceAllText": {"occurrencesChanged": 2}}],
                "commentUpdateState": "ALL_SAVED",
            },
        )

        result = await _run_batch(service, FIND_REPLACE_OPERATIONS)

        assert "WARNING" in result

    @pytest.mark.asyncio
    async def test_failed_post_batch_read_does_not_warn(self):
        """The batch already succeeded; a failed read is inconclusive."""
        service = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, RuntimeError("transient read error")]
        )

        result = await _run_batch(service, INSERT_TEXT_OPERATIONS)

        assert "Error" not in result
        assert "WARNING" not in result
        assert USER not in docs_tools._SUGGEST_MODE_VERIFIED_USERS

    @pytest.mark.asyncio
    async def test_failed_pre_batch_read_skips_verification(self):
        """Without a baseline there is nothing to diff against."""
        service = _make_service(
            [RuntimeError("transient read error"), DOC_LENGTH_STUB]
        )

        result = await _run_batch(service, INSERT_TEXT_OPERATIONS)

        assert "Error" not in result
        assert "WARNING" not in result

    @pytest.mark.asyncio
    async def test_direct_edit_batch_is_not_verified(self):
        """suggest_mode=false sends no writeControl and skips verification."""
        service = _make_service([DOC_LENGTH_STUB])

        result = await _run_batch(service, INSERT_TEXT_OPERATIONS, suggest_mode=False)

        assert "Error" not in result
        assert "WARNING" not in result
        assert USER not in docs_tools._SUGGEST_MODE_VERIFIED_USERS

        body = service.documents.return_value.batchUpdate.call_args.kwargs["body"]
        assert "writeControl" not in body


class TestManageDocSuggestions:
    """manage_doc_suggestions must stay available regardless of verification."""

    @pytest.mark.asyncio
    async def test_works_after_a_suggest_mode_warning(self):
        """A prior warning must not block suggestion-thread management."""
        warned = _make_service(
            [NO_SUGGESTIONS_DOC, DOC_LENGTH_STUB, NO_SUGGESTIONS_DOC]
        )
        assert "WARNING" in await _run_batch(warned, INSERT_TEXT_OPERATIONS)

        service = Mock()
        service.documents.return_value.batchUpdate.return_value.execute.return_value = {
            "commentUpdateState": "ALL_SAVED"
        }

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id="a" * 25,
            action="accept",
            suggestion_ids=["suggest.abc123"],
        )

        assert "Successfully" in result
        request = service.documents.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ][0]
        assert request == {"acceptSuggestion": {"suggestionId": "suggest.abc123"}}

    @pytest.mark.asyncio
    async def test_rejects_empty_suggestion_ids(self):
        """An empty ID list is a caller error, not an API call."""
        service = Mock()

        result = await _unwrap(docs_tools.manage_doc_suggestions)(
            service=service,
            user_google_email=USER,
            document_id="a" * 25,
            action="accept",
            suggestion_ids=[],
        )

        assert "Error" in result
        service.documents.return_value.batchUpdate.assert_not_called()


class TestSuggestionCoverage:
    """Which suggestion types the document walk can actually surface.

    A suggestion the walk cannot see is not merely missing from
    list_doc_suggestions - it has no reachable ID, so manage_doc_suggestions
    cannot accept, reject, or delete it either.
    """

    def test_paragraph_bullet_and_style_suggestions_are_found(self):
        """Bullet/paragraph-style changes live only on the paragraph.

        create_bullet_list and update_paragraph_style record their suggestion
        under suggestedBulletChanges / suggestedParagraphStyleChanges with no
        marker on any text run, so reading text runs alone missed them.
        """
        elements = [
            {
                "paragraph": {
                    "elements": [{"textRun": {"content": "First item line.\n"}}],
                    "suggestedBulletChanges": {"suggest.b1": {}},
                    "suggestedParagraphStyleChanges": {"suggest.b1": {}},
                }
            }
        ]
        found = {}
        docs_tools._collect_suggestions_from_elements(elements, found)

        assert set(found) == {"suggest.b1"}
        assert found["suggest.b1"]["types"] == {"bullet", "paragraph-style"}
        assert "First item line." in found["suggest.b1"]["text_preview"]

    def test_table_structure_suggestions_are_found(self):
        """Tables, rows, and cells carry their own suggestion IDs."""
        elements = [
            {
                "table": {
                    "suggestedInsertionIds": ["suggest.table"],
                    "tableRows": [
                        {
                            "suggestedInsertionIds": ["suggest.row"],
                            "tableCells": [
                                {
                                    "suggestedInsertionIds": ["suggest.cell"],
                                    "content": [],
                                }
                            ],
                        }
                    ],
                }
            }
        ]
        found = {}
        docs_tools._collect_suggestions_from_elements(elements, found)

        assert set(found) == {"suggest.table", "suggest.row", "suggest.cell"}

    def test_non_text_run_paragraph_elements_are_found(self):
        """Every ParagraphElement variant carries the same suggestion fields."""
        elements = [
            {
                "paragraph": {
                    "elements": [
                        {"pageBreak": {"suggestedInsertionIds": ["suggest.break"]}},
                        {
                            "inlineObjectElement": {
                                "inlineObjectId": "kix.1",
                                "suggestedInsertionIds": ["suggest.image"],
                            }
                        },
                        {
                            "horizontalRule": {
                                "suggestedDeletionIds": ["suggest.rule"]
                            }
                        },
                    ]
                }
            }
        ]
        found = {}
        docs_tools._collect_suggestions_from_elements(elements, found)

        assert set(found) == {"suggest.break", "suggest.image", "suggest.rule"}

    def test_text_run_previews_accumulate_across_fragments(self):
        """One suggestion split across runs reads as continuous text."""
        elements = [
            {
                "paragraph": {
                    "elements": [
                        {
                            "textRun": {
                                "content": "Hello ",
                                "suggestedInsertionIds": ["suggest.t"],
                            }
                        },
                        {
                            "textRun": {
                                "content": "world",
                                "suggestedInsertionIds": ["suggest.t"],
                            }
                        },
                    ]
                }
            }
        ]
        found = {}
        docs_tools._collect_suggestions_from_elements(elements, found)

        assert found["suggest.t"]["text_preview"] == "Hello world"

    def test_malformed_nodes_do_not_crash_the_walk(self):
        """Scalars, None, and lists among element values are skipped."""
        elements = [
            {
                "paragraph": {
                    "elements": [
                        {"startIndex": 0, "endIndex": 1, "textRun": None},
                        {"scalar": 5, "text": "x", "items": [1, 2]},
                    ]
                }
            }
        ]
        found = {}
        docs_tools._collect_suggestions_from_elements(elements, found)

        assert found == {}


class TestValidateSuggestModeOperations:
    """Client-side rejection of operations the API refuses in suggest mode."""

    def test_create_header_footer_rejected(self):
        """CreateHeader/CreateFooter cannot be suggested."""
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "create_header_footer", "section_type": "header"}]
        )
        assert error is not None
        assert "CreateHeader/CreateFooter" in error

    def test_named_range_rejected(self):
        """Named-range operations cannot be suggested."""
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "delete_named_range", "named_range_name": "x"}]
        )
        assert error is not None
        assert "DeleteNamedRange" in error

    def test_unsupported_document_style_field_rejected(self):
        """documentFormat and header/footer toggles cannot be suggested."""
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "update_document_style", "document_mode": "PAGELESS"}]
        )
        assert error is not None
        assert "document_mode" in error

    def test_plain_insert_text_allowed(self):
        """Ordinary text operations pass validation."""
        error = docs_tools._validate_suggest_mode_operations(
            [{"type": "insert_text", "end_of_segment": True, "text": "hi\n"}]
        )
        assert error is None


class TestSuggestModeDiffIsMeaningful:
    """The predicate deciding whether an empty diff is worth warning about."""

    def test_assumed_effect_operation_is_meaningful(self):
        """insert_text should have left a suggestion behind."""
        assert docs_tools._suggest_mode_diff_is_meaningful(
            INSERT_TEXT_OPERATIONS, {}
        )

    def test_zero_match_find_replace_is_not_meaningful(self):
        """Nothing matched, so nothing could have been suggested."""
        assert not docs_tools._suggest_mode_diff_is_meaningful(
            FIND_REPLACE_OPERATIONS, {"replace_all_text_occurrences": [0]}
        )

    def test_matched_find_replace_is_meaningful(self):
        """Something matched, so a suggestion was expected."""
        assert docs_tools._suggest_mode_diff_is_meaningful(
            FIND_REPLACE_OPERATIONS, {"replace_all_text_occurrences": [0, 3]}
        )

    def test_missing_occurrence_metadata_is_not_meaningful(self):
        """Absent counts must not be read as evidence of a problem."""
        assert not docs_tools._suggest_mode_diff_is_meaningful(
            FIND_REPLACE_OPERATIONS, {}
        )
