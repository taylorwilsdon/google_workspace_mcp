from types import SimpleNamespace

import pytest

import core.server as server_module


def test_configure_server_for_http_uses_base_required_scopes(monkeypatch):
    captured = {}

    class FakeGoogleProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.client_registration_options = SimpleNamespace(
                valid_scopes=kwargs.get("valid_scopes"),
                default_scopes=None,
            )

    monkeypatch.setattr(server_module, "get_transport_mode", lambda: "streamable-http")
    monkeypatch.setattr(server_module, "GoogleProvider", FakeGoogleProvider)
    monkeypatch.setattr(
        server_module,
        "get_current_scopes",
        lambda: [
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
    )
    monkeypatch.setattr(server_module, "set_auth_provider", lambda provider: None)

    # Capture and restore globals that configure_server_for_http() mutates directly
    monkeypatch.setattr(server_module, "_auth_provider", server_module._auth_provider)
    monkeypatch.setattr(server_module.server, "auth", server_module.server.auth)

    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: True,
            is_configured=lambda: True,
            is_public_client=lambda: False,
            is_external_oauth21_provider=lambda: False,
            client_id="client-id",
            client_secret="client-secret",
            get_oauth_base_url=lambda: "https://workspace-mcp.example.test",
            redirect_path="/oauth2callback",
        ),
    )

    server_module.configure_server_for_http()

    assert captured["required_scopes"] == sorted(server_module.BASE_SCOPES)
    assert captured["valid_scopes"] == sorted(server_module.get_current_scopes())
    assert (
        server_module.server.auth.client_registration_options.default_scopes
        == sorted(server_module.get_current_scopes())
    )


def test_configure_server_for_http_supports_public_client_with_jwt_key(monkeypatch):
    captured = {}

    class FakeGoogleProvider:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.client_registration_options = SimpleNamespace(
                valid_scopes=kwargs.get("valid_scopes"),
                default_scopes=None,
            )

    monkeypatch.setenv(
        "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY",
        "this-is-a-long-enough-jwt-signing-key",
    )
    monkeypatch.setattr(server_module, "get_transport_mode", lambda: "streamable-http")
    monkeypatch.setattr(server_module, "GoogleProvider", FakeGoogleProvider)
    monkeypatch.setattr(
        server_module,
        "get_current_scopes",
        lambda: [
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/userinfo.email",
            "openid",
        ],
    )
    monkeypatch.setattr(server_module, "set_auth_provider", lambda provider: None)
    monkeypatch.setattr(server_module, "_auth_provider", server_module._auth_provider)
    monkeypatch.setattr(server_module.server, "auth", server_module.server.auth)

    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: True,
            is_configured=lambda: True,
            is_public_client=lambda: True,
            is_external_oauth21_provider=lambda: False,
            client_id="public-client-id",
            client_secret=None,
            get_oauth_base_url=lambda: "https://workspace-mcp.example.test",
            redirect_path="/oauth2callback",
        ),
    )

    server_module.configure_server_for_http()

    assert captured["client_id"] == "public-client-id"
    assert captured["client_secret"] is None
    assert captured["jwt_signing_key"]


def test_configure_server_for_http_rejects_public_client_without_jwt_key(
    monkeypatch,
):
    monkeypatch.delenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", raising=False)
    monkeypatch.setattr(server_module, "get_transport_mode", lambda: "streamable-http")
    monkeypatch.setattr(server_module, "GoogleProvider", object)
    monkeypatch.setattr(server_module, "set_auth_provider", lambda provider: None)
    monkeypatch.setattr(server_module, "_auth_provider", server_module._auth_provider)
    monkeypatch.setattr(server_module.server, "auth", server_module.server.auth)
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: True,
            is_configured=lambda: True,
            is_public_client=lambda: True,
            is_external_oauth21_provider=lambda: False,
            client_id="public-client-id",
            client_secret=None,
            get_oauth_base_url=lambda: "https://workspace-mcp.example.test",
            redirect_path="/oauth2callback",
        ),
    )

    with pytest.raises(
        ValueError,
        match="Public client OAuth 2.1 requires FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY",
    ):
        server_module.configure_server_for_http()


def test_server_metadata_uses_env_overrides(monkeypatch):
    monkeypatch.setenv("MCP_NAME", "Custom Workspace MCP")
    monkeypatch.setenv("MCP_WEBSITE_URL", "https://example.test/workspace-mcp")

    assert server_module._get_server_name() == "Custom Workspace MCP"
    assert (
        server_module._get_server_website_url()
        == "https://example.test/workspace-mcp"
    )


def test_server_metadata_uses_generic_defaults(monkeypatch):
    monkeypatch.delenv("MCP_NAME", raising=False)
    monkeypatch.delenv("MCP_WEBSITE_URL", raising=False)
    monkeypatch.delenv("WORKSPACE_EXTERNAL_URL", raising=False)

    assert server_module._get_server_name() == "google_workspace"
    assert server_module._get_server_website_url() == "https://workspacemcp.com"


def test_verify_valkey_connectivity_uses_store_setup():
    class FakeStore:
        def __init__(self):
            self.setup_calls = 0

        async def setup(self):
            self.setup_calls += 1

    store = FakeStore()
    server_module._verify_valkey_connectivity(store, host="valkey", port=6379)
    assert store.setup_calls == 1


def test_verify_valkey_connectivity_raises_runtime_error_on_failure():
    class FakeStore:
        async def setup(self):
            raise OSError("connection refused")

    with pytest.raises(
        RuntimeError,
        match="Valkey storage configured but not reachable at valkey:6379",
    ):
        server_module._verify_valkey_connectivity(
            FakeStore(),
            host="valkey",
            port=6379,
        )


def test_configure_server_for_http_explicit_valkey_uses_setup_probe(monkeypatch):
    captured = {}

    class FakeValkeyStore:
        def __init__(self, **kwargs):
            captured["valkey_kwargs"] = kwargs
            self._client_config = SimpleNamespace(
                use_tls=False,
                request_timeout=None,
                advanced_config=None,
            )

        async def setup(self):
            captured["setup_called"] = True

    class FakeEncryptionWrapper:
        def __init__(self, *, key_value, fernet):
            self.key_value = key_value
            self.fernet = fernet

    class FakeGoogleProvider:
        def __init__(self, **kwargs):
            captured["provider_kwargs"] = kwargs
            self.client_registration_options = SimpleNamespace(default_scopes=None)

    def fake_verify(store, host, port):
        captured["verified_store"] = store
        captured["verified_host"] = host
        captured["verified_port"] = port

    monkeypatch.setenv("WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND", "valkey")
    monkeypatch.setenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST", "localhost")
    monkeypatch.setenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PORT", "6379")
    monkeypatch.setattr(server_module, "get_transport_mode", lambda: "streamable-http")
    monkeypatch.setattr(server_module, "GoogleProvider", FakeGoogleProvider)
    monkeypatch.setattr(server_module, "set_auth_provider", lambda provider: None)
    monkeypatch.setattr(server_module, "_auth_provider", server_module._auth_provider)
    monkeypatch.setattr(server_module.server, "auth", server_module.server.auth)
    monkeypatch.setattr(server_module, "_verify_valkey_connectivity", fake_verify)
    monkeypatch.setattr(
        "key_value.aio.stores.valkey.ValkeyStore",
        FakeValkeyStore,
    )
    monkeypatch.setattr(
        "key_value.aio.wrappers.encryption.FernetEncryptionWrapper",
        FakeEncryptionWrapper,
    )
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: True,
            is_configured=lambda: True,
            is_public_client=lambda: False,
            is_external_oauth21_provider=lambda: False,
            client_id="client-id",
            client_secret="client-secret",
            get_oauth_base_url=lambda: "https://workspace-mcp.example.test",
            redirect_path="/oauth2callback",
        ),
    )

    server_module.configure_server_for_http()

    assert captured["valkey_kwargs"] == {
        "host": "localhost",
        "port": 6379,
        "db": 0,
        "username": None,
        "password": None,
    }
    assert captured["verified_host"] == "localhost"
    assert captured["verified_port"] == 6379
    assert captured["verified_store"].__class__ is FakeValkeyStore
    assert captured["provider_kwargs"]["client_storage"].key_value is captured[
        "verified_store"
    ]
