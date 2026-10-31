"""Regression tests for caching credentials in the OAuth 2.1 session store.

Credentials loaded from the file credential store carry no ``id_token``, so
``save_credentials_to_session`` used to find no email, log a warning on every
tool call and skip the per-session cache. Callers already know the account
email, so they now pass it in and ``id_token`` decoding is only a fallback.
"""

import logging

import jwt

from auth.google_auth import get_credentials, save_credentials_to_session

SESSION_ID = "session-1"
JWT_KEY = "test-signing-key-at-least-32-bytes-long"
EMAIL = "user@example.com"
WARNING_TEXT = "no user email found"


class _Credentials:
    def __init__(self, id_token=None):
        self.token = "access-token"
        self.refresh_token = "refresh-token"
        self.token_uri = "https://oauth2.googleapis.com/token"
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.scopes = ["scope.a"]
        self.expiry = None
        self.id_token = id_token
        self.valid = True
        self.expired = False


class _SessionStore:
    def __init__(self):
        self.store_calls = []

    def store_session(self, **kwargs):
        self.store_calls.append(kwargs)

    def get_user_by_mcp_session(self, session_id):  # noqa: ARG002
        return None

    def get_credentials_by_mcp_session(self, session_id):  # noqa: ARG002
        return None


class _FileCredentialStore:
    def __init__(self, credentials):
        self._credentials = credentials

    def get_credential(self, user_email):  # noqa: ARG002
        return self._credentials


def _warning_messages(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING and WARNING_TEXT in record.getMessage()
    ]


def test_save_uses_explicit_email_without_id_token(monkeypatch, caplog):
    session_store = _SessionStore()
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: session_store
    )

    with caplog.at_level(logging.WARNING):
        save_credentials_to_session(SESSION_ID, _Credentials(), EMAIL)

    assert _warning_messages(caplog) == []
    assert len(session_store.store_calls) == 1
    call = session_store.store_calls[0]
    assert call["user_email"] == EMAIL
    assert call["mcp_session_id"] == SESSION_ID
    assert call["access_token"] == "access-token"


def test_save_falls_back_to_id_token_email(monkeypatch):
    session_store = _SessionStore()
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: session_store
    )
    id_token = jwt.encode({"email": "token@example.com"}, JWT_KEY, algorithm="HS256")

    save_credentials_to_session(SESSION_ID, _Credentials(id_token=id_token))

    assert [call["user_email"] for call in session_store.store_calls] == [
        "token@example.com"
    ]


def test_save_prefers_explicit_email_over_id_token(monkeypatch):
    session_store = _SessionStore()
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: session_store
    )
    id_token = jwt.encode({"email": "token@example.com"}, JWT_KEY, algorithm="HS256")

    save_credentials_to_session(SESSION_ID, _Credentials(id_token=id_token), EMAIL)

    assert [call["user_email"] for call in session_store.store_calls] == [EMAIL]


def test_save_without_any_email_still_warns_and_skips_cache(monkeypatch, caplog):
    session_store = _SessionStore()
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: session_store
    )

    with caplog.at_level(logging.WARNING):
        save_credentials_to_session(SESSION_ID, _Credentials())

    assert len(_warning_messages(caplog)) == 1
    assert session_store.store_calls == []


def test_get_credentials_from_file_store_populates_session_cache(monkeypatch, caplog):
    session_store = _SessionStore()
    file_credentials = _Credentials()

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: session_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store",
        lambda: _FileCredentialStore(file_credentials),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )
    monkeypatch.setattr(
        "auth.google_auth.load_credentials_from_session", lambda session_id: None
    )

    with caplog.at_level(logging.WARNING):
        result = get_credentials(
            user_google_email=EMAIL,
            required_scopes=["scope.a"],
            session_id=SESSION_ID,
        )

    assert result is file_credentials
    assert _warning_messages(caplog) == []
    assert [
        (call["user_email"], call["mcp_session_id"])
        for call in session_store.store_calls
    ] == [(EMAIL, SESSION_ID)]
