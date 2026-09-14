"""Tests for the opt-in 'openclaw' deployment preset (issue #1104).

Covers preset resolution (CLI flag vs. WORKSPACE_MCP_DEPLOYMENT_PRESET env
var) and its effect on transport/host selection, without touching the
network or actually starting a server.
"""

import pytest

from main import apply_deployment_preset, resolve_deployment_preset


class TestResolveDeploymentPreset:
    def test_no_cli_flag_no_env_returns_none(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_DEPLOYMENT_PRESET", raising=False)
        assert resolve_deployment_preset(None) is None

    def test_cli_flag_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DEPLOYMENT_PRESET", "openclaw")
        assert resolve_deployment_preset("openclaw") == "openclaw"

    def test_env_var_is_used_when_no_cli_flag(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DEPLOYMENT_PRESET", "openclaw")
        assert resolve_deployment_preset(None) == "openclaw"

    def test_env_var_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DEPLOYMENT_PRESET", "OpenClaw")
        assert resolve_deployment_preset(None) == "openclaw"

    def test_invalid_env_var_raises(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DEPLOYMENT_PRESET", "not-a-preset")
        with pytest.raises(ValueError, match="not-a-preset"):
            resolve_deployment_preset(None)

    def test_empty_env_var_returns_none(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_DEPLOYMENT_PRESET", "")
        assert resolve_deployment_preset(None) is None


class TestApplyDeploymentPreset:
    def test_no_preset_leaves_transport_unchanged(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)
        assert apply_deployment_preset(None, "stdio") == "stdio"
        # No preset must never touch WORKSPACE_MCP_HOST.
        import os

        assert "WORKSPACE_MCP_HOST" not in os.environ

    def test_openclaw_forces_streamable_http_from_stdio(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)
        assert apply_deployment_preset("openclaw", "stdio") == "streamable-http"

    def test_openclaw_is_idempotent_when_already_http(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)
        assert (
            apply_deployment_preset("openclaw", "streamable-http") == "streamable-http"
        )

    def test_openclaw_binds_loopback_by_default(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)
        import os

        apply_deployment_preset("openclaw", "stdio")
        assert os.environ["WORKSPACE_MCP_HOST"] == "127.0.0.1"

    def test_openclaw_respects_explicit_host_override(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_HOST", "0.0.0.0")
        import os

        apply_deployment_preset("openclaw", "stdio")
        assert os.environ["WORKSPACE_MCP_HOST"] == "0.0.0.0"
