"""
Comments anchored to text in a Google Doc.

The Drive API can only create document-level comments on a Google Doc: the
editor ignores any anchor it is given. The Docs API's InsertCommentRequest
(generally available since September 2026) ties a comment thread to an index
range instead, so a comment can sit in the margin next to the text it is about,
exactly like one made in the editor. The range is resolved from anchor text at
execution time, with the same rules as the semantic anchors used for insertions.
"""

import asyncio
import logging
from typing import Any, Optional

from core.utils import UserInputError
from gdocs.docs_anchors import AnchorResolutionError, resolve_anchor_range
from gdocs.managers.batch_operation_manager import (
    _find_document_tab,
    _first_document_tab,
)

logger = logging.getLogger(__name__)

# InsertCommentRequest.content is capped by the Docs API.
MAX_COMMENT_UTF8_UNITS = 2048

# BatchUpdateDocumentResponse.commentUpdateState when no comment was saved.
COMMENT_UPDATE_FAILED = "ALL_FAILED_UNKNOWN_REASON"


def _tab_body(doc: dict[str, Any], tab_id: Optional[str]) -> dict[str, Any]:
    """Body of the requested tab, or of the first tab when none is given."""
    tabs = doc.get("tabs", [])
    if tab_id:
        tab = _find_document_tab(tabs, tab_id)
        if tab is None:
            raise UserInputError(f"Tab '{tab_id}' not found in the document.")
    elif tabs:
        tab = _first_document_tab(tabs) or {}
    else:
        tab = doc
    return tab.get("body", {})


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
    """Create a comment anchored to anchor_text via the Docs API.

    Returns the created thread's comment ID, the quoted text and the resolved
    range. anchor_text must fit within one paragraph and occur exactly once in
    the tab; the search covers table cells too.

    When the read response supplies a revision ID, reject stale writes instead
    of retrying with old indices. Google omits revision IDs for users without
    edit access, so those calls retain the API's unguarded behavior.
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
    try:
        start_index, end_index = resolve_anchor_range(
            _tab_body(doc, tab_id), anchor_text
        )
    except AnchorResolutionError as e:
        raise UserInputError(str(e)) from e

    request = build_insert_comment_request(
        start_index, end_index, comment_content, tab_id
    )
    logger.info(
        f"[create_document_comment] Anchoring comment to {start_index}-{end_index} "
        f"in document {document_id}"
    )
    body: dict[str, Any] = {"requests": [request]}
    if doc.get("revisionId"):
        body["writeControl"] = {"requiredRevisionId": doc["revisionId"]}
    response = await asyncio.to_thread(
        docs_service.documents().batchUpdate(documentId=document_id, body=body).execute
    )

    # The batch response carries a comment-specific status; a reply alone is not
    # confirmation that the thread was saved.
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
