import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure env vars are clean before each test."""
    monkeypatch.delenv("WORKSPACE_MCP_STATELESS_HTTP", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_STATELESS_MODE", raising=False)
    monkeypatch.delenv("MCP_ENABLE_OAUTH21", raising=False)


def _reload_and_get():
    """Reload oauth_config and return is_stateless_http result."""
    from auth.oauth_config import reload_oauth_config, is_stateless_http

    reload_oauth_config()
    return is_stateless_http()


def test_defaults_to_stateless_mode_when_unset(monkeypatch):
    """Without WORKSPACE_MCP_STATELESS_HTTP, falls back to is_stateless_mode()."""
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "true")
    assert _reload_and_get() is True


def test_defaults_to_false_when_both_unset():
    """Both env vars unset → stateless_mode defaults to False → stateless_http False."""
    assert _reload_and_get() is False


def test_explicit_false_overrides_stateless_mode(monkeypatch):
    """WORKSPACE_MCP_STATELESS_HTTP=false overrides STATELESS_MODE=true."""
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "true")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "false")
    assert _reload_and_get() is False


def test_explicit_true(monkeypatch):
    """WORKSPACE_MCP_STATELESS_HTTP=true → True regardless of stateless_mode."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "false")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "true")
    assert _reload_and_get() is True


def test_explicit_1(monkeypatch):
    """WORKSPACE_MCP_STATELESS_HTTP=1 is truthy."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "1")
    assert _reload_and_get() is True


def test_explicit_yes(monkeypatch):
    """WORKSPACE_MCP_STATELESS_HTTP=yes is truthy."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "yes")
    assert _reload_and_get() is True


def test_explicit_on(monkeypatch):
    """WORKSPACE_MCP_STATELESS_HTTP=on is truthy."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "on")
    assert _reload_and_get() is True


def test_explicit_no_is_falsy(monkeypatch):
    """WORKSPACE_MCP_STATELESS_HTTP=no is falsy (not in truthy set)."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "no")
    assert _reload_and_get() is False


def test_whitespace_trimmed(monkeypatch):
    """Leading/trailing whitespace is trimmed."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "  true  ")
    assert _reload_and_get() is True


def test_case_insensitive(monkeypatch):
    """Value matching is case-insensitive."""
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_HTTP", "TRUE")
    assert _reload_and_get() is True
