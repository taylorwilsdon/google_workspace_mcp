from auth.google_auth import get_credentials
from auth.oauth21_session_store import OAuth21SessionStore


class _RefreshableCredentials:
    def __init__(self):
        self.token = "stale-token"
        self.refresh_token = "refresh-token"
        self.token_uri = "https://oauth2.googleapis.com/token"
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.scopes = ["scope.a"]
        self.expiry = None
        self.valid = False
        self.expired = True

    def refresh(self, request):  # noqa: ARG002
        self.token = "fresh-token"
        self.valid = True
        self.expired = False


class _OAuthSessionStore:
    def __init__(
        self,
        session_credentials=None,
        session_user="user@example.com",
        store_error=None,
    ):
        self._session_credentials = session_credentials
        self._session_user = session_user
        self._store_error = store_error
        self.store_calls = []

    def get_user_by_mcp_session(self, session_id):  # noqa: ARG002
        return self._session_user

    def get_credentials_by_mcp_session(self, session_id):  # noqa: ARG002
        return self._session_credentials

    def store_session(self, **kwargs):
        self.store_calls.append(kwargs)
        if self._store_error:
            raise self._store_error


class _CredentialStore:
    def __init__(self, existing_credentials=None, store_result=True):
        self._existing_credentials = existing_credentials
        self.store_result = store_result
        self.get_calls = []
        self.store_calls = []

    def get_credential(self, user_email):
        self.get_calls.append(user_email)
        return self._existing_credentials

    def store_credential(self, user_email, credentials):  # noqa: ARG002
        self.store_calls.append((user_email, credentials.token))
        return self.store_result


def test_get_credentials_skips_session_update_when_oauth21_persist_fails(monkeypatch):
    session_creds = _RefreshableCredentials()
    oauth_store = _OAuthSessionStore(session_credentials=session_creds)
    credential_store = _CredentialStore(store_result=False)

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )

    result = get_credentials(
        user_google_email="user@example.com",
        required_scopes=["scope.a"],
        session_id="session-1",
    )

    assert result is None
    assert credential_store.store_calls == [("user@example.com", "fresh-token")]
    assert oauth_store.store_calls == []


def test_get_credentials_skips_session_update_when_refresh_persist_fails(monkeypatch):
    file_creds = _RefreshableCredentials()
    oauth_store = _OAuthSessionStore(session_credentials=None)
    credential_store = _CredentialStore(
        existing_credentials=file_creds, store_result=False
    )
    session_cache_writes = []

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )
    monkeypatch.setattr(
        "auth.google_auth.load_credentials_from_session", lambda session_id: None
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session",
        lambda *args: session_cache_writes.append(args),
    )

    result = get_credentials(
        user_google_email="user@example.com",
        required_scopes=["scope.a"],
        session_id="session-1",
    )

    assert result is None
    assert credential_store.store_calls == [("user@example.com", "fresh-token")]
    assert oauth_store.store_calls == []
    assert len(session_cache_writes) == 1
    assert session_cache_writes[0][0] == "session-1"


def test_get_credentials_returns_refreshed_secondary_credentials_when_session_bound_to_primary(
    tmp_path,
    monkeypatch,
):
    oauth_store = OAuth21SessionStore(
        oauth_state_file=str(tmp_path / "oauth_states.json")
    )
    oauth_store.store_session(
        user_email="primary@example.com",
        access_token="primary-token",
        mcp_session_id="session-1",
    )
    file_creds = _RefreshableCredentials()
    credential_store = _CredentialStore(existing_credentials=file_creds)
    session_cache_writes = []

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session",
        lambda *args: session_cache_writes.append(args),
    )

    result = get_credentials(
        user_google_email="secondary@example.com",
        required_scopes=["scope.a"],
        session_id="session-1",
    )

    assert result is file_creds
    assert result.token == "fresh-token"
    assert credential_store.store_calls == [("secondary@example.com", "fresh-token")]
    assert oauth_store.get_user_by_mcp_session("session-1") == "primary@example.com"
    assert oauth_store.get_credentials("secondary@example.com").token == "fresh-token"
    assert session_cache_writes == [("session-1", file_creds)]


def test_store_session_drops_cross_account_mcp_binding_but_persists_credentials(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    store = OAuth21SessionStore(oauth_state_file=str(tmp_path / "oauth_states.json"))
    store.store_session(
        user_email="primary@example.com",
        access_token="primary-token",
        mcp_session_id="session-1",
    )

    store.store_session(
        user_email="secondary@example.com",
        access_token="secondary-token",
        mcp_session_id="session-1",
    )

    assert store.get_user_by_mcp_session("session-1") == "primary@example.com"
    assert store.get_credentials("secondary@example.com").token == "secondary-token"
    assert store._sessions["secondary@example.com"]["mcp_session_id"] is None


def test_store_session_keeps_same_user_mcp_binding(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    store = OAuth21SessionStore(oauth_state_file=str(tmp_path / "oauth_states.json"))
    store.store_session(
        user_email="primary@example.com",
        access_token="stale-token",
        mcp_session_id="session-1",
    )

    store.store_session(
        user_email="primary@example.com",
        access_token="fresh-token",
        mcp_session_id="session-1",
    )

    assert store.get_user_by_mcp_session("session-1") == "primary@example.com"
    assert store.get_credentials("primary@example.com").token == "fresh-token"
    assert store._sessions["primary@example.com"]["mcp_session_id"] == "session-1"


def test_get_credentials_returns_refreshed_credentials_when_session_store_fails(
    caplog,
    monkeypatch,
):
    file_creds = _RefreshableCredentials()
    oauth_store = _OAuthSessionStore(
        session_credentials=None,
        store_error=RuntimeError("session store unavailable"),
    )
    credential_store = _CredentialStore(existing_credentials=file_creds)

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session", lambda *args: None
    )

    result = get_credentials(
        user_google_email="user@example.com",
        required_scopes=["scope.a"],
        session_id="session-1",
    )

    assert result is file_creds
    assert result.token == "fresh-token"
    assert credential_store.store_calls == [("user@example.com", "fresh-token")]
    assert len(oauth_store.store_calls) == 1
    assert "Failed to update OAuth 2.1 session store" in caplog.text


def test_get_credentials_single_user_returns_none_for_missing_requested_user(
    monkeypatch,
):
    credential_store = _CredentialStore(existing_credentials=None)
    fallback_creds = _RefreshableCredentials()
    fallback_calls = []

    def _unexpected_fallback(credentials_base_dir):  # noqa: ARG001
        fallback_calls.append(True)
        return fallback_creds, "other@example.com"

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth._find_any_credentials", _unexpected_fallback)

    result = get_credentials(
        user_google_email="missing@example.com",
        required_scopes=["scope.a"],
    )

    assert result is None
    assert credential_store.get_calls == ["missing@example.com"]
    assert credential_store.store_calls == []
    assert fallback_calls == []
