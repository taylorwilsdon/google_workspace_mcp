"""Behaviour of MCPSessionMiddleware, driven directly as an ASGI app."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from auth.mcp_session_middleware import MCPSessionMiddleware
from auth.oauth21_session_store import get_session_context

pytestmark = pytest.mark.asyncio


def _scope(path="/mcp", headers=None, state=None, type_="http"):
    scope = {
        "type": type_,
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "scheme": "http",
        "server": ("test", 80),
        "client": ("c", 1),
    }
    if state is not None:
        scope["state"] = state
    return scope


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _drive(app, scope):
    sent = []

    async def send(message):
        sent.append(message)

    await MCPSessionMiddleware(app)(scope, _receive, send)
    return sent


def _recording_app(seen):
    async def app(scope, receive, send):
        seen.append(get_session_context())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


async def test_session_id_from_header_is_visible_to_app_and_cleared_after():
    seen = []
    await _drive(_recording_app(seen), _scope(headers={"mcp-session-id": "abc"}))
    ctx = seen[0]
    assert ctx.session_id == "abc"
    assert ctx.metadata["path"] == "/mcp"
    assert ctx.metadata["method"] == "POST"
    assert get_session_context() is None


async def test_auth_state_gives_google_session_and_user():
    auth = SimpleNamespace(claims={"email": "a@example.com"}, user_id="uid-1")
    seen = []
    await _drive(_recording_app(seen), _scope(state={"auth": auth}))
    ctx = seen[0]
    assert ctx.session_id == "google_a@example.com"
    assert ctx.user_id == "a@example.com"
    assert ctx.auth_context is auth
    assert get_session_context() is None


async def test_fastmcp_session_id_used_when_no_other_identity():
    seen = []
    await _drive(_recording_app(seen), _scope(state={"session_id": "fm-1"}))
    assert seen[0].session_id == "fm-1"
    assert seen[0].metadata["mcp_session_id"] == "fm-1"


async def test_no_identity_means_no_context():
    seen = []
    await _drive(_recording_app(seen), _scope())
    assert seen == [None]


async def test_context_cleared_and_error_propagates_when_app_raises():
    seen = []

    async def app(scope, receive, send):
        seen.append(get_session_context())
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await _drive(app, _scope(headers={"mcp-session-id": "abc"}))
    assert seen[0].session_id == "abc"
    assert get_session_context() is None


async def test_app_runs_exactly_once_when_it_raises():
    calls = []

    async def app(scope, receive, send):
        calls.append(1)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _drive(app, _scope(headers={"mcp-session-id": "abc"}))
    assert calls == [1]


async def test_session_lookup_failure_runs_app_once_without_context(monkeypatch):
    def fail(headers):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(
        "auth.mcp_session_middleware.extract_session_from_headers", fail
    )
    seen = []
    sent = await _drive(_recording_app(seen), _scope(headers={"mcp-session-id": "x"}))
    assert seen == [None]
    assert sent[-1]["body"] == b"ok"


async def test_non_mcp_path_is_passed_through_untouched():
    seen = []
    received = {}

    async def app(scope, receive, send):
        received.update(scope=scope, receive=receive, send=send)
        seen.append(get_session_context())

    scope = _scope(path="/health", headers={"mcp-session-id": "abc"})
    marker = object()

    async def send(message):
        pass

    await MCPSessionMiddleware(app)(scope, marker, send)
    assert seen == [None]
    assert received["scope"] is scope
    assert received["receive"] is marker
    assert received["send"] is send


@pytest.mark.parametrize("type_", ["lifespan", "websocket"])
async def test_non_http_scopes_pass_through(type_, caplog):
    got = []

    async def app(scope, receive, send):
        got.append((scope, get_session_context()))

    scope = _scope(type_=type_, headers={"mcp-session-id": "abc"})
    await MCPSessionMiddleware(app)(scope, _receive, lambda m: None)
    assert got == [(scope, None)]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_streaming_response_is_not_buffered_and_keeps_context():
    first_chunk_seen = asyncio.Event()
    sent = []
    ctx_during_stream = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True}
        )
        # Only completes if the middleware forwarded the first chunk already.
        await asyncio.wait_for(first_chunk_seen.wait(), timeout=2)
        ctx_during_stream.append(get_session_context())
        await send({"type": "http.response.body", "body": b"data: 2\n\n"})

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("more_body"):
            first_chunk_seen.set()

    scope = _scope(headers={"mcp-session-id": "abc"})
    await MCPSessionMiddleware(app)(scope, _receive, send)
    assert [m.get("body") for m in sent[1:]] == [b"data: 1\n\n", b"data: 2\n\n"]
    assert ctx_during_stream[0].session_id == "abc"
    assert get_session_context() is None


async def test_concurrent_requests_do_not_share_context():
    release = asyncio.Event()
    seen = {}

    async def app(scope, receive, send):
        sid = dict(scope["headers"])[b"mcp-session-id"].decode()
        await release.wait()
        seen[sid] = get_session_context().session_id

    mw = MCPSessionMiddleware(app)
    tasks = [
        asyncio.create_task(mw(_scope(headers={"mcp-session-id": s}), _receive, None))
        for s in ("one", "two")
    ]
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(*tasks)
    assert seen == {"one": "one", "two": "two"}
