import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep these tests independent of a developer's local .env, which main loads.
os.environ["MCP_ENABLE_OAUTH21"] = "false"
os.environ["WORKSPACE_MCP_STATELESS_MODE"] = "false"

import main  # noqa: E402
from auth.oauth_config import reload_oauth_config  # noqa: E402


def _modes(monkeypatch, env, **kwargs):
    for name in (
        "MCP_ENABLE_OAUTH21",
        "WORKSPACE_MCP_STATELESS_MODE",
        "MCP_SINGLE_USER_MODE",
        "OAUTHLIB_INSECURE_TRANSPORT",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    reload_oauth_config()
    return {
        label: (value, state)
        for label, value, state in main.describe_mode_config(**kwargs)
    }


def test_mode_rows_use_english_labels_and_name_their_env_var(monkeypatch):
    modes = _modes(
        monkeypatch,
        {"MCP_ENABLE_OAUTH21": "true", "WORKSPACE_MCP_STATELESS_MODE": "true"},
        disabled_tools={"send_gmail_message"},
    )

    assert modes == {
        "OAuth 2.1": ("on  · MCP_ENABLE_OAUTH21", "on"),
        "Stateless": ("on  · WORKSPACE_MCP_STATELESS_MODE", "on"),
        "Single-user": ("off · MCP_SINGLE_USER_MODE", "off"),
        "Insecure transport": ("off · OAUTHLIB_INSECURE_TRANSPORT", "off"),
        "Disabled tools": (
            "send_gmail_message · WORKSPACE_MCP_DISABLED_TOOLS",
            "on",
        ),
    }


@pytest.mark.parametrize("value", ["1", "yes", "TRUE "])
def test_oauth21_row_matches_resolved_config_not_raw_env(monkeypatch, value):
    modes = _modes(monkeypatch, {"MCP_ENABLE_OAUTH21": value})

    assert modes["OAuth 2.1"][1] == "off"


def test_single_user_row_reflects_cli_flag(monkeypatch):
    modes = _modes(monkeypatch, {}, single_user=True)

    assert modes["Single-user"][1] == "on"


def test_insecure_transport_warns_for_any_value_oauthlib_honors(monkeypatch):
    modes = _modes(monkeypatch, {"OAUTHLIB_INSECURE_TRANSPORT": "false"})

    assert modes["Insecure transport"][1] == "warn"
