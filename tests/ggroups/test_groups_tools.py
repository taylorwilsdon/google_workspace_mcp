"""
Unit tests for Google Groups (Cloud Identity API) tools.

Tests helper functions and formatting utilities.
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from ggroups.groups_tools import (
    _format_group,
    _format_membership,
)


class TestFormatGroup:
    """Tests for _format_group helper function."""

    def test_format_basic_group(self):
        """Test formatting a group with basic fields."""
        group = {
            "name": "groups/abc123",
            "groupKey": {"id": "engineering@example.com"},
            "displayName": "Engineering",
        }

        result = _format_group(group)

        assert "Group ID: abc123" in result
        assert "Email: engineering@example.com" in result
        assert "Display Name: Engineering" in result

    def test_format_group_with_description(self):
        """Test formatting a group with description."""
        group = {
            "name": "groups/abc123",
            "groupKey": {"id": "team@example.com"},
            "description": "The engineering team group",
        }

        result = _format_group(group)

        assert "Description: The engineering team group" in result

    def test_format_group_long_description_truncated(self):
        """Test that long descriptions are truncated."""
        long_desc = "A" * 300
        group = {
            "name": "groups/abc123",
            "description": long_desc,
        }

        result = _format_group(group)

        assert "..." in result
        desc_line = [l for l in result.split("\n") if l.startswith("Description:")][0]
        desc_content = desc_line.split("Description: ")[1]
        assert len(desc_content) <= 203  # 200 + "..."

    def test_format_group_with_parent(self):
        """Test formatting a group with parent resource."""
        group = {
            "name": "groups/abc123",
            "parent": "customers/C0123abc",
        }

        result = _format_group(group)

        assert "Parent: customers/C0123abc" in result

    def test_format_group_with_labels(self):
        """Test formatting a group with labels."""
        group = {
            "name": "groups/abc123",
            "labels": {
                "cloudidentity.googleapis.com/groups.discussion_forum": "",
                "cloudidentity.googleapis.com/groups.security": "",
            },
        }

        result = _format_group(group)

        assert "Labels:" in result
        assert "groups.discussion_forum" in result
        assert "groups.security" in result

    def test_format_group_with_create_time(self):
        """Test formatting a group with create time."""
        group = {
            "name": "groups/abc123",
            "createTime": "2024-01-15T10:30:00Z",
        }

        result = _format_group(group)

        assert "Created: 2024-01-15T10:30:00Z" in result

    def test_format_group_empty(self):
        """Test formatting a group with minimal fields."""
        group = {"name": "groups/xyz"}

        result = _format_group(group)

        assert "Group ID: xyz" in result

    def test_format_group_no_name(self):
        """Test formatting a group without a name."""
        group = {}

        result = _format_group(group)

        assert "Group ID: Unknown" in result

    def test_format_group_full(self):
        """Test formatting a group with all fields."""
        group = {
            "name": "groups/full123",
            "groupKey": {"id": "allhands@example.com"},
            "displayName": "All Hands",
            "description": "Company-wide group",
            "parent": "customers/C0123abc",
            "labels": {
                "cloudidentity.googleapis.com/groups.discussion_forum": "",
            },
            "createTime": "2024-06-01T00:00:00Z",
        }

        result = _format_group(group)

        assert "Group ID: full123" in result
        assert "Email: allhands@example.com" in result
        assert "Display Name: All Hands" in result
        assert "Description: Company-wide group" in result
        assert "Parent: customers/C0123abc" in result
        assert "Labels:" in result
        assert "Created: 2024-06-01T00:00:00Z" in result


class TestFormatMembership:
    """Tests for _format_membership helper function."""

    def test_format_basic_membership(self):
        """Test formatting a membership with basic fields."""
        membership = {
            "name": "groups/abc123/memberships/mem456",
            "preferredMemberKey": {"id": "user@example.com"},
            "roles": [{"name": "MEMBER"}],
        }

        result = _format_membership(membership)

        assert "Membership ID: mem456" in result
        assert "Member: user@example.com" in result
        assert "Roles: MEMBER" in result

    def test_format_membership_multiple_roles(self):
        """Test formatting a membership with multiple roles."""
        membership = {
            "name": "groups/abc123/memberships/mem456",
            "preferredMemberKey": {"id": "admin@example.com"},
            "roles": [{"name": "MEMBER"}, {"name": "MANAGER"}],
        }

        result = _format_membership(membership)

        assert "MEMBER" in result
        assert "MANAGER" in result

    def test_format_membership_with_type(self):
        """Test formatting a membership with member type."""
        membership = {
            "name": "groups/abc123/memberships/mem456",
            "preferredMemberKey": {"id": "user@example.com"},
            "type": "USER",
        }

        result = _format_membership(membership)

        assert "Type: USER" in result

    def test_format_membership_with_create_time(self):
        """Test formatting a membership with join time."""
        membership = {
            "name": "groups/abc123/memberships/mem456",
            "createTime": "2024-03-20T14:00:00Z",
        }

        result = _format_membership(membership)

        assert "Joined: 2024-03-20T14:00:00Z" in result

    def test_format_membership_owner_role(self):
        """Test formatting a membership with OWNER role."""
        membership = {
            "name": "groups/abc123/memberships/mem789",
            "preferredMemberKey": {"id": "owner@example.com"},
            "roles": [{"name": "OWNER"}],
        }

        result = _format_membership(membership)

        assert "Roles: OWNER" in result

    def test_format_membership_empty(self):
        """Test formatting a membership with minimal fields."""
        membership = {"name": "groups/abc123/memberships/mem000"}

        result = _format_membership(membership)

        assert "Membership ID: mem000" in result

    def test_format_membership_no_name(self):
        """Test formatting a membership without a name."""
        membership = {}

        result = _format_membership(membership)

        assert "Membership ID: Unknown" in result


class TestImports:
    """Tests to verify module imports work correctly."""

    def test_import_groups_tools(self):
        """Test that groups_tools module can be imported."""
        from ggroups import groups_tools

        assert hasattr(groups_tools, "search_groups")
        assert hasattr(groups_tools, "get_group")
        assert hasattr(groups_tools, "list_group_members")
        assert hasattr(groups_tools, "manage_group")

    def test_import_extended_tools(self):
        """Test that extended tools can be imported."""
        from ggroups import groups_tools

        assert hasattr(groups_tools, "manage_group_members")

    def test_import_complete_tools(self):
        """Test that complete tier tools can be imported."""
        from ggroups import groups_tools

        assert hasattr(groups_tools, "list_groups")
        assert hasattr(groups_tools, "lookup_group")


class TestConstants:
    """Tests for module constants."""

    def test_google_group_label(self):
        """Test the default Google Group label constant."""
        from ggroups.groups_tools import GOOGLE_GROUP_LABEL

        assert "cloudidentity.googleapis.com" in GOOGLE_GROUP_LABEL
        assert "discussion_forum" in GOOGLE_GROUP_LABEL
