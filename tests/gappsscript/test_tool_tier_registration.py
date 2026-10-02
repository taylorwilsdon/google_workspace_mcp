"""Every appscript tool must be listed in core/tool_tiers.yaml, or
core/tool_registry.py:filter_server_tools silently prunes it at startup."""

import os
import sys

import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import gappsscript.apps_script_tools  # noqa: F401  (registers tools as an import side effect)
from auth.permissions import get_scopes_for_permission
from auth.scopes import get_all_read_only_scopes
from core.server import server
from core.tool_registry import get_tool_components


def test_every_gappsscript_tool_is_listed_in_tool_tiers_yaml():
    tool_components = get_tool_components(server)

    gappsscript_tool_names = set()
    for name, component in tool_components.items():
        func = getattr(component, "fn", component)
        if getattr(func, "__module__", "") == "gappsscript.apps_script_tools":
            gappsscript_tool_names.add(name)

    assert gappsscript_tool_names, (
        "No gappsscript tools found registered on the shared server - "
        "this test's own detection is broken, not a real pass."
    )

    yaml_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "core", "tool_tiers.yaml"
    )
    with open(yaml_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    listed = set()
    for tier in ("core", "extended", "complete"):
        listed.update(config.get("appscript", {}).get(tier) or [])

    missing = gappsscript_tool_names - listed
    assert not missing, (
        "Tool(s) registered in gappsscript/apps_script_tools.py but missing from "
        "core/tool_tiers.yaml's appscript section (they will be silently pruned "
        f"at startup under any TOOL_TIER/TOOLS filtering): {sorted(missing)}"
    )


def _required_scopes(tool_name):
    component = get_tool_components(server)[tool_name]
    func = getattr(component, "fn", component)
    return set(getattr(func, "_required_google_scopes", []))


def test_script_read_tools_survive_read_only_filtering():
    allowed = set(get_all_read_only_scopes())
    read_tools = {
        "get_script_project",
        "list_script_deployments",
        "get_script_version",
        "get_script_activity",
        "generate_trigger_code",
    }

    for tool_name in read_tools:
        assert _required_scopes(tool_name) <= allowed, tool_name


def test_script_mutation_tools_survive_full_permission_filtering():
    allowed = set(get_scopes_for_permission("appscript", "full"))
    mutation_tools = {
        "manage_script_project",
        "manage_script_content",
        "manage_deployment",
        "manage_script_version",
        "manage_script_trigger",
        "run_script_function",
    }

    for tool_name in mutation_tools:
        assert _required_scopes(tool_name) <= allowed, tool_name
