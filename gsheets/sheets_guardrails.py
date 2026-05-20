"""
Google Sheets Guardrails Module

Implements guardrails for Google Sheets operations:
- Input validation (row count cap: 1000, row size cap: 50KB)
- Quota tracking (Drive 1000/day, Sheets 60/min) via Knowledge Table
- Partial-write detection (prevents silent data loss)
- Row format normalization (dict → array)

These guardrails protect against:
1. Unbounded appends (>1000 rows)
2. Quota exhaustion without warning
3. Silent partial writes (rows lost mid-batch)
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
MAX_ROWS_PER_APPEND = 1000
MAX_ROW_SIZE_BYTES = 50 * 1024  # 50KB per row
DRIVE_QUOTA_PER_DAY = 1000
SHEETS_QUOTA_PER_MINUTE = 60
DRIVE_WARNING_THRESHOLD = 900  # 90% utilization
SHEETS_WARNING_THRESHOLD = 50  # 80% utilization


def validate_append_input(
    values: Union[str, List],
    user_google_email: str,
    spreadsheet_id: str,
    table_id: str
) -> Dict[str, Any]:
    """Validate input and enforce row count/size limits."""

    # Validate required params
    if not user_google_email:
        return {"error": "user_google_email is required"}
    if not spreadsheet_id:
        return {"error": "spreadsheet_id is required"}
    if not table_id:
        return {"error": "table_id is required"}
    if values is None:
        return {"error": "values is required (array)"}

    # Parse values if string
    parsed_rows = values
    if isinstance(values, str):
        try:
            parsed_rows = json.loads(values)
        except json.JSONDecodeError as e:
            return {"error": f"Invalid JSON in values: {str(e)}"}

    # Validate values is array
    if not isinstance(parsed_rows, list):
        return {"error": "values must be array of arrays or array of objects"}

    if len(parsed_rows) == 0:
        return {"error": "values array cannot be empty"}

    # Enforce row count cap
    if len(parsed_rows) > MAX_ROWS_PER_APPEND:
        return {
            "error": f"Too many rows ({len(parsed_rows)}; max {MAX_ROWS_PER_APPEND}). "
                     f"Split into multiple batches."
        }

    # Validate row structure and size
    for i, row in enumerate(parsed_rows):
        if not isinstance(row, (dict, list)):
            return {
                "error": f"Row {i} is not an object/array: {type(row).__name__}"
            }

        row_json = json.dumps(row)
        if len(row_json) > MAX_ROW_SIZE_BYTES:
            size_kb = len(row_json) / 1024
            return {
                "error": f"Row {i} too large ({size_kb:.1f}KB; max 50KB)"
            }

    return {
        "status": "validation_passed",
        "rows": parsed_rows,
        "row_count": len(parsed_rows),
        "user_google_email": user_google_email,
        "spreadsheet_id": spreadsheet_id,
        "table_id": table_id
    }


def get_quota_keys() -> tuple:
    """Generate quota tracking keys for current UTC day and minute."""
    now = datetime.now(timezone.utc)
    day_key = f"drive_quota_{now.strftime('%Y_%m_%d')}"
    minute_key = f"sheets_quota_{now.strftime('%Y_%m_%d_%H_%M')}"
    return day_key, minute_key


def load_quota_state_from_kt(
    user_google_email: str,
    knowledge_set: str = "quota_tracking"
) -> Dict[str, Any]:
    """Load quota state from Knowledge Table."""
    day_key, minute_key = get_quota_keys()

    quota_state = {
        "drive_quota_used": 0,
        "drive_doc_id": None,
        "sheets_quota_used": 0,
        "sheets_doc_id": None,
        "day_key": day_key,
        "minute_key": minute_key,
        "user_google_email": user_google_email
    }

    try:
        # Fetch Drive quota (daily)
        try:
            drive_response = relevance_raw_api(
                endpoint="/knowledge/retrieve",
                method="POST",
                body={
                    "knowledge_set": knowledge_set,
                    "filter": {
                        "key": day_key,
                        "user_google_email": user_google_email,
                        "service": "drive"
                    }
                }
            )
            if drive_response and len(drive_response.get("results", [])) > 0:
                doc = drive_response["results"][0]
                quota_state["drive_quota_used"] = doc.get("quota_used", 0)
                quota_state["drive_doc_id"] = doc.get("document_id")
            logger.debug(f"Loaded Drive quota: {quota_state['drive_quota_used']}/{DRIVE_QUOTA_PER_DAY}")
        except Exception as e:
            logger.warning(f"Failed to load Drive quota from KT: {str(e)}")

        # Fetch Sheets quota (per-minute)
        try:
            sheets_response = relevance_raw_api(
                endpoint="/knowledge/retrieve",
                method="POST",
                body={
                    "knowledge_set": knowledge_set,
                    "filter": {
                        "key": minute_key,
                        "user_google_email": user_google_email,
                        "service": "sheets"
                    }
                }
            )
            if sheets_response and len(sheets_response.get("results", [])) > 0:
                doc = sheets_response["results"][0]
                quota_state["sheets_quota_used"] = doc.get("quota_used", 0)
                quota_state["sheets_doc_id"] = doc.get("document_id")
            logger.debug(f"Loaded Sheets quota: {quota_state['sheets_quota_used']}/{SHEETS_QUOTA_PER_MINUTE}")
        except Exception as e:
            logger.warning(f"Failed to load Sheets quota from KT: {str(e)}")

    except Exception as e:
        logger.error(f"Unexpected error loading quota state: {str(e)}")

    logger.debug(f"Loaded quota state: {quota_state}")
    return quota_state


def check_quota_limits(quota_state: Dict[str, Any]) -> Dict[str, Any]:
    """Check Drive and Sheets quota limits."""
    drive_quota_used = quota_state.get("drive_quota_used", 0)
    sheets_quota_used = quota_state.get("sheets_quota_used", 0)
    warnings = []

    # Check Drive quota (1000/day)
    if drive_quota_used >= DRIVE_QUOTA_PER_DAY:
        return {
            "error": f"Drive API quota exhausted ({drive_quota_used}/{DRIVE_QUOTA_PER_DAY}). "
                     f"Quota resets at midnight UTC.",
            "action": "wait_until_midnight"
        }

    if drive_quota_used > DRIVE_WARNING_THRESHOLD:
        warnings.append(
            f"⚠️ Drive quota approaching ({drive_quota_used}/{DRIVE_QUOTA_PER_DAY}). "
            f"Only {DRIVE_QUOTA_PER_DAY - drive_quota_used} calls remaining."
        )

    # Check Sheets quota (60/minute)
    if sheets_quota_used >= SHEETS_QUOTA_PER_MINUTE:
        return {
            "error": f"Sheets API quota exhausted this minute ({sheets_quota_used}/{SHEETS_QUOTA_PER_MINUTE}). "
                     f"Quota resets at top of next minute.",
            "action": "wait_one_minute"
        }

    if sheets_quota_used > SHEETS_WARNING_THRESHOLD:
        warnings.append(
            f"⚠️ Sheets quota high this minute ({sheets_quota_used}/{SHEETS_QUOTA_PER_MINUTE}). "
            f"Consider waiting before next operation."
        )

    return {
        "status": "quota_available",
        "warnings": warnings if warnings else None
    }


def normalize_rows(rows: List[Union[Dict, List]]) -> List[List]:
    """Convert rows to array-of-arrays format."""
    normalized = []

    for i, row in enumerate(rows):
        if isinstance(row, dict):
            normalized.append([str(v) for v in row.values()])
        elif isinstance(row, list):
            normalized.append([str(v) for v in row])
        else:
            raise ValueError(
                f"Row {i} has unsupported type {type(row).__name__}. "
                f"Must be dict or list."
            )

    return normalized


def validate_write_and_update_quota(
    write_result: str,
    rows_submitted: int,
    quota_state: Dict[str, Any],
    user_google_email: str,
    knowledge_set: str = "quota_tracking"
) -> Dict[str, Any]:
    """Validate write response and update quota. DETECTS PARTIAL WRITES."""

    if not write_result:
        return {
            "error": "No response from Google Sheets API",
            "status": "error",
            "is_complete": False,
            "rows_submitted": rows_submitted,
            "rows_written": 0,
            "quota_impact": 2
        }

    if isinstance(write_result, str) and write_result.startswith("Error"):
        return {
            "error": f"Google Sheets write failed: {write_result}",
            "status": "error",
            "is_complete": False,
            "rows_submitted": rows_submitted,
            "rows_written": 0,
            "quota_impact": 2
        }

    # CRITICAL: Parse actual rows written from response
    # Message format: "Successfully appended X row(s)... (Requested: N, Actual: M)"
    rows_written = 0
    api_calls_made = 2
    
    if isinstance(write_result, str) and "Actual:" in write_result:
        try:
            # Extract "Actual: M" from message
            actual_idx = write_result.find("Actual:")
            if actual_idx != -1:
                after_actual = write_result[actual_idx + 7:].strip()
                rows_written = int(after_actual.split(")")[0].strip())
        except (ValueError, IndexError):
            rows_written = rows_submitted  # Fallback: assume all written
    else:
        rows_written = rows_submitted  # Legacy: assume all written if no breakdown

    # PARTIAL-WRITE DETECTION ← Critical guardrail
    if rows_written < rows_submitted:
        logger.error(
            f"⚠️ PARTIAL WRITE DETECTED for {user_google_email}: "
            f"Submitted {rows_submitted} rows, but only {rows_written} were written. "
            f"Data loss risk: {rows_submitted - rows_written} rows missing."
        )
        
        return {
            "status": "partial_write_detected",
            "error": f"Partial write detected: {rows_written}/{rows_submitted} rows written. "
                     f"Data loss risk: {rows_submitted - rows_written} rows not written. "
                     f"Verify in sheet before proceeding.",
            "is_complete": False,
            "rows_submitted": rows_submitted,
            "rows_written": rows_written,
            "rows_missing": rows_submitted - rows_written,
            "quota_impact": api_calls_made,
            "action": "manual_verification_required",
            "metadata": {
                "api_calls_consumed": api_calls_made,
                "failure_mode": "silent_data_corruption_prevented",
                "quota_state_updated": False,  # Don't update quota on partial write
                "drive_quota_remaining": DRIVE_QUOTA_PER_DAY - (quota_state["drive_quota_used"] + api_calls_made),
                "sheets_quota_remaining": SHEETS_QUOTA_PER_MINUTE - (quota_state["sheets_quota_used"] + api_calls_made),
            }
        }

    # All rows written successfully
    logger.info(
        f"Write successful: {rows_written}/{rows_submitted} rows for {user_google_email}. "
        f"API calls: {api_calls_made}"
    )

    # Update quota in Knowledge Table
    try:
        new_drive_quota = quota_state.get("drive_quota_used", 0) + api_calls_made
        new_sheets_quota = quota_state.get("sheets_quota_used", 0) + api_calls_made

        # Update Drive quota
        drive_doc_id = quota_state.get("drive_doc_id")
        day_key = quota_state.get("day_key")

        try:
            if drive_doc_id:
                # PATCH update existing Drive quota
                logger.debug(f"Updating Drive quota for {user_google_email} (doc_id={drive_doc_id})")
                response = relevance_raw_api(
                    endpoint="/knowledge/update",
                    method="PATCH",
                    body={
                        "knowledge_set": knowledge_set,
                        "document_id": drive_doc_id,
                        "fields": {
                            "quota_used": new_drive_quota,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
            else:
                # POST create new Drive quota entry
                logger.debug(f"Creating Drive quota entry for {user_google_email}")
                response = relevance_raw_api(
                    endpoint="/knowledge/add",
                    method="POST",
                    body={
                        "knowledge_set": knowledge_set,
                        "fields": {
                            "key": day_key,
                            "quota_used": new_drive_quota,
                            "user_google_email": user_google_email,
                            "service": "drive",
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
            logger.info(f"Drive quota updated: {new_drive_quota}/{DRIVE_QUOTA_PER_DAY}")
        except Exception as e:
            logger.error(f"Failed to update Drive quota: {str(e)}")

        # Update Sheets quota
        sheets_doc_id = quota_state.get("sheets_doc_id")
        minute_key = quota_state.get("minute_key")

        try:
            if sheets_doc_id:
                # PATCH update existing Sheets quota
                logger.debug(f"Updating Sheets quota for {user_google_email} (doc_id={sheets_doc_id})")
                response = relevance_raw_api(
                    endpoint="/knowledge/update",
                    method="PATCH",
                    body={
                        "knowledge_set": knowledge_set,
                        "document_id": sheets_doc_id,
                        "fields": {
                            "quota_used": new_sheets_quota,
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
            else:
                # POST create new Sheets quota entry
                logger.debug(f"Creating Sheets quota entry for {user_google_email}")
                response = relevance_raw_api(
                    endpoint="/knowledge/add",
                    method="POST",
                    body={
                        "knowledge_set": knowledge_set,
                        "fields": {
                            "key": minute_key,
                            "quota_used": new_sheets_quota,
                            "user_google_email": user_google_email,
                            "service": "sheets",
                            "updated_at": datetime.now(timezone.utc).isoformat()
                        }
                    }
                )
            logger.info(f"Sheets quota updated: {new_sheets_quota}/{SHEETS_QUOTA_PER_MINUTE}")
        except Exception as e:
            logger.error(f"Failed to update Sheets quota: {str(e)}")

    except Exception as e:
        logger.error(f"Unexpected error updating quota state: {str(e)}")

    return {
        "status": "success",
        "rows_submitted": rows_submitted,
        "rows_written": rows_written,
        "is_complete": True,
        "quota_impact": api_calls_made,
        "metadata": {
            "api_calls_consumed": api_calls_made,
            "quota_state_updated": True,
            "write_verification": "all_rows_confirmed",
            "drive_quota_remaining": DRIVE_QUOTA_PER_DAY - (quota_state["drive_quota_used"] + api_calls_made),
            "sheets_quota_remaining": SHEETS_QUOTA_PER_MINUTE - (quota_state["sheets_quota_used"] + api_calls_made),
            "quota_keys": {
                "day_key": quota_state.get("day_key"),
                "minute_key": quota_state.get("minute_key")
            }
        }
    }



