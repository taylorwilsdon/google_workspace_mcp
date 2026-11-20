"""Service-account mode without domain-wide delegation (acts as itself)."""

import json
import logging
import os
import sys
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


def _base_env(monkeypatch):
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


def test_delegation_defaults_to_enabled(monkeypatch):
    _base_env(monkeypatch)
    cfg = OAuthConfig()
    assert cfg.service_account_delegation is True
    assert cfg.is_service_account_no_delegation() is False


@pytest.mark.parametrize("value", ["false", "0", "no", "OFF"])
def test_delegation_can_be_disabled(monkeypatch, value):
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", value)
    cfg = OAuthConfig()
    assert cfg.is_service_account_no_delegation() is True


def test_no_delegation_requires_a_key(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", "false")
    cfg = OAuthConfig()
    assert cfg.is_service_account_no_delegation() is False


def test_dwd_allowed_domains_ignored_without_delegation(monkeypatch, caplog):
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", "false")
    monkeypatch.setenv("DWD_ALLOWED_DOMAINS", "corp.com")
    with caplog.at_level(logging.WARNING):
        cfg = OAuthConfig()
    assert cfg.dwd_allowed_domains == []
    assert "DWD_ALLOWED_DOMAINS is ignored" in caplog.text


def test_dwd_allowed_domains_kept_with_delegation(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("DWD_ALLOWED_DOMAINS", "Corp.com")
    assert OAuthConfig().dwd_allowed_domains == ["corp.com"]


# --- decorator ---


def _patch_no_delegation(monkeypatch):
    monkeypatch.setattr(
        service_decorator, "is_service_account_no_delegation", lambda: True
    )
    monkeypatch.setattr(
        service_decorator, "_get_service_account_email", lambda: SA_EMAIL
    )
    captured = {}
    fake_service = object()

    def fake_creds(scopes, subject=None):
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


def test_user_email_is_managed_without_delegation(monkeypatch):
    monkeypatch.setattr(
        service_decorator, "is_service_account_no_delegation", lambda: True
    )
    assert service_decorator._user_email_is_managed() is True


def test_extract_managed_email_returns_service_account_email(monkeypatch):
    monkeypatch.setattr(
        service_decorator, "is_service_account_no_delegation", lambda: True
    )
    monkeypatch.setattr(
        service_decorator, "_get_service_account_email", lambda: SA_EMAIL
    )
    assert service_decorator._extract_managed_user_email(None, None, "tool") == SA_EMAIL


@pytest.mark.asyncio
async def test_authenticate_without_delegation_uses_no_subject(monkeypatch):
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
async def test_authenticate_without_delegation_widens_drive_file(monkeypatch):
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
async def test_unsupported_services_fail_clearly(monkeypatch, service_name):
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


def test_credentials_builder_passes_no_subject(monkeypatch):
    calls = {}

    def fake_from_info(info, scopes, subject):
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


def test_get_service_account_email_reads_key(monkeypatch):
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(
            service_account_key_file=None, service_account_key_json=SA_KEY_JSON
        ),
    )
    assert service_decorator._get_service_account_email() == SA_EMAIL


# --- gateway identity is incompatible with no delegation ---


def test_trust_gateway_identity_rejected_without_delegation(monkeypatch):
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
    monkeypatch,
):
    import inspect

    _patch_no_delegation(monkeypatch)
    # The connection-recycling wrapper expects a real-looking Resource.
    monkeypatch.setattr(
        service_decorator, "build_google_service", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(service_decorator, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(service_decorator, "is_trust_gateway_identity", lambda: False)

    @service_decorator.require_google_service("drive", "drive_read")
    async def sample_tool(service, user_google_email: str, query: str = "q"):
        return user_google_email, query

    assert "user_google_email" not in inspect.signature(sample_tool).parameters
    assert await sample_tool(query="abc") == (SA_EMAIL, "abc")


# --- server-level handling when USER_GOOGLE_EMAIL is also set ---


def _server_with_email_free_tool(monkeypatch):
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
async def test_call_tool_does_not_inject_email_without_delegation(monkeypatch):
    server = _server_with_email_free_tool(monkeypatch)
    result = await server.call_tool("echo", {"query": "hi"})
    assert result.content[0].text == "hi"


@pytest.mark.asyncio
async def test_call_tool_drops_caller_supplied_email_without_delegation(monkeypatch):
    server = _server_with_email_free_tool(monkeypatch)
    result = await server.call_tool(
        "echo", {"query": "hi", "user_google_email": "cached@example.com"}
    )
    assert result.content[0].text == "hi"


# --- key loading errors ---


def test_service_account_email_requires_client_email(monkeypatch):
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(
            service_account_key_file=None,
            service_account_key_json=json.dumps({"type": "service_account"}),
        ),
    )
    with pytest.raises(
        service_decorator.GoogleAuthenticationError, match="client_email"
    ):
        service_decorator._get_service_account_email()


def test_service_account_key_file_is_read(monkeypatch, tmp_path):
    key = tmp_path / "key.json"
    key.write_text(SA_KEY_JSON)
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(
            service_account_key_file=str(key), service_account_key_json=None
        ),
    )
    assert service_decorator._get_service_account_email() == SA_EMAIL


def test_service_account_key_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: MagicMock(
            service_account_key_file=None, service_account_key_json="{nope"
        ),
    )
    with pytest.raises(service_decorator.GoogleAuthenticationError):
        service_decorator._get_service_account_email()
