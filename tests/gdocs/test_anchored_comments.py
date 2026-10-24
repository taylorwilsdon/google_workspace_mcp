"""Tests for text-anchored Google Docs comments created through the Docs API."""

import inspect
import os
import sys
from unittest.mock import Mock

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.scopes import DRIVE_SCOPE
from core.comments import create_comment_tools
from core.utils import UserInputError
from gdocs.docs_anchors import (
    AnchorResolutionError,
    iter_paragraphs,
    resolve_anchor_range,
)
from gdocs.docs_comments import (
    COMMENT_UPDATE_FAILED,
    MAX_COMMENT_UTF8_UNITS,
    build_insert_comment_request,
    insert_anchored_comment,
)

DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdEf"


def _paragraph(start_index, text):
    end_index = start_index + len(text.encode("utf-16-le")) // 2
    return {
        "startIndex": start_index,
        "endIndex": end_index,
        "paragraph": {
            "elements": [
                {
                    "startIndex": start_index,
                    "endIndex": end_index,
                    "textRun": {"content": text},
                }
            ],
            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
        },
    }


def _table(start_index, cell_texts):
    """One-row table whose cells each hold one paragraph."""
    cells = []
    index = start_index + 2  # table start and row start each take one index
    for text in cell_texts:
        paragraph = _paragraph(index + 1, text)  # cell start takes one index
        cells.append({"content": [paragraph]})
        index = paragraph["endIndex"]
    return {
        "startIndex": start_index,
        "endIndex": index + 1,
        "table": {"tableRows": [{"tableCells": cells}]},
    }


# "Intro\n" 1-7, "The quick brown fox.\n" 7-28, table from 28
BODY = {
    "content": [
        _paragraph(1, "Intro\n"),
        _paragraph(7, "The quick brown fox.\n"),
        _table(28, ["Status\n", "Differs / Unknown\n"]),
    ]
}


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _docs_service(
    doc=None,
    comment_id="thread-1",
    quote="The quick brown fox.",
    update_state="ALL_SAVED",
):
    service = Mock()
    documents = service.documents.return_value
    documents.get.return_value.execute.return_value = doc or {"body": BODY}
    thread = {"commentId": comment_id, "plainTextQuote": quote} if comment_id else {}
    documents.batchUpdate.return_value.execute.return_value = {
        "replies": [{"insertComment": {"commentThread": thread}}],
        "commentUpdateState": update_state,
    }
    return service


def _batch_requests(service):
    return service.documents.return_value.batchUpdate.call_args.kwargs["body"][
        "requests"
    ]


@pytest.fixture
def manage_comment():
    tools = create_comment_tools(
        "document", "document_id", insert_anchored_comment=insert_anchored_comment
    )
    return _unwrap(tools["manage_comment"])


class TestResolveAnchorRange:
    def test_anchor_cannot_span_adjacent_paragraphs(self):
        with pytest.raises(AnchorResolutionError, match="was not found"):
            resolve_anchor_range(BODY, "Intro\nThe quick")

    def test_anchor_can_include_its_paragraph_ending_newline(self):
        assert resolve_anchor_range(BODY, "Intro\n") == (1, 7)

    def test_returns_the_span_of_a_unique_match(self):
        assert resolve_anchor_range(BODY, "quick brown") == (11, 22)

    def test_counts_non_bmp_characters_as_two_index_units(self):
        body = {"content": [_paragraph(1, "\U0001f600 go\n")]}
        # The emoji occupies indices 1-3, the space 3-4, "go" 4-6.
        assert resolve_anchor_range(body, "go") == (4, 6)

    def test_searches_table_cells(self):
        start, end = resolve_anchor_range(BODY, "Differs / Unknown")
        cell_paragraph = BODY["content"][2]["table"]["tableRows"][0]["tableCells"][1][
            "content"
        ][0]
        assert start == cell_paragraph["startIndex"]
        assert end == start + len("Differs / Unknown")

    def test_ambiguous_anchor_is_refused(self):
        with pytest.raises(AnchorResolutionError, match="matches 2 locations"):
            resolve_anchor_range(BODY, "ro")  # Intro and brown

    def test_missing_anchor_is_reported(self):
        with pytest.raises(AnchorResolutionError, match="was not found"):
            resolve_anchor_range(BODY, "lazy dog")

    @pytest.mark.parametrize("anchor", ["", "   "])
    def test_empty_anchor_is_refused(self, anchor):
        with pytest.raises(AnchorResolutionError, match="must not be empty"):
            resolve_anchor_range(BODY, anchor)

    def test_iter_paragraphs_skips_tables_by_default(self):
        texts = [text for _, _, text, _ in iter_paragraphs(BODY)]
        assert texts == ["Intro\n", "The quick brown fox.\n"]


class TestBuildInsertCommentRequest:
    def test_body_range_without_tab(self):
        assert build_insert_comment_request(11, 22, "Check this") == {
            "insertComment": {
                "range": {"startIndex": 11, "endIndex": 22},
                "content": "Check this",
            }
        }

    def test_tab_id_goes_into_the_range(self):
        request = build_insert_comment_request(11, 22, "x", tab_id="t.1")
        assert request["insertComment"]["range"]["tabId"] == "t.1"


class TestInsertAnchoredComment:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("revision", ["rev-1", None])
    async def test_uses_revision_guard_only_when_the_api_provides_one(self, revision):
        service = _docs_service({"body": BODY, "revisionId": revision})

        await insert_anchored_comment(service, DOC_ID, "quick brown", "Check this")

        expected = {
            "requests": [
                {
                    "insertComment": {
                        "range": {"startIndex": 11, "endIndex": 22},
                        "content": "Check this",
                    }
                }
            ]
        }
        if revision:
            expected["writeControl"] = {"requiredRevisionId": revision}
        assert service.documents.return_value.batchUpdate.call_args.kwargs == {
            "documentId": DOC_ID,
            "body": expected,
        }

    @pytest.mark.asyncio
    async def test_stale_revision_failure_is_not_retried_with_old_indices(self):
        service = _docs_service({"body": BODY, "revisionId": "rev-1"})
        failure = HttpError(
            Response({"status": "400"}),
            b'{"error":{"code":400,"message":"requiredRevisionId is not current"}}',
        )
        service.documents.return_value.batchUpdate.return_value.execute.side_effect = (
            failure
        )

        with pytest.raises(HttpError, match="requiredRevisionId"):
            await insert_anchored_comment(service, DOC_ID, "quick brown", "x")

        service.documents.return_value.get.assert_called_once()
        service.documents.return_value.batchUpdate.assert_called_once()
        service.documents.return_value.batchUpdate.return_value.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_creates_comment_on_resolved_range(self):
        service = _docs_service()

        created = await insert_anchored_comment(
            service, DOC_ID, "quick brown", "Check this"
        )

        assert _batch_requests(service) == [
            {
                "insertComment": {
                    "range": {"startIndex": 11, "endIndex": 22},
                    "content": "Check this",
                }
            }
        ]
        assert created == {
            "comment_id": "thread-1",
            "quoted_text": "The quick brown fox.",
            "start_index": 11,
            "end_index": 22,
        }
        get_kwargs = service.documents.return_value.get.call_args.kwargs
        assert get_kwargs["includeTabsContent"] is True

    @pytest.mark.asyncio
    async def test_resolves_anchor_in_the_requested_tab(self):
        other = {"content": [_paragraph(1, "Elsewhere\n")]}
        doc = {
            "tabs": [
                {"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": other}},
                {"tabProperties": {"tabId": "t.1"}, "documentTab": {"body": BODY}},
            ]
        }
        service = _docs_service(doc)

        await insert_anchored_comment(service, DOC_ID, "quick brown", "x", "t.1")

        comment_range = _batch_requests(service)[0]["insertComment"]["range"]
        assert comment_range == {"startIndex": 11, "endIndex": 22, "tabId": "t.1"}

    @pytest.mark.asyncio
    async def test_defaults_to_the_first_tab(self):
        doc = {
            "tabs": [{"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": BODY}}]
        }
        service = _docs_service(doc)

        created = await insert_anchored_comment(service, DOC_ID, "quick brown", "x")

        assert (created["start_index"], created["end_index"]) == (11, 22)
        assert "tabId" not in _batch_requests(service)[0]["insertComment"]["range"]

    @pytest.mark.asyncio
    async def test_unknown_tab_is_a_user_error(self):
        doc = {
            "tabs": [{"tabProperties": {"tabId": "t.0"}, "documentTab": {"body": BODY}}]
        }
        service = _docs_service(doc)

        with pytest.raises(UserInputError, match="Tab 'nope' not found"):
            await insert_anchored_comment(service, DOC_ID, "quick brown", "x", "nope")
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_ambiguous_anchor_is_a_user_error_before_any_write(self):
        service = _docs_service()

        with pytest.raises(UserInputError, match="matches 2 locations"):
            await insert_anchored_comment(service, DOC_ID, "ro", "x")
        service.documents.return_value.batchUpdate.assert_not_called()

    @pytest.mark.asyncio
    async def test_over_long_content_is_refused_before_reading_the_doc(self):
        service = _docs_service()

        with pytest.raises(UserInputError, match="2048"):
            await insert_anchored_comment(
                service, DOC_ID, "quick brown", "x" * (MAX_COMMENT_UTF8_UNITS + 1)
            )
        service.documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_comment_update_state_is_reported(self):
        service = _docs_service(update_state=COMMENT_UPDATE_FAILED)

        with pytest.raises(RuntimeError, match="was not saved"):
            await insert_anchored_comment(service, DOC_ID, "quick brown", "x")

    @pytest.mark.asyncio
    async def test_missing_comment_id_raises(self):
        service = _docs_service(comment_id=None)

        with pytest.raises(RuntimeError, match="did not return a comment ID"):
            await insert_anchored_comment(service, DOC_ID, "quick brown", "x")

    @pytest.mark.asyncio
    async def test_quote_falls_back_to_anchor_text(self):
        service = _docs_service(quote=None)

        created = await insert_anchored_comment(service, DOC_ID, "quick brown", "x")

        assert created["quoted_text"] == "quick brown"


class TestManageDocumentCommentAnchorText:
    @pytest.mark.asyncio
    async def test_create_with_anchor_text_uses_docs_api(self, manage_comment):
        drive = Mock()
        docs = _docs_service(comment_id="thread-42")

        result = await manage_comment(
            drive,
            docs,
            "user@example.com",
            DOC_ID,
            "create",
            comment_content="Looks off",
            anchor_text="quick brown",
        )

        assert _batch_requests(docs)[0]["insertComment"]["content"] == "Looks off"
        assert "Comment ID: thread-42" in result
        assert "Anchored text: The quick brown fox." in result
        assert "indices 11-22" in result
        drive.comments.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_without_anchor_text_uses_drive(self, manage_comment):
        drive = Mock()
        drive.comments.return_value.create.return_value.execute.return_value = {
            "id": "c1",
            "author": {"displayName": "Alice"},
            "createdTime": "2025-01-15T10:00:00Z",
        }
        docs = Mock()

        result = await manage_comment(
            drive,
            docs,
            "user@example.com",
            DOC_ID,
            "create",
            comment_content="Document-level",
        )

        assert "Comment ID: c1" in result
        docs.documents.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("anchor_text", ["", "   "])
    async def test_blank_anchor_text_is_rejected_before_any_api_call(
        self, manage_comment, anchor_text
    ):
        drive = Mock()
        docs = Mock()

        with pytest.raises(ValueError, match="anchor_text must not be empty"):
            await manage_comment(
                drive,
                docs,
                "user@example.com",
                DOC_ID,
                "create",
                comment_content="Document-level",
                anchor_text=anchor_text,
            )

        drive.comments.assert_not_called()
        docs.documents.assert_not_called()

    @pytest.mark.parametrize("action", ["reply", "resolve"])
    @pytest.mark.asyncio
    async def test_anchor_text_rejected_outside_create(self, manage_comment, action):
        with pytest.raises(ValueError, match="only supported for the create"):
            await manage_comment(
                Mock(),
                Mock(),
                "user@example.com",
                DOC_ID,
                action,
                comment_content="hi",
                comment_id="c1",
                anchor_text="quick brown",
            )

    @pytest.mark.asyncio
    async def test_tab_id_requires_anchor_text(self, manage_comment):
        with pytest.raises(ValueError, match="tab_id requires anchor_text"):
            await manage_comment(
                Mock(),
                Mock(),
                "user@example.com",
                DOC_ID,
                "create",
                comment_content="hi",
                tab_id="t.0",
            )

    @pytest.mark.asyncio
    async def test_anchor_text_requires_content(self, manage_comment):
        with pytest.raises(ValueError, match="comment_content is required"):
            await manage_comment(
                Mock(), Mock(), "user@example.com", DOC_ID, "create", anchor_text="x"
            )

    @pytest.mark.asyncio
    async def test_anchor_text_without_implementation_is_reported(self):
        tools = create_comment_tools("document", "document_id")
        fn = _unwrap(tools["manage_comment"])

        with pytest.raises(ValueError, match="insert_anchored_comment is required"):
            await fn(
                Mock(),
                Mock(),
                "user@example.com",
                DOC_ID,
                "create",
                comment_content="hi",
                anchor_text="quick brown",
            )

    def test_document_tool_exposes_anchor_parameters(self, manage_comment):
        params = inspect.signature(manage_comment).parameters
        assert params["anchor_text"].default is None
        assert params["tab_id"].default is None


def test_manage_document_comment_requires_only_drive_scope():
    tools = create_comment_tools(
        "document", "document_id", insert_anchored_comment=insert_anchored_comment
    )
    tool = tools["manage_comment"]
    fn = tool.fn if hasattr(tool, "fn") else tool
    assert set(fn._required_google_scopes) == {DRIVE_SCOPE}
