"""Tests for TLS configuration added to the streamable-http transport.

The SSL env-var logic in main() assembles:
    ssl_certfile = os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None
    ssl_keyfile  = os.getenv("WORKSPACE_MCP_SSL_KEYFILE")  or None
    uvicorn_ssl  = {"ssl_certfile": ..., "ssl_keyfile": ...} if both are set

These tests exercise that logic through main.server.run, patching out the
startup machinery that cannot run in a unit-test environment.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("MCP_ENABLE_OAUTH21", "false")
os.environ.setdefault("WORKSPACE_MCP_STATELESS_MODE", "false")

import main


class TestTlsEnvVarLogic:
    """Unit-level coverage of the SSL config assembly used in main()."""

    @staticmethod
    def _build_ssl_config(certfile: str | None, keyfile: str | None) -> dict | None:
        """Mirror the SSL config assembly from main(), tested in isolation."""
        ssl_certfile = certfile or None
        ssl_keyfile = keyfile or None
        if ssl_certfile and ssl_keyfile:
            return {"ssl_certfile": ssl_certfile, "ssl_keyfile": ssl_keyfile}
        return None

    def test_both_vars_set_returns_ssl_config(self):
        config = self._build_ssl_config("/certs/server.pem", "/certs/server.key")
        assert config == {
            "ssl_certfile": "/certs/server.pem",
            "ssl_keyfile": "/certs/server.key",
        }

    def test_only_certfile_set_returns_none(self):
        assert self._build_ssl_config("/certs/server.pem", None) is None

    def test_only_keyfile_set_returns_none(self):
        assert self._build_ssl_config(None, "/certs/server.key") is None

    def test_neither_set_returns_none(self):
        assert self._build_ssl_config(None, None) is None

    def test_empty_string_certfile_treated_as_unset(self):
        assert self._build_ssl_config("", "/certs/server.key") is None

    def test_empty_string_keyfile_treated_as_unset(self):
        assert self._build_ssl_config("/certs/server.pem", "") is None


class TestTlsEnvVarReading:
    """Verify that os.getenv("...") or None converts empty strings to None.

    main() uses `os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None` so that an
    explicitly set but empty env var is treated the same as absent.
    """

    def test_absent_env_var_gives_none(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_SSL_CERTFILE", raising=False)
        assert (os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None) is None

    def test_empty_env_var_gives_none(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "")
        assert (os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None) is None

    def test_set_env_var_returns_value(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "/path/to/cert.pem")
        assert (os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None) == "/path/to/cert.pem"


def test_tls_config_forwarded_to_server_run(monkeypatch):
    """server.run() receives uvicorn_config with cert/key when both env vars are set."""
    monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "/certs/server.pem")
    monkeypatch.setenv("WORKSPACE_MCP_SSL_KEYFILE", "/certs/server.key")
    monkeypatch.setenv("WORKSPACE_MCP_TRANSPORT", "streamable-http")

    run_kwargs: dict = {}

    def fake_run(**kwargs):
        run_kwargs.update(kwargs)

    monkeypatch.setattr(main.server, "run", fake_run)
    monkeypatch.setattr(main, "configure_server_for_http", lambda: None)
    monkeypatch.setattr(main, "set_transport_mode", lambda _t: None)
    monkeypatch.setattr(main, "check_credentials_directory_permissions", lambda: None)
    monkeypatch.setattr(main, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(main, "get_selected_backend", lambda: "local")
    monkeypatch.setattr(main, "wrap_server_tool_method", lambda _s: None)
    monkeypatch.setattr(main, "set_enabled_tool_names", lambda _t: None)
    monkeypatch.setattr(main, "filter_server_tools", lambda _s: 0)
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["main"])

    import socket
    from unittest.mock import MagicMock

    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda s: s
    fake_sock.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: fake_sock)

    monkeypatch.setattr(
        "importlib.import_module",
        lambda *a, **kw: MagicMock(),
    )

    main.main()

    assert run_kwargs.get("uvicorn_config") == {
        "ssl_certfile": "/certs/server.pem",
        "ssl_keyfile": "/certs/server.key",
    }


def test_no_tls_config_forwarded_when_env_vars_absent(monkeypatch):
    """server.run() receives uvicorn_config=None when SSL env vars are not set."""
    monkeypatch.delenv("WORKSPACE_MCP_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_SSL_KEYFILE", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_TRANSPORT", "streamable-http")

    run_kwargs: dict = {}

    def fake_run(**kwargs):
        run_kwargs.update(kwargs)

    monkeypatch.setattr(main.server, "run", fake_run)
    monkeypatch.setattr(main, "configure_server_for_http", lambda: None)
    monkeypatch.setattr(main, "set_transport_mode", lambda _t: None)
    monkeypatch.setattr(main, "check_credentials_directory_permissions", lambda: None)
    monkeypatch.setattr(main, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(main, "get_selected_backend", lambda: "local")
    monkeypatch.setattr(main, "wrap_server_tool_method", lambda _s: None)
    monkeypatch.setattr(main, "set_enabled_tool_names", lambda _t: None)
    monkeypatch.setattr(main, "filter_server_tools", lambda _s: 0)
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["main"])

    import socket
    from unittest.mock import MagicMock

    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda s: s
    fake_sock.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: fake_sock)

    monkeypatch.setattr(
        "importlib.import_module",
        lambda *a, **kw: MagicMock(),
    )

    main.main()

    assert run_kwargs.get("uvicorn_config") is None
