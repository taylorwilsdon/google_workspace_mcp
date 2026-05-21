import pytest
from google.oauth2.credentials import Credentials

from auth.google_auth import handle_auth_callback


class _DummyFlow:
    def __init__(self, credentials):
        self.credentials = credentials

    def fetch_token(self, authorization_response):  # noqa: ARG002
        return None


class _DummyOAuthStore:
    def __init__(self, session_credentials=None, latest_state_info=None):
        self._session_credentials = session_credentials
        self._latest_state_info = latest_state_info or {
            "session_id": None,
            "code_verifier": "verifier",
        }
        self.latest_calls = []
        self.stored_refresh_token = None
        self.store_calls = 0

    def validate_and_consume_oauth_state(self, state, session_id=None):  # noqa: ARG002
        return {"session_id": session_id, "code_verifier": "verifier"}

    def consume_latest_oauth_state(
        self,
        initiating_session_id=None,
        allow_any_session=False,
    ):
        self.latest_calls.append((initiating_session_id, allow_any_session))
        return self._latest_state_info

    def get_credentials_by_mcp_session(self, mcp_session_id):  # noqa: ARG002
        return self._session_credentials

    def store_session(self, **kwargs):
        self.store_calls += 1
        self.stored_refresh_token = kwargs.get("refresh_token")


class _DummyCredentialStore:
    def __init__(self, existing_credentials=None, store_result=True):
        self._existing_credentials = existing_credentials
        self.saved_credentials = None
        self.store_result = store_result

    def get_credential(self, user_email):  # noqa: ARG002
        return self._existing_credentials

    def store_credential(self, user_email, credentials):  # noqa: ARG002
        self.saved_credentials = credentials
        return self.store_result


def _make_credentials(refresh_token):
    return Credentials(
        token="access-token",
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["scope.a"],
    )


@pytest.mark.asyncio
async def test_callback_missing_state_does_not_use_latest_state_by_default(
    monkeypatch,
):
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    oauth_store = _DummyOAuthStore(session_credentials=None)

    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )

    with pytest.raises(ValueError, match="Missing OAuth state parameter"):
        await handle_auth_callback(
            scopes=["scope.a"],
            authorization_response="http://localhost/callback?code=code123",
            redirect_uri="http://localhost/callback",
            session_id=None,
        )

    assert oauth_store.latest_calls == []


@pytest.mark.asyncio
async def test_callback_missing_state_rejects_explicit_fallback_outside_single_user(
    monkeypatch,
):
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    oauth_store = _DummyOAuthStore(session_credentials=None)

    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )

    with pytest.raises(ValueError, match="Missing OAuth state parameter"):
        await handle_auth_callback(
            scopes=["scope.a"],
            authorization_response="http://localhost/callback?code=code123",
            redirect_uri="http://localhost/callback",
            session_id=None,
            allow_missing_state_fallback=True,
        )

    assert oauth_store.latest_calls == []


@pytest.mark.asyncio
async def test_callback_missing_state_uses_explicit_single_user_stdio_fallback(
    monkeypatch,
):
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    callback_credentials = _make_credentials(refresh_token="callback-refresh-token")
    oauth_store = _DummyOAuthStore(
        session_credentials=None,
        latest_state_info={
            "session_id": "stdio-origin-session",
            "code_verifier": "stdio-verifier",
        },
    )
    credential_store = _DummyCredentialStore(existing_credentials=None)

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

    _email, credentials = await handle_auth_callback(
        scopes=["scope.a"],
        authorization_response="http://localhost/callback?code=code123",
        redirect_uri="http://localhost/callback",
        session_id=None,
        allow_missing_state_fallback=True,
    )

    assert credentials.refresh_token == "callback-refresh-token"
    assert oauth_store.latest_calls == [(None, True)]


@pytest.mark.asyncio
async def test_callback_preserves_refresh_token_from_credential_store(monkeypatch):
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

    _email, credentials = await handle_auth_callback(
        scopes=["scope.a"],
        authorization_response="http://localhost/callback?state=abc123&code=code123",
        redirect_uri="http://localhost/callback",
        session_id="session-1",
    )

    assert credentials.refresh_token == "file-refresh-token"
    assert credential_store.saved_credentials.refresh_token == "file-refresh-token"
    assert oauth_store.stored_refresh_token == "file-refresh-token"


@pytest.mark.asyncio
async def test_callback_prefers_session_refresh_token_over_credential_store(
    monkeypatch,
):
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

    _email, credentials = await handle_auth_callback(
        scopes=["scope.a"],
        authorization_response="http://localhost/callback?state=abc123&code=code123",
        redirect_uri="http://localhost/callback",
        session_id="session-1",
    )

    assert credentials.refresh_token == "session-refresh-token"
    assert credential_store.saved_credentials.refresh_token == "session-refresh-token"
    assert oauth_store.stored_refresh_token == "session-refresh-token"


@pytest.mark.asyncio
async def test_callback_raises_when_google_rejects_pkce_verifier(monkeypatch):
    """Test that PKCE verifier rejection raises exception with clear error message.

    OAuth authorization codes are single-use, so retry is not possible.
    The auth flow must be restarted from the beginning.
    """
    oauth_store = _DummyOAuthStore(session_credentials=None)
    credential_store = _DummyCredentialStore(existing_credentials=None)

    class _FailingFlow:
        def fetch_token(self, authorization_response):  # noqa: ARG002
            raise Exception("(invalid_grant) code_verifier or verifier is not needed.")

    def _fake_create_oauth_flow(**kwargs):  # noqa: ARG001
        return _FailingFlow()

    monkeypatch.setattr("auth.google_auth.create_oauth_flow", _fake_create_oauth_flow)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: oauth_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    # Verify the exception is raised
    with pytest.raises(Exception, match="code_verifier or verifier is not needed"):
        await handle_auth_callback(
            scopes=["scope.a"],
            authorization_response="http://localhost/callback?state=abc123&code=code123",
            redirect_uri="http://localhost/callback",
            session_id="session-1",
        )


@pytest.mark.asyncio
async def test_callback_aborts_session_persistence_when_store_write_fails(monkeypatch):
    callback_credentials = _make_credentials(refresh_token="callback-refresh-token")
    oauth_store = _DummyOAuthStore(session_credentials=None)
    credential_store = _DummyCredentialStore(
        existing_credentials=None, store_result=False
    )
    session_cache_writes = []

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
        "auth.google_auth.save_credentials_to_session",
        lambda *args: session_cache_writes.append(args),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    with pytest.raises(RuntimeError, match="Failed to persist credentials"):
        await handle_auth_callback(
            scopes=["scope.a"],
            authorization_response="http://localhost/callback?state=abc123&code=code123",
            redirect_uri="http://localhost/callback",
            session_id="session-1",
        )

    assert credential_store.saved_credentials.refresh_token == "callback-refresh-token"
    assert oauth_store.store_calls == 0
    assert session_cache_writes == []
