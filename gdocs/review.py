"""Google Docs Developer Preview helpers for comments and suggestions."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote, urlencode

from googleapiclient.errors import HttpError


def _thread_id_fields(
    comment_id: str | None, suggestion_id: str | None
) -> dict[str, str]:
    if bool(comment_id) == bool(suggestion_id):
        raise ValueError("Provide exactly one of comment_id or suggestion_id.")
    if comment_id:
        return {"commentId": comment_id}
    return {"suggestionId": suggestion_id}  # type: ignore[dict-item]


def _validate_content(content: str | None, *, required: bool = True) -> None:
    if required and not content:
        raise ValueError("content is required and cannot be empty.")
    if content and len(content) > 2048:
        raise ValueError("content cannot exceed 2048 characters.")


def build_review_thread_request(
    action: str,
    *,
    content: str | None = None,
    comment_id: str | None = None,
    suggestion_id: str | None = None,
    post_id: str | None = None,
    start_index: int | None = None,
    end_index: int | None = None,
    tab_id: str | None = None,
    segment_id: str | None = None,
    assignee_email: str | None = None,
) -> dict[str, Any]:
    """Build one Docs-native comment or suggestion-thread request."""
    if action == "create_comment":
        _validate_content(content)
        if start_index is None or end_index is None:
            raise ValueError(
                "start_index and end_index are required for an anchored comment."
            )
        if start_index < 0 or end_index <= start_index:
            raise ValueError("Comment range must satisfy 0 <= start_index < end_index.")
        range_value: dict[str, Any] = {
            "startIndex": start_index,
            "endIndex": end_index,
        }
        if tab_id:
            range_value["tabId"] = tab_id
        if segment_id:
            range_value["segmentId"] = segment_id
        request: dict[str, Any] = {
            "content": content,
            "range": range_value,
        }
        if assignee_email:
            request["assigneeEmailAddress"] = assignee_email
        return {"insertComment": request}

    if action in {"reply", "resolve", "reopen"}:
        thread_id = _thread_id_fields(comment_id, suggestion_id)
        if action in {"resolve", "reopen"} and suggestion_id:
            raise ValueError(f"{action} is only supported for comment threads.")
        post: dict[str, str] = {}
        if action == "reply":
            _validate_content(content)
            post["content"] = content  # type: ignore[assignment]
            if assignee_email:
                post["assigneeEmail"] = assignee_email
        else:
            _validate_content(content, required=False)
            if content:
                post["content"] = content
            post["commentAction"] = action.upper()
        return {"addCommentReply": {**thread_id, "post": post}}

    if action == "update_post":
        thread_id = _thread_id_fields(comment_id, suggestion_id)
        _validate_content(content)
        if not post_id:
            raise ValueError("post_id is required for update_post.")
        return {
            "updateCommentPost": {
                **thread_id,
                "postId": post_id,
                "content": content,
            }
        }

    if action == "delete_comment":
        if not comment_id:
            raise ValueError("comment_id is required for delete_comment.")
        if suggestion_id:
            raise ValueError("suggestion_id is not valid for delete_comment.")
        return {"deleteComment": {"commentId": comment_id}}

    if action == "delete_reply":
        thread_id = _thread_id_fields(comment_id, suggestion_id)
        if not post_id:
            raise ValueError("post_id is required for delete_reply.")
        return {"deleteCommentReply": {**thread_id, "postId": post_id}}

    raise ValueError(f"Unsupported review-thread action: {action}.")


def build_suggestion_request(action: str, suggestion_id: str) -> dict[str, Any]:
    """Build one suggestion lifecycle request."""
    request_names = {
        "accept": "acceptSuggestion",
        "reject": "rejectSuggestion",
        "delete": "deleteSuggestion",
    }
    request_name = request_names.get(action)
    if not request_name:
        raise ValueError(f"Unsupported suggestion action: {action}.")
    if not suggestion_id:
        raise ValueError("suggestion_id is required.")
    return {request_name: {"suggestionId": suggestion_id}}


async def execute_review_request(
    service: Any,
    document_id: str,
    request: dict[str, Any],
    *,
    required_revision_id: str | None = None,
    target_revision_id: str | None = None,
) -> dict[str, Any]:
    """Execute one review request with optional revision protection."""
    if required_revision_id and target_revision_id:
        raise ValueError(
            "Provide only one of required_revision_id or target_revision_id."
        )
    body: dict[str, Any] = {"requests": [request]}
    if required_revision_id:
        body["writeControl"] = {"requiredRevisionId": required_revision_id}
    elif target_revision_id:
        body["writeControl"] = {"targetRevisionId": target_revision_id}

    return await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body=body)
        .execute
    )


def extract_tab_comment_anchors(tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return tab metadata and comment anchors, preserving nested tab structure."""
    extracted = []
    for tab in tabs:
        properties = tab.get("tabProperties", {})
        document_tab = tab.get("documentTab", {})
        item: dict[str, Any] = {
            "tab_id": properties.get("tabId"),
            "title": properties.get("title"),
            "comment_anchors": document_tab.get("commentAnchors", {}),
        }
        children = tab.get("childTabs", [])
        if children:
            item["child_tabs"] = extract_tab_comment_anchors(children)
        extracted.append(item)
    return extracted


async def fetch_review_document(service: Any, document_id: str) -> dict[str, Any]:
    """Fetch preview review fields even when the bundled discovery doc lags.

    Google publishes preview REST fields before they necessarily appear in the
    discovery document bundled by google-api-python-client. Use the generated
    client when it knows the parameter; otherwise make the same authorized
    request through the service's HTTP transport.
    """
    root_description = getattr(service, "_rootDesc", None)
    if isinstance(root_description, dict):
        get_parameters = (
            root_description.get("resources", {})
            .get("documents", {})
            .get("methods", {})
            .get("get", {})
            .get("parameters", {})
        )
        if "commentsViewMode" not in get_parameters:
            query = urlencode(
                {
                    "includeTabsContent": "true",
                    "suggestionsViewMode": "SUGGESTIONS_INLINE",
                    "commentsViewMode": "COMMENTS_VIEW_MODE_INCLUDED",
                }
            )
            uri = (
                "https://docs.googleapis.com/v1/documents/"
                f"{quote(document_id, safe='')}?{query}"
            )
            response, content = await asyncio.to_thread(
                service._http.request, uri=uri, method="GET"
            )
            status = int(getattr(response, "status", 0) or response.get("status", 0))
            if status >= 400:
                raise HttpError(response, content, uri=uri)
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return json.loads(content)

    return await asyncio.to_thread(
        service.documents()
        .get(
            documentId=document_id,
            includeTabsContent=True,
            suggestionsViewMode="SUGGESTIONS_INLINE",
            commentsViewMode="COMMENTS_VIEW_MODE_INCLUDED",
        )
        .execute
    )
