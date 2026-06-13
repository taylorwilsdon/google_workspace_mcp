"""Tests for the WORKSPACE_MCP_SERVER_ICON_* environment variables.

These cover the icon-construction helper in isolation; full server boot
with the icon attached is covered indirectly by smoke tests.
"""
import os
import sys
import importlib

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("MCP_ENABLE_OAUTH21", "false")
os.environ.setdefault("WORKSPACE_MCP_STATELESS_MODE", "false")


@pytest.fixture
def reload_server_module(monkeypatch):
    """Reload core.server with a clean env so module-level vars re-evaluate."""
    for var in (
        "WORKSPACE_MCP_SERVER_ICON_URL",
        "WORKSPACE_MCP_SERVER_ICON_MIME",
        "WORKSPACE_MCP_SERVER_ICON_SIZES",
        "WORKSPACE_MCP_SERVER_WEBSITE_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    yield


def test_no_icon_when_env_unset(reload_server_module):
    import core.server as server_mod
    importlib.reload(server_mod)
    assert server_mod._server_icons is None


def test_icon_built_from_url_only(reload_server_module, monkeypatch):
    monkeypatch.setenv(
        "WORKSPACE_MCP_SERVER_ICON_URL", "https://example.com/icon.png"
    )
    import core.server as server_mod
    importlib.reload(server_mod)

    icons = server_mod._server_icons
    assert icons is not None
    assert len(icons) == 1
    assert icons[0].src == "https://example.com/icon.png"
    assert icons[0].mimeType is None
    assert icons[0].sizes is None


def test_icon_built_with_mime_and_sizes(reload_server_module, monkeypatch):
    monkeypatch.setenv(
        "WORKSPACE_MCP_SERVER_ICON_URL", "https://example.com/icon.png"
    )
    monkeypatch.setenv("WORKSPACE_MCP_SERVER_ICON_MIME", "image/png")
    monkeypatch.setenv(
        "WORKSPACE_MCP_SERVER_ICON_SIZES", "48x48, 96x96 , 256x256"
    )
    import core.server as server_mod
    importlib.reload(server_mod)

    icons = server_mod._server_icons
    assert icons is not None and len(icons) == 1
    icon = icons[0]
    assert icon.src == "https://example.com/icon.png"
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["48x48", "96x96", "256x256"]


def test_website_url_from_env(reload_server_module, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_SERVER_WEBSITE_URL", "https://example.com")
    import core.server as server_mod
    importlib.reload(server_mod)

    assert server_mod._server_website_url == "https://example.com"


def test_blank_env_treated_as_unset(reload_server_module, monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_SERVER_ICON_URL", "   ")
    monkeypatch.setenv("WORKSPACE_MCP_SERVER_WEBSITE_URL", "  ")
    import core.server as server_mod
    importlib.reload(server_mod)

    assert server_mod._server_icons is None
    assert server_mod._server_website_url is None


def test_data_uri_icon_accepted(reload_server_module, monkeypatch):
    monkeypatch.setenv(
        "WORKSPACE_MCP_SERVER_ICON_URL",
        "data:image/png;base64,iVBORw0KGgo=",
    )
    import core.server as server_mod
    importlib.reload(server_mod)

    icons = server_mod._server_icons
    assert icons is not None and len(icons) == 1
    assert icons[0].src.startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com/icon.png",  # plain http
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/icon.png",
        "example.com/icon.png",
    ],
)
def test_icon_url_rejects_unsupported_schemes(
    reload_server_module, monkeypatch, bad_url
):
    monkeypatch.setenv("WORKSPACE_MCP_SERVER_ICON_URL", bad_url)
    import core.server as server_mod
    importlib.reload(server_mod)

    assert server_mod._server_icons is None


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://example.com",
        "javascript:alert(1)",
        "example.com",
    ],
)
def test_website_url_rejects_non_https(reload_server_module, monkeypatch, bad_url):
    monkeypatch.setenv("WORKSPACE_MCP_SERVER_WEBSITE_URL", bad_url)
    import core.server as server_mod
    importlib.reload(server_mod)

    assert server_mod._server_website_url is None
