"""Cross-account /oauth2callback regressions.

Both cases are the same shape: an MCP session already bound to account A
completes a consent flow for account B. The session store refuses to rebind the
session — correctly, bindings are immutable — and the callback path must survive
that refusal without (a) grafting A's refresh token onto B's credential or
(b) turning a good consent into a 500.
"""

import pytest

from auth.google_auth import handle_auth_callback

from tests.auth.test_google_auth_callback_refresh_token import (
    _DummyCredentialStore,
    _DummyFlow,
    _DummyOAuthStore,
    _make_credentials,
)


class _RebindRefusingOAuthStore(_DummyOAuthStore):
    """Session store that refuses to rebind a session already owned by someone else."""

    def __init__(self, bound_user, **kwargs):
        super().__init__(**kwargs)
        self._bound_user = bound_user

    def get_user_by_mcp_session(self, mcp_session_id):  # noqa: ARG002
        return self._bound_user

    def store_session(self, **kwargs):
        super().store_session(**kwargs)
        if kwargs.get("mcp_session_id") and self._bound_user != kwargs["user_email"]:
            raise ValueError(
                f"Session {kwargs['mcp_session_id']} is already bound to a different user"
            )


@pytest.mark.asyncio
async def test_cross_account_callback_does_not_borrow_session_refresh_token(
    monkeypatch,
):
    """A second account's consent must not inherit the first account's refresh token."""
    callback_credentials = _make_credentials(refresh_token=None)
    oauth_store = _RebindRefusingOAuthStore(
        bound_user="first@personal.com",
        session_credentials=_make_credentials(refresh_token="first-user-refresh-token"),
    )
    credential_store = _DummyCredentialStore(
        existing_credentials=_make_credentials(
            refresh_token="second-user-refresh-token"
        )
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
        lambda credentials: {"email": "second@work.org"},  # noqa: ARG005
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

    assert credentials.refresh_token == "second-user-refresh-token"


@pytest.mark.asyncio
async def test_cross_account_callback_survives_refused_session_rebind(monkeypatch):
    """A refused rebind is not a failed authorization — it must not 500 the callback."""
    callback_credentials = _make_credentials(refresh_token="second-user-refresh-token")
    oauth_store = _RebindRefusingOAuthStore(
        bound_user="first@personal.com", session_credentials=None
    )
    credential_store = _DummyCredentialStore(existing_credentials=None)
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
        lambda credentials: {"email": "second@work.org"},  # noqa: ARG005
    )
    monkeypatch.setattr(
        "auth.google_auth.save_credentials_to_session",
        lambda *args: session_cache_writes.append(args),
    )
    monkeypatch.setattr("auth.google_auth.is_stateless_mode", lambda: False)

    email, credentials = await handle_auth_callback(
        scopes=["scope.a"],
        authorization_response="http://localhost/callback?state=abc123&code=code123",
        redirect_uri="http://localhost/callback",
        session_id="session-1",
    )

    # The consent succeeded and the credential reached the on-disk store ...
    assert email == "second@work.org"
    assert credentials.refresh_token == "second-user-refresh-token"
    assert (
        credential_store.saved_credentials.refresh_token == "second-user-refresh-token"
    )
    # ... and the first user's session cache was left alone.
    assert session_cache_writes == []
