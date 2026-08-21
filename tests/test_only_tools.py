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
  validation, the selection branch that resolves services and sets the
  enabled-tool allowlist, and the post-registration scope-union derivation
  (driven through a real main() run stopped just after the union is applied —
  see TestScopeDerivationEndToEnd).
"""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep these tests independent of a developer's local .env. Importing main loads
# .env, and OAuth 2.1 mode changes tool schemas at decoration time. Mirrors
# tests/test_main_permissions_tier.py.
os.environ["MCP_ENABLE_OAUTH21"] = "false"
os.environ["WORKSPACE_MCP_STATELESS_MODE"] = "false"

# Selection env vars main() folds into args.* before the --only-tools guard runs.
# A value leaked from the developer's shell (e.g. WORKSPACE_MCP_DISABLED_TOOLS)
# would trip the mutual-exclusivity guard and turn an unrelated test's expected
# error into the guard's error. Scrubbed here AND per-test via _scrub_selection_env.
SELECTION_ENV_VARS = [
    "WORKSPACE_MCP_TOOLS",
    "WORKSPACE_MCP_TOOL_TIER",
    "WORKSPACE_MCP_PERMISSIONS",
    "WORKSPACE_MCP_READ_ONLY",
    "WORKSPACE_MCP_DISABLED_TOOLS",
]
for _var in SELECTION_ENV_VARS:
    os.environ.pop(_var, None)

import main  # noqa: E402

from auth.scopes import (  # noqa: E402
    BASE_SCOPES,
    DRIVE_FILE_SCOPE,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    get_scopes_for_tools,
    set_explicit_scopes,
    set_enabled_tools as set_enabled_scope_tools,
    set_read_only,
)
from core.tool_tier_loader import ToolTierLoader  # noqa: E402
import core.tool_registry as tool_registry  # noqa: E402
import auth.permissions as permissions  # noqa: E402


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

    def _reset_scope_globals(self):
        # Clear every global get_scopes_for_tools() consults, so the "normal path"
        # tests genuinely exercise service-scope derivation instead of a leaked
        # permissions / read-only short-circuit from another test.
        set_explicit_scopes(None)
        set_read_only(False)
        set_enabled_scope_tools(None)
        permissions.set_permissions(None)

    def setup_method(self):
        self._reset_scope_globals()

    def teardown_method(self):
        self._reset_scope_globals()

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

    def _reset(self):
        tool_registry.set_enabled_tools(None)
        # main() applies the disabled-tools block list before the only-tools
        # guard exits, so a rejected run still leaves it set globally.
        tool_registry.set_disabled_tools(set())
        set_explicit_scopes(None)
        set_enabled_scope_tools(None)
        set_read_only(False)
        permissions.set_permissions(None)

    def setup_method(self):
        self._reset()

    def teardown_method(self):
        self._reset()

    @pytest.fixture(autouse=True)
    def _scrub_selection_env(self, monkeypatch):
        # main() folds these env vars into args.* before the guards run, so a
        # value leaked from the developer's shell would change WHICH guard
        # fires (e.g. WORKSPACE_MCP_DISABLED_TOOLS turns the unknown-tool error
        # into the mutual-exclusivity error). Tests that want one set it
        # explicitly after this scrub.
        for var in SELECTION_ENV_VARS:
            monkeypatch.delenv(var, raising=False)

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

    def test_combined_with_disabled_tools_exits(self, monkeypatch, capsys):
        # --only-tools derives a minimal grant from its exact list; layering
        # --disabled-tools on top would drop a tool from the surface while still
        # requesting its scope. Reject the combination instead of leaking scope.
        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "main.py",
                "--only-tools",
                "send_gmail_message",
                "manage_drive_access",
                "--disabled-tools",
                "manage_drive_access",
            ],
        )

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        assert "--only-tools cannot be combined" in capsys.readouterr().err

    def test_combined_with_disabled_tools_env_var_exits(self, monkeypatch, capsys):
        # The block list also arrives via WORKSPACE_MCP_DISABLED_TOOLS; the guard
        # must see the resolved set, not just the CLI flag.
        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setenv("WORKSPACE_MCP_DISABLED_TOOLS", "manage_drive_access")
        monkeypatch.setattr(
            sys,
            "argv",
            ["main.py", "--only-tools", "send_gmail_message"],
        )

        with pytest.raises(SystemExit) as exc:
            main.main()

        assert exc.value.code == 1
        assert "--only-tools cannot be combined" in capsys.readouterr().err

    def test_only_tools_override_does_not_leak_across_runs(self, monkeypatch):
        """A prior --only-tools run leaves an explicit-scope override on the module
        global; a later non---only-tools run in the same process must clear it
        (main() resets it), so get_scopes_for_tools() stops short-circuiting on the
        stale exact-scope grant."""
        # Simulate the leftover override from an earlier --only-tools invocation.
        set_explicit_scopes({GMAIL_SEND_SCOPE})
        assert set(get_scopes_for_tools(["drive"])) == set(BASE_SCOPES) | {
            GMAIL_SEND_SCOPE
        }

        class _StopBeforeServer(Exception):
            pass

        def _stop(*_a, **_k):
            raise _StopBeforeServer()

        monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
        monkeypatch.setattr(main, "resolve_callback_port_for_transport", lambda t: None)
        monkeypatch.setattr(main, "validate_streamable_http_auth", lambda t: None)
        # Stop main() right after the scope-setup, before it launches a server.
        monkeypatch.setattr(main, "filter_server_tools", _stop)
        monkeypatch.setattr(sys, "argv", ["main.py", "--tools", "gmail"])

        with pytest.raises(_StopBeforeServer):
            main.main()

        # The override is gone: the normal service-scope path runs instead of the
        # stale explicit grant.
        assert set(get_scopes_for_tools(["drive"])) != set(BASE_SCOPES) | {
            GMAIL_SEND_SCOPE
        }


class TestScopeDerivationEndToEnd:
    """The headline feature: the post-registration scope union.

    main() reads each selected tool's ``_required_google_scopes`` off the live
    FastMCP registry and calls ``set_explicit_scopes`` with exactly that union.
    If those decorator internals ever move (a rename of ``.fn`` or of the scope
    attribute), the union silently collapses to the empty set and the OAuth
    grant shrinks to BASE_SCOPES — consent still succeeds, then every tool call
    fails at runtime. Only a test that drives a REAL main() run can catch that.

    Runs in a subprocess (same idiom as the streamable-http decoration boot
    test) because the run registers every tool and then *removes* all but the
    selected ones from the process-global server — an irreversible mutation
    that would poison any later test using the shared registry in-process.
    """

    def test_derived_grant_is_exactly_the_selected_tools_union(self):
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        code = """
import json, os, sys
import main

def _stop(*_a, **_k):
    # Runs INSIDE main() right after the scope union is applied
    # (set_transport_mode is the next hook), before any server binds a port.
    # Dump the state and hard-exit here: main() wraps its run in a broad
    # except Exception, so an exception-based sentinel would be swallowed.
    from auth.scopes import get_scopes_for_tools
    from core.tool_registry import get_tool_components

    sys.stdout.write(json.dumps({
        "scopes": sorted(get_scopes_for_tools()),
        "surface": sorted(get_tool_components(main.server)),
    }) + "\\n")
    sys.stdout.flush()
    os._exit(0)

main.set_transport_mode = _stop
sys.argv = ["main.py", "--only-tools", "send_gmail_message", "manage_drive_access"]
main.main()
raise SystemExit("main() was not stopped by the sentinel")
"""
        env = {
            **os.environ,
            "MCP_ENABLE_OAUTH21": "false",
            "WORKSPACE_MCP_STATELESS_MODE": "false",
        }
        for var in SELECTION_ENV_VARS:
            env.pop(var, None)
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 0, (
            f"main() run failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
        payload = json.loads(result.stdout.strip().splitlines()[-1])

        # The surface is exactly the selected tools — nothing else survived.
        assert payload["surface"] == ["manage_drive_access", "send_gmail_message"]

        # The grant is exactly base identity + the two tools' declared scopes.
        # Pinned literally on purpose: if a tool's declaration changes, the
        # consent screen changes, and this failing is the announcement.
        assert payload["scopes"] == sorted(
            set(BASE_SCOPES)
            | {GMAIL_SEND_SCOPE, GMAIL_READONLY_SCOPE, DRIVE_FILE_SCOPE}
        )
