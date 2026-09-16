"""Tests for the /status endpoint (issue #1104).

Must report non-secret deployment status only: service running, whether an
account is authorized, whether refresh capability is available, and which
services/scopes are enabled -- never token or refresh-token material.
"""

import json

import pytest
from starlette.requests import Request

from core.server import deployment_status


def _build_request() -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/status",
        "raw_path": b"/status",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8000),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


class _FakeCredentials:
    def __init__(self, refresh_token):
        self.refresh_token = refresh_token
        self.token = "super-secret-access-token"


class _FakeStore:
    def __init__(self, users, credentials_by_user):
        self._users = users
        self._credentials_by_user = credentials_by_user

    def list_users(self):
        return self._users

    def get_credential(self, user_email):
        return self._credentials_by_user.get(user_email)


@pytest.mark.asyncio
async def test_status_reports_authorized_and_refresh_capable(monkeypatch):
    store = _FakeStore(
        users=["user@example.com"],
        credentials_by_user={
            "user@example.com": _FakeCredentials(refresh_token="a-refresh-token")
        },
    )
    monkeypatch.setattr("auth.credential_store.get_credential_store", lambda: store)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: False)
    monkeypatch.setattr("auth.oauth_config.is_service_account_enabled", lambda: False)

    response = await deployment_status(_build_request())
    body = json.loads(response.body)

    assert body["status"] == "running"
    assert body["account_authorized"] is True
    assert body["refresh_capable"] is True
    assert "enabled_scopes" in body
    assert "enabled_services" in body


@pytest.mark.asyncio
async def test_status_reports_unauthorized_when_no_users(monkeypatch):
    store = _FakeStore(users=[], credentials_by_user={})
    monkeypatch.setattr("auth.credential_store.get_credential_store", lambda: store)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: False)
    monkeypatch.setattr("auth.oauth_config.is_service_account_enabled", lambda: False)

    response = await deployment_status(_build_request())
    body = json.loads(response.body)

    assert body["account_authorized"] is False
    assert body["refresh_capable"] is False


@pytest.mark.asyncio
async def test_status_never_leaks_token_material(monkeypatch):
    store = _FakeStore(
        users=["user@example.com"],
        credentials_by_user={
            "user@example.com": _FakeCredentials(refresh_token="a-refresh-token")
        },
    )
    monkeypatch.setattr("auth.credential_store.get_credential_store", lambda: store)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: False)
    monkeypatch.setattr("auth.oauth_config.is_service_account_enabled", lambda: False)

    response = await deployment_status(_build_request())
    raw = response.body.decode()

    assert "super-secret-access-token" not in raw
    assert "a-refresh-token" not in raw


@pytest.mark.asyncio
async def test_status_reports_stateless_mode_as_unauthorized_without_error(monkeypatch):
    def boom():
        raise AssertionError(
            "credential store should not be consulted in stateless mode"
        )

    monkeypatch.setattr("auth.credential_store.get_credential_store", boom)
    monkeypatch.setattr("auth.oauth_config.is_stateless_mode", lambda: True)
    monkeypatch.setattr("auth.oauth_config.is_service_account_enabled", lambda: False)

    response = await deployment_status(_build_request())
    body = json.loads(response.body)

    assert body["account_authorized"] is False
    assert body["refresh_capable"] is False
