"""
E2E tests for Google Drive tools with mocked API responses.

Tests cover:
- check_public_link_permission: permission checking
- format_public_sharing_error: error message formatting
- get_drive_image_url: URL generation
- build_drive_list_params: query parameter building
- Drive query pattern detection
"""

import pytest
from gdrive.drive_helpers import (
    check_public_link_permission,
    format_public_sharing_error,
    get_drive_image_url,
    build_drive_list_params,
    DRIVE_QUERY_PATTERNS,
)


class TestCheckPublicLinkPermission:
    """Tests for check_public_link_permission."""

    def test_public_reader_permission(self):
        permissions = [{"type": "anyone", "role": "reader"}]
        assert check_public_link_permission(permissions) is True

    def test_public_writer_permission(self):
        permissions = [{"type": "anyone", "role": "writer"}]
        assert check_public_link_permission(permissions) is True

    def test_public_commenter_permission(self):
        permissions = [{"type": "anyone", "role": "commenter"}]
        assert check_public_link_permission(permissions) is True

    def test_no_public_permission(self):
        permissions = [{"type": "user", "role": "reader", "emailAddress": "bob@example.com"}]
        assert check_public_link_permission(permissions) is False

    def test_empty_permissions(self):
        assert check_public_link_permission([]) is False

    def test_anyone_with_owner_role(self):
        """Owner role for 'anyone' should not count as public link."""
        permissions = [{"type": "anyone", "role": "owner"}]
        assert check_public_link_permission(permissions) is False

    def test_mixed_permissions(self):
        permissions = [
            {"type": "user", "role": "owner", "emailAddress": "alice@example.com"},
            {"type": "anyone", "role": "reader"},
        ]
        assert check_public_link_permission(permissions) is True


class TestFormatPublicSharingError:
    """Tests for format_public_sharing_error."""

    def test_message_includes_filename(self):
        result = format_public_sharing_error("MyDoc.pdf", "file_123")
        assert "MyDoc.pdf" in result

    def test_message_includes_file_id(self):
        result = format_public_sharing_error("MyDoc.pdf", "file_123")
        assert "file_123" in result

    def test_message_includes_drive_link(self):
        result = format_public_sharing_error("MyDoc.pdf", "file_123")
        assert "drive.google.com" in result

    def test_message_indicates_error(self):
        result = format_public_sharing_error("MyDoc.pdf", "file_123")
        assert "Permission Error" in result or "❌" in result


class TestGetDriveImageUrl:
    """Tests for get_drive_image_url."""

    def test_generates_correct_url(self):
        url = get_drive_image_url("abc123")
        assert url == "https://drive.google.com/uc?export=view&id=abc123"

    def test_different_ids(self):
        url1 = get_drive_image_url("id_1")
        url2 = get_drive_image_url("id_2")
        assert url1 != url2
        assert "id_1" in url1
        assert "id_2" in url2


class TestBuildDriveListParams:
    """Tests for build_drive_list_params."""

    def test_basic_params(self):
        params = build_drive_list_params("name contains 'test'", 10)
        assert params["q"] == "name contains 'test'"
        assert params["pageSize"] == 10
        assert params["supportsAllDrives"] is True

    def test_with_drive_id(self):
        params = build_drive_list_params("name = 'doc'", 5, drive_id="drive_123")
        assert params["driveId"] == "drive_123"
        assert params["corpora"] == "drive"  # Default when drive_id set

    def test_with_drive_id_and_corpora(self):
        params = build_drive_list_params("query", 10, drive_id="d1", corpora="allDrives")
        assert params["corpora"] == "allDrives"

    def test_without_drive_id_with_corpora(self):
        params = build_drive_list_params("query", 10, corpora="user")
        assert params["corpora"] == "user"
        assert "driveId" not in params

    def test_default_include_all_drives(self):
        params = build_drive_list_params("query", 10)
        assert params["includeItemsFromAllDrives"] is True

    def test_include_items_false(self):
        params = build_drive_list_params("query", 10, include_items_from_all_drives=False)
        assert params["includeItemsFromAllDrives"] is False

    def test_fields_include_required_info(self):
        params = build_drive_list_params("query", 10)
        assert "files(id" in params["fields"]
        assert "name" in params["fields"]


class TestDriveQueryPatterns:
    """Tests for Drive query pattern detection regex."""

    @pytest.mark.parametrize("query", [
        "name = 'test.txt'",
        "mimeType = 'application/vnd.google-apps.folder'",
        "'parent_id' in parents",
        "fullText contains 'report'",
        "trashed = false",
        "starred = true",
        "name contains 'budget'",
    ])
    def test_valid_queries_detected(self, query):
        """Valid Drive API queries should match at least one pattern."""
        matched = any(pattern.search(query) for pattern in DRIVE_QUERY_PATTERNS)
        assert matched, f"Query not detected: {query}"

    @pytest.mark.parametrize("query", [
        "just a simple search",
        "hello world",
    ])
    def test_plain_text_not_detected_as_query(self, query):
        """Plain text searches should not match Drive query syntax patterns."""
        matched = any(pattern.search(query) for pattern in DRIVE_QUERY_PATTERNS)
        assert not matched, f"Plain text incorrectly detected as Drive query: {query}"
