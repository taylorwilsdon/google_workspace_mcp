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
        """Both cert and key present returns a uvicorn-compatible SSL dict."""
        config = self._build_ssl_config("/certs/server.pem", "/certs/server.key")
        assert config == {
            "ssl_certfile": "/certs/server.pem",
            "ssl_keyfile": "/certs/server.key",
        }

    def test_only_certfile_set_returns_none(self):
        """Cert without key returns None — partial config is not usable."""
        assert self._build_ssl_config("/certs/server.pem", None) is None

    def test_only_keyfile_set_returns_none(self):
        """Key without cert returns None — partial config is not usable."""
        assert self._build_ssl_config(None, "/certs/server.key") is None

    def test_neither_set_returns_none(self):
        """Neither var set returns None — plaintext HTTP is the default."""
        assert self._build_ssl_config(None, None) is None

    def test_empty_string_certfile_treated_as_unset(self):
        """Empty-string certfile is coerced to None by the `or None` guard."""
        assert self._build_ssl_config("", "/certs/server.key") is None

    def test_empty_string_keyfile_treated_as_unset(self):
        """Empty-string keyfile is coerced to None by the `or None` guard."""
        assert self._build_ssl_config("/certs/server.pem", "") is None


class TestTlsEnvVarReading:
    """Verify that os.getenv("...") or None converts empty strings to None.

    main() uses `os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None` so that an
    explicitly set but empty env var is treated the same as absent.
    """

    def test_absent_env_var_gives_none(self, monkeypatch):
        """Unset env var returns None after the `or None` coercion."""
        monkeypatch.delenv("WORKSPACE_MCP_SSL_CERTFILE", raising=False)
        assert (os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None) is None

    def test_empty_env_var_gives_none(self, monkeypatch):
        """Empty-string env var is treated the same as absent."""
        monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "")
        assert (os.getenv("WORKSPACE_MCP_SSL_CERTFILE") or None) is None

    def test_set_env_var_returns_value(self, monkeypatch):
        """A non-empty env var passes through unchanged."""
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


# ---------------------------------------------------------------------------
# Partial TLS configuration — exactly one env var set must be a startup error.
# ---------------------------------------------------------------------------


def _patch_main_for_http(monkeypatch) -> None:
    """Patch out main() side-effects so we can reach the TLS validation step."""
    from unittest.mock import MagicMock
    import socket

    monkeypatch.setenv("WORKSPACE_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setattr(main.server, "run", lambda **kw: None)
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
    monkeypatch.setattr("importlib.import_module", lambda *a, **kw: MagicMock())

    fake_sock = MagicMock()
    fake_sock.__enter__ = lambda s: s
    fake_sock.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: fake_sock)


def test_certfile_only_exits_with_error(monkeypatch, capsys):
    """Setting only WORKSPACE_MCP_SSL_CERTFILE must abort startup with a clear error."""
    monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "/certs/server.pem")
    monkeypatch.delenv("WORKSPACE_MCP_SSL_KEYFILE", raising=False)
    _patch_main_for_http(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "WORKSPACE_MCP_SSL_KEYFILE" in stderr
    assert "WORKSPACE_MCP_SSL_CERTFILE" in stderr


def test_keyfile_only_exits_with_error(monkeypatch, capsys):
    """Setting only WORKSPACE_MCP_SSL_KEYFILE must abort startup with a clear error."""
    monkeypatch.delenv("WORKSPACE_MCP_SSL_CERTFILE", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_SSL_KEYFILE", "/certs/server.key")
    _patch_main_for_http(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "WORKSPACE_MCP_SSL_CERTFILE" in stderr
    assert "WORKSPACE_MCP_SSL_KEYFILE" in stderr


def test_partial_tls_error_fires_before_port_binding(monkeypatch, capsys):
    """Partial TLS config is rejected before any port bind attempt."""
    monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "/certs/server.pem")
    monkeypatch.delenv("WORKSPACE_MCP_SSL_KEYFILE", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr("sys.argv", ["main"])

    bind_called = []

    import socket as _socket
    from unittest.mock import MagicMock

    real_socket = _socket.socket

    def sentinel_socket(*a, **kw):
        bind_called.append(True)
        return real_socket(*a, **kw)

    monkeypatch.setattr(_socket, "socket", sentinel_socket)

    with pytest.raises(SystemExit) as exc_info:
        main.main()

    assert exc_info.value.code == 1
    assert not bind_called, "Port binding must not occur when TLS config is partial"


def test_tls_enabled_uses_https_in_display_url(monkeypatch, capsys, caplog):
    """When both SSL env vars are set, the advertised URL scheme is https://."""
    monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "/certs/server.pem")
    monkeypatch.setenv("WORKSPACE_MCP_SSL_KEYFILE", "/certs/server.key")
    monkeypatch.delenv("WORKSPACE_MCP_BASE_URI", raising=False)
    monkeypatch.delenv("WORKSPACE_EXTERNAL_URL", raising=False)

    run_kwargs: dict = {}

    def fake_run(**kwargs):
        run_kwargs.update(kwargs)

    _patch_main_for_http(monkeypatch)
    monkeypatch.setattr(main.server, "run", fake_run)

    import logging
    with caplog.at_level(logging.DEBUG):
        main.main()

    assert run_kwargs.get("uvicorn_config") == {
        "ssl_certfile": "/certs/server.pem",
        "ssl_keyfile": "/certs/server.key",
    }

    # safe_print() routes to logger.debug in non-TTY environments (tests),
    # so combine capsys output with caplog to cover all rendering paths.
    captured = capsys.readouterr()
    all_text = f"{captured.out}\n{captured.err}\n{caplog.text}"
    assert "https://" in all_text, (
        f"Expected 'https://' in startup output; got: {all_text!r}"
    )


class TestDefaultSchemeSelection:
    """_default_scheme in main() must be 'https' only for streamable-http + TLS."""

    @staticmethod
    def _scheme(tls_enabled: bool, transport: str) -> str:
        """Mirror the _default_scheme logic from main()."""
        return "https" if (tls_enabled and transport == "streamable-http") else "http"

    def test_streamable_http_with_tls_uses_https(self):
        """TLS + streamable-http → https:// scheme."""
        assert self._scheme(True, "streamable-http") == "https"

    def test_stdio_with_tls_uses_http(self):
        """TLS env vars must not make the stdio callback URL advertise https://."""
        assert self._scheme(True, "stdio") == "http"

    def test_streamable_http_without_tls_uses_http(self):
        """No TLS → http:// scheme regardless of transport."""
        assert self._scheme(False, "streamable-http") == "http"

    def test_stdio_without_tls_uses_http(self):
        """stdio without TLS → http:// scheme."""
        assert self._scheme(False, "stdio") == "http"


def test_stdio_with_tls_env_vars_does_not_advertise_https(monkeypatch, capsys):
    """When TLS env vars are set but transport is stdio, startup must not advertise
    https:// — the stdio callback server serves plain HTTP."""
    monkeypatch.setenv("WORKSPACE_MCP_SSL_CERTFILE", "/certs/server.pem")
    monkeypatch.setenv("WORKSPACE_MCP_SSL_KEYFILE", "/certs/server.key")
    monkeypatch.delenv("WORKSPACE_MCP_BASE_URI", raising=False)
    monkeypatch.delenv("WORKSPACE_EXTERNAL_URL", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_TRANSPORT", "stdio")
    monkeypatch.setattr("sys.argv", ["main"])
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr(main, "set_transport_mode", lambda _t: None)
    monkeypatch.setattr(main, "check_credentials_directory_permissions", lambda: None)
    monkeypatch.setattr(main, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(main, "get_selected_backend", lambda: "local")
    monkeypatch.setattr(main, "wrap_server_tool_method", lambda _s: None)
    monkeypatch.setattr(main, "set_enabled_tool_names", lambda _t: None)
    monkeypatch.setattr(main, "filter_server_tools", lambda _s: 0)
    monkeypatch.setattr("importlib.import_module", lambda *a, **kw: __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock())
    monkeypatch.setattr(main.server, "run", lambda **kw: None)

    main.main()

    captured = capsys.readouterr()
    all_text = f"{captured.out}\n{captured.err}"
    assert "https://" not in all_text, (
        f"stdio path must not advertise https:// when the callback server is plain HTTP; got: {all_text!r}"
    )
