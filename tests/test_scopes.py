"""
Unit tests for cross-service scope generation.

Verifies that docs and sheets tools automatically include the Drive scopes
they need for operations like search_docs, list_docs_in_folder,
export_doc_to_pdf, and list_spreadsheets — without requiring --tools drive.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from auth.scopes import (
    BASE_SCOPES,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_SCOPE,
    CONTACTS_READONLY_SCOPE,
    CONTACTS_SCOPE,
    DRIVE_FILE_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_LABELS_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_SETTINGS_BASIC_SCOPE,
    SHEETS_READONLY_SCOPE,
    SHEETS_WRITE_SCOPE,
    clear_permission_config,
    get_allowed_scopes_for_permissions,
    get_permission_config,
    get_scopes_for_permission_level,
    get_scopes_for_tools,
    has_required_scopes,
    set_permission_config,
    set_read_only,
)


class TestDocsScopes:
    """Tests for docs tool scope generation."""

    def test_docs_includes_drive_readonly(self):
        """search_docs, get_doc_content, list_docs_in_folder need drive.readonly."""
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_READONLY_SCOPE in scopes

    def test_docs_includes_drive_file(self):
        """export_doc_to_pdf needs drive.file to create the PDF."""
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_FILE_SCOPE in scopes

    def test_docs_does_not_include_full_drive(self):
        """docs should NOT request full drive access."""
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_SCOPE not in scopes


class TestSheetsScopes:
    """Tests for sheets tool scope generation."""

    def test_sheets_includes_drive_readonly(self):
        """list_spreadsheets needs drive.readonly."""
        scopes = get_scopes_for_tools(["sheets"])
        assert DRIVE_READONLY_SCOPE in scopes

    def test_sheets_does_not_include_full_drive(self):
        """sheets should NOT request full drive access."""
        scopes = get_scopes_for_tools(["sheets"])
        assert DRIVE_SCOPE not in scopes


class TestCombinedScopes:
    """Tests for combined tool scope generation."""

    def test_docs_sheets_no_duplicate_drive_readonly(self):
        """Combined docs+sheets should deduplicate drive.readonly."""
        scopes = get_scopes_for_tools(["docs", "sheets"])
        assert scopes.count(DRIVE_READONLY_SCOPE) <= 1

    def test_docs_sheets_returns_unique_scopes(self):
        """All returned scopes should be unique."""
        scopes = get_scopes_for_tools(["docs", "sheets"])
        assert len(scopes) == len(set(scopes))


class TestReadOnlyScopes:
    """Tests for read-only mode scope generation."""

    def setup_method(self):
        set_read_only(False)

    def teardown_method(self):
        set_read_only(False)

    def test_docs_readonly_includes_drive_readonly(self):
        """Even in read-only mode, docs needs drive.readonly for search/list."""
        set_read_only(True)
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_READONLY_SCOPE in scopes

    def test_docs_readonly_excludes_drive_file(self):
        """In read-only mode, docs should NOT request drive.file."""
        set_read_only(True)
        scopes = get_scopes_for_tools(["docs"])
        assert DRIVE_FILE_SCOPE not in scopes

    def test_sheets_readonly_includes_drive_readonly(self):
        """Even in read-only mode, sheets needs drive.readonly for list."""
        set_read_only(True)
        scopes = get_scopes_for_tools(["sheets"])
        assert DRIVE_READONLY_SCOPE in scopes


class TestHasRequiredScopes:
    """Tests for hierarchy-aware scope checking."""

    def test_exact_match(self):
        """Exact scope match should pass."""
        assert has_required_scopes([GMAIL_READONLY_SCOPE], [GMAIL_READONLY_SCOPE])

    def test_missing_scope_fails(self):
        """Missing scope with no covering broader scope should fail."""
        assert not has_required_scopes([GMAIL_READONLY_SCOPE], [GMAIL_SEND_SCOPE])

    def test_empty_available_fails(self):
        """Empty available scopes should fail when scopes are required."""
        assert not has_required_scopes([], [GMAIL_READONLY_SCOPE])

    def test_empty_required_passes(self):
        """No required scopes should always pass."""
        assert has_required_scopes([], [])
        assert has_required_scopes([GMAIL_READONLY_SCOPE], [])

    def test_none_available_fails(self):
        """None available scopes should fail when scopes are required."""
        assert not has_required_scopes(None, [GMAIL_READONLY_SCOPE])

    def test_none_available_empty_required_passes(self):
        """None available with no required scopes should pass."""
        assert has_required_scopes(None, [])

    # Gmail hierarchy: gmail.modify covers readonly, send, compose, labels
    def test_gmail_modify_covers_readonly(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_READONLY_SCOPE])

    def test_gmail_modify_covers_send(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_SEND_SCOPE])

    def test_gmail_modify_covers_compose(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_COMPOSE_SCOPE])

    def test_gmail_modify_covers_labels(self):
        assert has_required_scopes([GMAIL_MODIFY_SCOPE], [GMAIL_LABELS_SCOPE])

    def test_gmail_modify_does_not_cover_settings(self):
        """gmail.modify does NOT cover gmail.settings.basic."""
        assert not has_required_scopes(
            [GMAIL_MODIFY_SCOPE], [GMAIL_SETTINGS_BASIC_SCOPE]
        )

    def test_gmail_modify_covers_multiple_children(self):
        """gmail.modify should satisfy multiple child scopes at once."""
        assert has_required_scopes(
            [GMAIL_MODIFY_SCOPE],
            [GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE, GMAIL_LABELS_SCOPE],
        )

    # Drive hierarchy: drive covers drive.readonly and drive.file
    def test_drive_covers_readonly(self):
        assert has_required_scopes([DRIVE_SCOPE], [DRIVE_READONLY_SCOPE])

    def test_drive_covers_file(self):
        assert has_required_scopes([DRIVE_SCOPE], [DRIVE_FILE_SCOPE])

    def test_drive_readonly_does_not_cover_full(self):
        """Narrower scope should not satisfy broader scope."""
        assert not has_required_scopes([DRIVE_READONLY_SCOPE], [DRIVE_SCOPE])

    # Other hierarchies
    def test_calendar_covers_readonly(self):
        assert has_required_scopes([CALENDAR_SCOPE], [CALENDAR_READONLY_SCOPE])

    def test_sheets_write_covers_readonly(self):
        assert has_required_scopes([SHEETS_WRITE_SCOPE], [SHEETS_READONLY_SCOPE])

    def test_contacts_covers_readonly(self):
        assert has_required_scopes([CONTACTS_SCOPE], [CONTACTS_READONLY_SCOPE])

    # Mixed: some exact, some via hierarchy
    def test_mixed_exact_and_hierarchy(self):
        """Combination of exact matches and hierarchy-implied scopes."""
        available = [GMAIL_MODIFY_SCOPE, DRIVE_READONLY_SCOPE]
        required = [GMAIL_READONLY_SCOPE, DRIVE_READONLY_SCOPE]
        assert has_required_scopes(available, required)

    def test_mixed_partial_failure(self):
        """Should fail if hierarchy covers some but not all required scopes."""
        available = [GMAIL_MODIFY_SCOPE]
        required = [GMAIL_READONLY_SCOPE, DRIVE_READONLY_SCOPE]
        assert not has_required_scopes(available, required)


class TestPermissionLevelScopes:
    """Tests for per-service permission level scope generation."""

    def setup_method(self):
        set_read_only(False)
        clear_permission_config()

    def teardown_method(self):
        set_read_only(False)
        clear_permission_config()

    # --- Gmail level scopes ---

    def test_gmail_organize_scopes(self):
        """gmail:organize includes readonly, labels, modify."""
        set_permission_config({"gmail": "organize"})
        scopes = get_scopes_for_tools(["gmail"])
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE in scopes
        assert GMAIL_MODIFY_SCOPE in scopes
        assert GMAIL_COMPOSE_SCOPE not in scopes
        assert GMAIL_SEND_SCOPE not in scopes
        assert GMAIL_SETTINGS_BASIC_SCOPE not in scopes

    def test_gmail_drafts_scopes(self):
        """gmail:drafts includes organize scopes + compose."""
        set_permission_config({"gmail": "drafts"})
        scopes = get_scopes_for_tools(["gmail"])
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE in scopes
        assert GMAIL_MODIFY_SCOPE in scopes
        assert GMAIL_COMPOSE_SCOPE in scopes
        assert GMAIL_SEND_SCOPE not in scopes
        assert GMAIL_SETTINGS_BASIC_SCOPE not in scopes

    def test_gmail_send_scopes(self):
        """gmail:send includes drafts scopes + send."""
        set_permission_config({"gmail": "send"})
        scopes = get_scopes_for_tools(["gmail"])
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE in scopes
        assert GMAIL_MODIFY_SCOPE in scopes
        assert GMAIL_COMPOSE_SCOPE in scopes
        assert GMAIL_SEND_SCOPE in scopes
        assert GMAIL_SETTINGS_BASIC_SCOPE not in scopes

    def test_gmail_full_scopes(self):
        """gmail:full includes all 6 Gmail scopes."""
        set_permission_config({"gmail": "full"})
        scopes = get_scopes_for_tools(["gmail"])
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE in scopes
        assert GMAIL_MODIFY_SCOPE in scopes
        assert GMAIL_COMPOSE_SCOPE in scopes
        assert GMAIL_SEND_SCOPE in scopes
        assert GMAIL_SETTINGS_BASIC_SCOPE in scopes

    def test_gmail_readonly_scopes(self):
        """gmail:readonly includes only gmail.readonly."""
        set_permission_config({"gmail": "readonly"})
        scopes = get_scopes_for_tools(["gmail"])
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE not in scopes
        assert GMAIL_MODIFY_SCOPE not in scopes
        assert GMAIL_COMPOSE_SCOPE not in scopes
        assert GMAIL_SEND_SCOPE not in scopes
        assert GMAIL_SETTINGS_BASIC_SCOPE not in scopes

    # --- Generic fallback ---

    def test_calendar_readonly_fallback(self):
        """calendar:readonly uses TOOL_READONLY_SCOPES_MAP fallback."""
        scopes = get_scopes_for_permission_level("calendar", "readonly")
        assert CALENDAR_READONLY_SCOPE in scopes
        assert CALENDAR_SCOPE not in scopes

    def test_calendar_full_fallback(self):
        """calendar:full uses TOOL_SCOPES_MAP fallback."""
        scopes = get_scopes_for_permission_level("calendar", "full")
        assert CALENDAR_SCOPE in scopes

    # --- Invalid combinations ---

    def test_gmail_invalid_level_raises(self):
        """gmail:superadmin should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown permission level 'superadmin'"):
            get_scopes_for_permission_level("gmail", "superadmin")

    def test_calendar_organize_raises(self):
        """calendar:organize should raise ValueError (no custom levels defined)."""
        with pytest.raises(ValueError, match="Unknown permission level 'organize'"):
            get_scopes_for_permission_level("calendar", "organize")

    # --- Multi-service union ---

    def test_multi_service_allowed_scopes(self):
        """get_allowed_scopes_for_permissions() returns union of all services."""
        set_permission_config({"gmail": "organize", "calendar": "readonly"})
        allowed = get_allowed_scopes_for_permissions()
        # Gmail organize scopes
        assert GMAIL_READONLY_SCOPE in allowed
        assert GMAIL_LABELS_SCOPE in allowed
        assert GMAIL_MODIFY_SCOPE in allowed
        # Calendar readonly scope
        assert CALENDAR_READONLY_SCOPE in allowed
        # Base scopes always included
        for scope in BASE_SCOPES:
            assert scope in allowed
        # Not included
        assert GMAIL_SEND_SCOPE not in allowed
        assert CALENDAR_SCOPE not in allowed

    # --- State lifecycle ---

    def test_clear_permission_config(self):
        """clear_permission_config() resets to empty dict."""
        set_permission_config({"gmail": "organize"})
        assert get_permission_config() == {"gmail": "organize"}
        clear_permission_config()
        assert get_permission_config() == {}

    # --- get_scopes_for_tools integration ---

    def test_scopes_for_tools_with_permission_config(self):
        """get_scopes_for_tools uses permission config when set."""
        set_permission_config({"gmail": "organize"})
        scopes = get_scopes_for_tools(["gmail"])
        scope_set = set(scopes)
        # Should have base scopes + gmail organize scopes
        assert GMAIL_READONLY_SCOPE in scope_set
        assert GMAIL_LABELS_SCOPE in scope_set
        assert GMAIL_MODIFY_SCOPE in scope_set
        # Should NOT have compose/send/settings
        assert GMAIL_COMPOSE_SCOPE not in scope_set
        assert GMAIL_SEND_SCOPE not in scope_set

    def test_scopes_unique(self):
        """Returned scopes should be unique."""
        set_permission_config({"gmail": "drafts"})
        scopes = get_scopes_for_tools(["gmail"])
        assert len(scopes) == len(set(scopes))


class TestPermissionToolFiltering:
    """Tests for filter_server_tools() with permission levels."""

    def setup_method(self):
        set_read_only(False)
        clear_permission_config()

    def teardown_method(self):
        set_read_only(False)
        clear_permission_config()

    def _make_mock_tool(self, required_scopes=None):
        """Create a mock tool object with _required_google_scopes."""

        class MockTool:
            pass

        tool = MockTool()
        fn = MockTool()
        if required_scopes is not None:
            fn._required_google_scopes = required_scopes
        tool.fn = fn
        return tool

    def _make_tool_registry(self, tools_dict):
        """Create a mock server with a tool registry."""

        class MockToolManager:
            def __init__(self, tools):
                self._tools = dict(tools)

        class MockServer:
            def __init__(self, tools):
                self._tool_manager = MockToolManager(tools)

        return MockServer(tools_dict)

    def test_permission_filtering_removes_disallowed_tools(self):
        """Tools requiring scopes outside permission level should be removed."""
        from core.tool_registry import filter_server_tools

        set_permission_config({"gmail": "organize"})

        tools = {
            "search_gmail_messages": self._make_mock_tool([GMAIL_READONLY_SCOPE]),
            "manage_gmail_label": self._make_mock_tool([GMAIL_LABELS_SCOPE]),
            "send_gmail_message": self._make_mock_tool([GMAIL_SEND_SCOPE]),
            "create_gmail_filter": self._make_mock_tool(
                [GMAIL_SETTINGS_BASIC_SCOPE]
            ),
        }
        mock_server = self._make_tool_registry(tools)
        filter_server_tools(mock_server)

        remaining = set(mock_server._tool_manager._tools.keys())
        assert "search_gmail_messages" in remaining
        assert "manage_gmail_label" in remaining
        assert "send_gmail_message" not in remaining
        assert "create_gmail_filter" not in remaining

    def test_permission_filtering_keeps_tools_without_scopes(self):
        """Tools without _required_google_scopes (e.g. start_google_auth) should be kept."""
        from core.tool_registry import filter_server_tools

        set_permission_config({"gmail": "readonly"})

        tools = {
            "search_gmail_messages": self._make_mock_tool([GMAIL_READONLY_SCOPE]),
            "start_google_auth": self._make_mock_tool(),  # No scopes
        }
        mock_server = self._make_tool_registry(tools)
        filter_server_tools(mock_server)

        remaining = set(mock_server._tool_manager._tools.keys())
        assert "search_gmail_messages" in remaining
        assert "start_google_auth" in remaining

    def test_permission_filtering_subset_behavior(self):
        """Tool requiring multiple scopes: kept only if ALL are allowed."""
        from core.tool_registry import filter_server_tools

        set_permission_config({"gmail": "organize"})

        tools = {
            "tool_a": self._make_mock_tool([GMAIL_READONLY_SCOPE]),
            "tool_ab": self._make_mock_tool(
                [GMAIL_READONLY_SCOPE, GMAIL_LABELS_SCOPE]
            ),
            "tool_abc": self._make_mock_tool(
                [GMAIL_READONLY_SCOPE, GMAIL_LABELS_SCOPE, GMAIL_SEND_SCOPE]
            ),
        }
        mock_server = self._make_tool_registry(tools)
        filter_server_tools(mock_server)

        remaining = set(mock_server._tool_manager._tools.keys())
        assert "tool_a" in remaining
        assert "tool_ab" in remaining
        assert "tool_abc" not in remaining

    def test_drafts_level_allows_compose(self):
        """gmail:drafts should allow tools requiring gmail.compose."""
        from core.tool_registry import filter_server_tools

        set_permission_config({"gmail": "drafts"})

        tools = {
            "draft_gmail_message": self._make_mock_tool([GMAIL_COMPOSE_SCOPE]),
            "send_gmail_message": self._make_mock_tool([GMAIL_SEND_SCOPE]),
        }
        mock_server = self._make_tool_registry(tools)
        filter_server_tools(mock_server)

        remaining = set(mock_server._tool_manager._tools.keys())
        assert "draft_gmail_message" in remaining
        assert "send_gmail_message" not in remaining
