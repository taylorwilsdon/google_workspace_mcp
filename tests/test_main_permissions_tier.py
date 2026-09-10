import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep these tests independent of a developer's local .env. Importing main loads
# .env, and OAuth 2.1 mode changes tool schemas at decoration time.
os.environ["MCP_ENABLE_OAUTH21"] = "false"
os.environ["WORKSPACE_MCP_STATELESS_MODE"] = "false"

import main


def test_main_rejects_invalid_max_file_bytes_at_startup(monkeypatch, capsys):
    monkeypatch.setenv("WORKSPACE_MCP_MAX_FILE_BYTES", "5MB")
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr("core.telemetry.configure_telemetry", lambda: None)

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 2
    assert "WORKSPACE_MCP_MAX_FILE_BYTES" in capsys.readouterr().err


def test_resolve_permissions_mode_selection_without_tier():
    services = ["gmail", "drive"]
    resolved_services, tier_tool_filter = main.resolve_permissions_mode_selection(
        services, None
    )
    assert resolved_services == services
    assert tier_tool_filter is None


def test_resolve_permissions_mode_selection_with_tier_filters_services(monkeypatch):
    def fake_resolve_tools_from_tier(tier, services):
        assert tier == "core"
        assert services == ["gmail", "drive", "slides"]
        return ["search_gmail_messages"], ["gmail"]

    monkeypatch.setattr(main, "resolve_tools_from_tier", fake_resolve_tools_from_tier)

    resolved_services, tier_tool_filter = main.resolve_permissions_mode_selection(
        ["gmail", "drive", "slides"], "core"
    )
    assert resolved_services == ["gmail"]
    assert tier_tool_filter == {"search_gmail_messages"}


def test_narrow_permissions_to_services_keeps_selected_order():
    permissions = {"drive": "full", "gmail": "readonly", "calendar": "readonly"}
    narrowed = main.narrow_permissions_to_services(permissions, ["gmail", "drive"])
    assert narrowed == {"gmail": "readonly", "drive": "full"}


def test_narrow_permissions_to_services_drops_non_selected_services():
    permissions = {"gmail": "send", "drive": "full"}
    narrowed = main.narrow_permissions_to_services(permissions, ["gmail"])
    assert narrowed == {"gmail": "send"}


def test_resolve_stdio_callback_port_marks_resolved_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    seen_fallback: list[bool] = []
    seen_preferred: list[object] = []

    def fake_resolve_port(preferred=None, allow_fallback: bool = True) -> None:
        calls.append("resolve")
        seen_fallback.append(allow_fallback)
        seen_preferred.append(preferred)
        monkeypatch.setenv("WORKSPACE_MCP_PORT", "8123")
        monkeypatch.setenv("WORKSPACE_MCP_RESOLVED_PORT", "1")

    monkeypatch.delenv("GOOGLE_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setattr("auth.port_resolver.resolve_port", fake_resolve_port)
    monkeypatch.setattr(main, "reload_oauth_config", lambda: calls.append("reload"))

    main.resolve_stdio_callback_port()

    assert calls == ["resolve", "reload"]
    assert seen_fallback == [True]
    # No redirect URI pinned, so no preferred port is forced.
    assert seen_preferred == [None]
    assert os.environ["WORKSPACE_MCP_RESOLVED_PORT"] == "1"


def test_resolve_stdio_callback_port_disables_fallback_for_pinned_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit GOOGLE_OAUTH_REDIRECT_URI pins the port; fallback would mismatch."""
    seen_fallback: list[bool] = []
    seen_preferred: list[object] = []

    def fake_resolve_port(preferred=None, allow_fallback: bool = True) -> None:
        seen_fallback.append(allow_fallback)
        seen_preferred.append(preferred)

    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8103/oauth2callback"
    )
    monkeypatch.setattr("auth.port_resolver.resolve_port", fake_resolve_port)
    monkeypatch.setattr(main, "reload_oauth_config", lambda: None)

    main.resolve_stdio_callback_port()

    assert seen_fallback == [False]
    # The pinned loopback port, not WORKSPACE_MCP_PORT, drives the probe.
    assert seen_preferred == [8103]


def test_loopback_redirect_port_extracts_explicit_port():
    """An explicit loopback redirect URI names the port the listener must bind."""
    assert main._loopback_redirect_port("http://localhost:8103/oauth2callback") == 8103
    assert main._loopback_redirect_port("http://127.0.0.1:9000/oauth2callback") == 9000
    assert main._loopback_redirect_port("http://[::1]:9100/oauth2callback") == 9100


def test_loopback_redirect_port_ignores_unusable_uris():
    """Only an explicit loopback port may override WORKSPACE_MCP_PORT."""
    # No URI at all, and no explicit port, leave the env-var default in charge.
    assert main._loopback_redirect_port(None) is None
    assert main._loopback_redirect_port("") is None
    assert main._loopback_redirect_port("http://localhost/oauth2callback") is None
    # A non-loopback URI means a proxy fronts the callback; its public port says
    # nothing about which local port the listener should own.
    assert (
        main._loopback_redirect_port("https://mcp.example.com:443/oauth2callback")
        is None
    )
    # A malformed port must not raise out of startup.
    assert main._loopback_redirect_port("http://localhost:notaport/oauth2") is None
    # https on loopback means a local TLS terminator owns that port and forwards to
    # the plaintext listener elsewhere; MinimalOAuthServer cannot serve TLS itself.
    assert main._loopback_redirect_port("https://localhost:8104/oauth2callback") is None
    assert main._loopback_redirect_port("https://127.0.0.1:8443/oauth2callback") is None


def test_resolve_stdio_callback_port_binds_the_redirect_uris_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: listener port must follow an explicit loopback redirect URI.

    OAuthConfig returns GOOGLE_OAUTH_REDIRECT_URI verbatim, so Google sends the
    browser to that port regardless of WORKSPACE_MCP_PORT. If the resolver keeps
    the env-var port, the listener binds one port while the redirect targets
    another and the auth flow hangs.

    The probe is stubbed rather than binding real sockets: a free port discovered
    by bind-then-close can be taken by another process before the assertion runs,
    and this test is about which port the resolver *chooses*, not about whether
    the OS had it free.
    """
    import auth.port_resolver as port_resolver

    env_port, redirect_port = 8000, 8103
    monkeypatch.setattr(port_resolver, "_is_port_free", lambda host, port: True)
    monkeypatch.setenv("WORKSPACE_MCP_PORT", str(env_port))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_HOST", "127.0.0.1")
    monkeypatch.setenv(
        "GOOGLE_OAUTH_REDIRECT_URI", f"http://localhost:{redirect_port}/oauth2callback"
    )
    monkeypatch.setattr(main, "reload_oauth_config", lambda: None)

    main.resolve_stdio_callback_port()

    assert os.environ["WORKSPACE_MCP_PORT"] == str(redirect_port)
    assert os.environ["WORKSPACE_MCP_RESOLVED_PORT"] == "1"


def test_resolve_callback_port_for_transport_skips_streamable_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called() -> None:
        raise AssertionError("stdio port resolver must not run for streamable HTTP")

    monkeypatch.setattr(main, "resolve_stdio_callback_port", fail_if_called)
    monkeypatch.setenv("WORKSPACE_MCP_RESOLVED_PORT", "1")

    main.resolve_callback_port_for_transport("streamable-http")

    assert "WORKSPACE_MCP_RESOLVED_PORT" not in os.environ


def test_resolve_bind_host_defaults_legacy_streamable_http_to_loopback(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: False,
            is_configured=lambda: True,
        ),
    )
    monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)

    assert main.resolve_bind_host_for_transport("streamable-http") == "127.0.0.1"


def test_resolve_bind_host_preserves_explicit_legacy_streamable_http_host(
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: False,
            is_configured=lambda: True,
        ),
    )
    monkeypatch.setenv("WORKSPACE_MCP_HOST", "0.0.0.0")

    assert main.resolve_bind_host_for_transport("streamable-http") == "0.0.0.0"


def test_resolve_bind_host_preserves_oauth21_streamable_http_default(monkeypatch):
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: True,
            is_configured=lambda: True,
        ),
    )
    monkeypatch.delenv("WORKSPACE_MCP_HOST", raising=False)

    assert main.resolve_bind_host_for_transport("streamable-http") == "0.0.0.0"


def test_validate_streamable_http_auth_rejects_unconfigured_oauth21(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: True,
            is_configured=lambda: False,
        ),
    )

    with pytest.raises(SystemExit) as exc:
        main.validate_streamable_http_auth("streamable-http")

    assert exc.value.code == 1
    assert "requires GOOGLE_OAUTH_CLIENT_ID" in capsys.readouterr().err


def test_validate_streamable_http_auth_allows_stdio(monkeypatch):
    def fail_if_called():
        raise AssertionError("stdio should not check OAuth 2.1 config")

    monkeypatch.setattr(main, "get_oauth_config", fail_if_called)

    main.validate_streamable_http_auth("stdio")


def test_permissions_and_tools_flags_are_rejected(monkeypatch, capsys):
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["main.py", "--permissions", "gmail:readonly", "--tools", "gmail"],
    )

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "--permissions and --tools cannot be combined" in captured.err


def test_main_skips_gcs_store_initialization_in_service_account_mode(monkeypatch):
    service_account_json = '{"type":"service_account","project_id":"p","private_key":"k","client_email":"svc@example.com"}'

    def fail_if_called():
        raise AssertionError("credential store should not be initialized")

    def fail_permission_check():
        raise AssertionError("local credential directory check should be skipped")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        raise SystemExit(0)

    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr(main, "import_module", lambda name: object())  # noqa: ARG005
    monkeypatch.setattr(main, "set_enabled_tool_names", lambda names: None)
    monkeypatch.setattr(main, "wrap_server_tool_method", lambda server: None)
    monkeypatch.setattr(main, "filter_server_tools", lambda server: None)
    monkeypatch.setattr(main, "set_transport_mode", lambda transport: None)
    monkeypatch.setattr(main, "get_selected_backend", lambda: "gcs")
    monkeypatch.setattr(main, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(main, "get_credential_store", fail_if_called)
    monkeypatch.setattr(
        main, "check_credentials_directory_permissions", fail_permission_check
    )
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            service_account_key_file=None,
            service_account_key_json=service_account_json,
            client_secret=None,
            client_secrets_file=None,
        ),
    )
    monkeypatch.setattr(main.server, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["main.py", "--tools", "gmail"])
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "user@example.com")

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 0
