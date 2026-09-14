"""Tests for tool-tier behavior loaded from the shipped YAML configuration."""

from core.tool_tier_loader import ToolTierLoader


def test_gmail_extended_tier_exposes_delete_gmail_draft():
    """Removing tier registration must make the new tool undiscoverable."""
    tools = ToolTierLoader().get_tools_for_tier("extended", services=["gmail"])

    assert "delete_gmail_draft" in tools
