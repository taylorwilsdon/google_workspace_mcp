"""Google Docs Developer Preview helpers for comments and suggestions."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote, urlencode

from googleapiclient.errors import HttpError


async def execute_preview_rest_request(
    service: Any,
    uri: str,
    *,
    method: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute an authorized Docs Developer Preview REST request.

    The stable Docs discovery document does not currently expose the preview
    fields, so preview calls must bypass generated-client schema validation.
    The service's HTTP transport supplies the OAuth credentials.
    """
    transport = getattr(service, "_http", None)
    request = getattr(transport, "request", None)
    if not callable(request):
        raise ValueError(
            "Google Docs Developer Preview requires an authorized service "
            "with a raw HTTP transport."
        )

    headers = {"Accept": "application/json"}
    encoded_body = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        encoded_body = json.dumps(body).encode("utf-8")

    response, content = await asyncio.to_thread(
        request,
        uri=uri,
        method=method,
        body=encoded_body,
        headers=headers,
    )
    status = int(getattr(response, "status", 0) or response.get("status", 0))
    if status >= 400:
        raise HttpError(response, content, uri=uri)
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return json.loads(content) if content else {}


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
        if suggestion_id:
            raise ValueError(
                "suggestion_id must be resolved to a range before building "
                "an anchored comment request."
            )
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


def resolve_suggestion_comment_anchor(
    document: dict[str, Any], suggestion_id: str
) -> dict[str, Any]:
    """Resolve one open suggestion to a safe, contiguous comment range.

    Replacement suggestions expose both deleted and inserted text runs. Comments
    should follow the proposed text, so inserted runs take precedence. Deletion-
    only and style-only suggestions fall back to their affected ranges.
    """
    suggestion = next(
        (
            item
            for item in document.get("suggestions", [])
            if item.get("suggestionId") == suggestion_id
        ),
        None,
    )
    if suggestion is None:
        raise ValueError(f"Suggestion '{suggestion_id}' was not found.")
    if suggestion.get("status") not in {None, "OPEN"}:
        raise ValueError(
            f"Suggestion '{suggestion_id}' is not open "
            f"(status={suggestion.get('status')})."
        )

    inserted: set[tuple[str | None, str | None, int, int]] = set()
    affected: set[tuple[str | None, str | None, int, int]] = set()

    def collect(
        value: Any,
        *,
        tab_id: str | None,
        segment_id: str | None = None,
        inherited_start: int | None = None,
        inherited_end: int | None = None,
    ) -> None:
        if isinstance(value, list):
            for item in value:
                collect(
                    item,
                    tab_id=tab_id,
                    segment_id=segment_id,
                    inherited_start=inherited_start,
                    inherited_end=inherited_end,
                )
            return
        if not isinstance(value, dict):
            return

        start = value.get("startIndex", inherited_start)
        end = value.get("endIndex", inherited_end)
        candidate = None
        if isinstance(start, int) and isinstance(end, int) and end > start:
            candidate = (tab_id, segment_id, start, end)

        if candidate and suggestion_id in value.get("suggestedInsertionIds", []):
            inserted.add(candidate)
        if candidate and suggestion_id in value.get("suggestedDeletionIds", []):
            affected.add(candidate)

        for key, child in value.items():
            if key.startswith("suggested") and key.endswith("Changes"):
                if candidate and isinstance(child, dict) and suggestion_id in child:
                    affected.add(candidate)
                continue
            if key in {"suggestions", "comments", "commentAnchors"}:
                continue
            collect(
                child,
                tab_id=tab_id,
                segment_id=segment_id,
                inherited_start=start,
                inherited_end=end,
            )

    def collect_tabs(tabs: list[dict[str, Any]]) -> None:
        for tab in tabs:
            tab_id = tab.get("tabProperties", {}).get("tabId")
            document_tab = tab.get("documentTab", {})
            collect(document_tab.get("body", {}), tab_id=tab_id)
            for collection_name in ("headers", "footers", "footnotes"):
                for segment_id, segment in document_tab.get(
                    collection_name, {}
                ).items():
                    collect(segment, tab_id=tab_id, segment_id=segment_id)
            collect_tabs(tab.get("childTabs", []))

    collect_tabs(document.get("tabs", []))
    if not document.get("tabs"):
        collect(document.get("body", {}), tab_id=None)

    candidates = inserted or affected
    if not candidates:
        raise ValueError(
            f"Could not resolve a document range for suggestion '{suggestion_id}'."
        )

    ordered = sorted(candidates, key=lambda item: (str(item[0]), str(item[1]), item[2]))
    tab_id, segment_id, start, end = ordered[0]
    for next_tab_id, next_segment_id, next_start, next_end in ordered[1:]:
        if next_tab_id != tab_id or next_segment_id != segment_id or next_start > end:
            raise ValueError(
                f"Suggestion '{suggestion_id}' spans multiple non-contiguous "
                "ranges; provide an explicit verified range instead."
            )
        end = max(end, next_end)

    anchor: dict[str, Any] = {"start_index": start, "end_index": end}
    if tab_id:
        anchor["tab_id"] = tab_id
    if segment_id:
        anchor["segment_id"] = segment_id
    anchor["source"] = "suggested_insertion" if inserted else "suggested_change"
    return anchor


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

    uri = (
        "https://docs.googleapis.com/v1/documents/"
        f"{quote(document_id, safe='')}:batchUpdate"
    )
    return await execute_preview_rest_request(
        service,
        uri,
        method="POST",
        body=body,
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
    """Fetch preview review fields through the authorized raw REST transport."""
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
    return await execute_preview_rest_request(
        service,
        uri,
        method="GET",
    )
