from types import SimpleNamespace

import pytest

from auth.auth_info_middleware import AuthInfoMiddleware


class _FakeFastMCPContext:
    def __init__(self):
        self.state = {}
        self.session_id = "session-123"

    async def set_state(self, key, value, serializable=True):  # noqa: ARG002
        self.state[key] = value

    async def get_state(self, key):
        return self.state.get(key)


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

    class _FakeProvider:
        async def verify_token(self, token):
            observed["token"] = token
            return SimpleNamespace(
                email="user@example.com",
                claims={"email": "user@example.com"},
                client_id="google",
                scopes=["scope-a"],
                expires_at=1234567890,
                sub="user@example.com",
            )

    monkeypatch.setattr("core.server.get_auth_provider", lambda: _FakeProvider())
    monkeypatch.setattr(
        "auth.auth_info_middleware.ensure_session_from_access_token",
        lambda *args, **kwargs: None,
    )

    async def call_next(ctx):
        assert ctx is context
        return "ok"

    result = await middleware.on_call_tool(context, call_next)

    assert result == "ok"
    assert observed["args"] == ()
    assert observed["kwargs"] == {"include": {"authorization"}}
    assert observed["token"] == "ya29.token"
    assert fastmcp_context.state["authenticated_user_email"] == "user@example.com"
    assert fastmcp_context.state["authenticated_via"] == "bearer_token"


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

    class _FakeProvider:
        async def verify_token(self, token):
            observed["token"] = token
            return SimpleNamespace(
                email="user@example.com",
                claims={"email": "user@example.com"},
                client_id="google",
                scopes=["scope-a"],
                expires_at=1234567890,
                sub="user@example.com",
            )

    monkeypatch.setattr("core.server.get_auth_provider", lambda: _FakeProvider())
    monkeypatch.setattr(
        "auth.auth_info_middleware.ensure_session_from_access_token",
        lambda *args, **kwargs: None,
    )

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
    assert fastmcp_context.state["authenticated_user_email"] == "user@example.com"
    assert fastmcp_context.state["authenticated_via"] == "bearer_token"
