"""Tests for JWT signing key length validation in configure_server_for_http.

The MR changed the enforcement from a warning (< 12 chars) to a ValueError
(< 32 chars) so that weak keys are rejected at startup rather than silently
accepted.
"""

import importlib

import pytest


def _setup_oauth21_env(monkeypatch, *, jwt_key: str | None = None):
    """Set the minimum env vars needed to reach the JWT key validation path."""
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "dummy-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8000")
    monkeypatch.delenv("WORKSPACE_EXTERNAL_URL", raising=False)
    monkeypatch.setenv("EXTERNAL_OAUTH21_PROVIDER", "false")
    if jwt_key is not None:
        monkeypatch.setenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", jwt_key)
    else:
        monkeypatch.delenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", raising=False)


def test_jwt_key_shorter_than_32_chars_raises_value_error(monkeypatch):
    _setup_oauth21_env(monkeypatch, jwt_key="tooshort")  # 8 chars

    import core.server as core_server
    from auth.oauth_config import reload_oauth_config

    reload_oauth_config()
    core_server = importlib.reload(core_server)
    core_server.set_transport_mode("streamable-http")

    with pytest.raises(ValueError, match="at least 32 characters"):
        core_server.configure_server_for_http()


def test_jwt_key_exactly_32_chars_is_accepted(monkeypatch):
    key_32 = "a" * 32
    _setup_oauth21_env(monkeypatch, jwt_key=key_32)

    import core.server as core_server
    from auth.oauth_config import reload_oauth_config

    reload_oauth_config()
    core_server = importlib.reload(core_server)
    core_server.set_transport_mode("streamable-http")

    # Should not raise — no assertion needed beyond the absence of an exception.
    core_server.configure_server_for_http()


def test_jwt_key_31_chars_raises_value_error(monkeypatch):
    _setup_oauth21_env(monkeypatch, jwt_key="b" * 31)

    import core.server as core_server
    from auth.oauth_config import reload_oauth_config

    reload_oauth_config()
    core_server = importlib.reload(core_server)
    core_server.set_transport_mode("streamable-http")

    with pytest.raises(ValueError, match="at least 32 characters"):
        core_server.configure_server_for_http()
