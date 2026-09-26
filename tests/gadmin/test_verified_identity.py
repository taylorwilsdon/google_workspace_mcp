"""Admin clients prove which account their credentials belong to with Google's
userinfo response, never with the caller's selection or an unverified ID token.

These tests run the real authentication chain (gadmin.auth -> _authenticate_service
-> the legacy or OAuth 2.1 authenticator) and replace only the stored credentials
and the HTTP transport.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import jwt
import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMockSequence

import auth.google_auth as google_auth
import auth.service_decorator as service_decorator
import gadmin.auth as admin_auth
from auth.google_auth import GoogleAuthenticationError
from gadmin.auth import AdminAuthenticationError, get_admin_service
from gadmin.registry import get_operation

ADMIN = "admin@op.example"
OTHER = "someone-else@op.example"
USERS_GET = get_operation("directory.users.get")


class _ScriptedHttp(HttpMockSequence):
    """HttpMockSequence plus the close() that the real authorized transport has."""

    def close(self):
        pass


def _credentials(id_token_email: str | None = None) -> Credentials:
    id_token = None
    if id_token_email:
        # Unsigned claims anyone could mint; they must not count as proof.
        id_token = jwt.encode(
            {"email": id_token_email}, "not-signed-by-google-" * 2, "HS256"
        )
    return Credentials(token="access-token", id_token=id_token, scopes=[])


def _userinfo(email: str, verified: bool = True) -> tuple[dict, str]:
    body = {"id": "1", "email": email, "verified_email": verified}
    return {"status": "200"}, json.dumps(body)


def _userinfo_error(status: str) -> tuple[dict, str]:
    return {"status": status}, json.dumps({"error": {"code": int(status)}})


@pytest.fixture
def transport(monkeypatch):
    """Route every Google HTTP call through a scripted response sequence."""
    responses: list[tuple[dict, str]] = []
    monkeypatch.setattr(
        google_auth,
        "_build_authorized_http",
        lambda credentials, timeout=30: _ScriptedHttp(responses),
    )
    return responses


@pytest.fixture
def legacy(monkeypatch, transport):
    """Legacy OAuth 2.0 with a stored credential whose owner the test chooses."""
    monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(admin_auth, "_current_session_id", lambda: None)
    monkeypatch.setattr(service_decorator, "is_service_account_enabled", lambda: False)
    stored = SimpleNamespace(credentials=_credentials())
    monkeypatch.setattr(
        google_auth, "get_credentials", lambda **kwargs: stored.credentials
    )
    return SimpleNamespace(stored=stored, transport=transport)


# --- Legacy OAuth 2.0 ------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_without_id_token_for_another_account_is_rejected(legacy):
    legacy.transport.append(_userinfo(OTHER))

    with pytest.raises(AdminAuthenticationError, match="Authenticated account"):
        await get_admin_service(ADMIN, USERS_GET, None)


@pytest.mark.asyncio
async def test_unverified_id_token_does_not_override_userinfo(legacy):
    legacy.stored.credentials = _credentials(id_token_email=ADMIN)
    legacy.transport.append(_userinfo(OTHER))

    with pytest.raises(AdminAuthenticationError, match="Authenticated account"):
        await get_admin_service(ADMIN, USERS_GET, None)


@pytest.mark.parametrize("status", ["401", "403", "500"])
@pytest.mark.asyncio
async def test_userinfo_failure_fails_closed(legacy, status):
    legacy.transport.append(_userinfo_error(status))

    with pytest.raises(GoogleAuthenticationError):
        await get_admin_service(ADMIN, USERS_GET, None)


@pytest.mark.asyncio
async def test_unverified_userinfo_email_fails_closed(legacy):
    legacy.transport.append(_userinfo(ADMIN, verified=False))

    with pytest.raises(GoogleAuthenticationError):
        await get_admin_service(ADMIN, USERS_GET, None)


@pytest.mark.asyncio
async def test_matching_userinfo_returns_directory_client(legacy):
    legacy.transport.append(_userinfo(ADMIN.upper()))

    service = await get_admin_service(ADMIN, USERS_GET, None)

    assert hasattr(service, "users")
    service.close()


@pytest.mark.asyncio
async def test_userinfo_scope_is_required_for_admin_clients(legacy, monkeypatch):
    requested = {}

    def get_credentials(**kwargs):
        requested["scopes"] = kwargs["required_scopes"]
        return legacy.stored.credentials

    monkeypatch.setattr(google_auth, "get_credentials", get_credentials)
    legacy.transport.append(_userinfo(ADMIN))

    (await get_admin_service(ADMIN, USERS_GET, None)).close()

    assert "https://www.googleapis.com/auth/userinfo.email" in requested["scopes"]


@pytest.mark.asyncio
async def test_non_admin_tools_keep_existing_behaviour(legacy):
    # No userinfo response is scripted: any userinfo call would raise.
    service, email = await google_auth.get_authenticated_google_service(
        "gmail", "v1", "search_gmail_messages", ADMIN, []
    )

    assert email == ADMIN
    service.close()


# --- Typed tools: require_google_service(bind_identity=True) --------------------


@pytest.mark.asyncio
async def test_bound_decorator_rejects_credential_for_another_account(
    legacy, monkeypatch
):
    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(service_decorator, "_detect_oauth_version", lambda *a: False)
    legacy.transport.append(_userinfo(OTHER))

    @service_decorator.require_google_service(
        "admin-directory", "admin_directory_user_read", bind_identity=True
    )
    async def sample_admin_tool(service, user_google_email: str) -> str:
        return "reached"

    with pytest.raises(GoogleAuthenticationError):
        await sample_admin_tool(user_google_email=ADMIN)


# --- OAuth 2.1 session store ------------------------------------------------------


@pytest.mark.asyncio
async def test_oauth21_session_credentials_are_verified_with_userinfo(
    monkeypatch, transport
):
    monkeypatch.setattr(service_decorator, "get_auth_provider", lambda: None)
    monkeypatch.setattr(service_decorator, "get_access_token", lambda: None)
    store = MagicMock()
    store.get_credentials_with_validation.return_value = _credentials()
    monkeypatch.setattr(service_decorator, "get_oauth21_session_store", lambda: store)
    monkeypatch.setattr(service_decorator, "build", MagicMock())
    transport.append(_userinfo(OTHER))

    _, email = await service_decorator.get_authenticated_google_service_oauth21(
        "admin", "directory_v1", "get_admin_user", ADMIN, [], verify_account=True
    )

    assert email == OTHER


# --- Service-account domain-wide delegation ---------------------------------------


@pytest.fixture
def service_account(monkeypatch, transport):
    monkeypatch.setattr(admin_auth, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(admin_auth, "_current_session_id", lambda: None)
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", ADMIN)
    monkeypatch.setattr(service_decorator, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: SimpleNamespace(dwd_allowed_domains=[]),
    )
    subjects = []

    def fake_credentials(requested_scopes, subject):
        subjects.append(subject)
        return object()

    monkeypatch.setattr(
        service_decorator, "_get_service_account_credentials", fake_credentials
    )
    monkeypatch.setattr(service_decorator, "build", MagicMock())
    return subjects


@pytest.mark.asyncio
async def test_service_account_binds_to_configured_subject_without_userinfo(
    service_account, monkeypatch
):
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)

    # No userinfo response is scripted; the signed subject is the binding.
    await get_admin_service(ADMIN, USERS_GET, None)

    assert service_account == [ADMIN]


@pytest.mark.asyncio
async def test_service_account_other_subject_needs_matching_gateway_principal(
    service_account, monkeypatch
):
    monkeypatch.setattr(admin_auth, "is_trust_gateway_identity", lambda: True)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: True)

    async def principal():
        return "gateway-user@op.example"

    monkeypatch.setattr(service_decorator, "get_verified_gateway_principal", principal)
    identity = SimpleNamespace(email=OTHER, via="gateway_assertion")

    with pytest.raises(GoogleAuthenticationError):
        await get_admin_service(OTHER, USERS_GET, identity)
    assert service_account == []
