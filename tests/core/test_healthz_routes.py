import pytest
from starlette.requests import Request

from core.health import mark_mcp_tools_ready, mcp_tools_ready
from core.server import healthz_liveness, healthz_readiness


def _build_request(path: str) -> Request:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("localhost", 8080),
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_healthz_liveness_always_ok():
    response = await healthz_liveness(_build_request("/healthz"))
    assert response.status_code == 200
    assert response.body == b"OK\n"


@pytest.mark.asyncio
async def test_healthz_ready_before_tools_loaded():
    # Reset readiness for isolated test run order.
    import core.health as health_module

    health_module._mcp_tools_ready.clear()
    assert not mcp_tools_ready()

    response = await healthz_readiness(_build_request("/healthz/ready"))
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_healthz_ready_after_tools_loaded():
    mark_mcp_tools_ready()
    response = await healthz_readiness(_build_request("/healthz/ready"))
    assert response.status_code == 200
    assert response.body == b"OK\n"
