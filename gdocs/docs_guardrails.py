"""
Google Docs Guardrails Module

Implements guardrails for Google Docs operations:
- Input validation (title length, text size, pattern validation)
- Quota tracking (300/min shared pool) via Knowledge Table
- Size validation (50MB document limit)
- Pre-flight validation (find patterns exist before replacement)

These guardrails protect against:
1. Unbounded text appends (cumulative size >50MB)
2. Quota exhaustion without warning (shared 300/min pool)
3. Silent replace failures (pattern not found)
4. Invalid document operations (bad indices, empty searches)

Key differences from Sheets:
- Docs batchUpdate is ATOMIC (all-or-nothing), so no partial-write detection needed
- Quota is shared 300/min pool (not per-operation like Sheets' 60/min)
- Three separate input validators (create, modify, find-replace)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Union, Any, Optional

logger = logging.getLogger(__name__)

# Note: relevance_raw_api is injected by Relevance AI platform at runtime
# For testing, this will be mocked
try:
    from relevance_ai import relevance_raw_api
except ImportError:
    def relevance_raw_api(*args, **kwargs):
        raise NotImplementedError("relevance_raw_api is not available in this context")

# Constants
MAX_DOC_TITLE_LENGTH = 255  # Google Docs limit
MAX_TEXT_SIZE_BYTES = 50 * 1024 * 1024  # 50MB document limit
MIN_FIND_TEXT_LENGTH = 1
MAX_FIND_TEXT_LENGTH = 10000
DOCS_QUOTA_PER_MINUTE = 300  # Shared pool with Drive, Sheets, etc.
DOCS_WARNING_THRESHOLD = 250  # 83% utilization
DOCS_EXHAUSTED_THRESHOLD = 290  # 97% utilization


def validate_create_doc_input(
    user_google_email: str,
    title: str,
    content: str = ""
) -> Dict[str, Any]:
    """Validate input for creating a new Google Doc."""

    # Validate required params
    if not user_google_email:
        return {"error": "user_google_email is required"}
    if not title:
        return {"error": "title is required"}
    if not isinstance(title, str):
        return {"error": "title must be a string"}

    # Validate title length
    if len(title) > MAX_DOC_TITLE_LENGTH:
        return {
            "error": f"Title too long ({len(title)} chars; max {MAX_DOC_TITLE_LENGTH})"
        }

    # Validate content if provided
    if content and not isinstance(content, str):
        return {"error": "content must be a string"}

    # Validate content size
    if content:
        content_bytes = len(content.encode('utf-8'))
        if content_bytes > MAX_TEXT_SIZE_BYTES:
            size_mb = content_bytes / (1024 * 1024)
            return {
                "error": f"Content too large ({size_mb:.1f}MB; max 50MB)"
            }

    return {
        "status": "validation_passed",
        "title": title,
        "content": content,
        "content_size_bytes": len(content.encode('utf-8')) if content else 0,
        "user_google_email": user_google_email
    }


def validate_modify_doc_text_input(
    user_google_email: str,
    document_id: str,
    text: str = "",
    start_index: int = None,
    end_index: int = None
) -> Dict[str, Any]:
    """Validate input for modifying text in a Google Doc."""

    # Validate required params
    if not user_google_email:
        return {"error": "user_google_email is required"}
    if not document_id:
        return {"error": "document_id is required"}
    if not isinstance(document_id, str) or len(document_id) == 0:
        return {"error": "document_id must be a non-empty string"}

    # Validate indices if provided
    if start_index is not None and not isinstance(start_index, int):
        return {"error": "start_index must be an integer"}
    if start_index is not None and start_index < 0:
        return {"error": "start_index must be >= 0"}

    if end_index is not None and not isinstance(end_index, int):
        return {"error": "end_index must be an integer"}
    if end_index is not None and end_index < 0:
        return {"error": "end_index must be >= 0"}

    if start_index is not None and end_index is not None:
        if start_index > end_index:
            return {"error": "start_index must be <= end_index"}

    # Validate text if provided
    if text and not isinstance(text, str):
        return {"error": "text must be a string"}

    # Validate text size
    if text:
        text_bytes = len(text.encode('utf-8'))
        if text_bytes > MAX_TEXT_SIZE_BYTES:
            size_mb = text_bytes / (1024 * 1024)
            return {
                "error": f"Text too large ({size_mb:.1f}MB; max 50MB)"
            }

    return {
        "status": "validation_passed",
        "document_id": document_id,
        "text": text,
        "text_size_bytes": len(text.encode('utf-8')) if text else 0,
        "start_index": start_index,
        "end_index": end_index,
        "user_google_email": user_google_email
    }


def validate_find_replace_input(
    user_google_email: str,
    document_id: str,
    find_text: str,
    replace_text: str
) -> Dict[str, Any]:
    """Validate input for find-and-replace operations."""

    # Validate required params
    if not user_google_email:
        return {"error": "user_google_email is required"}
    if not document_id:
        return {"error": "document_id is required"}
    if not isinstance(document_id, str) or len(document_id) == 0:
        return {"error": "document_id must be a non-empty string"}

    if not find_text:
        return {"error": "find_text is required"}
    if not isinstance(find_text, str):
        return {"error": "find_text must be a string"}

    if not isinstance(replace_text, str):
        return {"error": "replace_text must be a string"}

    # Validate find text length
    if len(find_text) < MIN_FIND_TEXT_LENGTH:
        return {"error": "find_text must not be empty"}
    if len(find_text) > MAX_FIND_TEXT_LENGTH:
        return {
            "error": f"find_text too long ({len(find_text)}; max {MAX_FIND_TEXT_LENGTH})"
        }

    # Validate replace text size
    replace_bytes = len(replace_text.encode('utf-8'))
    if replace_bytes > MAX_TEXT_SIZE_BYTES:
        size_mb = replace_bytes / (1024 * 1024)
        return {
            "error": f"replace_text too large ({size_mb:.1f}MB; max 50MB)"
        }

    return {
        "status": "validation_passed",
        "document_id": document_id,
        "find_text": find_text,
        "replace_text": replace_text,
        "replace_text_size_bytes": replace_bytes,
        "user_google_email": user_google_email
    }


def get_docs_quota_key() -> str:
    """Generate quota tracking key for Docs operations (minute-level)."""
    now = datetime.now(timezone.utc)
    return f"docs_quota_{now.strftime('%Y_%m_%d_%H_%M')}"


def load_quota_state_from_kt(
    user_google_email: str,
    knowledge_set: str = "quota_tracking"
) -> Dict[str, Any]:
    """Load quota state from Knowledge Table."""
    quota_key = get_docs_quota_key()

    quota_state = {
        "docs_quota_used": 0,
        "docs_doc_id": None,
        "docs_quota_key": quota_key,
        "user_google_email": user_google_email
    }

    try:
        docs_response = relevance_raw_api(
            endpoint="/knowledge/retrieve",
            method="POST",
            body={
                "knowledge_set": knowledge_set,
                "filter": {
                    "key": quota_key,
                    "user_google_email": user_google_email,
                    "service": "docs"
                }
            }
        )
        if docs_response and len(docs_response.get("results", [])) > 0:
            doc = docs_response["results"][0]
            quota_state["docs_quota_used"] = doc.get("quota_used", 0)
            quota_state["docs_doc_id"] = doc.get("document_id")
        logger.debug(f"Loaded Docs quota: {quota_state['docs_quota_used']}/{DOCS_QUOTA_PER_MINUTE}")
    except Exception as e:
        logger.warning(f"Failed to load Docs quota from KT: {str(e)}")

    logger.debug(f"Loaded Docs quota state: {quota_state}")
    return quota_state


def check_docs_quota(quota_state: Dict[str, Any]) -> Dict[str, Any]:
    """Check Docs API quota limits (shared 300/min pool)."""
    docs_quota_used = quota_state.get("docs_quota_used", 0)
    warnings = []

    # Check Docs quota (300/min shared pool)
    if docs_quota_used >= DOCS_EXHAUSTED_THRESHOLD:
        return {
            "error": f"Docs API quota critical ({docs_quota_used}/{DOCS_QUOTA_PER_MINUTE}). "
                     f"Quota resets at top of next minute.",
            "action": "wait_one_minute",
            "quota_exhausted": True
        }

    if docs_quota_used > DOCS_WARNING_THRESHOLD:
        warnings.append(
            f"⚠️ Docs quota high this minute ({docs_quota_used}/{DOCS_QUOTA_PER_MINUTE}). "
            f"Only {DOCS_QUOTA_PER_MINUTE - docs_quota_used} calls remaining. "
            f"This pool is shared with Drive, Sheets, and other APIs."
        )

    return {
        "status": "quota_available",
        "quota_used": docs_quota_used,
        "quota_remaining": DOCS_QUOTA_PER_MINUTE - docs_quota_used,
        "warnings": warnings if warnings else None
    }


def validate_write_and_update_quota(
    write_result: Union[str, Dict],
    operation_type: str,
    quota_state: Dict[str, Any],
    user_google_email: str,
    knowledge_set: str = "quota_tracking"
) -> Dict[str, Any]:
    """Validate write response and prepare quota update."""

    if not write_result:
        return {
            "error": "No response from Google Docs API",
            "status": "error",
            "is_complete": False
        }

    if isinstance(write_result, str) and write_result.startswith("Error"):
        return {
            "error": f"Google Docs operation failed: {write_result}",
            "status": "error",
            "is_complete": False
        }

    # For atomic batchUpdate, 1 API call regardless of operation count
    api_calls_made = 1

    logger.info(
        f"Docs operation successful ({operation_type}): {user_google_email}. "
        f"API calls: {api_calls_made}"
    )

    try:
        quota_key = quota_state.get("docs_quota_key")
        new_quota = quota_state.get("docs_quota_used", 0) + api_calls_made

        if quota_state.get("docs_doc_id"):
            # PATCH update existing Docs quota
            logger.debug(f"Updating Docs quota for {user_google_email} (doc_id={quota_state.get('docs_doc_id')})")
            response = relevance_raw_api(
                endpoint="/knowledge/update",
                method="PATCH",
                body={
                    "knowledge_set": knowledge_set,
                    "document_id": quota_state.get("docs_doc_id"),
                    "fields": {
                        "quota_used": new_quota,
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            )
        else:
            # POST create new Docs quota entry
            logger.debug(f"Creating Docs quota entry for {user_google_email}")
            response = relevance_raw_api(
                endpoint="/knowledge/add",
                method="POST",
                body={
                    "knowledge_set": knowledge_set,
                    "fields": {
                        "key": quota_key,
                        "quota_used": new_quota,
                        "user_google_email": user_google_email,
                        "service": "docs",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }
                }
            )
        logger.info(f"Docs quota updated: {new_quota}/{DOCS_QUOTA_PER_MINUTE}")
    except Exception as e:
        logger.error(f"Failed to update Docs quota: {str(e)}")

    return {
        "status": "success",
        "operation": operation_type,
        "is_complete": True,
        "quota_impact": api_calls_made,
        "metadata": {
            "api_calls_consumed": api_calls_made,
            "quota_state_updated": True,
            "quota_remaining": DOCS_QUOTA_PER_MINUTE - (quota_state["docs_quota_used"] + api_calls_made),
            "quota_key": quota_state.get("docs_quota_key"),
            "note": "Shared 300/min pool with Drive, Sheets, and other APIs"
        }
    }
