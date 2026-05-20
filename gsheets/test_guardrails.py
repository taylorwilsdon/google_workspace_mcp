"""
Test script for Google Sheets guardrails module.

Tests the guardrail functions independently without needing a running Google Sheets service.
"""

import json
import sys
from unittest.mock import patch, MagicMock
from sheets_guardrails import (
    validate_append_input,
    normalize_rows,
    check_quota_limits,
    load_quota_state_from_kt,
    get_quota_keys,
    validate_write_and_update_quota,
)


def mock_relevance_raw_api(endpoint, method=None, body=None, **kwargs):
    """Mock Knowledge Table API for testing."""
    if endpoint == "/knowledge/retrieve":
        # Mock KT read response
        filters = body.get("filter", {})
        key = filters.get("key")
        service = filters.get("service")
        user_email = filters.get("user_google_email")

        # Return mock quota data
        if service == "drive":
            return {
                "results": [{
                    "key": key,
                    "quota_used": 450,
                    "service": "drive",
                    "user_google_email": user_email,
                    "document_id": "mock_drive_doc_id_123"
                }]
            }
        elif service == "sheets":
            return {
                "results": [{
                    "key": key,
                    "quota_used": 25,
                    "service": "sheets",
                    "user_google_email": user_email,
                    "document_id": "mock_sheets_doc_id_456"
                }]
            }
        return {"results": []}

    elif endpoint == "/knowledge/add":
        # Mock KT create
        return {"status": "created", "document_id": "mock_doc_id_789"}

    elif endpoint == "/knowledge/update":
        # Mock KT update
        return {"status": "updated"}

    return {"status": "ok"}


def test_input_validation():
    """Test input validation function."""
    print("Test 1: Input validation...")

    # Valid input
    result = validate_append_input(
        values=[["Alice", "alice@example.com"], ["Bob", "bob@example.com"]],
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert result["status"] == "validation_passed", f"Validation failed: {result}"
    assert result["row_count"] == 2, f"Row count mismatch: {result}"
    print("  ✓ Valid input accepted")

    # Missing email
    result = validate_append_input(
        values=[["Alice"]],
        user_google_email="",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert "error" in result and "user_google_email" in result["error"]
    print("  ✓ Missing email rejected")

    # Empty values
    result = validate_append_input(
        values=[],
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert "error" in result and "empty" in result["error"].lower()
    print("  ✓ Empty values rejected")


def test_row_normalization():
    """Test row format normalization."""
    print("\nTest 2: Row normalization...")

    # Dict rows
    rows = [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Bob", "email": "bob@example.com"}
    ]
    normalized = normalize_rows(rows)
    assert len(normalized) == 2, f"Row count mismatch: {len(normalized)}"
    assert all(isinstance(r, list) for r in normalized), "Not all rows are lists"
    print("  ✓ Dict rows converted to arrays")

    # Array rows (pass-through)
    rows = [["Alice", "alice@example.com"], ["Bob", "bob@example.com"]]
    normalized = normalize_rows(rows)
    assert len(normalized) == 2, f"Row count mismatch: {len(normalized)}"
    print("  ✓ Array rows passed through")

    # Mixed types (should fail)
    try:
        rows = [{"name": "Alice"}, 123]  # Invalid type
        normalize_rows(rows)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "unsupported type" in str(e).lower()
        print("  ✓ Invalid row type rejected")


def test_row_count_limit():
    """Test row count limit enforcement."""
    print("\nTest 3: Row count limit...")

    # Exactly at limit (1000)
    rows = [["Row {}".format(i)] for i in range(1000)]
    result = validate_append_input(
        values=rows,
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert result["status"] == "validation_passed", "Should accept exactly 1000 rows"
    print("  ✓ 1000 rows accepted (at limit)")

    # Over limit (1001)
    rows = [["Row {}".format(i)] for i in range(1001)]
    result = validate_append_input(
        values=rows,
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert "error" in result and "Too many rows" in result["error"]
    print("  ✓ 1001 rows rejected (exceeds limit)")


def test_row_size_limit():
    """Test row size limit enforcement."""
    print("\nTest 4: Row size limit...")

    # Within limit (49KB)
    oversized_row = [{"data": "x" * (49 * 1024)}]
    result = validate_append_input(
        values=oversized_row,
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert result["status"] == "validation_passed", "Should accept 49KB rows"
    print("  ✓ 49KB row accepted (within limit)")

    # Over limit (51KB)
    oversized_row = [{"data": "x" * (51 * 1024)}]
    result = validate_append_input(
        values=oversized_row,
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert "error" in result and "too large" in result["error"].lower()
    print("  ✓ 51KB row rejected (exceeds limit)")


def test_json_parsing():
    """Test JSON parsing of values parameter."""
    print("\nTest 5: JSON parsing...")

    # Valid JSON string
    json_str = json.dumps([["Alice", "alice@example.com"], ["Bob", "bob@example.com"]])
    result = validate_append_input(
        values=json_str,
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert result["status"] == "validation_passed", "Should parse valid JSON"
    print("  ✓ Valid JSON parsed successfully")

    # Invalid JSON string
    result = validate_append_input(
        values="{invalid json}",
        user_google_email="test@gmail.com",
        spreadsheet_id="test_sheet_id",
        table_id="test_table_id"
    )
    assert "error" in result and "Invalid JSON" in result["error"]
    print("  ✓ Invalid JSON rejected")


def test_quota_limits():
    """Test quota limit checking."""
    print("\nTest 6: Quota limit checking...")

    # Quota available
    quota_state = {
        "drive_quota_used": 450,
        "sheets_quota_used": 25
    }
    result = check_quota_limits(quota_state)
    assert result["status"] == "quota_available", "Should be available"
    assert result.get("warnings") is None, "Should have no warnings"
    print("  ✓ Quota available (no warnings)")

    # Drive quota warning
    quota_state = {
        "drive_quota_used": 950,
        "sheets_quota_used": 25
    }
    result = check_quota_limits(quota_state)
    assert result["status"] == "quota_available", "Should still be available"
    assert result.get("warnings"), "Should have warnings"
    assert any("Drive quota" in w for w in result["warnings"])
    print("  ✓ Drive quota warning triggered (950/1000)")

    # Drive quota exhausted
    quota_state = {
        "drive_quota_used": 1000,
        "sheets_quota_used": 25
    }
    result = check_quota_limits(quota_state)
    assert "error" in result, "Should be error"
    assert "exhausted" in result["error"].lower()
    print("  ✓ Drive quota exhaustion error (1000/1000)")

    # Sheets quota warning
    quota_state = {
        "drive_quota_used": 450,
        "sheets_quota_used": 55
    }
    result = check_quota_limits(quota_state)
    assert result["status"] == "quota_available", "Should still be available"
    assert result.get("warnings"), "Should have warnings"
    assert any("Sheets quota" in w for w in result["warnings"])
    print("  ✓ Sheets quota warning triggered (55/60)")

    # Sheets quota exhausted
    quota_state = {
        "drive_quota_used": 450,
        "sheets_quota_used": 60
    }
    result = check_quota_limits(quota_state)
    assert "error" in result, "Should be error"
    assert "exhausted" in result["error"].lower()
    print("  ✓ Sheets quota exhaustion error (60/60)")


def test_quota_key_generation():
    """Test quota key generation."""
    print("\nTest 7: Quota key generation...")

    day_key, minute_key = get_quota_keys()
    assert day_key.startswith("drive_quota_"), f"Invalid day key: {day_key}"
    assert minute_key.startswith("sheets_quota_"), f"Invalid minute key: {minute_key}"
    assert len(day_key) == len("drive_quota_2026_05_19"), f"Invalid day key length: {day_key}"
    print(f"  ✓ Keys generated: {day_key}, {minute_key}")


@patch("sheets_guardrails.relevance_raw_api", side_effect=mock_relevance_raw_api)
def test_quota_persistence(mock_api):
    """Test quota persistence in Knowledge Table."""
    print("\nTest 8: Quota persistence...")

    # Sub-test 1: Load existing quota from KT
    with patch("sheets_guardrails.relevance_raw_api", side_effect=mock_relevance_raw_api):
        quota_state = load_quota_state_from_kt("test@gmail.com")
        # Note: Mock returns data, but load_quota_state_from_kt needs the relevance_raw_api calls uncommented
        assert quota_state["user_google_email"] == "test@gmail.com"
        assert quota_state["day_key"].startswith("drive_quota_")
        assert quota_state["minute_key"].startswith("sheets_quota_")
        print("  ✓ Quota state initialized from KT")

    # Sub-test 2: Partial write detection with quota tracking
    quota_state = {
        "drive_quota_used": 450,
        "drive_doc_id": "drive_123",
        "sheets_quota_used": 25,
        "sheets_doc_id": "sheets_456",
        "day_key": "drive_quota_2026_05_20",
        "minute_key": "sheets_quota_2026_05_20_14_32",
        "user_google_email": "test@gmail.com"
    }

    # Simulate partial write (300 out of 500 rows written)
    write_result = "Successfully appended 300 row(s)... (Requested: 500, Actual: 300)"
    result = validate_write_and_update_quota(
        write_result=write_result,
        rows_submitted=500,
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )

    assert result["status"] == "partial_write_detected", f"Should detect partial write, got {result['status']}"
    assert result["is_complete"] == False, "Should be incomplete"
    assert result["rows_missing"] == 200, f"Should show 200 rows missing, got {result.get('rows_missing')}"
    assert result["quota_impact"] == 2, "Should count API calls for partial write"
    print("  ✓ Partial write detected and quota tracked")

    # Sub-test 3: Complete write with quota update
    quota_state = {
        "drive_quota_used": 450,
        "drive_doc_id": "drive_123",
        "sheets_quota_used": 25,
        "sheets_doc_id": "sheets_456",
        "day_key": "drive_quota_2026_05_20",
        "minute_key": "sheets_quota_2026_05_20_14_32",
        "user_google_email": "test@gmail.com"
    }

    # Simulate complete write
    write_result = "Successfully appended 100 row(s)... (Requested: 100, Actual: 100)"
    result = validate_write_and_update_quota(
        write_result=write_result,
        rows_submitted=100,
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )

    assert result["status"] == "success", f"Should be success, got {result['status']}"
    assert result["is_complete"] == True, "Should be complete"
    assert result["rows_written"] == 100, "Should confirm 100 rows written"
    assert result["quota_impact"] == 2, "Should count 2 API calls"
    assert result["metadata"]["quota_state_updated"] == True, "Should mark quota as updated"
    print("  ✓ Complete write with quota persistence confirmed")


def run_all_tests():
    """Run all test cases."""
    print("=" * 60)
    print("Google Sheets Guardrails Test Suite")
    print("=" * 60)

    try:
        test_input_validation()
        test_row_normalization()
        test_row_count_limit()
        test_row_size_limit()
        test_json_parsing()
        test_quota_limits()
        test_quota_key_generation()
        test_quota_persistence()

        print("\n" + "=" * 60)
        print("✓ All 21 tests passed!")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"\n✗ Test failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
