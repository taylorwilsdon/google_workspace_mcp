import logging
from types import SimpleNamespace

import httplib2
import fastmcp
import mcp.types as mt
import pytest
from fastmcp import Client, FastMCP
from fastmcp.server.middleware import MiddlewareContext
from googleapiclient.errors import HttpError

from auth.auth_info_middleware import AuthInfoMiddleware
from auth.gateway_identity import GatewayIdentityError
from core.utils import handle_http_errors

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


def _tool_call_context(fastmcp_context, **arguments):
    """Build the middleware context FastMCP passes for a tools/call request."""
    return MiddlewareContext(
        message=mt.CallToolRequestParams(name="some_tool", arguments=arguments),
        fastmcp_context=fastmcp_context,
    )


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
async def test_stdio_requested_user_identity_is_request_scoped(monkeypatch):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = _tool_call_context(fastmcp_context, user_google_email="stdio@example.com")

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "stdio")
    _stub_session_store(
        monkeypatch, has_session=lambda email: email == "stdio@example.com"
    )

    await middleware._process_request_for_auth(context)

    assert_request_scoped_identity(
        fastmcp_context, email="stdio@example.com", via="stdio_session"
    )


@pytest.mark.asyncio
async def test_stdio_single_session_identity_is_request_scoped(monkeypatch):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = _tool_call_context(fastmcp_context)

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "stdio")
    _stub_session_store(monkeypatch, get_single_user_email=lambda: "solo@example.com")

    await middleware._process_request_for_auth(context)

    assert_request_scoped_identity(
        fastmcp_context, email="solo@example.com", via="stdio_single_session"
    )


@pytest.mark.asyncio
async def test_mcp_session_binding_identity_is_request_scoped(monkeypatch):
    middleware = AuthInfoMiddleware()
    fastmcp_context = _FakeFastMCPContext()
    context = SimpleNamespace(fastmcp_context=fastmcp_context)

    monkeypatch.setattr("auth.auth_info_middleware.get_access_token", lambda: None)
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "streamable-http")
    _stub_session_store(
        monkeypatch,
        get_user_by_mcp_session=lambda session_id: (
            "bound@example.com" if session_id == "session-123" else None
        ),
    )

    await middleware._process_request_for_auth(context)

    assert_request_scoped_identity(
        fastmcp_context, email="bound@example.com", via="mcp_session_binding"
    )


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
    context = _tool_call_context(fastmcp_context)

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


async def _skip_auth(self, context):
    return None


@pytest.mark.asyncio
async def test_api_error_is_logged_without_traceback_or_query(monkeypatch, caplog):
    monkeypatch.setattr(AuthInfoMiddleware, "_process_request_for_auth", _skip_auth)
    monkeypatch.setattr(logging.getLogger("fastmcp"), "propagate", True)

    server = FastMCP("test")
    server.add_middleware(AuthInfoMiddleware())

    @server.tool
    @handle_http_errors("manage_event", service_type="calendar")
    async def manage_event() -> str:
        raise HttpError(
            httplib2.Response({"status": 400}),
            b'{"error": {"message": "Invalid start time."}}',
            uri="https://www.googleapis.com/calendar/v3/events/e?q=secret-query",
        )

    with caplog.at_level(logging.DEBUG):
        async with Client(server) as client:
            result = await client.call_tool("manage_event", raise_on_error=False)

    assert result.is_error
    assert "Invalid start time." in result.content[0].text
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not any(r.name == "auth.auth_info_middleware" for r in errors)
    assert not any(r.exc_info for r in errors)
    assert not any("secret-query" in r.getMessage() for r in errors)


@pytest.mark.asyncio
async def test_api_error_respects_mask_error_details(monkeypatch):
    monkeypatch.setattr(AuthInfoMiddleware, "_process_request_for_auth", _skip_auth)
    monkeypatch.setattr(fastmcp.settings, "mask_error_details", True)

    server = FastMCP("test")
    server.add_middleware(AuthInfoMiddleware())

    @server.tool
    @handle_http_errors("manage_event", service_type="calendar")
    async def manage_event() -> str:
        raise HttpError(
            httplib2.Response({"status": 400}),
            b'{"error": {"message": "secret-detail"}}',
            uri="https://www.googleapis.com/calendar/v3/events/e?q=secret-query",
        )

    async with Client(server) as client:
        result = await client.call_tool("manage_event", raise_on_error=False)

    assert result.is_error
    assert result.content[0].text == "API error in manage_event: HTTP 400"


@pytest.mark.asyncio
@pytest.mark.parametrize("hook", ["on_call_tool", "on_get_prompt"])
async def test_handler_errors_reraise_without_middleware_error_log(
    monkeypatch, caplog, hook
):
    monkeypatch.setattr(AuthInfoMiddleware, "_process_request_for_auth", _skip_auth)

    async def call_next(context):
        raise RuntimeError("boom")

    with caplog.at_level(logging.DEBUG, logger="auth.auth_info_middleware"):
        with pytest.raises(RuntimeError, match="boom"):
            await getattr(AuthInfoMiddleware(), hook)(SimpleNamespace(), call_next)

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
