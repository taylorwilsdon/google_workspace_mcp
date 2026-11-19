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
    assert cfg.is_service_account_delegation_enabled() is True
    assert cfg.is_service_account_no_delegation() is False


@pytest.mark.parametrize("value", ["false", "0", "no", "OFF"])
def test_delegation_can_be_disabled(monkeypatch, value):
    _base_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_DELEGATION", value)
    cfg = OAuthConfig()
    assert cfg.is_service_account_no_delegation() is True
    assert cfg.is_service_account_delegation_enabled() is False


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
@pytest.mark.parametrize("service_name", ["gmail", "people", "chat"])
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
