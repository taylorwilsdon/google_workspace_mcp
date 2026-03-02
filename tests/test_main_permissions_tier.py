import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main


def test_resolve_permissions_mode_selection_without_tier():
    services = ["gmail", "drive"]
    resolved_services, tier_tool_filter = main.resolve_permissions_mode_selection(
        services, None
    )
    assert resolved_services == services
    assert tier_tool_filter is None


def test_resolve_permissions_mode_selection_with_tier_filters_services(monkeypatch):
    def fake_resolve_tools_from_tier(tier, services):
        assert tier == "core"
        assert services == ["gmail", "drive", "slides"]
        return ["search_gmail_messages"], ["gmail"]

    monkeypatch.setattr(main, "resolve_tools_from_tier", fake_resolve_tools_from_tier)

    resolved_services, tier_tool_filter = main.resolve_permissions_mode_selection(
        ["gmail", "drive", "slides"], "core"
    )
    assert resolved_services == ["gmail"]
    assert tier_tool_filter == {"search_gmail_messages"}


def test_narrow_permissions_to_services_keeps_selected_order():
    permissions = {"drive": "full", "gmail": "readonly", "calendar": "readonly"}
    narrowed = main.narrow_permissions_to_services(permissions, ["gmail", "drive"])
    assert narrowed == {"gmail": "readonly", "drive": "full"}


def test_narrow_permissions_to_services_drops_non_selected_services():
    permissions = {"gmail": "send", "drive": "full"}
    narrowed = main.narrow_permissions_to_services(permissions, ["gmail"])
    assert narrowed == {"gmail": "send"}


def test_permissions_and_tools_flags_are_rejected(monkeypatch, capsys):
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--permissions", "gmail:readonly", "--tools", "gmail"],
    )

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "--permissions and --tools cannot be combined" in captured.err
