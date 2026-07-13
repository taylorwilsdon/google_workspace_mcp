"""Tests for Docs-native Developer Preview review operations."""

import json
from unittest.mock import Mock

import pytest

from gdocs import docs_tools


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _docs_service(batch_result=None, document_result=None):
    service = Mock()
    batch_payload = batch_result or {
        "replies": [{}],
        "commentUpdateState": "ALL_SAVED",
    }
    document_payload = document_result or {}

    def request(*, method, **_kwargs):
        payload = document_payload if method == "GET" else batch_payload
        return Mock(status=200), json.dumps(payload).encode("utf-8")

    service._http.request.side_effect = request
    return service


def _last_rest_body(service):
    return json.loads(service._http.request.call_args.kwargs["body"])


@pytest.mark.asyncio
async def test_create_anchored_comment_builds_docs_request():
    service = _docs_service()

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Why should this clause change?",
        start_index=12,
        end_index=28,
        tab_id="tab-1",
    )

    assert "ALL_SAVED" in result
    body = _last_rest_body(service)
    assert body == {
        "requests": [
            {
                "insertComment": {
                    "content": "Why should this clause change?",
                    "range": {
                        "startIndex": 12,
                        "endIndex": 28,
                        "tabId": "tab-1",
                    },
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_reply_to_suggestion_thread_builds_docs_request():
    service = _docs_service()

    await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="reply",
        suggestion_id="suggestion-1",
        content="Agreed with this wording.",
    )

    body = _last_rest_body(service)
    assert body["requests"] == [
        {
            "addCommentReply": {
                "suggestionId": "suggestion-1",
                "post": {"content": "Agreed with this wording."},
            }
        }
    ]


@pytest.mark.asyncio
async def test_resolve_comment_creates_action_reply():
    service = _docs_service()

    await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="resolve",
        comment_id="comment-1",
    )

    body = _last_rest_body(service)
    assert body["requests"] == [
        {
            "addCommentReply": {
                "commentId": "comment-1",
                "post": {"commentAction": "RESOLVE"},
            }
        }
    ]


@pytest.mark.asyncio
async def test_update_and_delete_reply_require_exact_thread_and_post():
    service = _docs_service()

    await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="update_post",
        comment_id="comment-1",
        post_id="post-1",
        content="Updated rationale",
    )
    update_request = _last_rest_body(service)["requests"][0]
    assert update_request == {
        "updateCommentPost": {
            "commentId": "comment-1",
            "postId": "post-1",
            "content": "Updated rationale",
        }
    }

    await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="delete_reply",
        suggestion_id="suggestion-1",
        post_id="post-1",
    )
    delete_request = _last_rest_body(service)["requests"][0]
    assert delete_request == {
        "deleteCommentReply": {
            "suggestionId": "suggestion-1",
            "postId": "post-1",
        }
    }


@pytest.mark.asyncio
async def test_review_thread_rejects_ambiguous_thread_id():
    service = _docs_service()

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="reply",
        comment_id="comment-1",
        suggestion_id="suggestion-1",
        content="Ambiguous",
    )

    assert result.startswith("Error:")
    assert "exactly one" in result
    service._http.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "request_name"),
    [
        ("accept", "acceptSuggestion"),
        ("reject", "rejectSuggestion"),
        ("delete", "deleteSuggestion"),
    ],
)
async def test_manage_doc_suggestion_actions(action, request_name):
    service = _docs_service(
        batch_result={
            "suggestionResponses": [{f"{action}edSuggestionIds": ["suggestion-1"]}]
        }
    )

    result = await _unwrap(docs_tools.manage_doc_suggestion)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action=action,
        suggestion_id="suggestion-1",
        required_revision_id="revision-1",
    )

    assert "suggestion-1" in result
    body = _last_rest_body(service)
    assert body == {
        "requests": [{request_name: {"suggestionId": "suggestion-1"}}],
        "writeControl": {"requiredRevisionId": "revision-1"},
    }


@pytest.mark.asyncio
async def test_get_doc_review_threads_includes_comments_suggestions_and_anchors():
    service = _docs_service(
        document_result={
            "documentId": "document-id-1234567890",
            "title": "Review document",
            "revisionId": "revision-1",
            "comments": [{"commentId": "comment-1", "status": "OPEN"}],
            "suggestions": [{"suggestionId": "suggestion-1", "status": "OPEN"}],
            "tabs": [
                {
                    "tabProperties": {"tabId": "tab-1", "title": "Main"},
                    "documentTab": {
                        "commentAnchors": {
                            "anchor-1": {
                                "range": {
                                    "startIndex": 12,
                                    "endIndex": 28,
                                    "tabId": "tab-1",
                                }
                            }
                        }
                    },
                }
            ],
        }
    )

    result = await _unwrap(docs_tools.get_doc_review_threads)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
    )

    parsed = json.loads(result)
    assert parsed["revision_id"] == "revision-1"
    assert parsed["comments"][0]["commentId"] == "comment-1"
    assert parsed["suggestions"][0]["suggestionId"] == "suggestion-1"
    assert parsed["tabs"][0]["comment_anchors"]["anchor-1"]["range"]["startIndex"] == 12
    call = service._http.request.call_args.kwargs
    assert "commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED" in call["uri"]
    assert "suggestionsViewMode=SUGGESTIONS_INLINE" in call["uri"]
    assert "includeTabsContent=true" in call["uri"]


@pytest.mark.asyncio
async def test_get_review_threads_uses_authorized_http_when_discovery_lags():
    service = _docs_service()
    service._rootDesc = {
        "resources": {
            "documents": {
                "methods": {"get": {"parameters": {"suggestionsViewMode": {}}}}
            }
        }
    }
    response = Mock(status=200)
    service._http.request.return_value = (
        response,
        b'{"documentId":"document-id-1234567890","comments":[],"suggestions":[]}',
    )

    result = await _unwrap(docs_tools.get_doc_review_threads)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
    )

    assert json.loads(result)["document_id"] == "document-id-1234567890"
    request = service._http.request.call_args
    assert request.kwargs["method"] == "GET"
    assert "commentsViewMode=COMMENTS_VIEW_MODE_INCLUDED" in request.kwargs["uri"]
    service.documents.return_value.get.assert_not_called()
