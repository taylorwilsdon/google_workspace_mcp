from core.tool_tier_loader import get_tools_for_tier


def test_gmail_extended_tier_includes_full_draft_lifecycle_tools():
    tools = get_tools_for_tier("extended", services=["gmail"])

    assert "draft_gmail_message" in tools
    assert "update_gmail_draft" in tools
    assert "delete_gmail_draft" in tools
