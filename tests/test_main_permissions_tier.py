import logging
import os
import subprocess
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


def test_main_rejects_invalid_max_office_xml_bytes_at_startup(monkeypatch, capsys):
    monkeypatch.delenv("WORKSPACE_MCP_MAX_FILE_BYTES", raising=False)
    monkeypatch.setenv("WORKSPACE_MCP_MAX_OFFICE_XML_BYTES", "5MB")
    monkeypatch.setattr(sys, "argv", ["main.py"])
    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr("core.telemetry.configure_telemetry", lambda: None)

    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 2
    assert "WORKSPACE_MCP_MAX_OFFICE_XML_BYTES" in capsys.readouterr().err


def test_fastmcp_entrypoint_rejects_invalid_file_limits_at_startup():
    env = os.environ.copy()
    env["WORKSPACE_MCP_MAX_FILE_BYTES"] = "0"
    env["WORKSPACE_MCP_MAX_OFFICE_XML_BYTES"] = "5MB"

    result = subprocess.run(
        [sys.executable, "-c", "import fastmcp_server"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "WORKSPACE_MCP_MAX_OFFICE_XML_BYTES" in result.stderr


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

    def fake_resolve_port() -> None:
        calls.append("resolve")
        monkeypatch.setenv("WORKSPACE_MCP_PORT", "8123")
        monkeypatch.setenv("WORKSPACE_MCP_RESOLVED_PORT", "1")

    monkeypatch.setattr("auth.port_resolver.resolve_port", fake_resolve_port)
    monkeypatch.setattr(main, "reload_oauth_config", lambda: calls.append("reload"))

    main.resolve_stdio_callback_port()

    assert calls == ["resolve", "reload"]
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


def test_main_logs_once_when_signed_download_urls_are_set_on_stdio(monkeypatch, caplog):
    """The flag only takes effect over streamable-http; a stdio operator who sets
    it gets exactly one startup line saying so instead of silence."""

    def fake_run(*args, **kwargs):  # noqa: ARG001
        raise SystemExit(0)

    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr(main, "import_module", lambda name: object())  # noqa: ARG005
    monkeypatch.setattr(main, "set_enabled_tool_names", lambda names: None)
    monkeypatch.setattr(main, "wrap_server_tool_method", lambda server: None)
    monkeypatch.setattr(main, "filter_server_tools", lambda server: None)
    monkeypatch.setattr(main, "set_transport_mode", lambda transport: None)
    monkeypatch.setattr(main, "get_selected_backend", lambda: "local_directory")
    monkeypatch.setattr(main, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(main, "get_credential_store", lambda: object())
    monkeypatch.setattr(main, "check_credentials_directory_permissions", lambda: None)
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            service_account_key_file=None,
            service_account_key_json=None,
            client_secret="secret",
            client_secrets_file=None,
            is_oauth21_enabled=lambda: False,
            is_configured=lambda: True,
        ),
    )
    monkeypatch.setattr(main.server, "run", fake_run)
    monkeypatch.setattr(
        sys, "argv", ["main.py", "--tools", "gmail", "--transport", "stdio"]
    )
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "user@example.com")
    monkeypatch.setenv("WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS", "true")

    main.STARTUP_NOTICES.clear()
    with pytest.raises(SystemExit) as exc:
        main.main()

    assert exc.value.code == 0
    # Queued for the startup screen with the other configuration advisories.
    notes = [n for n in main.STARTUP_NOTICES if "ignored" in n]
    assert len(notes) == 1 and "WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS" in notes[0]


# --- Startup validation for signed download URLs -----------------------------------

_SIGNED_ENV_TO_CLEAN = (
    "MCP_ENABLE_OAUTH21",
    "EXTERNAL_OAUTH21_PROVIDER",
    "WORKSPACE_MCP_STATELESS_MODE",
    "MCP_SINGLE_USER_MODE",
    "WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS",
    "WORKSPACE_MCP_MAX_FILE_BYTES",
    "WORKSPACE_MCP_MAX_OFFICE_XML_BYTES",
    "WORKSPACE_EXTERNAL_URL",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY",
    "WORKSPACE_MCP_TRANSPORT",
    "WORKSPACE_MCP_TOOLS",
    "WORKSPACE_MCP_PERMISSIONS",
    "WORKSPACE_MCP_READ_ONLY",
    "WORKSPACE_MCP_TOOL_TIER",
    "WORKSPACE_MCP_HTTP_PORT",
    "WORKSPACE_MCP_RESOLVED_PORT",
    "WORKSPACE_MCP_HOST",
)
_SIGNED_FLAG = "WORKSPACE_MCP_SIGNED_DOWNLOAD_URLS"
_BASE_URL_NOTE = "/attachments/signed/* must be publicly reachable"


@pytest.fixture
def signed_startup(monkeypatch):
    """Run ``main.main()`` to the point of ``server.run`` (stubbed) with a clean
    environment, so ambient settings cannot decide the outcome. Returns a runner
    taking the transport and the env to set; it returns the exit code."""
    from auth import oauth_config
    from core import signed_downloads

    for name in _SIGNED_ENV_TO_CLEAN:
        monkeypatch.delenv(name, raising=False)
    main.STARTUP_NOTICES.clear()
    # No key material unless a test sets it: the client-secret fallback reads the
    # OAuth config, which a developer's .env or client-secrets file could fill.
    monkeypatch.setattr(
        oauth_config,
        "get_oauth_config",
        lambda: SimpleNamespace(
            client_secret=None,
            is_service_account_enabled=lambda: False,
            is_external_oauth21_provider=lambda: False,
        ),
    )
    monkeypatch.setenv("PORT", "0")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "0")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "user@example.com")

    def fake_run(*args, **kwargs):  # noqa: ARG001
        raise SystemExit(0)

    monkeypatch.setattr(main, "configure_safe_logging", lambda: None)
    monkeypatch.setattr("core.telemetry.configure_telemetry", lambda: None)
    monkeypatch.setattr(main, "import_module", lambda name: object())  # noqa: ARG005
    monkeypatch.setattr(main, "set_enabled_tool_names", lambda names: None)
    monkeypatch.setattr(main, "wrap_server_tool_method", lambda server: None)
    monkeypatch.setattr(main, "filter_server_tools", lambda server: None)
    monkeypatch.setattr(main, "set_transport_mode", lambda transport: None)
    monkeypatch.setattr(main, "configure_server_for_http", lambda: None)
    monkeypatch.setattr(main, "get_selected_backend", lambda: "local_directory")
    monkeypatch.setattr(main, "is_stateless_mode", lambda: False)
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: False)
    monkeypatch.setattr(main, "get_credential_store", lambda: object())
    monkeypatch.setattr(main, "check_credentials_directory_permissions", lambda: None)
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: SimpleNamespace(
            service_account_key_file=None,
            service_account_key_json=None,
            client_secret="secret",
            client_secrets_file=None,
            is_oauth21_enabled=lambda: False,
            is_configured=lambda: True,
        ),
    )
    monkeypatch.setattr(main.server, "run", fake_run)

    def run(transport, **env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr(
            sys, "argv", ["main.py", "--tools", "gmail", "--transport", transport]
        )
        signed_downloads._signing_key.cache_clear()
        try:
            with pytest.raises(SystemExit) as exc:
                main.main()
        finally:
            signed_downloads._signing_key.cache_clear()
        return exc.value.code

    return run


def _base_url_notes(caplog):  # noqa: ARG001
    """The base-URL line joins the startup screen's notices, not the log."""
    return [n for n in main.STARTUP_NOTICES if _BASE_URL_NOTE in n]


def test_signed_downloads_refuse_to_start_without_external_url(signed_startup, capsys):
    code = signed_startup(
        "streamable-http",
        **{_SIGNED_FLAG: "true", "GOOGLE_OAUTH_CLIENT_SECRET": "secret"},
    )
    err = capsys.readouterr().err
    assert code == 2
    assert _SIGNED_FLAG in err and "WORKSPACE_EXTERNAL_URL" in err


@pytest.mark.parametrize(
    "external_url", ["", "   ", "mcp.example.com", "/mcp", "ftp://mcp.example.com"]
)
def test_signed_downloads_refuse_to_start_with_unusable_external_url(
    signed_startup, capsys, external_url
):
    code = signed_startup(
        "streamable-http",
        **{
            _SIGNED_FLAG: "true",
            "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
            "WORKSPACE_EXTERNAL_URL": external_url,
        },
    )
    err = capsys.readouterr().err
    assert code == 2
    assert _SIGNED_FLAG in err and "WORKSPACE_EXTERNAL_URL" in err


def test_signed_downloads_refuse_to_start_without_key_material(signed_startup, capsys):
    code = signed_startup(
        "streamable-http",
        **{_SIGNED_FLAG: "true", "WORKSPACE_EXTERNAL_URL": "https://mcp.example.com"},
    )
    err = capsys.readouterr().err
    assert code == 2
    assert _SIGNED_FLAG in err and "GOOGLE_OAUTH_CLIENT_SECRET" in err
    assert "WORKSPACE_EXTERNAL_URL" not in err


def test_signed_downloads_name_every_problem_at_once(signed_startup, capsys):
    code = signed_startup("streamable-http", **{_SIGNED_FLAG: "true"})
    err = capsys.readouterr().err
    assert code == 2
    assert "WORKSPACE_EXTERNAL_URL" in err and "GOOGLE_OAUTH_CLIENT_SECRET" in err


@pytest.mark.parametrize(
    "key_env",
    ["GOOGLE_OAUTH_CLIENT_SECRET", "FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY"],
)
def test_signed_downloads_valid_config_starts_and_logs_the_base_url_once(
    signed_startup, caplog, key_env
):
    with caplog.at_level(logging.INFO, logger="core.signed_downloads"):
        code = signed_startup(
            "streamable-http",
            **{
                _SIGNED_FLAG: "true",
                "WORKSPACE_EXTERNAL_URL": "https://mcp.example.com/",
                key_env: "material-with-enough-entropy",
            },
        )
    assert code == 0
    notes = _base_url_notes(caplog)
    assert notes == [
        f"{_SIGNED_FLAG} is on: signed download links will use base URL "
        "https://mcp.example.com; /attachments/signed/* must be publicly "
        "reachable there."
    ]


def test_signed_downloads_flag_off_starts_without_checks_or_log(signed_startup, caplog):
    with caplog.at_level(logging.DEBUG, logger="core.signed_downloads"):
        code = signed_startup("streamable-http")
    assert code == 0
    assert _base_url_notes(caplog) == []
    assert [r for r in caplog.records if r.name == "core.signed_downloads"] == []


def test_signed_downloads_flag_on_stdio_starts_and_only_notes_it_is_ignored(
    signed_startup, caplog
):
    with caplog.at_level(logging.INFO, logger="core.signed_downloads"):
        code = signed_startup("stdio", **{_SIGNED_FLAG: "true"})
    assert code == 0
    notes = [n for n in main.STARTUP_NOTICES if "ignored" in n]
    assert len(notes) == 1 and _SIGNED_FLAG in notes[0]
    assert _base_url_notes(caplog) == []


def test_fastmcp_entrypoint_refuses_signed_downloads_without_external_url():
    env = {k: v for k, v in os.environ.items() if k not in _SIGNED_ENV_TO_CLEAN}
    env[_SIGNED_FLAG] = "true"
    # Explicitly relative (not unset) so a local .env cannot supply a valid one.
    env["WORKSPACE_EXTERNAL_URL"] = "mcp.example.com"
    env["GOOGLE_OAUTH_CLIENT_SECRET"] = "secret"

    result = subprocess.run(
        [sys.executable, "-c", "import fastmcp_server"],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert _SIGNED_FLAG in result.stderr and "WORKSPACE_EXTERNAL_URL" in result.stderr


def test_signed_downloads_loads_inside_the_stdout_capture():
    """main.py captures stdout at import so stray output cannot corrupt the stdio
    JSON-RPC stream; signed_downloads (Google and HTTP stacks) must load after
    that capture, alongside the other deferred startup imports."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(main.__file__).read_text())
    top_level = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any("signed_downloads" in alias.name for alias in node.names)
    ]
    assert top_level == []
    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_load_startup_dependencies"
    )
    assert "signed_downloads" in ast.unparse(loader)
