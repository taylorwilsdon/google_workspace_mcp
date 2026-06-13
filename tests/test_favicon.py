"""Tests for the favicon HTTP routes and directory resolution.

The favicon feature lets operators drop favicon.ico / favicon.png /
apple-touch-icon.png into a pre-mounted persistent-storage directory and
have the server serve them at the corresponding root paths.
"""
import os
import sys

import pytest
from starlette.responses import FileResponse
from starlette.responses import JSONResponse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("MCP_ENABLE_OAUTH21", "false")
os.environ.setdefault("WORKSPACE_MCP_STATELESS_MODE", "false")


@pytest.fixture
def clean_favicon_env(monkeypatch):
    for var in (
        "WORKSPACE_MCP_FAVICON_DIR",
        "WORKSPACE_MCP_CREDENTIALS_DIR",
        "GOOGLE_MCP_CREDENTIALS_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def test_favicon_dir_explicit_override(clean_favicon_env, monkeypatch, tmp_path):
    from core.server import _resolve_favicon_dir

    monkeypatch.setenv("WORKSPACE_MCP_FAVICON_DIR", str(tmp_path))
    assert _resolve_favicon_dir() == str(tmp_path)


def test_favicon_dir_falls_back_to_workspace_creds(
    clean_favicon_env, monkeypatch, tmp_path
):
    from core.server import _resolve_favicon_dir

    monkeypatch.setenv("WORKSPACE_MCP_CREDENTIALS_DIR", str(tmp_path))
    assert _resolve_favicon_dir() == str(tmp_path)


def test_favicon_dir_falls_back_to_google_creds(
    clean_favicon_env, monkeypatch, tmp_path
):
    from core.server import _resolve_favicon_dir

    monkeypatch.setenv("GOOGLE_MCP_CREDENTIALS_DIR", str(tmp_path))
    assert _resolve_favicon_dir() == str(tmp_path)


def test_favicon_dir_explicit_wins_over_creds(
    clean_favicon_env, monkeypatch, tmp_path
):
    from core.server import _resolve_favicon_dir

    favicon_dir = tmp_path / "favicons"
    favicon_dir.mkdir()
    creds_dir = tmp_path / "creds"
    creds_dir.mkdir()
    monkeypatch.setenv("WORKSPACE_MCP_FAVICON_DIR", str(favicon_dir))
    monkeypatch.setenv("WORKSPACE_MCP_CREDENTIALS_DIR", str(creds_dir))
    assert _resolve_favicon_dir() == str(favicon_dir)


@pytest.mark.asyncio
async def test_serve_favicon_returns_404_when_missing(
    clean_favicon_env, monkeypatch, tmp_path
):
    from core.server import _serve_favicon_file

    monkeypatch.setenv("WORKSPACE_MCP_FAVICON_DIR", str(tmp_path))
    response = await _serve_favicon_file("favicon.ico", "image/vnd.microsoft.icon")
    assert isinstance(response, JSONResponse)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_serve_favicon_returns_file_when_present(
    clean_favicon_env, monkeypatch, tmp_path
):
    from core.server import _serve_favicon_file

    monkeypatch.setenv("WORKSPACE_MCP_FAVICON_DIR", str(tmp_path))
    ico_path = tmp_path / "favicon.ico"
    ico_path.write_bytes(b"\x00\x00\x01\x00")  # minimal ICO header bytes
    response = await _serve_favicon_file("favicon.ico", "image/vnd.microsoft.icon")
    assert isinstance(response, FileResponse)
    assert response.media_type == "image/vnd.microsoft.icon"
    assert response.headers.get("cache-control") == "public, max-age=86400"
