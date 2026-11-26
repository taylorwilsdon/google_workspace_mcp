"""Comments anchored to text in a Google Doc, created through the Docs API."""

import asyncio
from typing import Any, Optional

from core.utils import UserInputError
from gdocs.docs_anchors import AnchorResolutionError, resolve_anchor_range
from gdocs.managers.batch_operation_manager import BatchOperationManager

# InsertCommentRequest.content is capped by the Docs API.
MAX_COMMENT_UTF8_UNITS = 2048

# BatchUpdateDocumentResponse.commentUpdateState when no comment was saved.
COMMENT_UPDATE_FAILED = "ALL_FAILED_UNKNOWN_REASON"


def build_insert_comment_request(
    start_index: int,
    end_index: int,
    comment_content: str,
    tab_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the batchUpdate request that creates a comment thread on a range."""
    comment_range: dict[str, Any] = {"startIndex": start_index, "endIndex": end_index}
    if tab_id:
        comment_range["tabId"] = tab_id
    return {"insertComment": {"range": comment_range, "content": comment_content}}


async def insert_anchored_comment(
    docs_service,
    document_id: str,
    anchor_text: str,
    comment_content: str,
    tab_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create a comment anchored to anchor_text in a tab (the first by default).

    Returns the thread's comment_id, quoted_text, start_index and end_index.
    """
    if len(comment_content.encode("utf-8")) > MAX_COMMENT_UTF8_UNITS:
        raise UserInputError(
            f"comment_content exceeds the Docs API limit of {MAX_COMMENT_UTF8_UNITS} "
            "UTF-8 code units for an anchored comment; shorten it or split it."
        )

    doc = await asyncio.to_thread(
        docs_service.documents()
        .get(documentId=document_id, includeTabsContent=True)
        .execute
    )
    tab_body = BatchOperationManager._target_segment(doc, tab_id)
    if tab_body is None and tab_id:
        raise UserInputError(f"Tab '{tab_id}' not found in the document.")
    try:
        start_index, end_index = resolve_anchor_range(tab_body or {}, anchor_text)
    except AnchorResolutionError as e:
        raise UserInputError(str(e)) from e

    request = build_insert_comment_request(
        start_index, end_index, comment_content, tab_id
    )
    body: dict[str, Any] = {"requests": [request]}
    # Absent for users without edit access; their write goes unguarded.
    if doc.get("revisionId"):
        body["writeControl"] = {"requiredRevisionId": doc["revisionId"]}
    response = await asyncio.to_thread(
        docs_service.documents().batchUpdate(documentId=document_id, body=body).execute
    )

    if response.get("commentUpdateState") == COMMENT_UPDATE_FAILED:
        raise RuntimeError(
            "Docs API reported that the comment was not saved "
            f"({COMMENT_UPDATE_FAILED}); check list_document_comments before retrying."
        )
    replies = response.get("replies") or [{}]
    thread = replies[0].get("insertComment", {}).get("commentThread", {})
    comment_id = thread.get("commentId")
    if not comment_id:
        raise RuntimeError(
            "Docs API did not return a comment ID; check list_document_comments "
            "before retrying."
        )
    return {
        "comment_id": comment_id,
        "quoted_text": thread.get("plainTextQuote") or anchor_text,
        "start_index": start_index,
        "end_index": end_index,
    }
