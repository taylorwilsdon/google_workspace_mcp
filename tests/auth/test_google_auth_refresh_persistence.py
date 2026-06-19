from auth.google_auth import get_credentials


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
    def __init__(self, session_credentials=None, session_user="user@example.com"):
        self._session_credentials = session_credentials
        self._session_user = session_user
        self.store_calls = []

    def get_user_by_mcp_session(self, session_id):  # noqa: ARG002
        return self._session_user

    def get_credentials_by_mcp_session(self, session_id):  # noqa: ARG002
        return self._session_credentials

    def store_session(self, **kwargs):
        self.store_calls.append(kwargs)


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
