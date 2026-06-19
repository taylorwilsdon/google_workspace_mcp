from types import SimpleNamespace

import pytest

from google.auth.exceptions import RefreshError

from auth.google_auth import _determine_oauth_prompt


class _DummyCredentialStore:
    def __init__(self, credentials_by_email=None):
        self._credentials_by_email = credentials_by_email or {}

    def get_credential(self, user_email):
        return self._credentials_by_email.get(user_email)


class _DummySessionStore:
    def __init__(self, user_by_session=None, credentials_by_session=None):
        self._user_by_session = user_by_session or {}
        self._credentials_by_session = credentials_by_session or {}

    def get_user_by_mcp_session(self, mcp_session_id):
        return self._user_by_session.get(mcp_session_id)

    def get_credentials_by_mcp_session(self, mcp_session_id):
        return self._credentials_by_session.get(mcp_session_id)


def _credentials_with_scopes(scopes, valid=True, refresh_token="fake-token"):
    return SimpleNamespace(scopes=scopes, valid=valid, refresh_token=refresh_token)


@pytest.mark.asyncio
async def test_prompt_select_account_when_existing_credentials_cover_scopes(
    monkeypatch,
):
    required_scopes = ["scope.a", "scope.b"]
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store",
        lambda: _DummySessionStore(),
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store",
        lambda: _DummyCredentialStore(
            {"user@gmail.com": _credentials_with_scopes(required_scopes, valid=True)}
        ),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    prompt = await _determine_oauth_prompt(
        user_google_email="user@gmail.com",
        required_scopes=required_scopes,
        session_id=None,
    )

    assert prompt == "select_account"


@pytest.mark.asyncio
async def test_prompt_consent_when_credentials_revoked(monkeypatch):
    """When credentials have required scopes but refresh fails (revoked),
    prompt must be 'consent' so Google performs full re-authorization."""
    required_scopes = ["scope.a", "scope.b"]

    def _raise_on_refresh(_self, _request):
        raise RefreshError("invalid_grant: Token has been revoked")

    creds = _credentials_with_scopes(required_scopes, valid=False)
    creds.refresh = _raise_on_refresh.__get__(creds)

    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store",
        lambda: _DummySessionStore(),
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store",
        lambda: _DummyCredentialStore({"user@gmail.com": creds}),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    prompt = await _determine_oauth_prompt(
        user_google_email="user@gmail.com",
        required_scopes=required_scopes,
        session_id=None,
    )

    assert prompt == "consent"


@pytest.mark.asyncio
async def test_prompt_consent_when_existing_credentials_missing_scopes(monkeypatch):
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store",
        lambda: _DummySessionStore(),
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store",
        lambda: _DummyCredentialStore(
            {"user@gmail.com": _credentials_with_scopes(["scope.a"])}
        ),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    prompt = await _determine_oauth_prompt(
        user_google_email="user@gmail.com",
        required_scopes=["scope.a", "scope.b"],
        session_id=None,
    )

    assert prompt == "consent"


@pytest.mark.asyncio
async def test_prompt_consent_when_no_existing_credentials(monkeypatch):
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store",
        lambda: _DummySessionStore(),
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store",
        lambda: _DummyCredentialStore(),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    prompt = await _determine_oauth_prompt(
        user_google_email="new_user@gmail.com",
        required_scopes=["scope.a"],
        session_id=None,
    )

    assert prompt == "consent"


@pytest.mark.asyncio
async def test_prompt_uses_session_mapping_when_email_not_provided(monkeypatch):
    session_id = "session-123"
    required_scopes = ["scope.a"]
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store",
        lambda: _DummySessionStore(
            user_by_session={session_id: "mapped@gmail.com"},
            credentials_by_session={
                session_id: _credentials_with_scopes(required_scopes, valid=True)
            },
        ),
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store",
        lambda: _DummyCredentialStore(),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    prompt = await _determine_oauth_prompt(
        user_google_email=None,
        required_scopes=required_scopes,
        session_id=session_id,
    )

    assert prompt == "select_account"
