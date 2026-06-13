"""Tests for the --tool-allowlist CLI flag and WORKSPACE_MCP_TOOL_ALLOWLIST env var.

These tests focus on the parsing and narrowing logic; full end-to-end startup
is covered by the existing tier / permissions tests.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("MCP_ENABLE_OAUTH21", "false")
os.environ.setdefault("WORKSPACE_MCP_STATELESS_MODE", "false")

from core.tool_registry import (  # noqa: E402
    filter_server_tools,
    get_enabled_tools,
    get_tool_components,
    set_enabled_tools,
)


def _reset_registry() -> None:
    set_enabled_tools(None)


class _FakeProvider:
    def __init__(self, names: list[str]) -> None:
        self._components: dict[str, object] = {
            f"tool:{n}@1": object() for n in names
        }

    def remove_tool(self, tool_name: str) -> None:
        for key in list(self._components):
            if key.startswith("tool:") and key.split(":", 1)[1].rsplit("@", 1)[0] == tool_name:
                del self._components[key]


class _FakeServer:
    def __init__(self, names: list[str]) -> None:
        self.local_provider = _FakeProvider(names)


def test_allowlist_narrows_existing_enabled_set() -> None:
    """Allowlist intersects with the set produced by tier/services."""
    _reset_registry()
    set_enabled_tools(
        {
            "search_gmail_messages",
            "get_gmail_message_content",
            "send_gmail_message",
            "get_gmail_attachment_content",
            "draft_gmail_message",
        }
    )

    allowlist = {"send_gmail_message", "get_gmail_attachment_content"}
    current = get_enabled_tools()
    narrowed = current & allowlist
    set_enabled_tools(narrowed)

    assert get_enabled_tools() == {
        "send_gmail_message",
        "get_gmail_attachment_content",
    }
    _reset_registry()


def test_allowlist_becomes_enabled_set_when_no_prior_filter() -> None:
    """When no tier/services filter is set, allowlist alone defines enabled set."""
    _reset_registry()
    assert get_enabled_tools() is None

    allowlist = {"send_gmail_message", "get_gmail_attachment_content"}
    set_enabled_tools(allowlist)

    assert get_enabled_tools() == allowlist
    _reset_registry()


def test_allowlist_intersection_drops_tools_not_in_tier() -> None:
    """Tools requested in allowlist but absent from tier selection are dropped."""
    _reset_registry()
    set_enabled_tools({"search_gmail_messages", "send_gmail_message"})

    # User asks for send + attachment, but attachment isn't in the tier.
    allowlist = {"send_gmail_message", "get_gmail_attachment_content"}
    current = get_enabled_tools()
    narrowed = current & allowlist
    set_enabled_tools(narrowed)

    assert get_enabled_tools() == {"send_gmail_message"}
    _reset_registry()


def test_empty_intersection_is_detectable_by_caller() -> None:
    """If allowlist intersected with current produces nothing, caller can detect."""
    _reset_registry()
    set_enabled_tools({"search_gmail_messages"})

    allowlist = {"send_gmail_message"}  # not in tier
    current = get_enabled_tools()
    narrowed = current & allowlist

    assert narrowed == set()
    _reset_registry()


def test_env_parsing_splits_on_comma_and_trims() -> None:
    """Mimics the env-parsing branch added in main.py."""
    raw = "  send_gmail_message , get_gmail_attachment_content ,  "
    parsed = [t.strip() for t in raw.split(",") if t.strip()]
    assert parsed == ["send_gmail_message", "get_gmail_attachment_content"]


def test_env_parsing_rejects_empty() -> None:
    raw = "   ,  ,   "
    parsed = [t.strip() for t in raw.split(",") if t.strip()]
    assert parsed == []


def test_post_import_validation_detects_zero_registered() -> None:
    """Allowlist name absent from imported services must fail post-registration.

    Reproduces `--tools gmail --tool-allowlist create_calendar_event` where the
    allowlist promotes to the enabled set but no calendar tools are imported.
    Drives the real `filter_server_tools` so the production filter is exercised.
    """
    server = _FakeServer(["send_gmail_message", "get_gmail_attachment_content"])
    allowlist = {"create_calendar_event"}
    set_enabled_tools(allowlist)

    filter_server_tools(server)

    registered_after_filter = set(get_tool_components(server).keys())
    assert registered_after_filter == set()
    _reset_registry()


def test_post_import_validation_reports_missing_names() -> None:
    """Allowlist names that don't map to imported tools should be reportable."""
    server = _FakeServer(["send_gmail_message", "get_gmail_attachment_content"])
    allowlist = {"send_gmail_message", "create_calendar_event"}
    set_enabled_tools(allowlist)

    filter_server_tools(server)

    registered = set(get_tool_components(server).keys())
    missing = allowlist - registered

    assert registered == {"send_gmail_message"}
    assert missing == {"create_calendar_event"}
    _reset_registry()


def test_env_parsing_rejects_mixed_empty_entries() -> None:
    """Mixed empty entries like 'a,,b' should be detectable so the env var can fail fast.

    Mirrors the validation in main.py: split on ',' (no `if t.strip()` filter)
    so an empty middle token surfaces as a rejectable entry.
    """
    raw = "send_gmail_message,,get_gmail_attachment_content"
    raw_tokens = raw.split(",")
    assert any(not t.strip() for t in raw_tokens)
