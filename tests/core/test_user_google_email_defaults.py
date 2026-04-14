import inspect
from types import SimpleNamespace

import pytest

import auth.service_decorator as service_decorator
import core.server as server_module
from core.server import SecureFastMCP


def _sample_sig():
    def sample_tool(user_google_email: str, query: str = "default") -> str:
        return query

    return inspect.signature(sample_tool)


def _result_text(result) -> str:
    return result.content[0].text


def test_extract_oauth20_user_email_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", "configured@example.com")
    kwargs = {}

    user_google_email = service_decorator._extract_oauth20_user_email(
        (), kwargs, _sample_sig()
    )

    assert user_google_email == "configured@example.com"
    assert kwargs["user_google_email"] == "configured@example.com"


def test_extract_oauth20_user_email_raises_without_arg_or_env(monkeypatch):
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)

    with pytest.raises(Exception, match="user_google_email"):
        service_decorator._extract_oauth20_user_email((), {}, _sample_sig())


@pytest.mark.asyncio
async def test_list_tools_marks_user_google_email_optional_when_default_configured(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", "configured@example.com")
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)

    server = SecureFastMCP(name="test_server")

    def echo_email(user_google_email: str) -> str:
        return user_google_email

    server.tool()(echo_email)

    tool = next(
        t
        for t in await server.list_tools(run_middleware=False)
        if t.name == "echo_email"
    )

    assert "user_google_email" not in tool.parameters.get("required", [])
    assert (
        tool.parameters["properties"]["user_google_email"]["default"]
        == "configured@example.com"
    )


@pytest.mark.asyncio
async def test_list_tools_leaves_schema_unchanged_without_default(monkeypatch):
    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", None)
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)

    server = SecureFastMCP(name="test_server")

    def echo_email(user_google_email: str) -> str:
        return user_google_email

    server.tool()(echo_email)

    tool = next(
        t
        for t in await server.list_tools(run_middleware=False)
        if t.name == "echo_email"
    )

    assert "user_google_email" in tool.parameters.get("required", [])
    assert tool.parameters["properties"]["user_google_email"].get("default") is None


@pytest.mark.asyncio
async def test_call_tool_injects_default_email_before_validation(monkeypatch):
    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", "configured@example.com")
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)

    server = SecureFastMCP(name="test_server")

    def echo_email(user_google_email: str) -> str:
        return user_google_email

    server.tool()(echo_email)

    result = await server.call_tool("echo_email", None)

    assert _result_text(result) == "configured@example.com"


def test_extract_oauth20_user_email_reads_runtime_env(monkeypatch):
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "configured@example.com")
    kwargs = {}

    user_google_email = service_decorator._extract_oauth20_user_email(
        (), kwargs, _sample_sig()
    )

    assert user_google_email == "configured@example.com"
    assert kwargs["user_google_email"] == "configured@example.com"


def test_get_service_account_credentials_raises_without_key_source(monkeypatch):
    monkeypatch.setattr(
        service_decorator,
        "get_oauth_config",
        lambda: SimpleNamespace(
            service_account_key_file=None,
            service_account_key_json=None,
        ),
    )

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="service_account_key_json",
    ):
        service_decorator._get_service_account_credentials(
            ["scope-a"], "configured@example.com"
        )


@pytest.mark.asyncio
async def test_authenticate_service_account_uses_runtime_configured_user(monkeypatch):
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "configured@example.com")
    monkeypatch.setattr(service_decorator, "is_service_account_enabled", lambda: True)

    captured = {}
    fake_service = object()
    fake_credentials = object()

    def fake_get_service_account_credentials(scopes, subject):
        captured["scopes"] = scopes
        captured["subject"] = subject
        return fake_credentials

    def fake_build(service_name, service_version, credentials):
        captured["service_name"] = service_name
        captured["service_version"] = service_version
        captured["credentials"] = credentials
        return fake_service

    monkeypatch.setattr(
        service_decorator,
        "_get_service_account_credentials",
        fake_get_service_account_credentials,
    )
    monkeypatch.setattr(service_decorator, "build", fake_build)

    service, actual_user = await service_decorator._authenticate_service(
        use_oauth21=False,
        service_name="gmail",
        service_version="v1",
        tool_name="sample_tool",
        user_google_email="caller@example.com",
        resolved_scopes=["scope-a"],
        mcp_session_id=None,
        authenticated_user=None,
    )

    assert service is fake_service
    assert actual_user == "configured@example.com"
    assert captured == {
        "scopes": ["scope-a"],
        "subject": "configured@example.com",
        "service_name": "gmail",
        "service_version": "v1",
        "credentials": fake_credentials,
    }


@pytest.mark.asyncio
async def test_authenticate_service_account_raises_without_configured_user(
    monkeypatch,
):
    monkeypatch.setattr(service_decorator, "_ENV_USER_EMAIL", None)
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(service_decorator, "is_service_account_enabled", lambda: True)

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="Service account mode requires USER_GOOGLE_EMAIL to be configured",
    ):
        await service_decorator._authenticate_service(
            use_oauth21=False,
            service_name="gmail",
            service_version="v1",
            tool_name="sample_tool",
            user_google_email="caller@example.com",
            resolved_scopes=["scope-a"],
            mcp_session_id=None,
            authenticated_user=None,
        )
