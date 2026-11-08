from types import SimpleNamespace

import pytest

from core.server import start_google_auth


@pytest.mark.asyncio
async def test_start_google_auth_skips_preflight_outside_stdio(monkeypatch):
    async def fake_start_auth_flow(**kwargs):  # noqa: ARG001
        return "auth-url"

    def fail_if_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("callback preflight should not run outside stdio")

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr(
        "auth.oauth_callback_server.get_transport_mode", lambda: "streamable-http"
    )
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)
    monkeypatch.setattr(
        "auth.oauth_callback_server.ensure_oauth_callback_available",
        fail_if_called,
    )

    result = await start_google_auth("Gmail", "user@gmail.com")

    assert result == "auth-url"


@pytest.mark.asyncio
async def test_start_google_auth_preflights_in_stdio(monkeypatch):
    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append((fn.__name__, args, kwargs))
        return fn(*args, **kwargs)

    async def fake_start_auth_flow(**kwargs):  # noqa: ARG001
        return "auth-url"

    def fake_ensure(transport_mode, port, base_uri):
        calls.append(("ensure", (transport_mode, port, base_uri), {}))
        return True, ""

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr(
        "auth.oauth_callback_server.get_transport_mode", lambda: "stdio"
    )
    monkeypatch.setattr("core.server.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)
    monkeypatch.setattr(
        "auth.oauth_callback_server.get_oauth_config",
        lambda: SimpleNamespace(port=8000, base_uri="http://localhost"),
    )
    monkeypatch.setattr(
        "auth.oauth_callback_server.ensure_oauth_callback_available",
        fake_ensure,
    )

    result = await start_google_auth("Gmail", "user@gmail.com")

    assert result == "auth-url"
    assert ("ensure", ("stdio", 8000, "http://localhost"), {}) in calls


@pytest.mark.asyncio
async def test_start_google_auth_uses_verified_gateway_principal(monkeypatch):
    captured = {}

    async def fake_get_verified_gateway_principal():
        return "verified@example.com"

    async def fake_start_auth_flow(**kwargs):
        captured.update(kwargs)
        return "auth-url"

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: True)
    monkeypatch.setattr(
        "core.server.get_verified_gateway_principal",
        fake_get_verified_gateway_principal,
    )
    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr(
        "auth.oauth_callback_server.get_transport_mode", lambda: "streamable-http"
    )
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode",
        lambda: "https://mcp.example.com/oauth2callback",
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)

    result = await start_google_auth("Gmail", "spoofed@example.com")

    assert result == "auth-url"
    assert captured["user_google_email"] == "verified@example.com"
    assert captured["principal_source"] == "gateway_assertion"


@pytest.fixture
def legacy_mode(monkeypatch):
    """Force legacy OAuth 2.0 mode in both the server and the credential-store helper."""
    for target in ("core.server", "auth.oauth_config"):
        monkeypatch.setattr(f"{target}.is_oauth21_enabled", lambda: False)
        monkeypatch.setattr(f"{target}.is_trust_gateway_identity", lambda: False)


def _use_store(monkeypatch, store):
    """Make the credential-store helper return the given fake store."""
    monkeypatch.setattr("auth.credential_store.get_credential_store", lambda: store)


class _FakeStore:
    """Credential store stub returning a fixed list of users."""

    def __init__(self, users):
        self._users = users

    def list_users(self):
        """Return the configured users."""
        return list(self._users)


@pytest.mark.asyncio
async def test_start_google_auth_lists_accounts_in_legacy_mode(
    monkeypatch, legacy_mode
):
    """list_accounts=True reports every stored account in legacy mode."""
    _use_store(monkeypatch, _FakeStore(["alice@example.com", "bob@example.com"]))

    result = await start_google_auth(list_accounts=True)

    assert result == "Authenticated accounts: alice@example.com, bob@example.com"


@pytest.mark.asyncio
async def test_start_google_auth_lists_accounts_when_service_name_omitted(
    monkeypatch, legacy_mode
):
    """Omitting service_name, or passing list_accounts, lists accounts."""
    _use_store(monkeypatch, _FakeStore(["alice@example.com"]))

    assert await start_google_auth() == "Authenticated accounts: alice@example.com"
    assert (
        await start_google_auth(service_name="gmail", list_accounts=True)
        == "Authenticated accounts: alice@example.com"
    )


@pytest.mark.asyncio
async def test_start_google_auth_blank_service_name_does_not_list(
    monkeypatch, legacy_mode
):
    """An explicitly blank service_name keeps the authentication path."""
    _use_store(monkeypatch, _FakeStore(["alice@example.com"]))

    with pytest.raises(ValueError) as excinfo:
        await start_google_auth(service_name="", user_google_email="")

    assert "user_google_email must be provided" in str(excinfo.value)


@pytest.mark.asyncio
async def test_start_google_auth_lists_accounts_when_empty(monkeypatch, legacy_mode):
    """An empty store reports that no accounts are authenticated."""
    _use_store(monkeypatch, _FakeStore([]))

    result = await start_google_auth(list_accounts=True)

    assert result == "No accounts are currently authenticated."


@pytest.mark.asyncio
async def test_start_google_auth_reports_store_failure(monkeypatch, legacy_mode):
    """A store that cannot be opened is reported, not shown as 'no accounts'."""

    def broken_store():
        raise OSError("credentials directory unreadable")

    monkeypatch.setattr("auth.credential_store.get_credential_store", broken_store)

    result = await start_google_auth(list_accounts=True)

    assert result.startswith("Could not list authenticated accounts:")
    assert "No accounts are currently authenticated" not in result
    assert "unreadable" not in result


@pytest.mark.asyncio
async def test_start_google_auth_list_accounts_disabled_in_oauth21(monkeypatch):
    """OAuth 2.1 mode never lists stored accounts."""
    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: True)
    monkeypatch.setattr("core.server.is_external_oauth21_provider", lambda: False)
    _use_store(monkeypatch, _FakeStore(["secret_user@example.com"]))

    result = await start_google_auth(list_accounts=True)

    assert "start_google_auth is disabled when OAuth 2.1 is enabled" in result
    assert "secret_user@example.com" not in result


@pytest.mark.asyncio
async def test_start_google_auth_list_accounts_disabled_in_gateway_mode(monkeypatch):
    """Trusted-gateway mode never lists stored accounts."""
    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: True)
    _use_store(monkeypatch, _FakeStore(["secret_user@example.com"]))

    for result in (
        await start_google_auth(list_accounts=True),
        await start_google_auth(),
    ):
        assert (
            "Listing accounts is disabled when gateway identity verification is enabled."
            in result
        )
        assert "secret_user@example.com" not in result


@pytest.mark.asyncio
async def test_start_google_auth_missing_email_reports_authenticated_accounts(
    monkeypatch, legacy_mode
):
    """A missing user_google_email error names the authenticated accounts."""
    _use_store(monkeypatch, _FakeStore(["user1@example.com", "user2@example.com"]))

    with pytest.raises(ValueError) as excinfo:
        await start_google_auth(service_name="Gmail", user_google_email="")

    msg = str(excinfo.value)
    assert "user_google_email must be provided" in msg
    assert "Authenticated accounts: user1@example.com, user2@example.com" in msg


@pytest.mark.asyncio
async def test_start_google_auth_output_never_exposes_tokens_or_file_paths(
    monkeypatch, legacy_mode
):
    """Only email addresses appear in the account list."""

    class FakeStoreWithSensitiveData(_FakeStore):
        """Store stub carrying token-like attributes that must never be printed."""

        token = "ya29.sensitive_access_token_data"
        refresh_token = "1//secret_refresh_token_data"
        file_path = "/home/user/.credentials/user@example.com.json"

    _use_store(monkeypatch, FakeStoreWithSensitiveData(["alice@example.com"]))

    result = await start_google_auth(list_accounts=True)

    assert result == "Authenticated accounts: alice@example.com"
    for forbidden in ["ya29", "secret", ".credentials", "/home/user", "json"]:
        assert forbidden not in result.lower()


def test_get_legacy_authenticated_users_helper(monkeypatch, legacy_mode):
    """The helper filters non-emails, respects modes and separates failures."""
    from auth.credential_store import (
        CredentialStoreUnavailableError,
        get_legacy_authenticated_users,
    )

    _use_store(
        monkeypatch, _FakeStore(["b@example.com", "a@example.com", "not-an-email"])
    )
    assert get_legacy_authenticated_users() == ["b@example.com", "a@example.com"]

    monkeypatch.setattr("auth.oauth_config.is_oauth21_enabled", lambda: True)
    assert get_legacy_authenticated_users() == []

    monkeypatch.setattr("auth.oauth_config.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("auth.oauth_config.is_trust_gateway_identity", lambda: True)
    assert get_legacy_authenticated_users() == []
    monkeypatch.setattr("auth.oauth_config.is_trust_gateway_identity", lambda: False)

    class NoEnumerationStore:
        """Backend that cannot enumerate users."""

        def list_users(self):
            """Signal that enumeration is unsupported."""
            raise NotImplementedError("unsupported")

    _use_store(monkeypatch, NoEnumerationStore())
    assert get_legacy_authenticated_users() == []

    class FailingStore:
        """Backend whose enumeration fails."""

        def list_users(self):
            """Fail as an unreadable store would."""
            raise OSError("disk error")

    _use_store(monkeypatch, FailingStore())
    with pytest.raises(CredentialStoreUnavailableError):
        get_legacy_authenticated_users()


@pytest.mark.asyncio
async def test_start_google_auth_blank_service_name_uses_generic_label(
    monkeypatch, legacy_mode
):
    """A blank service_name still authenticates, with a generic display label."""
    captured = {}

    async def fake_start_auth_flow(**kwargs):
        captured.update(kwargs)
        return "auth-url"

    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr("auth.oauth_callback_server.get_transport_mode", lambda: "http")
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)

    result = await start_google_auth(service_name="  ", user_google_email="a@x.com")

    assert result == "auth-url"
    assert captured["service_name"] == "Google Workspace"
    assert captured["user_google_email"] == "a@x.com"
