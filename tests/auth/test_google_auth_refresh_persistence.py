import asyncio
from typing import Any, Optional

from pytest import MonkeyPatch

from auth.google_auth import get_authenticated_google_service, get_credentials


class _RefreshableCredentials:
    def __init__(self, valid: bool = False) -> None:
        self.token: str = "stale-token"
        self.refresh_token: str = "refresh-token"
        self.token_uri: str = "https://oauth2.googleapis.com/token"
        self.client_id: str = "client-id"
        self.client_secret: str = "client-secret"
        self.scopes: list[str] = ["scope.a"]
        self.expiry: Optional[Any] = None
        self.id_token: Optional[str] = None
        self.valid: bool = valid
        self.expired: bool = not valid

    def refresh(self, request: Any) -> None:  # noqa: ARG002
        self.token = "fresh-token"
        self.valid = True
        self.expired = False


class _OAuthSessionStore:
    def __init__(
        self,
        session_credentials: Optional[_RefreshableCredentials] = None,
        session_user: str = "user@example.com",
    ) -> None:
        self._session_credentials: Optional[_RefreshableCredentials] = session_credentials
        self._session_user: str = session_user
        self.store_calls: list[dict[str, Any]] = []

    def get_user_by_mcp_session(self, session_id: str) -> str:  # noqa: ARG002
        return self._session_user

    def get_credentials_by_mcp_session(
        self, session_id: str
    ) -> Optional[_RefreshableCredentials]:  # noqa: ARG002
        return self._session_credentials

    def store_session(self, **kwargs: Any) -> None:
        self.store_calls.append(kwargs)


class _CredentialStore:
    def __init__(
        self,
        existing_credentials: Optional[_RefreshableCredentials] = None,
        store_result: bool = True,
        credentials_by_user: Optional[dict[str, _RefreshableCredentials]] = None,
        users: Optional[list[str]] = None,
    ) -> None:
        self._existing_credentials: Optional[_RefreshableCredentials] = existing_credentials
        self.store_result: bool = store_result
        self.credentials_by_user: dict[str, _RefreshableCredentials] = (
            credentials_by_user or {}
        )
        self.users: list[str] = list(
            users if users is not None else self.credentials_by_user
        )
        self.get_calls: list[str] = []
        self.list_calls: int = 0
        self.store_calls: list[tuple[str, str]] = []

    def get_credential(self, user_email: str) -> Optional[_RefreshableCredentials]:
        self.get_calls.append(user_email)
        if self.credentials_by_user:
            return self.credentials_by_user.get(user_email)
        return self._existing_credentials

    def list_users(self) -> list[str]:
        self.list_calls += 1
        return self.users

    def store_credential(
        self, user_email: str, credentials: _RefreshableCredentials
    ) -> bool:  # noqa: ARG002
        self.store_calls.append((user_email, credentials.token))
        return self.store_result


def test_get_credentials_skips_session_update_when_oauth21_persist_fails(
    monkeypatch: MonkeyPatch,
) -> None:
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


def test_get_credentials_skips_session_update_when_refresh_persist_fails(
    monkeypatch: MonkeyPatch,
) -> None:
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


def test_get_credentials_single_user_uses_sole_stored_user_when_requested_user_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    stored_credentials = _RefreshableCredentials(valid=True)
    credential_store = _CredentialStore(
        credentials_by_user={"actual@example.com": stored_credentials}
    )

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )

    result = get_credentials(
        user_google_email="missing@example.com",
        required_scopes=["scope.a"],
    )

    assert result is stored_credentials
    assert credential_store.get_calls == ["missing@example.com", "actual@example.com"]
    assert credential_store.list_calls == 1
    assert credential_store.store_calls == []


def test_get_credentials_reports_resolved_single_user_email(
    monkeypatch: MonkeyPatch,
) -> None:
    stored_credentials = _RefreshableCredentials(valid=True)
    credential_store = _CredentialStore(
        credentials_by_user={"actual@example.com": stored_credentials}
    )
    resolved_emails = []

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )

    result = get_credentials(
        user_google_email="missing@example.com",
        required_scopes=["scope.a"],
        resolved_user_email=resolved_emails.append,
    )

    assert result is stored_credentials
    assert resolved_emails == ["actual@example.com"]


def test_get_authenticated_google_service_returns_resolved_single_user_email(
    monkeypatch: MonkeyPatch,
) -> None:
    stored_credentials = _RefreshableCredentials(valid=True)
    credential_store = _CredentialStore(
        credentials_by_user={"actual@example.com": stored_credentials}
    )
    built_service = object()

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr(
        "auth.google_auth.has_required_scopes", lambda scopes, required: True
    )
    monkeypatch.setattr("auth.google_auth.get_fastmcp_session_id", lambda: None)
    monkeypatch.setattr("auth.google_auth.get_fastmcp_context", None)
    monkeypatch.setattr(
        "auth.google_auth.build",
        lambda service_name, version, credentials: built_service,
    )

    async def run():
        return await get_authenticated_google_service(
            service_name="gmail",
            version="v1",
            tool_name="test_tool",
            user_google_email="missing@example.com",
            required_scopes=["scope.a"],
        )

    service, user_email = asyncio.run(run())

    assert service is built_service
    assert user_email == "actual@example.com"


def test_get_credentials_single_user_does_not_fallback_when_multiple_users_exist(
    monkeypatch: MonkeyPatch,
) -> None:
    credential_store = _CredentialStore(
        credentials_by_user={
            "first@example.com": _RefreshableCredentials(valid=True),
            "second@example.com": _RefreshableCredentials(valid=True),
        }
    )

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )

    result = get_credentials(
        user_google_email="missing@example.com",
        required_scopes=["scope.a"],
    )

    assert result is None
    assert credential_store.get_calls == ["missing@example.com"]
    assert credential_store.list_calls == 1
    assert credential_store.store_calls == []
