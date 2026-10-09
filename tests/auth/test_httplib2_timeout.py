"""Regression tests for Issue #835 httplib2 socket timeout."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.auth.credentials import AnonymousCredentials

import auth.google_auth as google_auth
from auth.google_auth import (
    _build_authorized_http,
    build_google_service,
    get_authenticated_google_service,
    get_google_api_timeout,
    get_user_info,
    recycling,
)


_TIMEOUT_ENV = "WORKSPACE_MCP_GOOGLE_API_TIMEOUT_SECONDS"


@pytest.mark.parametrize(("raw", "expected"), [(None, 60), ("45", 45)])
def test_build_authorized_http_uses_configured_timeout(monkeypatch, raw, expected):
    # Set after import: the timeout is read per connection, not at import.
    if raw is None:
        monkeypatch.delenv(_TIMEOUT_ENV, raising=False)
    else:
        monkeypatch.setenv(_TIMEOUT_ENV, raw)
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
        result = _build_authorized_http(mock_credentials)

    mock_http_cls.assert_called_once_with(timeout=expected)
    mock_auth_http_cls.assert_called_once_with(mock_credentials, http=mock_http)
    assert mock_http.redirect_codes == {300, 301, 302, 303, 307}
    assert result is mock_authorized


def test_google_api_timeout_follows_env(monkeypatch):
    monkeypatch.setenv(_TIMEOUT_ENV, " 120 ")
    assert get_google_api_timeout() == 120


@pytest.mark.parametrize("raw", ["0", "-5", "abc", "1.5"])
def test_google_api_timeout_rejects_invalid_values(monkeypatch, raw):
    monkeypatch.setenv(_TIMEOUT_ENV, raw)
    with pytest.raises(ValueError, match=_TIMEOUT_ENV):
        get_google_api_timeout()


def test_recycled_connection_is_reused():
    service = build_google_service("gmail", "v1", AnonymousCredentials())
    pooled_http = service._http.http

    with recycling(service):
        pass

    assert _build_authorized_http(AnonymousCredentials()).http is pooled_http
    assert not google_auth._idle_http


def test_stale_connection_is_closed_and_replaced(monkeypatch):
    stale_http = MagicMock()
    google_auth._idle_http.append((0.0, stale_http))
    monkeypatch.setattr(
        google_auth.time, "monotonic", lambda: google_auth._HTTP_MAX_IDLE_SECONDS
    )

    http = google_auth._acquire_http()

    stale_http.close.assert_called_once_with()
    assert http is not stale_http
    assert not google_auth._idle_http


def test_failed_block_closes_instead_of_recycling():
    service = MagicMock()

    with pytest.raises(ValueError):
        with recycling(service):
            raise ValueError("boom")

    service.close.assert_called_once_with()
    assert not google_auth._idle_http


def test_get_user_info_keeps_short_timeout(monkeypatch):
    monkeypatch.setenv(_TIMEOUT_ENV, "300")
    credentials = SimpleNamespace(valid=True)
    service = MagicMock()
    service.userinfo.return_value.get.return_value.execute.return_value = {
        "email": "user@example.com"
    }
    build = MagicMock(return_value=service)
    monkeypatch.setattr("auth.google_auth.build", build)

    assert get_user_info(credentials) == {"email": "user@example.com"}
    authorized_http = build.call_args.kwargs["http"]
    assert authorized_http.credentials is credentials
    assert authorized_http.http.timeout == 30


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
        allow_auth_flow=True,
    )

    assert result is service
    assert user_email == "user@example.com"
    build.assert_called_once_with("gmail", "v1", http=authorized_http)
