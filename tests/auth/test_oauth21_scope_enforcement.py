"""Finding 29: missing scope metadata must not disable scope enforcement.

`get_authenticated_google_service_oauth21` used to substitute `required_scopes` when a
stored session had no recorded scopes, so the subsequent `has_required_scopes` check
compared the requirement against itself and always passed. Any tool could then run on
a session regardless of what the user actually consented to.
"""

from types import SimpleNamespace

import pytest

import auth.oauth21_session_store as session_store
import auth.service_decorator as service_decorator
from auth.service_decorator import get_authenticated_google_service_oauth21

DRIVE_READ = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_WRITE = "https://www.googleapis.com/auth/drive"


class _Credentials:
    def __init__(self, scopes):
        self.scopes = scopes
        self.token = "token"
        self.refresh_token = "refresh"
        self.token_uri = "https://oauth2.googleapis.com/token"
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.expiry = None


def _patch_store_path(monkeypatch, credentials):
    """Route through the session-store branch (no provider / no access token)."""
    monkeypatch.setattr(service_decorator, "get_auth_provider", lambda: None)
    monkeypatch.setattr(service_decorator, "get_access_token", lambda: None)
    store = SimpleNamespace(
        get_credentials_with_validation=lambda **kwargs: credentials  # noqa: ARG005
    )
    monkeypatch.setattr(service_decorator, "get_oauth21_session_store", lambda: store)
    monkeypatch.setattr(
        service_decorator,
        "build",
        lambda name, version, credentials: SimpleNamespace(name=name),
    )


@pytest.mark.asyncio
async def test_session_without_scopes_is_rejected(monkeypatch):
    _patch_store_path(monkeypatch, _Credentials(scopes=None))

    with pytest.raises(
        service_decorator.GoogleAuthenticationError, match="no recorded scopes"
    ):
        await get_authenticated_google_service_oauth21(
            service_name="drive",
            version="v3",
            tool_name="t",
            user_google_email="user@example.com",
            required_scopes=[DRIVE_WRITE],
        )


@pytest.mark.asyncio
async def test_session_with_empty_scope_list_is_rejected(monkeypatch):
    _patch_store_path(monkeypatch, _Credentials(scopes=[]))

    with pytest.raises(
        service_decorator.GoogleAuthenticationError, match="no recorded scopes"
    ):
        await get_authenticated_google_service_oauth21(
            service_name="drive",
            version="v3",
            tool_name="t",
            user_google_email="user@example.com",
            required_scopes=[DRIVE_WRITE],
        )


@pytest.mark.asyncio
async def test_insufficient_scopes_are_still_rejected(monkeypatch):
    _patch_store_path(monkeypatch, _Credentials(scopes=[DRIVE_READ]))

    with pytest.raises(
        service_decorator.GoogleAuthenticationError, match="lack required scopes"
    ):
        await get_authenticated_google_service_oauth21(
            service_name="drive",
            version="v3",
            tool_name="t",
            user_google_email="user@example.com",
            required_scopes=[DRIVE_WRITE],
        )


@pytest.mark.asyncio
async def test_sufficient_scopes_still_authenticate(monkeypatch):
    _patch_store_path(monkeypatch, _Credentials(scopes=[DRIVE_WRITE]))

    service, email = await get_authenticated_google_service_oauth21(
        service_name="drive",
        version="v3",
        tool_name="t",
        user_google_email="user@example.com",
        required_scopes=[DRIVE_WRITE],
    )

    assert service.name == "drive"
    assert email == "user@example.com"


@pytest.mark.asyncio
async def test_session_scopes_are_backfilled_from_the_verified_token(monkeypatch):
    """A provider credential without scope metadata is filled from the access token.

    The token was already verified by the provider, so its scope list is trustworthy
    and keeps legitimate flows working instead of storing an unusable session.
    """
    stored = {}

    async def fake_build_credentials_from_provider():
        return _Credentials(scopes=None)

    monkeypatch.setattr(
        session_store,
        "_build_credentials_from_provider",
        fake_build_credentials_from_provider,
    )
    monkeypatch.setattr(session_store, "is_external_oauth21_provider", lambda: False)
    monkeypatch.setattr(
        session_store,
        "get_oauth21_session_store",
        lambda: SimpleNamespace(store_session=lambda **kwargs: stored.update(kwargs)),
    )

    access_token = SimpleNamespace(
        token="token",
        scopes=[DRIVE_WRITE],
        claims={"email": "user@example.com"},
        expires_at=None,
    )

    credentials = await session_store.ensure_session_from_access_token(
        access_token, "user@example.com", None
    )

    assert credentials.scopes == [DRIVE_WRITE]
    assert stored["scopes"] == [DRIVE_WRITE]
