import logging
from types import SimpleNamespace

import pytest

from auth.auth_info_middleware import AuthInfoMiddleware
from auth.gateway_identity import GatewayIdentityError

# The only identity keys any consumer reads. Asserting the exact set is what stops a
# write-only key from creeping back in: every extra key used to cost a session-scoped
# state-store entry per MCP session.
IDENTITY_KEYS = {"authenticated_user_email", "authenticated_via"}


class _FakeFastMCPContext:
    def __init__(self):
        self.state = {}
        self.state_serializable = {}
        self.deleted_state = []
        self.session_id = "session-123"

    async def set_state(self, key, value, serializable=True):
        self.state[key] = value
        self.state_serializable[key] = serializable

    async def get_state(self, key):
        return self.state.get(key)

    async def delete_state(self, key):
        self.deleted_state.append(key)
        self.state.pop(key, None)
        self.state_serializable.pop(key, None)


def assert_request_scoped_identity(ctx, *, email, via):
    """Assert exactly the two live keys were written, request-scoped, with no deletes."""
    assert set(ctx.state) == IDENTITY_KEYS
    assert ctx.state["authenticated_user_email"] == email
    assert ctx.state["authenticated_via"] == via
    # serializable=False keeps identity in Context._request_state instead of the
    # session-scoped store, so it dies with the request that derived it.
    assert all(scoped is False for scoped in ctx.state_serializable.values())
    # The stale-identity defence is a request-scoped shadow, not a store delete;
    # nothing here should be reaching into the session store.
    assert ctx.deleted_state == []


def _stub_google_provider(monkeypatch, observed, email="user@example.com"):
    class _FakeProvider:
        async def verify_token(self, token):
            observed["token"] = token
            return SimpleNamespace(
                email=email,
                claims={"email": email},
                client_id="google",
                scopes=["scope-a"],
                expires_at=1234567890,
                sub=email,
            )

    monkeypatch.setattr("core.server.get_auth_provider", lambda: _FakeProvider())

    async def _noop_ensure_session(*args, **kwargs):
        return None

    monkeypatch.setattr(
        "auth.auth_info_middleware.ensure_session_from_access_token",
        _noop_ensure_session,
    )


def _stub_session_store(monkeypatch, **methods):
    """Session store whose lookups all miss, unless a test overrides one."""
    behaviour = {
        "has_session": lambda email: False,
        "get_single_user_email": lambda: None,
        "get_user_by_mcp_session": lambda session_id: None,
        **methods,
    }
    store = SimpleNamespace(**behaviour)
    monkeypatch.setattr(
        "auth.oauth21_session_store.get_oauth21_session_store", lambda: store
    )
    return store


@pytest.mark.asyncio
async def test_on_call_tool_includes_authorization_header_for_bearer_auth(
    monkeypatch,
):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(fastmcp_context=fastmcp_context)
    observed = {}

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)

    def fake_get_http_headers(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return {"authorization": "Bearer ya29.token"}

    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers",
        fake_get_http_headers,
    )
    _stub_google_provider(monkeypatch, observed)

    async def call_next(ctx):
        assert ctx is context
        return "ok"

    result = await middleware.on_call_tool(context, call_next)

    assert result == "ok"
    assert observed["args"] == ()
    assert observed["kwargs"] == {"include": {"authorization"}}
    assert observed["token"] == "ya29.token"
    assert_request_scoped_identity(
        fastmcp_context, email="user@example.com", via="bearer_token"
    )


@pytest.mark.asyncio
async def test_fastmcp_oauth_identity_is_request_scoped(monkeypatch):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(fastmcp_context=fastmcp_context)
    access_token = SimpleNamespace(
        email="passthrough@local",
        claims={"email": "passthrough@local"},
    )

    monkeypatch.setattr(
        "auth.auth_info_middleware.get_access_token", lambda: access_token
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers",
        lambda *args, **kwargs: pytest.fail(
            "fastmcp_oauth path must not fall through to bearer header parsing"
        ),
    )

    await middleware._process_request_for_auth(context)

    assert_request_scoped_identity(
        fastmcp_context, email="passthrough@local", via="fastmcp_oauth"
    )


@pytest.mark.asyncio
async def test_gateway_identity_shadows_stale_session_state(monkeypatch):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    fastmcp_context.state.update(
        {
            "authenticated_user_email": "stale@example.com",
            "authenticated_via": "mcp_session_binding",
        }
    )
    context = SimpleNamespace(fastmcp_context=fastmcp_context)

    monkeypatch.setattr(
        "auth.auth_info_middleware.is_trust_gateway_identity", lambda: True
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_oauth_config",
        lambda: SimpleNamespace(gateway_identity_header="x-gateway-assertion"),
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers",
        lambda **kwargs: (
            {"x-gateway-assertion": "signed.jwt"}
            if kwargs == {"include": {"x-gateway-assertion"}}
            else {}
        ),
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.extract_email_from_assertion",
        lambda assertion: "verified@example.com" if assertion == "signed.jwt" else None,
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_access_token",
        lambda: pytest.fail("gateway mode must not inspect legacy access tokens"),
    )

    await middleware._process_request_for_auth(context)

    assert_request_scoped_identity(
        fastmcp_context, email="verified@example.com", via="gateway_assertion"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "verified_email"),
    [
        ({}, None),
        ({"x-gateway-assertion": "invalid.jwt"}, None),
    ],
)
async def test_gateway_identity_failure_does_not_fall_back(
    monkeypatch, headers, verified_email
):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    fastmcp_context.state["authenticated_user_email"] = "stale@example.com"
    context = SimpleNamespace(fastmcp_context=fastmcp_context)

    monkeypatch.setattr(
        "auth.auth_info_middleware.is_trust_gateway_identity", lambda: True
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_oauth_config",
        lambda: SimpleNamespace(gateway_identity_header="x-gateway-assertion"),
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers",
        lambda **kwargs: headers,
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.extract_email_from_assertion",
        lambda assertion: verified_email,
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_access_token",
        lambda: pytest.fail("gateway mode must not inspect legacy access tokens"),
    )

    with pytest.raises(GatewayIdentityError):
        await middleware._process_request_for_auth(context)

    # Shadowed to None by request entry, so the stale principal is unreadable even
    # though the request aborted before any identity was installed.
    assert fastmcp_context.state["authenticated_user_email"] is None


@pytest.mark.asyncio
async def test_stdio_ignores_caller_supplied_user_google_email(monkeypatch):
    """Findings 8/43: a tool argument must never establish the stdio principal.

    The account is real and has a stored session -- previously enough to be adopted
    as the principal. With USER_GOOGLE_EMAIL pinning the server to someone else,
    nothing may be resolved from the argument.
    """
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(
        fastmcp_context=fastmcp_context,
        request=SimpleNamespace(params={"user_google_email": "victim@example.com"}),
    )

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "stdio")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "owner@example.com")
    _stub_session_store(
        monkeypatch,
        # Both accounts have sessions; only the configured one may be adopted.
        has_session=lambda email: email in {"victim@example.com", "owner@example.com"},
        get_single_user_email=lambda: None,
    )

    await middleware._process_request_for_auth(context)

    assert fastmcp_context.state["authenticated_user_email"] == "owner@example.com"
    assert fastmcp_context.state["authenticated_via"] == "stdio_configured_user"


@pytest.mark.asyncio
async def test_stdio_configured_user_without_session_resolves_nothing(monkeypatch):
    """A pinned principal with no stored session must not fall back to another account."""
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(
        fastmcp_context=fastmcp_context,
        request=SimpleNamespace(params={"user_google_email": "someone@example.com"}),
    )

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "stdio")
    monkeypatch.setenv("USER_GOOGLE_EMAIL", "owner@example.com")
    _stub_session_store(
        monkeypatch,
        has_session=lambda email: email == "someone@example.com",
        get_single_user_email=lambda: "someone@example.com",
    )

    await middleware._process_request_for_auth(context)

    assert fastmcp_context.state["authenticated_user_email"] is None
    assert fastmcp_context.state["authenticated_via"] is None


@pytest.mark.asyncio
async def test_stdio_single_session_identity_is_request_scoped(monkeypatch):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(fastmcp_context=fastmcp_context)

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "stdio")
    monkeypatch.delenv("USER_GOOGLE_EMAIL", raising=False)
    monkeypatch.setattr("core.config.USER_GOOGLE_EMAIL", None, raising=False)
    _stub_session_store(monkeypatch, get_single_user_email=lambda: "solo@example.com")

    await middleware._process_request_for_auth(context)

    assert_request_scoped_identity(
        fastmcp_context, email="solo@example.com", via="stdio_single_session"
    )


@pytest.mark.asyncio
async def test_mcp_session_binding_no_longer_authenticates(monkeypatch):
    """Finding 25: a bare Mcp-Session-Id must not resolve a principal.

    The binding still exists in the store, but presenting only the session id --
    no bearer token -- must leave the request unauthenticated.
    """
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(fastmcp_context=fastmcp_context)
    lookups = []

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "streamable-http")

    def _record_lookup(session_id):
        lookups.append(session_id)
        return "bound@example.com"

    _stub_session_store(monkeypatch, get_user_by_mcp_session=_record_lookup)

    await middleware._process_request_for_auth(context)

    assert fastmcp_context.state["authenticated_user_email"] is None
    assert fastmcp_context.state["authenticated_via"] is None
    # The store must not even be consulted for authentication purposes.
    assert lookups == []


@pytest.mark.asyncio
async def test_failed_auth_shadows_stale_session_identity(monkeypatch):
    """A request that resolves no principal must not inherit an earlier one.

    Every non-gateway path falls through to the tool on failure, so without the
    request-entry shadow FastMCP's get_state would fall back to whatever
    session-scoped identity a previous request left behind.
    """
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    fastmcp_context.state.update(
        {
            "authenticated_user_email": "stale@example.com",
            "authenticated_via": "bearer_token",
        }
    )
    context = SimpleNamespace(fastmcp_context=fastmcp_context)

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "streamable-http")
    _stub_session_store(monkeypatch)

    async def call_next(ctx):
        return "ok"

    assert await middleware.on_call_tool(context, call_next) == "ok"

    assert set(fastmcp_context.state) == IDENTITY_KEYS
    assert fastmcp_context.state["authenticated_user_email"] is None
    assert fastmcp_context.state["authenticated_via"] is None
    assert all(
        scoped is False for scoped in fastmcp_context.state_serializable.values()
    )


@pytest.mark.asyncio
async def test_on_call_tool_requests_authorization_header_when_default_headers_are_empty(
    monkeypatch,
):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(fastmcp_context=fastmcp_context)
    observed = {"calls": []}

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)

    def fake_get_http_headers(*args, **kwargs):
        observed["calls"].append({"args": args, "kwargs": kwargs})
        if kwargs == {"include": {"authorization"}}:
            return {"authorization": "Bearer ya29.token"}
        return {}

    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers",
        fake_get_http_headers,
    )
    _stub_google_provider(monkeypatch, observed)

    async def call_next(ctx):
        assert ctx is context
        return "ok"

    result = await middleware.on_call_tool(context, call_next)

    assert result == "ok"
    assert observed["calls"] == [
        {"args": (), "kwargs": {}},
        {"args": (), "kwargs": {"include": {"authorization"}}},
    ]
    assert observed["token"] == "ya29.token"
    assert_request_scoped_identity(
        fastmcp_context, email="user@example.com", via="bearer_token"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "oauth21_enabled", "headers", "expect_warning"),
    [
        ("streamable-http", False, {}, False),
        (
            "streamable-http",
            False,
            {"authorization": "Bearer invalid-token"},
            True,
        ),
        (
            "streamable-http",
            False,
            {"authorization": "bearer invalid-token"},
            True,
        ),
        ("streamable-http", True, {}, True),
        ("stdio", False, {}, True),
    ],
)
async def test_unresolved_auth_warning_is_suppressed_only_for_legacy_http_no_bearer(
    monkeypatch,
    caplog,
    transport,
    oauth21_enabled,
    headers,
    expect_warning,
):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    fastmcp_context.session_id = None
    context = SimpleNamespace(fastmcp_context=fastmcp_context)

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda **kwargs: headers
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_oauth_config",
        lambda: SimpleNamespace(
            is_oauth21_enabled=lambda: oauth21_enabled,
        ),
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.is_trust_gateway_identity", lambda: False
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: transport)
    monkeypatch.setattr(
        "auth.oauth21_session_store.get_oauth21_session_store",
        lambda: SimpleNamespace(get_single_user_email=lambda: None),
    )

    with caplog.at_level(logging.WARNING, logger="auth.auth_info_middleware"):
        await middleware._process_request_for_auth(context)

    unresolved_warnings = [
        record
        for record in caplog.records
        if "reason=all_auth_paths_failed" in record.getMessage()
    ]
    assert bool(unresolved_warnings) is expect_warning
