"""
Test Suite for Google Docs Guardrails

Tests cover:
1. Input validation for create_doc (title length, content size)
2. Input validation for modify_doc_text (indices, text size, document_id)
3. Input validation for find_and_replace (pattern length, replacement size)
4. Quota tracking (minute-level key generation)
5. Quota limit enforcement (300/min shared pool)
6. Write result validation (success and error cases)
"""

import sys
import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from docs_guardrails import (
    validate_create_doc_input,
    validate_modify_doc_text_input,
    validate_find_replace_input,
    get_docs_quota_key,
    load_quota_state_from_kt,
    check_docs_quota,
    validate_write_and_update_quota,
    MAX_DOC_TITLE_LENGTH,
    MAX_TEXT_SIZE_BYTES,
    DOCS_QUOTA_PER_MINUTE,
    DOCS_WARNING_THRESHOLD,
    DOCS_EXHAUSTED_THRESHOLD,
)


def mock_relevance_raw_api(endpoint, method=None, body=None, **kwargs):
    """Mock Knowledge Table API for testing."""
    if endpoint == "/knowledge/retrieve":
        # Mock KT read response for Docs quota
        filters = body.get("filter", {})
        key = filters.get("key")
        service = filters.get("service")
        user_email = filters.get("user_google_email")

        if service == "docs":
            return {
                "results": [{
                    "key": key,
                    "quota_used": 150,
                    "service": "docs",
                    "user_google_email": user_email,
                    "document_id": "mock_docs_quota_id_xyz"
                }]
            }
        return {"results": []}

    elif endpoint == "/knowledge/add":
        # Mock KT create
        return {"status": "created", "document_id": "mock_doc_id_new"}

    elif endpoint == "/knowledge/update":
        # Mock KT update
        return {"status": "updated"}

    return {"status": "ok"}


def test_create_doc_validation():
    """Test 1: Create Doc input validation"""
    print("\n=== Test 1: Create Doc Input Validation ===")

    # Test 1.1: Valid input
    result = validate_create_doc_input(
        user_google_email="test@gmail.com",
        title="My Document",
        content="Hello world"
    )
    assert result["status"] == "validation_passed", f"Test 1.1 failed: {result}"
    assert result["title"] == "My Document"
    print("✓ Test 1.1: Valid create_doc input accepted")

    # Test 1.2: Missing email
    result = validate_create_doc_input(
        user_google_email="",
        title="My Document"
    )
    assert "error" in result, "Test 1.2 failed: should reject empty email"
    print("✓ Test 1.2: Empty email rejected")

    # Test 1.3: Title too long
    long_title = "x" * (MAX_DOC_TITLE_LENGTH + 1)
    result = validate_create_doc_input(
        user_google_email="test@gmail.com",
        title=long_title
    )
    assert "error" in result, "Test 1.3 failed: should reject title > 255 chars"
    print(f"✓ Test 1.3: Title > {MAX_DOC_TITLE_LENGTH} chars rejected")

    # Test 1.4: Content too large
    large_content = "x" * (MAX_TEXT_SIZE_BYTES + 1)
    result = validate_create_doc_input(
        user_google_email="test@gmail.com",
        title="Test",
        content=large_content
    )
    assert "error" in result, "Test 1.4 failed: should reject content > 50MB"
    print("✓ Test 1.4: Content > 50MB rejected")

    # Test 1.5: Empty title
    result = validate_create_doc_input(
        user_google_email="test@gmail.com",
        title=""
    )
    assert "error" in result, "Test 1.5 failed: should reject empty title"
    print("✓ Test 1.5: Empty title rejected")


def test_modify_doc_text_validation():
    """Test 2: Modify Doc Text input validation"""
    print("\n=== Test 2: Modify Doc Text Input Validation ===")

    # Test 2.1: Valid input with indices
    result = validate_modify_doc_text_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        text="New text",
        start_index=5,
        end_index=10
    )
    assert result["status"] == "validation_passed", f"Test 2.1 failed: {result}"
    print("✓ Test 2.1: Valid modify_doc_text input accepted")

    # Test 2.2: Missing document_id
    result = validate_modify_doc_text_input(
        user_google_email="test@gmail.com",
        document_id="",
        text="New text"
    )
    assert "error" in result, "Test 2.2 failed: should reject empty document_id"
    print("✓ Test 2.2: Empty document_id rejected")

    # Test 2.3: Invalid start_index (not int)
    result = validate_modify_doc_text_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        start_index="five"
    )
    assert "error" in result, "Test 2.3 failed: should reject non-int start_index"
    print("✓ Test 2.3: Non-integer start_index rejected")

    # Test 2.4: start_index > end_index
    result = validate_modify_doc_text_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        start_index=10,
        end_index=5
    )
    assert "error" in result, "Test 2.4 failed: should reject start > end"
    print("✓ Test 2.4: start_index > end_index rejected")

    # Test 2.5: Text too large
    large_text = "x" * (MAX_TEXT_SIZE_BYTES + 1)
    result = validate_modify_doc_text_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        text=large_text
    )
    assert "error" in result, "Test 2.5 failed: should reject text > 50MB"
    print("✓ Test 2.5: Text > 50MB rejected")

    # Test 2.6: Negative start_index
    result = validate_modify_doc_text_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        start_index=-1
    )
    assert "error" in result, "Test 2.6 failed: should reject negative start_index"
    print("✓ Test 2.6: Negative start_index rejected")


def test_find_replace_validation():
    """Test 3: Find-and-Replace input validation"""
    print("\n=== Test 3: Find-and-Replace Input Validation ===")

    # Test 3.1: Valid input
    result = validate_find_replace_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        find_text="Hello",
        replace_text="Hi"
    )
    assert result["status"] == "validation_passed", f"Test 3.1 failed: {result}"
    print("✓ Test 3.1: Valid find-replace input accepted")

    # Test 3.2: Empty find_text
    result = validate_find_replace_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        find_text="",
        replace_text="Hi"
    )
    assert "error" in result, "Test 3.2 failed: should reject empty find_text"
    print("✓ Test 3.2: Empty find_text rejected")

    # Test 3.3: find_text too long
    long_find = "x" * 10001
    result = validate_find_replace_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        find_text=long_find,
        replace_text="Hi"
    )
    assert "error" in result, "Test 3.3 failed: should reject find_text > 10000 chars"
    print("✓ Test 3.3: find_text > 10000 chars rejected")

    # Test 3.4: replace_text too large
    large_replace = "x" * (MAX_TEXT_SIZE_BYTES + 1)
    result = validate_find_replace_input(
        user_google_email="test@gmail.com",
        document_id="abc123",
        find_text="Hello",
        replace_text=large_replace
    )
    assert "error" in result, "Test 3.4 failed: should reject replace_text > 50MB"
    print("✓ Test 3.4: replace_text > 50MB rejected")

    # Test 3.5: Missing document_id
    result = validate_find_replace_input(
        user_google_email="test@gmail.com",
        document_id="",
        find_text="Hello",
        replace_text="Hi"
    )
    assert "error" in result, "Test 3.5 failed: should reject empty document_id"
    print("✓ Test 3.5: Empty document_id rejected")


def test_quota_key_generation():
    """Test 4: Quota key generation"""
    print("\n=== Test 4: Quota Key Generation ===")

    key = get_docs_quota_key()
    now = datetime.now(timezone.utc)
    expected_prefix = f"docs_quota_{now.strftime('%Y_%m_%d_%H_%M')}"

    assert key.startswith("docs_quota_"), "Test 4.1 failed: key should start with 'docs_quota_'"
    assert len(key) == len(expected_prefix), "Test 4.1 failed: key format incorrect"
    print(f"✓ Test 4.1: Quota key generated correctly: {key}")


def test_quota_state_loading():
    """Test 5: Quota state loading"""
    print("\n=== Test 5: Quota State Loading ===")

    quota_state = load_quota_state_from_kt("test@gmail.com")

    assert quota_state["docs_quota_used"] == 0, "Test 5.1 failed: should initialize to 0"
    assert quota_state["docs_doc_id"] is None, "Test 5.2 failed: should initialize to None"
    assert "docs_quota_key" in quota_state, "Test 5.3 failed: should have docs_quota_key"
    assert quota_state["user_google_email"] == "test@gmail.com", "Test 5.4 failed: should store email"

    print("✓ Test 5.1: Quota state initializes to 0")
    print("✓ Test 5.2: Quota state has None doc_id initially")
    print("✓ Test 5.3: Quota state includes docs_quota_key")
    print("✓ Test 5.4: Quota state includes user email")


def test_quota_limits():
    """Test 6: Quota limit enforcement"""
    print("\n=== Test 6: Quota Limit Enforcement ===")

    # Test 6.1: Quota available
    quota_state = {"docs_quota_used": 50}
    result = check_docs_quota(quota_state)
    assert result["status"] == "quota_available", "Test 6.1 failed: should allow below threshold"
    print("✓ Test 6.1: Quota available when < 250 calls used")

    # Test 6.2: Quota warning
    quota_state = {"docs_quota_used": 260}
    result = check_docs_quota(quota_state)
    assert result["status"] == "quota_available", "Test 6.2 failed: should warn but allow"
    assert result["warnings"] is not None, "Test 6.2 failed: should include warning"
    print("✓ Test 6.2: Quota warning at 260/300 calls")

    # Test 6.3: Quota exhausted
    quota_state = {"docs_quota_used": 290}
    result = check_docs_quota(quota_state)
    assert "error" in result, "Test 6.3 failed: should error when quota critical"
    assert result.get("quota_exhausted") == True, "Test 6.3 failed: should set exhausted flag"
    print("✓ Test 6.3: Quota exhausted error at 290/300 calls")

    # Test 6.4: Quota remaining calculation
    quota_state = {"docs_quota_used": 100}
    result = check_docs_quota(quota_state)
    assert result["quota_remaining"] == 200, "Test 6.4 failed: quota_remaining should be 200"
    print("✓ Test 6.4: Quota remaining calculated correctly (200)")


def test_write_validation():
    """Test 7: Write result validation"""
    print("\n=== Test 7: Write Result Validation ===")

    # Test 7.1: Valid result
    quota_state = load_quota_state_from_kt("test@gmail.com")
    result = validate_write_and_update_quota(
        write_result="Replaced 5 occurrences",
        operation_type="find_and_replace",
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )
    assert result["status"] == "success", f"Test 7.1 failed: {result}"
    assert result["quota_impact"] == 1, "Test 7.1 failed: atomic ops should have quota_impact=1"
    print("✓ Test 7.1: Valid write result accepted")

    # Test 7.2: Empty result
    result = validate_write_and_update_quota(
        write_result="",
        operation_type="create_doc",
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )
    assert "error" in result, "Test 7.2 failed: should reject empty result"
    print("✓ Test 7.2: Empty write result rejected")

    # Test 7.3: Error result
    result = validate_write_and_update_quota(
        write_result="Error: Document not found",
        operation_type="modify_doc",
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )
    assert result["status"] == "error", "Test 7.3 failed: should mark as error"
    print("✓ Test 7.3: Error result marked as failed")

    # Test 7.4: Quota impact is always 1 (atomic)
    result = validate_write_and_update_quota(
        write_result="Success",
        operation_type="create_doc",
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )
    assert result["quota_impact"] == 1, "Test 7.4 failed: all ops should use 1 API call (atomic)"
    print("✓ Test 7.4: All operations quota impact is 1 (atomic batchUpdate)")


@patch("docs_guardrails.relevance_raw_api", side_effect=mock_relevance_raw_api)
def test_quota_persistence(mock_api):
    """Test 8: Quota persistence in Knowledge Table"""
    print("\n=== Test 8: Quota Persistence ===")

    # Test 8.1: Load quota from KT
    with patch("docs_guardrails.relevance_raw_api", side_effect=mock_relevance_raw_api):
        quota_state = load_quota_state_from_kt("test@gmail.com")
        assert quota_state["user_google_email"] == "test@gmail.com"
        assert quota_state["docs_quota_key"].startswith("docs_quota_")
        print("✓ Test 8.1: Quota state initialized from KT")

    # Test 8.2: Update quota after successful write
    quota_state = {
        "docs_quota_used": 150,
        "docs_doc_id": "docs_quota_id_123",
        "docs_quota_key": "docs_quota_2026_05_20_14_32",
        "user_google_email": "test@gmail.com"
    }

    write_result = "Successfully created document with 500 character(s)"
    result = validate_write_and_update_quota(
        write_result=write_result,
        operation_type="create_doc",
        quota_state=quota_state,
        user_google_email="test@gmail.com"
    )

    assert result["status"] == "success", f"Test 8.2 failed: should be success, got {result['status']}"
    assert result["metadata"]["quota_state_updated"] == True, "Test 8.2 failed: quota should be updated"
    assert result["quota_impact"] == 1, "Test 8.2 failed: all ops should use 1 API call (atomic)"
    print("✓ Test 8.2: Quota updated in KT after successful write")


def run_all_tests():
    """Run all test suites"""
    test_create_doc_validation()
    test_modify_doc_text_validation()
    test_find_replace_validation()
    test_quota_key_generation()
    test_quota_state_loading()
    test_quota_limits()
    test_write_validation()
    test_quota_persistence()

    print("\n" + "="*60)
    print("✅ All Docs Guardrail Tests Passed!")
    print("="*60)
    print("\nTest Summary:")
    print("- Create Doc validation: 5 tests ✓")
    print("- Modify Doc Text validation: 6 tests ✓")
    print("- Find-Replace validation: 5 tests ✓")
    print("- Quota key generation: 1 test ✓")
    print("- Quota state loading: 4 tests ✓")
    print("- Quota limit enforcement: 4 tests ✓")
    print("- Write result validation: 4 tests ✓")
    print("- Quota persistence: 2 tests ✓")
    print("\nTotal: 31 test cases passed")


if __name__ == "__main__":
    run_all_tests()
