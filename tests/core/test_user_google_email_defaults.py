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


def _no_configured_email(monkeypatch):
    """Remove both sources `get_configured_user_email` reads."""
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr("core.config.USER_GOOGLE_EMAIL", None, raising=False)


def test_resolve_legacy_user_email_falls_back_to_configured(monkeypatch):
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr(
        "core.config.USER_GOOGLE_EMAIL", "configured@example.com", raising=False
    )
    kwargs = {}

    user_google_email, args = service_decorator._resolve_legacy_user_email(
        (), kwargs, _sample_sig(), None, "sample_tool"
    )

    assert user_google_email == "configured@example.com"
    assert kwargs["user_google_email"] == "configured@example.com"
    assert args == ()


def test_resolve_legacy_user_email_raises_without_principal_or_config(monkeypatch):
    _no_configured_email(monkeypatch)

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="Cannot determine the account to act as",
    ):
        service_decorator._resolve_legacy_user_email(
            (), {}, _sample_sig(), None, "sample_tool"
        )


def test_resolve_legacy_user_email_rejects_caller_supplied_email(monkeypatch):
    """Findings 21/22/35-37: an unverified caller claim must not select the account."""
    _no_configured_email(monkeypatch)
    monkeypatch.setattr(
        service_decorator, "allow_caller_supplied_user_email", lambda: False
    )

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="Cannot determine the account to act as",
    ):
        service_decorator._resolve_legacy_user_email(
            (),
            {"user_google_email": "victim@example.com"},
            _sample_sig(),
            None,
            "sample_tool",
        )


def test_resolve_legacy_user_email_rejects_mismatch_against_configured(monkeypatch):
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "owner@example.com")

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="does not match the authenticated account",
    ):
        service_decorator._resolve_legacy_user_email(
            (),
            {"user_google_email": "victim@example.com"},
            _sample_sig(),
            None,
            "sample_tool",
        )


def test_resolve_legacy_user_email_rejects_mismatch_against_principal(monkeypatch):
    """A verified principal always wins, even with the opt-in flag enabled."""
    _no_configured_email(monkeypatch)
    monkeypatch.setattr(
        service_decorator, "allow_caller_supplied_user_email", lambda: True
    )

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="does not match the authenticated account",
    ):
        service_decorator._resolve_legacy_user_email(
            (),
            {"user_google_email": "victim@example.com"},
            _sample_sig(),
            "principal@example.com",
            "sample_tool",
        )


def test_resolve_legacy_user_email_accepts_case_insensitive_match(monkeypatch):
    _no_configured_email(monkeypatch)
    kwargs = {"user_google_email": "Owner@Example.COM"}

    user_google_email, _ = service_decorator._resolve_legacy_user_email(
        (), kwargs, _sample_sig(), "owner@example.com", "sample_tool"
    )

    assert user_google_email == "owner@example.com"
    assert kwargs["user_google_email"] == "owner@example.com"


def test_resolve_legacy_user_email_honours_explicit_opt_in(monkeypatch):
    _no_configured_email(monkeypatch)
    monkeypatch.setattr(
        service_decorator, "allow_caller_supplied_user_email", lambda: True
    )
    kwargs = {"user_google_email": "caller@example.com"}

    user_google_email, _ = service_decorator._resolve_legacy_user_email(
        (), kwargs, _sample_sig(), None, "sample_tool"
    )

    assert user_google_email == "caller@example.com"


def test_resolve_legacy_user_email_rewrites_positional_argument(monkeypatch):
    """A positionally supplied email is rewritten in args, not duplicated in kwargs."""
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "Owner@example.com")
    kwargs = {}

    user_google_email, args = service_decorator._resolve_legacy_user_email(
        ("owner@example.com",), kwargs, _sample_sig(), None, "sample_tool"
    )

    assert user_google_email == "Owner@example.com"
    assert args == ("Owner@example.com",)
    assert "user_google_email" not in kwargs


@pytest.mark.asyncio
async def test_list_tools_marks_user_google_email_optional_when_default_configured(
    monkeypatch,
):
    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", "configured@example.com")
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(server_module, "is_trust_gateway_identity", lambda: False)

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
    monkeypatch.setattr(server_module, "is_trust_gateway_identity", lambda: False)

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
    monkeypatch.setattr(server_module, "is_trust_gateway_identity", lambda: False)

    server = SecureFastMCP(name="test_server")

    def echo_email(user_google_email: str) -> str:
        return user_google_email

    server.tool()(echo_email)

    result = await server.call_tool("echo_email", None)

    assert _result_text(result) == "configured@example.com"


@pytest.mark.asyncio
async def test_gateway_mode_strips_email_and_ignores_configured_default(monkeypatch):
    """In gateway mode call_tool must drop caller-supplied user_google_email and
    never inject USER_GOOGLE_EMAIL — tool signatures no longer accept the param."""
    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", "configured@example.com")
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(server_module, "is_trust_gateway_identity", lambda: True)

    server = SecureFastMCP(name="test_server")

    def search_messages(query: str) -> str:
        return query

    server.tool()(search_messages)

    result = await server.call_tool(
        "search_messages",
        {"query": "hello", "user_google_email": "spoofed@example.com"},
    )

    assert _result_text(result) == "hello"


@pytest.mark.asyncio
async def test_gateway_mode_hides_start_google_auth_email(monkeypatch):
    monkeypatch.setattr(server_module, "USER_GOOGLE_EMAIL", None)
    monkeypatch.setattr(server_module, "is_oauth21_enabled", lambda: False)
    monkeypatch.setattr(server_module, "is_trust_gateway_identity", lambda: True)

    server = SecureFastMCP(name="test_server")

    def start_google_auth(
        service_name: str, user_google_email: str | None = None
    ) -> str:
        return f"{service_name}:{user_google_email}"

    server.tool()(start_google_auth)

    tool = next(
        t
        for t in await server.list_tools(run_middleware=False)
        if t.name == "start_google_auth"
    )

    assert "user_google_email" not in tool.parameters.get("properties", {})
    assert "user_google_email" not in tool.parameters.get("required", [])


def test_resolve_legacy_user_email_reads_runtime_env(monkeypatch):
    monkeypatch.setattr("core.config.USER_GOOGLE_EMAIL", None, raising=False)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "configured@example.com")
    kwargs = {}

    user_google_email, _ = service_decorator._resolve_legacy_user_email(
        (), kwargs, _sample_sig(), None, "sample_tool"
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
async def test_authenticate_service_account_pins_subject_to_configured_user(
    monkeypatch,
):
    """Without a verified principal the DWD subject is USER_GOOGLE_EMAIL, not the caller's."""
    monkeypatch.setattr("core.config.USER_GOOGLE_EMAIL", None, raising=False)
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
        user_google_email="configured@example.com",
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
    monkeypatch.setattr("core.config.USER_GOOGLE_EMAIL", None, raising=False)
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


# --- DWD per-request impersonation tests ---


def _patch_service_account(monkeypatch, *, allowed_domains=""):
    """Common monkeypatching for DWD impersonation tests."""
    monkeypatch.setattr("core.config.USER_GOOGLE_EMAIL", None, raising=False)
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "canonical@corp.com")
    monkeypatch.setattr(service_decorator, "is_service_account_enabled", lambda: True)

    config = SimpleNamespace(
        service_account_key_file="/fake/key.json",
        service_account_key_json=None,
        dwd_allowed_domains=(
            [d.strip().lower() for d in allowed_domains.split(",") if d.strip()]
            if allowed_domains
            else []
        ),
    )
    monkeypatch.setattr(service_decorator, "get_oauth_config", lambda: config)

    captured = {}
    fake_service = object()
    fake_credentials = object()

    def fake_get_creds(scopes, subject):
        captured["subject"] = subject
        return fake_credentials

    def fake_build(service_name, service_version, credentials):
        return fake_service

    monkeypatch.setattr(
        service_decorator,
        "_get_service_account_credentials",
        fake_get_creds,
    )
    monkeypatch.setattr(service_decorator, "build", fake_build)
    return captured, fake_service


@pytest.mark.asyncio
async def test_dwd_rejects_caller_supplied_subject(monkeypatch):
    """Findings 2/4/5/16/17/18/33: a caller argument must not become the DWD subject.

    Without a verified principal the subject is pinned to USER_GOOGLE_EMAIL, so naming
    a different account -- even one inside the same Workspace domain, which a domain
    allowlist would happily accept -- must be refused.
    """
    captured, _ = _patch_service_account(monkeypatch, allowed_domains="corp.com")

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="does not match the authenticated account",
    ):
        await service_decorator._authenticate_service(
            use_oauth21=False,
            service_name="gmail",
            service_version="v1",
            tool_name="t",
            user_google_email="victim@corp.com",
            resolved_scopes=["scope-a"],
            mcp_session_id=None,
            authenticated_user=None,
        )

    assert "subject" not in captured


@pytest.mark.asyncio
async def test_dwd_request_impersonation_falls_back_to_canonical(monkeypatch):
    captured, fake_service = _patch_service_account(monkeypatch)

    service, actual_user = await service_decorator._authenticate_service(
        use_oauth21=False,
        service_name="gmail",
        service_version="v1",
        tool_name="t",
        user_google_email="",
        resolved_scopes=["scope-a"],
        mcp_session_id=None,
        authenticated_user=None,
    )

    assert actual_user == "canonical@corp.com"
    assert captured["subject"] == "canonical@corp.com"


@pytest.mark.asyncio
async def test_dwd_uses_verified_gateway_principal_as_subject(monkeypatch):
    """A gateway-verified principal is the only per-request subject source."""
    captured, _ = _patch_service_account(
        monkeypatch, allowed_domains="corp.com,partner.io"
    )

    _, actual_user = await service_decorator._authenticate_service(
        use_oauth21=False,
        service_name="gmail",
        service_version="v1",
        tool_name="t",
        user_google_email="alice@partner.io",
        resolved_scopes=["scope-a"],
        mcp_session_id=None,
        authenticated_user="alice@partner.io",
    )

    assert actual_user == "alice@partner.io"
    assert captured["subject"] == "alice@partner.io"


@pytest.mark.asyncio
async def test_dwd_per_request_subject_requires_domain_allowlist(monkeypatch):
    """Finding 5: an unset allowlist disables per-request impersonation entirely."""
    captured, _ = _patch_service_account(monkeypatch, allowed_domains="")

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="DWD_ALLOWED_DOMAINS is not configured",
    ):
        await service_decorator._authenticate_service(
            use_oauth21=False,
            service_name="gmail",
            service_version="v1",
            tool_name="t",
            user_google_email="alice@partner.io",
            resolved_scopes=["scope-a"],
            mcp_session_id=None,
            authenticated_user="alice@partner.io",
        )

    assert "subject" not in captured


@pytest.mark.asyncio
async def test_dwd_request_impersonation_domain_allowlist_rejects(monkeypatch):
    """A verified principal outside the allowlisted domains is still refused."""
    captured, _ = _patch_service_account(monkeypatch, allowed_domains="corp.com")

    with pytest.raises(
        service_decorator.GoogleAuthenticationError,
        match="not in DWD_ALLOWED_DOMAINS",
    ):
        await service_decorator._authenticate_service(
            use_oauth21=False,
            service_name="gmail",
            service_version="v1",
            tool_name="t",
            user_google_email="evil@external.com",
            resolved_scopes=["scope-a"],
            mcp_session_id=None,
            authenticated_user="evil@external.com",
        )

    assert "subject" not in captured
