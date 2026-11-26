"""Service-account mode without domain-wide delegation (acts as itself)."""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth import oauth_config as oauth_config_module
from auth import service_decorator
from auth.oauth_config import OAuthConfig
from auth.scopes import DRIVE_FILE_SCOPE, DRIVE_SCOPE

SA_EMAIL = "bot@proj.iam.gserviceaccount.com"
SA_KEY_JSON = json.dumps(
    {
        "type": "service_account",
        "project_id": "proj",
        "private_key": "k",
        "client_email": SA_EMAIL,
    }
)


def _base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "MCP_ENABLE_OAUTH21",
        "EXTERNAL_OAUTH21_PROVIDER",
        "TRUST_GATEWAY_IDENTITY",
        "GATEWAY_IDENTITY_JWKS_URL",
        "GATEWAY_IDENTITY_AUDIENCE",
        "USER_GOOGLE_EMAIL",
        "WORKSPACE_MCP_STATELESS_MODE",
        "GOOGLE_SERVICE_ACCOUNT_KEY_FILE",
        "GOOGLE_SERVICE_ACCOUNT_DELEGATION",
        "DWD_ALLOWED_DOMAINS",
        "GOOGLE_CLIENT_SECRET_PATH",
        "GOOGLE_CLIENT_SECRETS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        oauth_config_module,
        "get_client_secrets_path",
        lambda: "/nonexistent/client_secret.json",
    )
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON", SA_KEY_JSON)


# --- config ---


def test_delegation_defaults_to_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    cfg = OAuthConfig()
    assert cfg.service_account_delegation is True
    assert cfg.is_service_account_no_delegation() is False


@pytest.mark.parametrize("value", ["false", "0", "no", "OFF"])
def test_delegation_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", value)
    cfg = OAuthConfig()
    assert cfg.is_service_account_no_delegation() is True


def test_no_delegation_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", "false")
    cfg = OAuthConfig()
    assert cfg.is_service_account_no_delegation() is False


def test_dwd_allowed_domains_ignored_without_delegation(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", "false")
    monkeypatch.setenv("DWD_ALLOWED_DOMAINS", "corp.com")
    with caplog.at_level(logging.WARNING):
        cfg = OAuthConfig()
    assert cfg.dwd_allowed_domains == []
    assert "DWD_ALLOWED_DOMAINS is ignored" in caplog.text


def test_dwd_allowed_domains_kept_with_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("DWD_ALLOWED_DOMAINS", "Corp.com")
    assert OAuthConfig().dwd_allowed_domains == ["corp.com"]


# --- decorator ---


def _patch_no_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], object]:
    monkeypatch.setattr(
        service_decorator, "is_service_account_no_delegation", lambda: True
    )
    monkeypatch.setattr(
        service_decorator, "_get_service_account_email", lambda: SA_EMAIL
    )
    captured = {}
    fake_service = object()

    def fake_creds(scopes: list[str], subject: str | None = None) -> object:
        captured["scopes"] = scopes
        captured["subject"] = subject
        return object()

    monkeypatch.setattr(
        service_decorator, "_get_service_account_credentials", fake_creds
    )
    monkeypatch.setattr(
        service_decorator, "build_google_service", lambda *a, **k: fake_service
    )
    monkeypatch.setattr(service_decorator, "record_google_principal", MagicMock())
    return captured, fake_service


def test_user_email_is_managed_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_decorator, "is_service_account_no_delegation", lambda: True
    )
    assert service_decorator._user_email_is_managed() is True


def test_extract_managed_email_returns_service_account_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_decorator, "is_service_account_no_delegation", lambda: True
    )
    monkeypatch.setattr(
        service_decorator, "_get_service_account_email", lambda: SA_EMAIL
    )
    assert service_decorator._extract_managed_user_email(None, None, "tool") == SA_EMAIL


@pytest.mark.asyncio
async def test_authenticate_without_delegation_uses_no_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, fake_service = _patch_no_delegation(monkeypatch)
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)

    service, actual = await service_decorator._authenticate_service(
        use_oauth21=False,
        service_name="sheets",
        service_version="v4",
        tool_name="read_sheet_values",
        user_google_email=SA_EMAIL,
        resolved_scopes=["scope-a"],
        mcp_session_id=None,
        authenticated_user=None,
    )

    assert service is fake_service
    assert actual == SA_EMAIL
    assert captured == {"scopes": ["scope-a"], "subject": None}


@pytest.mark.asyncio
async def test_authenticate_without_delegation_widens_drive_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, _ = _patch_no_delegation(monkeypatch)
    await service_decorator._authenticate_service(
        use_oauth21=False,
        service_name="drive",
        service_version="v3",
        tool_name="create_drive_file",
        user_google_email=SA_EMAIL,
        resolved_scopes=[DRIVE_FILE_SCOPE],
        mcp_session_id=None,
        authenticated_user=None,
    )
    assert captured["scopes"] == [DRIVE_SCOPE]


@pytest.mark.asyncio
@pytest.mark.parametrize("service_name", ["gmail", "people", "chat", "script"])
async def test_unsupported_services_fail_clearly(
    monkeypatch: pytest.MonkeyPatch, service_name: str
) -> None:
    captured, _ = _patch_no_delegation(monkeypatch)
    with pytest.raises(service_decorator.GoogleAuthenticationError) as exc:
        await service_decorator._authenticate_service(
            use_oauth21=False,
            service_name=service_name,
            service_version="v1",
            tool_name="some_tool",
            user_google_email=SA_EMAIL,
            resolved_scopes=["scope-a"],
            mcp_session_id=None,
            authenticated_user=None,
        )
    assert "some_tool" in str(exc.value)
    assert captured == {}


def test_credentials_builder_passes_no_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {}

    def fake_from_info(
        info: dict[str, Any], scopes: list[str], subject: str | None
    ) -> str:
        calls["subject"] = subject
        return "creds"

    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(
            service_account_key_file=None, service_account_key_json=SA_KEY_JSON
        ),
    )
    monkeypatch.setattr(
        service_decorator.google_service_account.Credentials,
        "from_service_account_info",
        staticmethod(fake_from_info),
    )
    assert service_decorator._get_service_account_credentials(["s"]) == "creds"
    assert calls["subject"] is None


# --- gateway identity is incompatible with no delegation ---


def test_trust_gateway_identity_rejected_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", "false")
    monkeypatch.setenv("TRUST_GATEWAY_IDENTITY", "true")
    monkeypatch.setenv(
        "GATEWAY_IDENTITY_JWKS_URL", "https://gateway.example.com/.well-known/jwks.json"
    )
    monkeypatch.setenv("GATEWAY_IDENTITY_AUDIENCE", "mcp.example.com")
    with pytest.raises(ValueError, match="without delegation"):
        OAuthConfig()


# --- decorator signature / injection ---


@pytest.mark.asyncio
async def test_decorated_tool_hides_email_and_receives_service_account_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspect

    _patch_no_delegation(monkeypatch)
    # The connection-recycling wrapper expects a real-looking Resource.
    monkeypatch.setattr(
        service_decorator, "build_google_service", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)

    @service_decorator.require_google_service("drive", "drive_read")
    async def sample_tool(
        service: Any, user_google_email: str, query: str = "q"
    ) -> tuple[str, str]:
        return user_google_email, query

    assert "user_google_email" not in inspect.signature(sample_tool).parameters
    assert await sample_tool(query="abc") == (SA_EMAIL, "abc")


# --- server-level handling when USER_GOOGLE_EMAIL is also set ---


def _server_with_email_free_tool(monkeypatch: pytest.MonkeyPatch) -> Any:
    from core import server as server_module
    from core.server import SecureFastMCP

    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", "stale@example.com")
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(server_module, "is_trust_gateway_identity", lambda: False)
    monkeypatch.setattr(server_module, "is_service_account_no_delegation", lambda: True)

    server = SecureFastMCP(name="sa_test")

    def echo(query: str) -> str:
        return query

    server.tool()(echo)
    return server


@pytest.mark.asyncio
async def test_call_tool_does_not_inject_email_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server_with_email_free_tool(monkeypatch)
    result = await server.call_tool("echo", {"query": "hi"})
    assert result.content[0].text == "hi"


@pytest.mark.asyncio
async def test_call_tool_drops_caller_supplied_email_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _server_with_email_free_tool(monkeypatch)
    result = await server.call_tool(
        "echo", {"query": "hi", "user_google_email": "cached@example.com"}
    )
    assert result.content[0].text == "hi"


# --- key loading errors ---


# --- delegation flag validation ---


@pytest.mark.parametrize("value", ["flase", "maybe", "2"])
def test_unrecognized_delegation_value_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", value)
    with pytest.raises(ValueError, match="GOOGLE_SERVICE_ACCOUNT_DELEGATION"):
        OAuthConfig()


@pytest.mark.parametrize("value", ["", "  ", "true", "1", "YES", "on"])
def test_empty_or_truthy_delegation_value_keeps_delegation(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", value)
    assert OAuthConfig().service_account_delegation is True


# --- start_google_auth ---


@pytest.mark.asyncio
async def test_start_google_auth_is_a_noop_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core import server as server_module

    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(server_module, "is_service_account_no_delegation", lambda: True)
    result = await server_module.start_google_auth(service_name="drive")
    assert "not used in service-account mode" in result


# --- service account email, read once from the key when config loads ---


def test_get_service_account_email_uses_config_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(service_account_email=SA_EMAIL),
    )
    assert service_decorator._get_service_account_email() == SA_EMAIL


def test_get_service_account_email_raises_when_key_has_no_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(service_account_email=None),
    )
    with pytest.raises(
        service_decorator.GoogleAuthenticationError, match="client_email"
    ):
        service_decorator._get_service_account_email()


def test_read_client_email_from_key_file(tmp_path: Path) -> None:
    key = tmp_path / "key.json"
    key.write_text(SA_KEY_JSON)
    assert (
        oauth_config_module._read_service_account_client_email(str(key), None)
        == SA_EMAIL
    )


def test_read_client_email_from_json() -> None:
    assert (
        oauth_config_module._read_service_account_client_email(None, SA_KEY_JSON)
        == SA_EMAIL
    )


@pytest.mark.parametrize(
    "key_json", ["{nope", "[]", json.dumps({"type": "service_account"}), ""]
)
def test_read_client_email_returns_none_for_bad_keys(key_json: str) -> None:
    assert (
        oauth_config_module._read_service_account_client_email(None, key_json) is None
    )


def test_config_stores_client_email(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    assert OAuthConfig().service_account_email == SA_EMAIL


def test_config_has_no_email_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _base_env(monkeypatch)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON")
    assert OAuthConfig().service_account_email is None


# --- positional arguments in managed mode ---


@pytest.mark.asyncio
async def test_positional_args_do_not_collide_with_injected_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_no_delegation(monkeypatch)
    monkeypatch.setattr(
        service_decorator, "build_google_service", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)

    @service_decorator.require_google_service("drive", "drive_read")
    async def sample_tool(
        service: Any, user_google_email: str, query: str = "q"
    ) -> tuple[str, str]:
        return user_google_email, query

    assert await sample_tool("abc") == (SA_EMAIL, "abc")


def test_managed_call_kwargs_rejects_duplicate_and_extra_args() -> None:
    import inspect

    def visible(query: str) -> None:
        return None

    sig = inspect.signature(visible)
    assert service_decorator._managed_call_kwargs(sig, ("x",), {}) == {"query": "x"}
    with pytest.raises(TypeError, match="multiple values"):
        service_decorator._managed_call_kwargs(sig, ("x",), {"query": "y"})
    with pytest.raises(TypeError, match="positional arguments"):
        service_decorator._managed_call_kwargs(sig, ("x", "y"), {})


# --- services dropped from the tool list without delegation ---


def _main_module() -> Any:
    import main

    return main


def test_unsupported_services_dropped_without_delegation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = _main_module()
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(
        main,
        "get_oauth_config",
        lambda: MagicMock(service_account_delegation=False),
    )
    notices: list[str] = []
    monkeypatch.setattr(main, "add_startup_notice", notices.append)

    kept = main._without_delegation_unsupported(
        ["drive", "gmail", "calendar", "chat", "contacts", "appscript", "docs"]
    )
    assert kept == ["drive", "calendar", "docs"]
    assert notices and "gmail" in notices[0] and "appscript" in notices[0]


def test_services_kept_with_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    main = _main_module()
    monkeypatch.setattr(main, "is_service_account_enabled", lambda: True)
    monkeypatch.setattr(
        main, "get_oauth_config", lambda: MagicMock(service_account_delegation=True)
    )
    services = ["drive", "gmail", "chat"]
    assert main._without_delegation_unsupported(services) == services
