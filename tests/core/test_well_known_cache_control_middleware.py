import importlib
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import Response
from starlette.routing import Route
from starlette.testclient import TestClient


def test_well_known_cache_control_middleware_rewrites_headers():
    from core.server import WellKnownCacheControlMiddleware, _compute_scope_fingerprint

    async def well_known_endpoint(request):
        response = Response("ok")
        response.headers["Cache-Control"] = "public, max-age=3600"
        response.set_cookie("a", "1")
        response.set_cookie("b", "2")
        return response

    async def regular_endpoint(request):
        response = Response("ok")
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    app = Starlette(
        routes=[
            Route("/.well-known/oauth-authorization-server", well_known_endpoint),
            Route("/.well-known/oauth-authorization-server-extra", regular_endpoint),
            Route("/health", regular_endpoint),
        ],
        middleware=[Middleware(WellKnownCacheControlMiddleware)],
    )
    client = TestClient(app)

    well_known = client.get("/.well-known/oauth-authorization-server")
    assert well_known.status_code == 200
    assert well_known.headers["cache-control"] == "no-store, must-revalidate"
    assert well_known.headers["etag"] == f'"{_compute_scope_fingerprint()}"'
    assert sorted(well_known.headers.get_list("set-cookie")) == sorted(
        ["a=1; Path=/; SameSite=lax", "b=2; Path=/; SameSite=lax"]
    )

    regular = client.get("/health")
    assert regular.status_code == 200
    assert regular.headers["cache-control"] == "public, max-age=3600"
    assert "etag" not in regular.headers

    extra = client.get("/.well-known/oauth-authorization-server-extra")
    assert extra.status_code == 200
    assert extra.headers["cache-control"] == "public, max-age=3600"
    assert "etag" not in extra.headers


def test_origin_validation_rejects_untrusted_browser_origin(monkeypatch):
    from core.server import OriginValidationMiddleware

    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url=None,
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[Route("/health", endpoint)],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    assert (
        client.get("/health", headers={"Origin": "http://evil.test"}).status_code == 403
    )
    assert (
        client.get("/health", headers={"Origin": "http://localhost:5173"}).status_code
        == 200
    )
    assert client.get("/health").status_code == 200


def test_origin_validation_allows_configured_external_origin(monkeypatch):
    from core.server import OriginValidationMiddleware

    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url="https://workspace.example.com/mcp",
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[Route("/health", endpoint)],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    response = client.get(
        "/health", headers={"Origin": "https://workspace.example.com"}
    )
    assert response.status_code == 200


def test_origin_validation_trusts_any_vscode_webview_origin(monkeypatch):
    from core.server import OriginValidationMiddleware

    # VS Code assigns a fresh, random GUID authority to every webview, so its
    # origin can never be enumerated in an allowlist. The scheme is the trust
    # boundary; any vscode-webview origin must be accepted regardless of host.
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url=None,
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[Route("/health", endpoint)],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    # Real-world VS Code webview origins carry a unique per-session GUID host.
    for host in (
        "1a2b3c4d-5e6f-7a8b-9c0d-1234567890ab",
        "ffffffff-0000-1111-2222-333344445555",
        "publisher.extension",
    ):
        assert (
            client.get(
                "/health", headers={"Origin": f"vscode-webview://{host}"}
            ).status_code
            == 200
        )
    # A genuine browser web origin that is not configured is still rejected.
    assert (
        client.get("/health", headers={"Origin": "https://evil.test"}).status_code
        == 403
    )


def test_origin_validation_allows_same_origin_request(monkeypatch):
    from core.server import OriginValidationMiddleware

    # The OAuth proxy consent form posts to itself (action=""), so the request is
    # always same-origin with the host that served the page. A request whose Origin
    # matches its own Host must be accepted even if that host was never added to the
    # allowlist (e.g. WORKSPACE_EXTERNAL_URL unset or misconfigured) — a same-origin
    # request is the server's own page, never the cross-site threat this guard stops.
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url=None,
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[Route("/consent", endpoint, methods=["POST"])],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    # Same-origin consent POST to an unconfigured external host is allowed.
    same_origin = client.post(
        "/consent",
        headers={
            "Origin": "https://app.example.com",
            "Host": "app.example.com",
        },
    )
    assert same_origin.status_code == 200

    # A cross-origin request to that same host is still rejected.
    cross_origin = client.post(
        "/consent",
        headers={
            "Origin": "https://evil.test",
            "Host": "app.example.com",
        },
    )
    assert cross_origin.status_code == 403


def test_origin_validation_rejects_null_origin_consent_by_default(monkeypatch):
    from core.server import OriginValidationMiddleware

    monkeypatch.delenv("WORKSPACE_MCP_ALLOW_NULL_ORIGIN_CONSENT", raising=False)
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url=None,
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[Route("/consent", endpoint, methods=["POST"])],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    response = client.post("/consent", headers={"Origin": "null"})
    assert response.status_code == 403
    assert response.json() == {"error": "Origin not allowed"}


def test_origin_validation_allows_null_origin_consent_when_enabled(monkeypatch):
    from core.server import OriginValidationMiddleware

    monkeypatch.setenv("WORKSPACE_MCP_ALLOW_NULL_ORIGIN_CONSENT", "true")
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url=None,
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[Route("/consent", endpoint, methods=["GET", "POST"])],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    allowed = client.post("/consent", headers={"Origin": "null"})
    assert allowed.status_code == 200

    assert client.get("/consent", headers={"Origin": "null"}).status_code == 403
    assert (
        client.post(
            "/consent",
            headers={
                "Origin": "https://evil.test",
                "Host": "workspace.example.com",
            },
        ).status_code
        == 403
    )


def test_origin_validation_null_origin_bypass_only_applies_to_consent_post(
    monkeypatch,
):
    from core.server import OriginValidationMiddleware

    monkeypatch.setenv("WORKSPACE_MCP_ALLOW_NULL_ORIGIN_CONSENT", "true")
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(
            get_allowed_origins=lambda: ["http://localhost:8000"],
            external_url=None,
        ),
    )

    async def endpoint(request):
        return Response("ok")

    app = Starlette(
        routes=[
            Route("/mcp", endpoint, methods=["POST"]),
            Route("/token", endpoint, methods=["POST"]),
            Route("/.well-known/oauth-authorization-server", endpoint),
        ],
        middleware=[Middleware(OriginValidationMiddleware)],
    )
    client = TestClient(app)

    assert client.post("/mcp", headers={"Origin": "null"}).status_code == 403
    assert client.post("/token", headers={"Origin": "null"}).status_code == 403
    assert (
        client.get(
            "/.well-known/oauth-authorization-server",
            headers={"Origin": "null"},
        ).status_code
        == 403
    )


def test_configured_server_applies_no_cache_to_served_oauth_discovery_routes(
    monkeypatch,
):
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "dummy-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8000")
    monkeypatch.delenv("WORKSPACE_EXTERNAL_URL", raising=False)
    monkeypatch.setenv("EXTERNAL_OAUTH21_PROVIDER", "false")

    import core.server as core_server
    from auth.oauth_config import reload_oauth_config

    reload_oauth_config()
    core_server = importlib.reload(core_server)
    core_server.set_transport_mode("streamable-http")
    core_server.configure_server_for_http()

    app = core_server.server.http_app(transport="streamable-http", path="/mcp")
    client = TestClient(app)

    authorization_server = client.get("/.well-known/oauth-authorization-server")
    assert authorization_server.status_code == 200
    assert authorization_server.headers["cache-control"] == "no-store, must-revalidate"
    assert authorization_server.headers["etag"].startswith('"')
    assert authorization_server.headers["etag"].endswith('"')

    protected_resource = client.get("/.well-known/oauth-protected-resource/mcp")
    assert protected_resource.status_code == 200
    assert protected_resource.headers["cache-control"] == "no-store, must-revalidate"
    assert protected_resource.headers["etag"].startswith('"')
    assert protected_resource.headers["etag"].endswith('"')

    # Ensure we did not create a shadow route at the wrong path.
    wrong_path = client.get("/.well-known/oauth-protected-resource")
    assert wrong_path.status_code == 404


def test_external_oauth_metadata_matches_mcp_resource_and_challenge(monkeypatch):
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "dummy-client")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "dummy-secret")
    monkeypatch.setenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
    monkeypatch.setenv("WORKSPACE_MCP_PORT", "8000")
    monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://workspace.example.com")
    monkeypatch.setenv("EXTERNAL_OAUTH21_PROVIDER", "true")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "true")

    import core.server as core_server
    from auth.oauth_config import reload_oauth_config

    reload_oauth_config()
    core_server = importlib.reload(core_server)
    core_server.set_transport_mode("streamable-http")
    core_server.configure_server_for_http()

    app = core_server.server.http_app(transport="streamable-http", path="/mcp")
    client = TestClient(app)

    protected_resource = client.get("/.well-known/oauth-protected-resource/mcp")
    assert protected_resource.status_code == 200
    assert protected_resource.json()["resource"] == "https://workspace.example.com/mcp"

    wrong_path = client.get("/.well-known/oauth-protected-resource")
    assert wrong_path.status_code == 404

    challenge = client.post(
        "/mcp",
        headers={"Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        },
    )
    assert challenge.status_code == 401
    assert (
        'resource_metadata="https://workspace.example.com/'
        '.well-known/oauth-protected-resource/mcp"'
        in challenge.headers["www-authenticate"]
    )
