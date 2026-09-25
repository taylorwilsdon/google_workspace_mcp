"""WORKSPACE_MCP_NO_BROWSER keeps the OAuth consent URL out of the local default browser."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import auth.google_auth as google_auth

AUTH_URL = "https://accounts.google.com/o/oauth2/auth?state=oauth-state-1"


@pytest.fixture(autouse=True)
def _legacy_stdio(monkeypatch):
    monkeypatch.setattr(google_auth, "get_transport_mode", lambda: "stdio")
    monkeypatch.setattr(google_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.delenv("WORKSPACE_MCP_NO_BROWSER", raising=False)
    monkeypatch.setenv("OAUTHLIB_INSECURE_TRANSPORT", "1")


def test_legacy_stdio_opens_browser_by_default():
    assert google_auth._should_auto_open_browser() is True


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " True "])
def test_no_browser_env_disables_auto_open(monkeypatch, value):
    monkeypatch.setenv("WORKSPACE_MCP_NO_BROWSER", value)
    assert google_auth._should_auto_open_browser() is False


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_no_browser_env_falsy_values_keep_auto_open(monkeypatch, value):
    monkeypatch.setenv("WORKSPACE_MCP_NO_BROWSER", value)
    assert google_auth._should_auto_open_browser() is True


def test_never_opens_outside_legacy_stdio(monkeypatch):
    monkeypatch.setattr(google_auth, "get_transport_mode", lambda: "streamable-http")
    assert google_auth._should_auto_open_browser() is False

    monkeypatch.setattr(google_auth, "get_transport_mode", lambda: "stdio")
    monkeypatch.setattr(google_auth, "is_oauth21_enabled", lambda: True)
    assert google_auth._should_auto_open_browser() is False


def _fake_flow():
    flow = MagicMock()
    flow.authorization_url.return_value = (AUTH_URL, "oauth-state-1")
    flow.code_verifier = "verifier"
    return flow


async def _run_start_auth_flow():
    with (
        patch("auth.google_auth.create_oauth_flow", return_value=_fake_flow()),
        patch("auth.google_auth.get_current_scopes", return_value=["openid"]),
        patch("auth.google_auth.get_fastmcp_session_id", return_value=None),
        patch(
            "auth.google_auth._determine_oauth_prompt",
            new=AsyncMock(return_value="consent"),
        ),
        patch("auth.google_auth.get_oauth21_session_store", return_value=MagicMock()),
        patch("auth.google_auth.webbrowser.open", return_value=True) as mock_open,
    ):
        result = await google_auth.start_auth_flow(
            user_google_email="user@example.com",
            service_name="sheets",
            redirect_uri="http://localhost:8000/oauth2callback",
        )
    return result, mock_open


@pytest.mark.asyncio
async def test_start_auth_flow_skips_browser_when_env_set(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_NO_BROWSER", "true")
    result, mock_open = await _run_start_auth_flow()

    mock_open.assert_not_called()
    assert AUTH_URL in result
    assert "automatically opened" not in result


@pytest.mark.asyncio
async def test_start_auth_flow_opens_browser_by_default():
    result, mock_open = await _run_start_auth_flow()

    mock_open.assert_called_once_with(AUTH_URL)
    assert AUTH_URL in result
    assert "automatically opened" in result
