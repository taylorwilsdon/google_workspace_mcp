"""Regression tests for Issue #835 httplib2 socket timeout."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from auth.google_auth import _build_authorized_http, get_authenticated_google_service
from auth.google_auth import WORKSPACE_MCP_TIMEOUT, get_user_info
from core import config


def test_build_authorized_http_uses_explicit_timeout():
    mock_credentials = MagicMock()
    mock_http = MagicMock()
    mock_http.redirect_codes = {300, 301, 302, 303, 307, 308}
    mock_authorized = MagicMock()

    with (
        patch(
            "auth.google_auth.httplib2.Http", return_value=mock_http
        ) as mock_http_cls,
        patch(
            "auth.google_auth.google_auth_httplib2.AuthorizedHttp",
            return_value=mock_authorized,
        ) as mock_auth_http_cls,
    ):
        result = _build_authorized_http(mock_credentials, timeout=42)

    mock_http_cls.assert_called_once_with(timeout=42)
    mock_auth_http_cls.assert_called_once_with(mock_credentials, http=mock_http)
    assert mock_http.redirect_codes == {300, 301, 302, 303, 307}
    assert result is mock_authorized


def test_build_authorized_http_defaults_to_workspace_mcp_timeout():
    mock_credentials = MagicMock()

    with (
        patch("auth.google_auth.httplib2.Http") as mock_http_cls,
        patch(
            "auth.google_auth.google_auth_httplib2.AuthorizedHttp",
        ) as mock_auth_http_cls,
    ):
        _build_authorized_http(mock_credentials)

    mock_http_cls.assert_called_once_with(timeout=WORKSPACE_MCP_TIMEOUT)
    mock_auth_http_cls.assert_called_once_with(
        mock_credentials, http=mock_http_cls.return_value
    )


def test_workspace_mcp_timeout_defaults_to_300(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_TIMEOUT", raising=False)

    assert config.WORKSPACE_MCP_TIMEOUT == 300


def test_workspace_mcp_timeout_reads_environment(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_TIMEOUT", "600")

    assert config.WORKSPACE_MCP_TIMEOUT == 600


def test_get_user_info_builds_service_with_authorized_http(monkeypatch):
    credentials = SimpleNamespace(valid=True)
    authorized_http = object()
    service = MagicMock()
    service.userinfo.return_value.get.return_value.execute.return_value = {
        "email": "user@example.com"
    }

    monkeypatch.setattr(
        "auth.google_auth._build_authorized_http", lambda creds: authorized_http
    )
    build = MagicMock(return_value=service)
    monkeypatch.setattr("auth.google_auth.build", build)

    assert get_user_info(credentials) == {"email": "user@example.com"}
    build.assert_called_once_with("oauth2", "v2", http=authorized_http)


@pytest.mark.asyncio
async def test_get_authenticated_google_service_builds_service_with_authorized_http(
    monkeypatch,
):
    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    credentials = SimpleNamespace(valid=True, id_token=None)
    authorized_http = object()
    service = MagicMock()

    monkeypatch.setattr("auth.google_auth.get_fastmcp_session_id", lambda: None)
    monkeypatch.setattr("auth.google_auth.get_fastmcp_context", None)
    monkeypatch.setattr("auth.google_auth.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "auth.google_auth.get_credentials", lambda **kwargs: credentials
    )
    monkeypatch.setattr(
        "auth.google_auth._build_authorized_http", lambda creds: authorized_http
    )
    build = MagicMock(return_value=service)
    monkeypatch.setattr("auth.google_auth.build", build)

    result, user_email = await get_authenticated_google_service(
        service_name="gmail",
        version="v1",
        tool_name="test_tool",
        user_google_email="user@example.com",
        required_scopes=["scope.a"],
    )

    assert result is service
    assert user_email == "user@example.com"
    build.assert_called_once_with("gmail", "v1", http=authorized_http)
