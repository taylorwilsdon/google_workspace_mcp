"""
Tests for auth.port_resolver -- late-binding the OAuth callback port across
preferred + fallback range. Covers preferred-when-free, fallback-when-held,
raise-when-all-held, OAuthConfig redirect_uri integration, and PEP 562
lazy-evaluation of core.config.WORKSPACE_MCP_PORT.
"""

import os
import socket
import sys
from contextlib import contextmanager

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_PORT", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_PORT_FALLBACK_COUNT", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_RESOLVED_PORT", raising=False)


@contextmanager
def _hold_port(port: int, host: str = "127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    s.bind((host, port))
    s.listen(1)
    try:
        yield port
    finally:
        s.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _import_fresh(modname: str):
    if modname in sys.modules:
        del sys.modules[modname]
    return __import__(modname, fromlist=["*"])


def test_resolves_preferred_when_free():
    pr = _import_fresh("auth.port_resolver")
    p = _free_port()
    bound = pr.resolve_port(preferred=p, fallback_count=2, host="127.0.0.1")
    assert bound == p
    assert os.environ["WORKSPACE_MCP_PORT"] == str(p)
    assert os.environ["WORKSPACE_MCP_RESOLVED_PORT"] == "1"


def test_falls_back_when_preferred_in_use():
    pr = _import_fresh("auth.port_resolver")
    p = _free_port()
    with _hold_port(p):
        bound = pr.resolve_port(preferred=p, fallback_count=4, host="127.0.0.1")
    assert bound != p, "Resolver should have skipped the held port"
    assert bound > p, "Fallback should be a higher port"
    assert os.environ["WORKSPACE_MCP_PORT"] == str(bound)


def test_raises_when_all_ports_held():
    pr = _import_fresh("auth.port_resolver")
    p = _free_port()
    sockets = []
    try:
        for offset in range(3):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            s.bind(("127.0.0.1", p + offset))
            s.listen(1)
            sockets.append(s)
        with pytest.raises(pr.NoAvailablePortError):
            pr.resolve_port(preferred=p, fallback_count=2, host="127.0.0.1")
    finally:
        for s in sockets:
            s.close()


def test_oauthconfig_reload_picks_up_resolved_port():
    """After resolve_port + reload_oauth_config, redirect_uri reflects bound port."""
    pr = _import_fresh("auth.port_resolver")
    p = _free_port()
    with _hold_port(p):
        bound = pr.resolve_port(preferred=p, fallback_count=4, host="127.0.0.1")
    oauth_config = _import_fresh("auth.oauth_config")
    config = oauth_config.reload_oauth_config()
    assert str(bound) in config.redirect_uri, (
        f"redirect_uri {config.redirect_uri!r} must reflect bound port {bound}"
    )


def test_raises_on_malformed_port_env(monkeypatch):
    """Non-numeric WORKSPACE_MCP_PORT raises PortConfigError with actionable message."""
    pr = _import_fresh("auth.port_resolver")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "not_a_number")
    with pytest.raises(pr.PortConfigError, match="WORKSPACE_MCP_PORT.*not_a_number"):
        pr.resolve_port()


def test_raises_on_malformed_fallback_count_env(monkeypatch):
    """Non-numeric WORKSPACE_MCP_PORT_FALLBACK_COUNT raises PortConfigError."""
    pr = _import_fresh("auth.port_resolver")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8000")
    monkeypatch.setenv("WORKSPACE_MCP_PORT_FALLBACK_COUNT", "abc")
    with pytest.raises(
        pr.PortConfigError, match="WORKSPACE_MCP_PORT_FALLBACK_COUNT.*abc"
    ):
        pr.resolve_port()


def test_lazy_workspace_mcp_port_via_pep562(monkeypatch):
    """core.config.WORKSPACE_MCP_PORT must read env at access time, not import time."""
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8009")
    cfg = _import_fresh("core.config")
    assert cfg.WORKSPACE_MCP_PORT == 8009
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8011")
    assert cfg.WORKSPACE_MCP_PORT == 8011


def test_lazy_workspace_mcp_port_prefers_resolved_workspace_env(monkeypatch):
    """WORKSPACE_MCP_PORT is authoritative after the resolver mutates it."""
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8011")
    monkeypatch.setenv("WORKSPACE_MCP_RESOLVED_PORT", "1")
    cfg = _import_fresh("core.config")
    assert cfg.WORKSPACE_MCP_PORT == 8011


def test_lazy_workspace_mcp_port_preserves_port_precedence_without_resolver(
    monkeypatch,
):
    """HTTP/OAuth 2.1 mode keeps existing PORT precedence when no stdio resolver ran."""
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8011")
    monkeypatch.delenv("WORKSPACE_MCP_RESOLVED_PORT", raising=False)
    cfg = _import_fresh("core.config")
    assert cfg.WORKSPACE_MCP_PORT == 8000


def test_allow_fallback_false_probes_only_preferred():
    """A pinned redirect URI means neighbouring ports are useless -- don't try them."""
    pr = _import_fresh("auth.port_resolver")
    p = _free_port()
    with _hold_port(p), pytest.raises(pr.NoAvailablePortError):
        pr.resolve_port(
            preferred=p, fallback_count=4, host="127.0.0.1", allow_fallback=False
        )


def test_allow_fallback_false_still_binds_free_preferred():
    pr = _import_fresh("auth.port_resolver")
    p = _free_port()
    bound = pr.resolve_port(
        preferred=p, fallback_count=4, host="127.0.0.1", allow_fallback=False
    )
    assert bound == p


def test_stdio_resolver_survives_exhausted_range(monkeypatch):
    """A busy callback port must not take down the whole stdio server."""
    import main as main_module

    p = _free_port()
    monkeypatch.setenv("WORKSPACE_MCP_PORT", str(p))
    monkeypatch.setenv("WORKSPACE_MCP_PORT_FALLBACK_COUNT", "0")
    monkeypatch.setenv("WORKSPACE_MCP_HOST", "127.0.0.1")
    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)

    with _hold_port(p):
        main_module.resolve_stdio_callback_port()

    assert os.environ["WORKSPACE_MCP_PORT"] == str(p)
    assert "WORKSPACE_MCP_RESOLVED_PORT" not in os.environ


def test_stdio_resolver_pins_port_when_redirect_uri_is_explicit(monkeypatch):
    """With GOOGLE_OAUTH_REDIRECT_URI set, the resolver must not wander off-port."""
    import main as main_module

    p = _free_port()
    monkeypatch.setenv("WORKSPACE_MCP_PORT", str(p))
    monkeypatch.setenv("WORKSPACE_MCP_PORT_FALLBACK_COUNT", "4")
    monkeypatch.setenv("WORKSPACE_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI", f"http://localhost:{p}/oauth2callback"
    )

    with _hold_port(p):
        main_module.resolve_stdio_callback_port()

    # Fallback would have picked p+1; pinned mode leaves the port untouched.
    assert os.environ["WORKSPACE_MCP_PORT"] == str(p)
    assert "WORKSPACE_MCP_RESOLVED_PORT" not in os.environ
