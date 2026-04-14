from auth.oauth_config import OAuthConfig


def test_oauth_config_public_client_oauth21_is_configured_without_jwt_signing_key(
    monkeypatch,
):
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "public-client-id")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", raising=False)

    cfg = OAuthConfig()
    assert cfg.is_public_client() is True
    assert cfg.is_configured() is True
    assert cfg.get_authorization_server_metadata()[
        "token_endpoint_auth_methods_supported"
    ] == ["none"]
