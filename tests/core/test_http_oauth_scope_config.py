from types import SimpleNamespace

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
    assert server_module.server.auth.client_registration_options.default_scopes == sorted(
        server_module.get_current_scopes()
    )
