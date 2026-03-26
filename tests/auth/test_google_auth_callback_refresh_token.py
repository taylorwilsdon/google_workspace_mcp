from google.oauth2.credentials import Credentials

from auth.google_auth import handle_auth_callback


class _DummyFlow:
    def __init__(self, credentials):
        self.credentials = credentials

    def fetch_token(self, authorization_response):  # noqa: ARG002
        return None


class _DummyOAuthStore:
    def __init__(self, session_credentials=None):
        self._session_credentials = session_credentials
        self.stored_refresh_token = None

    def validate_and_consume_oauth_state(self, state, session_id=None):  # noqa: ARG002
        return {"session_id": session_id, "code_verifier": "verifier"}

    def get_credentials_by_mcp_session(self, mcp_session_id):  # noqa: ARG002
        return self._session_credentials

    def store_session(self, **kwargs):
        self.stored_refresh_token = kwargs.get("refresh_token")


class _DummyCredentialStore:
    def __init__(self, existing_credentials=None):
        self._existing_credentials = existing_credentials
        self.saved_credentials = None

    def get_credential(self, user_email):  # noqa: ARG002
        return self._existing_credentials

    def store_credential(self, user_email, credentials):  # noqa: ARG002
        self.saved_credentials = credentials
        return True


def _make_credentials(refresh_token):
    return Credentials(
        token="access-token",
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["scope.a"],
    )


def test_callback_preserves_refresh_token_from_credential_store(monkeypatch):
    callback_credentials = _make_credentials(refresh_token=None)
    oauth_store = _DummyOAuthStore(session_credentials=None)
    credential_store = _DummyCredentialStore(
        existing_credentials=_make_credentials(refresh_token="file-refresh-token")
    )

    monkeypatch.setattr(
        "auth.google_auth.create_oauth_flow",
        lambda **kwargs: _DummyFlow(callback_credentials),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_user_info",
        lambda credentials: {"email": "user@gmail.com"},  # noqa: ARG005
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session", lambda *args: None
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    _email, credentials = handle_auth_callback(
        scopes=["scope.a"],
        authorization_response="http://localhost/callback?state=abc123&code=code123",
        redirect_uri="http://localhost/callback",
        session_id="session-1",
    )

    assert credentials.refresh_token == "file-refresh-token"
    assert credential_store.saved_credentials.refresh_token == "file-refresh-token"
    assert oauth_store.stored_refresh_token == "file-refresh-token"


def test_callback_prefers_session_refresh_token_over_credential_store(monkeypatch):
    callback_credentials = _make_credentials(refresh_token=None)
    oauth_store = _DummyOAuthStore(
        session_credentials=_make_credentials(refresh_token="session-refresh-token")
    )
    credential_store = _DummyCredentialStore(
        existing_credentials=_make_credentials(refresh_token="file-refresh-token")
    )

    monkeypatch.setattr(
        "auth.google_auth.create_oauth_flow",
        lambda **kwargs: _DummyFlow(callback_credentials),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_user_info",
        lambda credentials: {"email": "user@gmail.com"},  # noqa: ARG005
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session", lambda *args: None
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    _email, credentials = handle_auth_callback(
        scopes=["scope.a"],
        authorization_response="http://localhost/callback?state=abc123&code=code123",
        redirect_uri="http://localhost/callback",
        session_id="session-1",
    )

    assert credentials.refresh_token == "session-refresh-token"
    assert credential_store.saved_credentials.refresh_token == "session-refresh-token"
    assert oauth_store.stored_refresh_token == "session-refresh-token"
