"""
Unit tests for the --only-tools CLI flag.

The flag is a per-tool-name allowlist that ALSO requests only those specific
tools' Google OAuth scopes. It is exercised across three layers:

- core/tool_tier_loader.py: ToolTierLoader.get_all_tool_names() — the union of
  every tool name across all services/tiers, used to validate requested names.
- auth/scopes.py: set_explicit_scopes() / _EXPLICIT_SCOPES — when set,
  get_scopes_for_tools() returns exactly BASE_SCOPES + the explicit scopes,
  bypassing the service-granular maps.
- main.py: argparse wiring, the mutual-exclusivity guard, the unknown-tool
  validation, and the selection branch that resolves services and sets the
  enabled-tool allowlist.

The post-import scope-union step (reading each registered tool's
_required_google_scopes off a live FastMCP server) needs a fully-registered
server, so it is integration-level and covered by a manual smoke test rather
than here.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep these tests independent of a developer's local .env. Importing main loads
# .env, and OAuth 2.1 mode changes tool schemas at decoration time. Mirrors
# tests/test_main_permissions_tier.py.
os.environ["MCP_ENABLE_OAUTH21"] = "false"
os.environ["WORKSPACE_MCP_STATELESS_MODE"] = "false"

import main  # noqa: E402

from auth.scopes import (  # noqa: E402
    BASE_SCOPES,
    GMAIL_SEND_SCOPE,
    get_scopes_for_tools,
    set_explicit_scopes,
)
from core.tool_tier_loader import ToolTierLoader  # noqa: E402
import core.tool_registry as tool_registry  # noqa: E402


# A couple of tool names that genuinely exist in core/tool_tiers.yaml. We assert
# their presence rather than hard-coding the full list so the test does not break
# every time a tool is added or moved between tiers.
KNOWN_TOOLS = ["send_gmail_message", "search_gmail_messages", "manage_drive_access"]


class TestGetAllToolNames:
    """ToolTierLoader.get_all_tool_names() — the validation source of truth."""

    def test_returns_non_empty_set(self):
        names = ToolTierLoader().get_all_tool_names()
        assert isinstance(names, set)
        assert len(names) > 0

    def test_includes_known_tools(self):
        """Expectations derived from the real tool_tiers.yaml, not hard-coded."""
        names = ToolTierLoader().get_all_tool_names()
        for tool in KNOWN_TOOLS:
            assert tool in names, f"{tool} should be defined in tool_tiers.yaml"

    def test_is_union_across_all_tiers(self):
        """The union must cover core + extended + complete for every service."""
        loader = ToolTierLoader()
        all_names = loader.get_all_tool_names()
        # The 'complete' tier (which get_tools_up_to_tier walks through every
        # tier to build) must be a subset of the full union.
        complete = set(loader.get_tools_up_to_tier("complete"))
        assert complete == all_names


class TestExplicitScopeOverride:
    """auth/scopes.py: set_explicit_scopes() short-circuits get_scopes_for_tools()."""

    def setup_method(self):
        set_explicit_scopes(None)

    def teardown_method(self):
        # Reset module-global state so other tests don't see a leaked override.
        set_explicit_scopes(None)

    def test_override_returns_base_plus_exactly_that_scope(self):
        set_explicit_scopes({GMAIL_SEND_SCOPE})
        scopes = get_scopes_for_tools()

        expected = set(BASE_SCOPES) | {GMAIL_SEND_SCOPE}
        assert set(scopes) == expected

    def test_override_ignores_enabled_tool_list(self):
        """When explicit scopes are set, the passed-in tool list is bypassed."""
        set_explicit_scopes({GMAIL_SEND_SCOPE})
        # Asking for 'drive' tools must NOT leak any drive scope.
        scopes = set(get_scopes_for_tools(["drive", "calendar"]))
        assert scopes == set(BASE_SCOPES) | {GMAIL_SEND_SCOPE}

    def test_override_returns_unique_scopes(self):
        set_explicit_scopes({GMAIL_SEND_SCOPE})
        scopes = get_scopes_for_tools()
        assert len(scopes) == len(set(scopes))

    def test_clearing_override_reverts_to_normal_behavior(self):
        set_explicit_scopes({GMAIL_SEND_SCOPE})
        assert set(get_scopes_for_tools(["drive"])) == set(BASE_SCOPES) | {
            GMAIL_SEND_SCOPE
        }

        set_explicit_scopes(None)
        reverted = get_scopes_for_tools(["drive"])
        # Normal path adds the drive service scopes on top of base; at minimum it
        # must no longer be the explicit-only set.
        assert set(reverted) != set(BASE_SCOPES) | {GMAIL_SEND_SCOPE}
        assert len(reverted) > len(BASE_SCOPES)


class TestSelectionBranch:
    """main.py selection logic for --only-tools.

    The mutual-exclusivity guard and the unknown-tool validation both run early
    in main.main() and sys.exit(1) before any server is started, so they are
    driven the same way tests/test_main_permissions_tier.py drives the
    permissions/tools guard: monkeypatch sys.argv + expect SystemExit.

    The happy-path *continuation* (importing modules and running the server) is
    not reachable as a unit, so the resolution it performs is asserted directly
    against the same helpers main.py calls: ToolTierLoader.get_services_for_tools
    and the enabled-tool registry.
    """

    def setup_method(self):
        tool_registry.set_enabled_tools(None)

    def teardown_method(self):
        tool_registry.set_enabled_tools(None)

    def test_selection_resolves_services_and_allowlist(self):
        """--only-tools send_gmail_message manage_drive_access resolves to the
        gmail + drive services, and the enabled allowlist is exactly that set."""
        requested = ["send_gmail_message", "manage_drive_access"]
        loader = ToolTierLoader()

        # Service resolution — the same call main.py makes for tools_to_import.
        services = loader.get_services_for_tools(requested)
        assert services == {"gmail", "drive"}

        # Enabled-tool allowlist — main.py calls set_enabled_tool_names(set(...)),
        # an alias for core.tool_registry.set_enabled_tools.
        main.set_enabled_tool_names(set(requested))
        assert tool_registry.get_enabled_tools() == set(requested)
        assert tool_registry.is_tool_enabled("send_gmail_message")
        assert tool_registry.is_tool_enabled("manage_drive_access")
        assert not tool_registry.is_tool_enabled("search_gmail_messages")

    def test_set_enabled_tool_names_is_registry_alias(self):
        """main.set_enabled_tool_names must be core.tool_registry.set_enabled_tools."""
        assert main.set_enabled_tool_names is tool_registry.set_enabled_tools

    def test_unknown_tool_name_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setattr(
            sys,
            "argv",
            ["main.py", "--only-tools", "send_gmail_message", "not_a_real_tool"],
        )

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        assert "unknown tool name" in capsys.readouterr().err.lower()

    def test_combined_with_read_only_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setattr(
            sys,
            "argv",
            ["main.py", "--only-tools", "send_gmail_message", "--read-only"],
        )

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        assert "--only-tools cannot be combined" in capsys.readouterr().err

    def test_combined_with_tool_tier_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "main.py",
                "--only-tools",
                "send_gmail_message",
                "--tool-tier",
                "core",
            ],
        )

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        assert "--only-tools cannot be combined" in capsys.readouterr().err

    def test_combined_with_tools_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setattr(
            sys,
            "argv",
            ["main.py", "--only-tools", "send_gmail_message", "--tools", "gmail"],
        )

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        assert "--only-tools cannot be combined" in capsys.readouterr().err
