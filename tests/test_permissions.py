"""
Unit tests for granular per-service permission parsing and scope resolution.

Covers parse_permissions_arg() validation (format, duplicates, unknown
service/level) and cumulative scope expansion in get_scopes_for_permission().
"""

import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from auth.permissions import (
    get_scopes_for_permission,
    is_action_denied,
    parse_permissions_arg,
    set_permissions,
    SERVICE_PERMISSION_LEVELS,
)
from auth.scopes import (
    GMAIL_READONLY_SCOPE,
    GMAIL_LABELS_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_SCOPE,
    TASKS_READONLY_SCOPE,
    TASKS_SCOPE,
    DRIVE_FILE_SCOPE,
)


class TestParsePermissionsArg:
    """Tests for parse_permissions_arg()."""

    def test_single_valid_entry(self):
        result = parse_permissions_arg(["gmail:readonly"])
        assert result == {"gmail": "readonly"}

    def test_multiple_valid_entries(self):
        result = parse_permissions_arg(["gmail:organize", "drive:full"])
        assert result == {"gmail": "organize", "drive": "full"}

    def test_all_services_at_readonly(self):
        entries = [f"{svc}:readonly" for svc in SERVICE_PERMISSION_LEVELS]
        result = parse_permissions_arg(entries)
        assert set(result.keys()) == set(SERVICE_PERMISSION_LEVELS.keys())

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError, match="Invalid permission format"):
            parse_permissions_arg(["gmail_readonly"])

    def test_duplicate_service_raises(self):
        with pytest.raises(ValueError, match="Duplicate service"):
            parse_permissions_arg(["gmail:readonly", "gmail:full"])

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="Unknown service"):
            parse_permissions_arg(["fakesvc:readonly"])

    def test_unknown_level_raises(self):
        with pytest.raises(ValueError, match="Unknown level"):
            parse_permissions_arg(["gmail:superadmin"])

    def test_empty_list_returns_empty(self):
        assert parse_permissions_arg([]) == {}

    def test_extra_colon_in_value(self):
        """A level containing a colon should fail as unknown level."""
        with pytest.raises(ValueError, match="Unknown level"):
            parse_permissions_arg(["gmail:read:only"])

    def test_tasks_manage_is_valid_level(self):
        """tasks:manage should be accepted by parse_permissions_arg."""
        result = parse_permissions_arg(["tasks:manage"])
        assert result == {"tasks": "manage"}


class TestGetScopesForPermission:
    """Tests for get_scopes_for_permission() cumulative scope expansion."""

    def test_gmail_readonly_returns_readonly_scope(self):
        scopes = get_scopes_for_permission("gmail", "readonly")
        assert GMAIL_READONLY_SCOPE in scopes

    def test_gmail_organize_includes_readonly(self):
        """Organize level should cumulatively include readonly scopes."""
        scopes = get_scopes_for_permission("gmail", "organize")
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE in scopes
        assert GMAIL_MODIFY_SCOPE in scopes

    def test_gmail_drafts_includes_organize_and_readonly(self):
        scopes = get_scopes_for_permission("gmail", "drafts")
        assert GMAIL_READONLY_SCOPE in scopes
        assert GMAIL_LABELS_SCOPE in scopes
        assert GMAIL_COMPOSE_SCOPE in scopes

    def test_drive_readonly_excludes_full(self):
        scopes = get_scopes_for_permission("drive", "readonly")
        assert DRIVE_READONLY_SCOPE in scopes
        assert DRIVE_SCOPE not in scopes
        assert DRIVE_FILE_SCOPE not in scopes

    def test_drive_full_includes_readonly(self):
        scopes = get_scopes_for_permission("drive", "full")
        assert DRIVE_READONLY_SCOPE in scopes
        assert DRIVE_SCOPE in scopes

    def test_unknown_service_raises(self):
        with pytest.raises(ValueError, match="Unknown service"):
            get_scopes_for_permission("nonexistent", "readonly")

    def test_unknown_level_raises(self):
        with pytest.raises(ValueError, match="Unknown permission level"):
            get_scopes_for_permission("gmail", "nonexistent")

    def test_no_duplicate_scopes(self):
        """Cumulative expansion should deduplicate scopes."""
        for service, levels in SERVICE_PERMISSION_LEVELS.items():
            for level_name, _ in levels:
                scopes = get_scopes_for_permission(service, level_name)
                assert len(scopes) == len(set(scopes)), (
                    f"Duplicate scopes for {service}:{level_name}"
                )

    def test_tasks_manage_includes_write_scope(self):
        """Manage level should cumulatively include readonly and write scopes."""
        scopes = get_scopes_for_permission("tasks", "manage")
        assert TASKS_SCOPE in scopes
        assert TASKS_READONLY_SCOPE in scopes

    def test_tasks_full_includes_write_scope(self):
        """Full level should include write and readonly scopes from lower levels."""
        scopes = get_scopes_for_permission("tasks", "full")
        assert TASKS_SCOPE in scopes
        assert TASKS_READONLY_SCOPE in scopes


@pytest.fixture(autouse=True)
def _reset_permissions_state():
    """Ensure each test starts and ends with no active permissions."""
    set_permissions(None)
    yield
    set_permissions(None)


class TestIsActionDenied:
    """Tests for is_action_denied() and SERVICE_DENIED_ACTIONS."""

    def test_no_permissions_mode_allows_all(self):
        """Without granular permissions, no action is denied."""
        set_permissions(None)
        assert is_action_denied("tasks", "delete") is False

    def test_tasks_full_allows_delete(self):
        """Full level should not deny delete."""
        set_permissions({"tasks": "full"})
        assert is_action_denied("tasks", "delete") is False

    def test_tasks_manage_denies_delete(self):
        """Manage level should deny delete."""
        set_permissions({"tasks": "manage"})
        assert is_action_denied("tasks", "delete") is True

    def test_tasks_manage_allows_create(self):
        """Manage level should allow create."""
        set_permissions({"tasks": "manage"})
        assert is_action_denied("tasks", "create") is False

    def test_tasks_manage_allows_update(self):
        """Manage level should allow update."""
        set_permissions({"tasks": "manage"})
        assert is_action_denied("tasks", "update") is False

    def test_tasks_manage_allows_move(self):
        """Manage level should allow move."""
        set_permissions({"tasks": "manage"})
        assert is_action_denied("tasks", "move") is False

    def test_tasks_manage_denies_clear_completed(self):
        """Manage level should deny clear_completed."""
        set_permissions({"tasks": "manage"})
        assert is_action_denied("tasks", "clear_completed") is True

    def test_tasks_full_allows_clear_completed(self):
        """Full level should not deny clear_completed."""
        set_permissions({"tasks": "full"})
        assert is_action_denied("tasks", "clear_completed") is False

    def test_service_not_in_permissions_allows_all(self):
        """A service not listed in permissions should allow all actions."""
        set_permissions({"gmail": "readonly"})
        assert is_action_denied("tasks", "delete") is False

    def test_service_without_denied_actions_allows_all(self):
        """A service with no SERVICE_DENIED_ACTIONS entry should allow all actions."""
        set_permissions({"gmail": "readonly"})
        assert is_action_denied("gmail", "delete") is False
