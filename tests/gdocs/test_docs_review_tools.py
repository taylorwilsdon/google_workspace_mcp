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
async def test_create_comment_resolves_inserted_range_from_suggestion_id():
    service = _docs_service(
        document_result={
            "documentId": "document-id-1234567890",
            "revisionId": "revision-1",
            "suggestions": [{"suggestionId": "suggestion-1", "status": "OPEN"}],
            "tabs": [
                {
                    "tabProperties": {"tabId": "tab-1", "title": "Main"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "paragraph": {
                                        "elements": [
                                            {
                                                "startIndex": 12,
                                                "endIndex": 20,
                                                "textRun": {
                                                    "content": "old text",
                                                    "suggestedDeletionIds": [
                                                        "suggestion-1"
                                                    ],
                                                },
                                            },
                                            {
                                                "startIndex": 20,
                                                "endIndex": 36,
                                                "textRun": {
                                                    "content": "replacement text",
                                                    "suggestedInsertionIds": [
                                                        "suggestion-1"
                                                    ],
                                                },
                                            },
                                        ]
                                    }
                                }
                            ]
                        }
                    },
                }
            ],
        }
    )

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Why should this clause change?",
        suggestion_id="suggestion-1",
    )

    assert "ALL_SAVED" in result
    body = _last_rest_body(service)
    assert body == {
        "requests": [
            {
                "insertComment": {
                    "content": "Why should this clause change?",
                    "range": {
                        "startIndex": 20,
                        "endIndex": 36,
                        "tabId": "tab-1",
                    },
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_create_comment_rejects_suggestion_id_with_explicit_range():
    service = _docs_service()

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Ambiguous anchor",
        suggestion_id="suggestion-1",
        start_index=12,
        end_index=28,
        tab_id="tab-1",
    )

    assert result.startswith("Error:")
    assert "either suggestion_id or an explicit range" in result
    service._http.request.assert_not_called()


@pytest.mark.asyncio
async def test_create_comment_fails_closed_for_discontiguous_suggestion_ranges():
    service = _docs_service(
        document_result={
            "documentId": "document-id-1234567890",
            "revisionId": "revision-1",
            "suggestions": [{"suggestionId": "suggestion-1", "status": "OPEN"}],
            "tabs": [
                {
                    "tabProperties": {"tabId": "tab-1", "title": "Main"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "paragraph": {
                                        "elements": [
                                            {
                                                "startIndex": 12,
                                                "endIndex": 20,
                                                "textRun": {
                                                    "content": "first",
                                                    "suggestedInsertionIds": [
                                                        "suggestion-1"
                                                    ],
                                                },
                                            },
                                            {
                                                "startIndex": 30,
                                                "endIndex": 40,
                                                "textRun": {
                                                    "content": "second",
                                                    "suggestedInsertionIds": [
                                                        "suggestion-1"
                                                    ],
                                                },
                                            },
                                        ]
                                    }
                                }
                            ]
                        }
                    },
                }
            ],
        }
    )

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Do not guess",
        suggestion_id="suggestion-1",
    )

    assert result.startswith("Error:")
    assert "multiple non-contiguous ranges" in result
    assert [call.kwargs["method"] for call in service._http.request.call_args_list] == [
        "GET"
    ]


@pytest.mark.asyncio
async def test_create_comment_falls_back_to_deletion_range():
    service = _docs_service(
        document_result={
            "suggestions": [{"suggestionId": "suggestion-1", "status": "OPEN"}],
            "body": {
                "content": [
                    {
                        "startIndex": 8,
                        "endIndex": 18,
                        "paragraph": {
                            "elements": [
                                {
                                    "textRun": {
                                        "content": "remove me",
                                        "suggestedDeletionIds": ["suggestion-1"],
                                    }
                                }
                            ]
                        },
                    }
                ]
            },
        }
    )

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Why remove this clause?",
        suggestion_id="suggestion-1",
    )

    payload = json.loads(result)
    assert payload["resolved_anchor"] == {
        "start_index": 8,
        "end_index": 18,
        "source": "suggested_change",
    }
    assert _last_rest_body(service)["requests"][0]["insertComment"]["range"] == {
        "startIndex": 8,
        "endIndex": 18,
    }


@pytest.mark.asyncio
async def test_create_comment_falls_back_to_style_change_range():
    service = _docs_service(
        document_result={
            "suggestions": [{"suggestionId": "suggestion-1", "status": "OPEN"}],
            "tabs": [
                {
                    "tabProperties": {"tabId": "tab-1"},
                    "documentTab": {
                        "body": {
                            "content": [
                                {
                                    "startIndex": 4,
                                    "endIndex": 14,
                                    "paragraph": {
                                        "elements": [
                                            {
                                                "textRun": {
                                                    "content": "bold text",
                                                    "suggestedTextStyleChanges": {
                                                        "suggestion-1": {
                                                            "textStyle": {"bold": True}
                                                        }
                                                    },
                                                }
                                            }
                                        ]
                                    },
                                }
                            ]
                        }
                    },
                }
            ],
        }
    )

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Review this emphasis.",
        suggestion_id="suggestion-1",
    )

    payload = json.loads(result)
    assert payload["resolved_anchor"] == {
        "start_index": 4,
        "end_index": 14,
        "tab_id": "tab-1",
        "source": "suggested_change",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suggestions", "expected_error"),
    [
        ([], "was not found"),
        (
            [{"suggestionId": "suggestion-1", "status": "ACCEPTED"}],
            "is not open",
        ),
    ],
)
async def test_create_comment_rejects_missing_or_closed_suggestion(
    suggestions, expected_error
):
    service = _docs_service(document_result={"suggestions": suggestions})

    result = await _unwrap(docs_tools.manage_doc_review_thread)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="create_comment",
        content="Unsafe anchor",
        suggestion_id="suggestion-1",
    )

    assert result.startswith("Error:")
    assert expected_error in result
    assert [call.kwargs["method"] for call in service._http.request.call_args_list] == [
        "GET"
    ]


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
async def test_manage_doc_suggestion_refreshes_stale_revision_once():
    service = Mock()
    stale_response = Mock(status=400, reason="Bad Request")
    latest_document = {"revisionId": "revision-2"}
    success = {"suggestionResponses": [{"acceptedSuggestionIds": ["suggestion-1"]}]}
    service._http.request.side_effect = [
        (stale_response, b'{"error":{"message":"Revision mismatch"}}'),
        (Mock(status=200), json.dumps(latest_document).encode("utf-8")),
        (Mock(status=200), json.dumps(success).encode("utf-8")),
    ]

    result = await _unwrap(docs_tools.manage_doc_suggestion)(
        service=service,
        user_google_email="user@example.com",
        document_id="document-id-1234567890",
        action="accept",
        suggestion_id="suggestion-1",
        required_revision_id="revision-1",
    )

    assert '"success": true' in result
    assert service._http.request.call_count == 3
    retry_body = json.loads(service._http.request.call_args_list[-1].kwargs["body"])
    assert retry_body["writeControl"] == {"requiredRevisionId": "revision-2"}


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
