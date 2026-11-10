import jwt
import pytest

from auth.google_auth import get_credentials
from auth.oauth21_session_store import OAuth21SessionStore


class _RefreshableCredentials:
    def __init__(self, id_token=None):
        self.token = "stale-token"
        self.refresh_token = "refresh-token"
        self.token_uri = "https://oauth2.googleapis.com/token"
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.scopes = ["scope.a"]
        self.expiry = None
        self.valid = False
        self.expired = True
        self.id_token = id_token
        self.refreshed_email = None

    def refresh(self, request):  # noqa: ARG002
        self.token = "fresh-token"
        self.valid = True
        self.expired = False
        if self.refreshed_email:
            self.id_token = jwt.encode(
                {"email": self.refreshed_email},
                "test-signing-key-that-is-long-enough",
                algorithm="HS256",
            )


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
    def __init__(self, existing_credentials=None, store_result=True, users=()):
        self._existing_credentials = existing_credentials
        self.store_result = store_result
        self.users = list(users)
        self.get_calls = []
        self.store_calls = []

    def get_credential(self, user_email):
        self.get_calls.append(user_email)
        if self.users and user_email not in self.users:
            return None
        return self._existing_credentials

    def list_users(self):
        return self.users

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


@pytest.mark.parametrize(
    ("refreshed_email", "expect_credentials"),
    [("primary@example.com", False), ("Secondary@Example.com", True), (None, True)],
)
def test_get_credentials_discards_refresh_that_returns_another_account(
    monkeypatch, refreshed_email, expect_credentials
):
    file_creds = _RefreshableCredentials()
    file_creds.refreshed_email = refreshed_email
    credential_store = _CredentialStore(existing_credentials=file_creds)

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store",
        lambda: _OAuthSessionStore(session_user=None),
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )

    result = get_credentials(
        user_google_email="secondary@example.com", required_scopes=["scope.a"]
    )

    assert (result is file_creds) is expect_credentials
    assert bool(credential_store.store_calls) is expect_credentials


@pytest.mark.parametrize("secondary_session_id", [None, "session-2"])
def test_get_credentials_keeps_refresh_for_account_outside_bound_session(
    monkeypatch, tmp_path, secondary_session_id
):
    oauth_store = OAuth21SessionStore(
        oauth_state_file=str(tmp_path / "oauth_states.json")
    )
    oauth_store.store_session(
        user_email="primary@example.com",
        access_token="primary-token",
        mcp_session_id="session-1",
    )
    if secondary_session_id:
        oauth_store.store_session(
            user_email="secondary@example.com",
            access_token="secondary-token",
            mcp_session_id=secondary_session_id,
        )
    file_creds = _RefreshableCredentials(
        id_token=jwt.encode(
            {"email": "secondary@example.com"},
            "test-signing-key-that-is-long-enough",
            algorithm="HS256",
        )
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

    result = get_credentials(
        user_google_email="secondary@example.com",
        required_scopes=["scope.a"],
        session_id="session-1",
    )

    assert result is file_creds
    assert credential_store.store_calls == [("secondary@example.com", "fresh-token")]
    assert oauth_store.get_credentials("secondary@example.com").token == "fresh-token"
    assert oauth_store.get_user_by_mcp_session("session-1") == "primary@example.com"
    assert oauth_store.get_credentials_by_mcp_session("session-1").token == (
        "primary-token"
    )
    if secondary_session_id:
        assert (
            oauth_store.get_user_by_mcp_session(secondary_session_id)
            == "secondary@example.com"
        )
        secondary_creds = oauth_store.get_credentials_by_mcp_session(
            secondary_session_id
        )
        assert secondary_creds.token == "fresh-token"


def test_get_credentials_single_user_returns_none_for_missing_requested_user(
    monkeypatch,
):
    credential_store = _CredentialStore(
        existing_credentials=_RefreshableCredentials(), users=["other@example.com"]
    )
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


@pytest.mark.parametrize(
    ("stored", "requested"),
    [
        ("luke@example.com", "Luke@Example.com"),
        ("Luke@Example.com", "luke@example.com"),
    ],
)
def test_get_credentials_single_user_matches_stored_email_case_insensitively(
    monkeypatch, stored, requested
):
    credential_store = _CredentialStore(
        existing_credentials=_RefreshableCredentials(), users=[stored]
    )
    oauth_store = _OAuthSessionStore(session_user=None)

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
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
        user_google_email=requested,
        required_scopes=["scope.a"],
    )

    assert result is not None and result.token == "fresh-token"
    assert credential_store.get_calls == [stored]
    assert credential_store.store_calls == [(stored, "fresh-token")]
    assert [call["user_email"] for call in oauth_store.store_calls] == [stored]
