import logging
from types import SimpleNamespace

from auth.google_auth import get_credentials

EMAIL = "user@example.com"
SESSION_ID = "session-1"


class _SessionStore:
    def __init__(self) -> None:
        self.store_calls: list[dict[str, object]] = []

    def store_session(self, **kwargs: object) -> None:
        self.store_calls.append(kwargs)

    def get_user_by_mcp_session(self, session_id: str) -> None:  # noqa: ARG002
        return None

    def get_credentials_by_mcp_session(self, session_id: str) -> None:  # noqa: ARG002
        return None


def test_file_store_credentials_are_returned_without_a_session_write(
    monkeypatch, caplog
) -> None:
    session_store = _SessionStore()
    file_credentials = SimpleNamespace(
        valid=True,
        expired=False,
        scopes=["scope.a"],
        refresh_token="refresh-token",
        id_token=None,
    )
    credential_store = SimpleNamespace(get_credential=lambda email: file_credentials)
    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    monkeypatch.setattr(
        "auth.google_auth.get_oauth21_session_store", lambda: session_store
    )
    monkeypatch.setattr(
        "auth.google_auth.get_credential_store", lambda: credential_store
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    with caplog.at_level(logging.WARNING):
        result = get_credentials(
            user_google_email=EMAIL,
            required_scopes=["scope.a"],
            session_id=SESSION_ID,
        )

    assert result is file_credentials
    assert session_store.store_calls == []
    assert "no user email found" not in caplog.text
