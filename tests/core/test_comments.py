"""Tests for core comments module."""

import inspect
import sys
import os
import pytest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.comments import (
    _read_comments_impl,
    _create_comment_impl,
    create_comment_tools,
)


def _make_comment(comment_id, content="Comment text", author="Alice"):
    """Helper to build a comment dict for mocking."""
    return {
        "id": comment_id,
        "content": content,
        "author": {"displayName": author},
        "createdTime": "2025-01-15T10:00:00Z",
        "modifiedTime": "2025-01-15T10:00:00Z",
        "resolved": False,
        "replies": [],
    }


def _mock_service_pages(pages):
    """Build a mock service returning pages as (comments_list, next_page_token_or_None) tuples."""
    mock_service = Mock()
    responses = []
    for comments, next_token in pages:
        resp = {"comments": comments}
        if next_token is not None:
            resp["nextPageToken"] = next_token
        responses.append(resp)

    execute_mock = Mock(side_effect=responses)
    mock_service.comments.return_value.list.return_value.execute = execute_mock
    return mock_service


@pytest.mark.asyncio
async def test_read_comments_includes_quoted_text():
    """Verify that quotedFileContent.value is surfaced in the output."""
    comments = [
        {
            **_make_comment("c1", "Needs a citation here."),
            "quotedFileContent": {
                "mimeType": "text/html",
                "value": "the specific text that was highlighted",
            },
        },
        _make_comment("c2", "General comment without anchor.", "Bob"),
    ]
    mock_service = _mock_service_pages([(comments, None)])

    result = await _read_comments_impl(mock_service, "document", "doc123")

    assert "Quoted text: the specific text that was highlighted" in result
    assert "Needs a citation here." in result

    parts = result.split("\\n")
    bob_section_started = False
    for part in parts:
        if "Author: Bob" in part:
            bob_section_started = True
        if bob_section_started and "Quoted text:" in part:
            pytest.fail(
                "Comment without quotedFileContent should not show 'Quoted text'"
            )
        if bob_section_started and "Content: General comment" in part:
            break


@pytest.mark.asyncio
async def test_read_comments_empty():
    """Verify empty comments returns appropriate message."""
    mock_service = _mock_service_pages([([], None)])

    result = await _read_comments_impl(mock_service, "document", "doc123")
    assert "No comments found" in result


@pytest.mark.asyncio
async def test_read_comments_with_replies():
    """Verify replies are included in output."""
    comment = {
        **_make_comment("c1", "Question?"),
        "quotedFileContent": {"value": "some text"},
        "replies": [
            {
                "id": "r1",
                "content": "Answer.",
                "author": {"displayName": "Bob"},
                "createdTime": "2025-01-15T11:00:00Z",
                "modifiedTime": "2025-01-15T11:00:00Z",
            }
        ],
    }
    mock_service = _mock_service_pages([([comment], None)])

    result = await _read_comments_impl(mock_service, "document", "doc123")
    assert "Question?" in result
    assert "Answer." in result
    assert "Bob" in result
    assert "Quoted text: some text" in result


@pytest.mark.asyncio
async def test_create_comment():
    """Verify creating a document-level comment via the Drive API."""
    mock_service = Mock()
    mock_service.comments.return_value.create.return_value.execute = Mock(
        return_value={
            "id": "c1",
            "content": "A general comment",
            "author": {"displayName": "Alice"},
            "createdTime": "2025-01-15T10:00:00Z",
        }
    )

    result = await _create_comment_impl(
        mock_service, "document", "doc123", "A general comment"
    )

    call_kwargs = mock_service.comments.return_value.create.call_args.kwargs
    body = call_kwargs["body"]
    assert body == {"content": "A general comment"}
    assert "Comment created successfully" in result


class TestReadCommentsImplPagination:
    """Pagination behavior in _read_comments_impl."""

    @pytest.mark.asyncio
    async def test_single_page_returns_all(self):
        """When all comments fit in one page, no extra API calls."""
        comments = [_make_comment(f"c{i}") for i in range(5)]
        mock_service = _mock_service_pages([(comments, None)])

        result = await _read_comments_impl(mock_service, "document", "doc1")

        assert "Found 5 comments" in result
        mock_service.comments.return_value.list.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_page_fetches_all(self):
        """Multiple pages are fetched and combined."""
        page1 = [_make_comment(f"c{i}") for i in range(3)]
        page2 = [_make_comment(f"c{i}") for i in range(3, 6)]
        mock_service = _mock_service_pages(
            [
                (page1, "token2"),
                (page2, None),
            ]
        )

        result = await _read_comments_impl(
            mock_service, "document", "doc1", max_comments=10
        )

        assert "Found 6 comments" in result
        assert mock_service.comments.return_value.list.call_count == 2

    @pytest.mark.asyncio
    async def test_max_comments_stops_early(self):
        """Pagination stops when max_comments is reached."""
        page1 = [_make_comment(f"c{i}") for i in range(3)]
        page2 = [_make_comment(f"c{i}") for i in range(3, 6)]
        mock_service = _mock_service_pages(
            [
                (page1, "token2"),
                (page2, "token3"),
            ]
        )

        result = await _read_comments_impl(
            mock_service, "document", "doc1", max_comments=5
        )

        assert "Found 5 comments" in result
        calls = mock_service.comments.return_value.list.call_args_list
        assert calls[0].kwargs["pageSize"] == 5  # min(100, 5)
        assert calls[1].kwargs["pageSize"] == 2  # min(100, 5-3)


class TestCommentToolsFactory:
    """All three tool variants pass max_comments through to _read_comments_impl."""

    @pytest.mark.asyncio
    async def test_document_passes_max_comments_to_api(self):
        """Document list_comments with max_comments sets correct pageSize."""
        comments = [_make_comment("c1")]
        mock_service = _mock_service_pages([(comments, None)])

        await _read_comments_impl(mock_service, "document", "doc1", max_comments=50)
        call_kwargs = mock_service.comments.return_value.list.call_args.kwargs
        assert call_kwargs["pageSize"] == 50

    @pytest.mark.asyncio
    async def test_all_variants_accept_max_comments(self):
        """Verify all three list_comments variants have max_comments param."""
        for app, param in [
            ("document", "document_id"),
            ("spreadsheet", "spreadsheet_id"),
            ("presentation", "presentation_id"),
        ]:
            tools = create_comment_tools(app, param)
            sig = inspect.signature(tools["list_comments"])
            assert "max_comments" in sig.parameters, (
                f"{app} list_comments missing max_comments parameter"
            )
            p = sig.parameters["max_comments"]
            assert p.default is None, f"{app} max_comments default should be None"


class TestCommentsEnvVar:
    """Environment variable resolution for default max_comments."""

    @pytest.mark.asyncio
    async def test_env_var_sets_default(self):
        """WORKSPACE_MCP_COMMENTS_MAX env var is used when max_comments not provided."""
        comments = [_make_comment(f"c{i}") for i in range(3)]
        mock_service = _mock_service_pages([(comments, None)])

        with patch.dict(os.environ, {"WORKSPACE_MCP_COMMENTS_MAX": "50"}):
            await _read_comments_impl(mock_service, "document", "doc1")

        call_kwargs = mock_service.comments.return_value.list.call_args.kwargs
        assert call_kwargs["pageSize"] == 50

    @pytest.mark.asyncio
    async def test_per_call_overrides_env_var(self):
        """Per-call max_comments takes precedence over env var."""
        comments = [_make_comment(f"c{i}") for i in range(3)]
        mock_service = _mock_service_pages([(comments, None)])

        with patch.dict(os.environ, {"WORKSPACE_MCP_COMMENTS_MAX": "50"}):
            await _read_comments_impl(mock_service, "document", "doc1", max_comments=25)

        call_kwargs = mock_service.comments.return_value.list.call_args.kwargs
        assert call_kwargs["pageSize"] == 25

    @pytest.mark.asyncio
    async def test_missing_env_var_falls_back_to_100(self):
        """Without env var, default is 100."""
        comments = [_make_comment(f"c{i}") for i in range(3)]
        mock_service = _mock_service_pages([(comments, None)])

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("WORKSPACE_MCP_COMMENTS_MAX", None)
            await _read_comments_impl(mock_service, "document", "doc1")

        call_kwargs = mock_service.comments.return_value.list.call_args.kwargs
        assert call_kwargs["pageSize"] == 100


class TestCommentsEdgeCases:
    """Edge cases for comment pagination."""

    @pytest.mark.asyncio
    async def test_max_comments_zero_returns_empty(self):
        """max_comments=0 returns the no-comments message."""
        mock_service = Mock()
        result = await _read_comments_impl(
            mock_service, "document", "doc1", max_comments=0
        )
        assert "No comments found" in result
        mock_service.comments.assert_not_called()

    @pytest.mark.asyncio
    async def test_negative_max_comments_falls_back_to_default(self):
        """Negative max_comments falls back to the default (100)."""
        comments = [_make_comment(f"c{i}") for i in range(3)]
        mock_service = _mock_service_pages([(comments, None)])

        await _read_comments_impl(mock_service, "document", "doc1", max_comments=-5)
        call_kwargs = mock_service.comments.return_value.list.call_args.kwargs
        assert call_kwargs["pageSize"] == 100

    @pytest.mark.asyncio
    async def test_zero_comments_returns_empty_message(self):
        """Document with no comments returns appropriate message."""
        mock_service = _mock_service_pages([([], None)])

        result = await _read_comments_impl(mock_service, "document", "doc1")
        assert "No comments found" in result

    @pytest.mark.asyncio
    async def test_mid_pagination_api_error_reraises(self):
        """API error during pagination is re-raised, partial results discarded."""
        mock_service = Mock()
        page1_response = {
            "comments": [_make_comment("c1")],
            "nextPageToken": "token2",
        }
        mock_service.comments.return_value.list.return_value.execute = Mock(
            side_effect=[page1_response, Exception("API quota exceeded")]
        )

        with pytest.raises(Exception, match="API quota exceeded"):
            await _read_comments_impl(mock_service, "document", "doc1", max_comments=10)

    @pytest.mark.asyncio
    async def test_invalid_env_var_falls_back_to_default(self):
        """Non-numeric env var falls back to 100."""
        comments = [_make_comment(f"c{i}") for i in range(3)]
        mock_service = _mock_service_pages([(comments, None)])

        with patch.dict(os.environ, {"WORKSPACE_MCP_COMMENTS_MAX": "not_a_number"}):
            await _read_comments_impl(mock_service, "document", "doc1")

        call_kwargs = mock_service.comments.return_value.list.call_args.kwargs
        assert call_kwargs["pageSize"] == 100
