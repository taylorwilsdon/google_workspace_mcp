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


@pytest.mark.asyncio
async def test_start_google_auth_lists_accounts_in_legacy_mode(monkeypatch):
    class FakeStore:
        def list_users(self):
            return ["alice@example.com", "bob@example.com"]

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    result = await start_google_auth(list_accounts=True)

    assert result == "Authenticated accounts: alice@example.com, bob@example.com"
    assert "alice@example.com" in result
    assert "bob@example.com" in result


@pytest.mark.asyncio
async def test_start_google_auth_lists_accounts_when_service_name_omitted(monkeypatch):
    class FakeStore:
        def list_users(self):
            return ["alice@example.com"]

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    result_default = await start_google_auth()
    assert result_default == "Authenticated accounts: alice@example.com"

    result_flag = await start_google_auth(service_name="gmail", list_accounts=True)
    assert result_flag == "Authenticated accounts: alice@example.com"


@pytest.mark.asyncio
async def test_start_google_auth_lists_accounts_when_empty(monkeypatch):
    class FakeStore:
        def list_users(self):
            return []

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    result = await start_google_auth(list_accounts=True)
    assert result == "No accounts are currently authenticated."


@pytest.mark.asyncio
async def test_start_google_auth_list_accounts_disabled_in_oauth21(monkeypatch):
    class FakeStore:
        def list_users(self):
            return ["secret_user@example.com"]

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: True)
    monkeypatch.setattr("core.server.is_external_oauth21_provider", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    result = await start_google_auth(list_accounts=True)

    assert "start_google_auth is disabled when OAuth 2.1 is enabled" in result
    assert "secret_user@example.com" not in result


@pytest.mark.asyncio
async def test_start_google_auth_list_accounts_disabled_in_gateway_mode(monkeypatch):
    class FakeStore:
        def list_users(self):
            return ["secret_user@example.com"]

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: True)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    result = await start_google_auth(list_accounts=True)
    assert (
        "Listing accounts is disabled when gateway identity verification is enabled."
        in result
    )
    assert "secret_user@example.com" not in result

    result_list = await start_google_auth()
    assert (
        "Listing accounts is disabled when gateway identity verification is enabled."
        in result_list
    )
    assert "secret_user@example.com" not in result_list


@pytest.mark.asyncio
async def test_start_google_auth_missing_email_reports_authenticated_accounts(
    monkeypatch,
):
    class FakeStore:
        def list_users(self):
            return ["user1@example.com", "user2@example.com"]

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    with pytest.raises(ValueError) as excinfo:
        await start_google_auth(service_name="Gmail", user_google_email="")

    msg = str(excinfo.value)
    assert "user_google_email must be provided" in msg
    assert "Authenticated accounts: user1@example.com, user2@example.com" in msg


@pytest.mark.asyncio
async def test_start_google_auth_output_never_exposes_tokens_or_file_paths(monkeypatch):
    class FakeStoreWithSensitiveData:
        token = "ya29.sensitive_access_token_data"
        refresh_token = "1//secret_refresh_token_data"
        file_path = "/home/user/.credentials/user@example.com.json"

        def list_users(self):
            return ["alice@example.com"]

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store",
        lambda: FakeStoreWithSensitiveData(),
    )

    result = await start_google_auth(list_accounts=True)

    assert result == "Authenticated accounts: alice@example.com"
    for forbidden in [
        "ya29",
        "sensitive_access_token",
        "secret_refresh_token",
        ".credentials",
        "/home/user",
        "token",
        "secret",
        "json",
    ]:
        assert forbidden not in result.lower()


def test_get_legacy_authenticated_users_helper(monkeypatch):
    from auth.credential_store import get_legacy_authenticated_users

    class FakeStore:
        def list_users(self):
            return ["b@example.com", "a@example.com", "not-an-email"]

    monkeypatch.setattr("auth.oauth_config.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("auth.oauth_config.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: FakeStore()
    )

    users = get_legacy_authenticated_users()
    assert users == ["b@example.com", "a@example.com"]

    # In OAuth 2.1 mode -> returns empty
    monkeypatch.setattr("auth.oauth_config.is_oauth21_enabled", lambda: True)
    assert get_legacy_authenticated_users() == []

    # In gateway identity mode -> returns empty
    monkeypatch.setattr("auth.oauth_config.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("auth.oauth_config.is_trust_gateway_identity", lambda: True)
    assert get_legacy_authenticated_users() == []

    # Exception / unsupported store backend -> returns empty
    class BrokenStore:
        def list_users(self):
            raise NotImplementedError("unsupported")

    monkeypatch.setattr("auth.oauth_config.is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(
        "auth.credential_store.get_credential_store", lambda: BrokenStore()
    )
    assert get_legacy_authenticated_users() == []
