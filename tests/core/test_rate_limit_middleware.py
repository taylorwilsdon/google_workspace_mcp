"""Tests for RateLimitMiddleware in core/server.py."""

import time
from unittest.mock import patch

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient


def _make_app(env_overrides=None, **middleware_kwargs):
    """Build a minimal Starlette app wrapped in RateLimitMiddleware."""
    from core.server import RateLimitMiddleware

    async def endpoint(request):
        return Response("ok")

    return Starlette(
        routes=[
            Route("/health", endpoint),
            Route("/", endpoint),
            Route("/mcp", endpoint),
            Route("/sse", endpoint),
            Route("/messages", endpoint),
            Route("/oauth2callback", endpoint),
            Route("/authorize", endpoint),
            Route("/token", endpoint),
            Route("/register", endpoint),
            Route("/.well-known/oauth-test", endpoint),
            Route("/api/data", endpoint),
        ],
        middleware=[Middleware(RateLimitMiddleware)],
    )


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_OAUTH_RPM", "3")
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_TOOLS_RPM", "5")
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_DEFAULT_RPM", "10")
    return _make_app()


@pytest.fixture()
def client(app):
    return TestClient(app)


# --- disabled ---


def test_rate_limit_disabled_passes_all_requests(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_ENABLED", "false")
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_OAUTH_RPM", "1")
    app = _make_app()
    c = TestClient(app)
    for _ in range(10):
        assert c.get("/oauth2callback").status_code == 200


@pytest.mark.parametrize("falsy", ["false", "0", "no", "off"])
def test_rate_limit_disabled_recognises_falsy_values(monkeypatch, falsy):
    from core.server import RateLimitMiddleware

    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_ENABLED", falsy)
    mw = RateLimitMiddleware(app=None)
    assert mw.enabled is False


# --- probe path exemption ---


def test_health_probe_never_rate_limited(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_DEFAULT_RPM", "1")
    app = _make_app()
    c = TestClient(app)
    for _ in range(20):
        assert c.get("/health").status_code == 200


def test_root_probe_never_rate_limited(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_DEFAULT_RPM", "1")
    app = _make_app()
    c = TestClient(app)
    for _ in range(20):
        assert c.get("/").status_code == 200


# --- path classification ---


def test_classify_oauth_paths(monkeypatch):
    from core.server import RateLimitMiddleware

    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_OAUTH_RPM", "7")
    mw = RateLimitMiddleware(app=None)
    for path in (
        "/oauth2callback",
        "/.well-known/oauth-authorization-server",
        "/oauth/start",
        "/register",
        "/authorize",
        "/token",
    ):
        cat, limit = mw._classify(path)
        assert cat == "oauth", f"Expected oauth for {path}, got {cat}"
        assert limit == 7


def test_classify_mcp_paths(monkeypatch):
    from core.server import RateLimitMiddleware

    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_TOOLS_RPM", "50")
    mw = RateLimitMiddleware(app=None)
    for path in ("/mcp", "/mcp/tools/call", "/sse", "/messages"):
        cat, limit = mw._classify(path)
        assert cat == "tools", f"Expected tools for {path}, got {cat}"
        assert limit == 50


def test_classify_default_paths(monkeypatch):
    from core.server import RateLimitMiddleware

    monkeypatch.setenv("WORKSPACE_MCP_RATE_LIMIT_DEFAULT_RPM", "99")
    mw = RateLimitMiddleware(app=None)
    for path in ("/api/data", "/some/other/endpoint", "/static/app.js"):
        cat, limit = mw._classify(path)
        assert cat == "default", f"Expected default for {path}, got {cat}"
        assert limit == 99


# --- IP extraction ---


def test_client_ip_from_x_forwarded_for(monkeypatch):
    from core.server import RateLimitMiddleware

    mw = RateLimitMiddleware(app=None)
    scope = {
        "headers": [(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        "client": ("127.0.0.1", 12345),
    }
    assert mw._client_ip(scope) == "203.0.113.5"


def test_client_ip_falls_back_to_scope_client(monkeypatch):
    from core.server import RateLimitMiddleware

    mw = RateLimitMiddleware(app=None)
    scope = {"headers": [], "client": ("192.168.1.10", 5000)}
    assert mw._client_ip(scope) == "192.168.1.10"


def test_client_ip_unknown_when_no_client(monkeypatch):
    from core.server import RateLimitMiddleware

    mw = RateLimitMiddleware(app=None)
    scope = {"headers": [], "client": None}
    assert mw._client_ip(scope) == "unknown"


# --- sliding window enforcement ---


def test_requests_under_limit_are_allowed(client):
    for _ in range(3):  # oauth RPM = 3
        r = client.get("/oauth2callback", headers={"X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 200


def test_request_exceeding_limit_returns_429(client):
    for _ in range(3):  # consume oauth RPM = 3
        client.get("/oauth2callback", headers={"X-Forwarded-For": "5.5.5.5"})
    r = client.get("/oauth2callback", headers={"X-Forwarded-For": "5.5.5.5"})
    assert r.status_code == 429


def test_429_response_includes_retry_after_header(client):
    for _ in range(3):
        client.get("/oauth2callback", headers={"X-Forwarded-For": "6.6.6.6"})
    r = client.get("/oauth2callback", headers={"X-Forwarded-For": "6.6.6.6"})
    assert r.status_code == 429
    assert r.headers.get("retry-after") == "60"
    assert r.json()["error"] == "Too Many Requests"


def test_different_ips_have_independent_counters(client):
    for _ in range(3):  # saturate ip-a's oauth bucket
        client.get("/oauth2callback", headers={"X-Forwarded-For": "10.0.0.1"})
    # ip-b should still be allowed
    r = client.get("/oauth2callback", headers={"X-Forwarded-For": "10.0.0.2"})
    assert r.status_code == 200


def test_sliding_window_evicts_old_timestamps(monkeypatch):
    """Requests older than 60 s fall out of the window; new ones are allowed."""
    from core.server import RateLimitMiddleware

    mw = RateLimitMiddleware(app=None)
    ip, category = "7.7.7.7", "oauth"
    limit = 2

    base = 1_000_000.0
    # Fill up the window at t=base
    with patch("time.monotonic", return_value=base):
        assert mw._check_and_record(ip, category, limit) is True
        assert mw._check_and_record(ip, category, limit) is True
        assert mw._check_and_record(ip, category, limit) is False  # now denied

    # Advance past the 60 s cutoff
    with patch("time.monotonic", return_value=base + 61.0):
        assert mw._check_and_record(ip, category, limit) is True


def test_check_and_record_thread_safe_under_limit(monkeypatch):
    """Concurrent calls from the same IP stay within the limit."""
    import threading
    from core.server import RateLimitMiddleware

    mw = RateLimitMiddleware(app=None)
    results = []
    limit = 5

    def hit():
        results.append(mw._check_and_record("9.9.9.9", "tools", limit))

    threads = [threading.Thread(target=hit) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    allowed = sum(1 for r in results if r)
    denied = sum(1 for r in results if not r)
    assert allowed == limit
    assert denied == 10 - limit
