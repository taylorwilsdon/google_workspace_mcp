"""Finding 19: HTTP request bodies must be bounded.

Every MCP tool call arrives as a JSON body that the transport buffers and parses, so
an unbounded body is a memory-exhaustion DoS reachable before authentication runs.
"""

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from core.server import (
    MAX_HTTP_REQUEST_BODY_BYTES,
    RequestBodySizeLimitMiddleware,
)

LIMIT = 1024


def _app(limit=LIMIT, record=None):
    async def endpoint(request):
        body = await request.body()
        if record is not None:
            record.append(len(body))
        return JSONResponse({"received": len(body)})

    return Starlette(
        routes=[Route("/rpc", endpoint, methods=["POST"])],
        middleware=[Middleware(RequestBodySizeLimitMiddleware, max_body_bytes=limit)],
    )


def test_default_limit_is_fixed_at_50_mib():
    """The ceiling is a constant, so no deployment can be configured to remove it."""
    assert MAX_HTTP_REQUEST_BODY_BYTES == 50 * 1024 * 1024


def test_body_at_the_limit_is_accepted():
    client = TestClient(_app())

    response = client.post("/rpc", content=b"x" * LIMIT)

    assert response.status_code == 200
    assert response.json() == {"received": LIMIT}


def test_declared_oversized_body_is_rejected_without_being_read():
    """Content-Length over the limit is refused before the body is read at all."""
    received = []
    client = TestClient(_app(record=received))

    response = client.post("/rpc", content=b"x" * (LIMIT + 1))

    assert response.status_code == 413
    assert response.json()["max_body_bytes"] == LIMIT
    assert received == []


def test_oversized_chunked_body_is_rejected():
    """Content-Length is client-supplied and absent on chunked transfers.

    Counting only the declared size would leave the same DoS reachable with
    `Transfer-Encoding: chunked`, so the streamed chunks are counted too.
    """

    def chunks():
        for _ in range(4):
            yield b"y" * (LIMIT // 2)

    client = TestClient(_app())

    response = client.post("/rpc", content=chunks())

    assert response.status_code == 413
    assert response.json()["max_body_bytes"] == LIMIT


def test_chunked_body_within_the_limit_still_works():
    def chunks():
        yield b"a" * 100
        yield b"b" * 100

    client = TestClient(_app())

    response = client.post("/rpc", content=chunks())

    assert response.status_code == 200
    assert response.json() == {"received": 200}


@pytest.mark.asyncio
async def test_non_http_scopes_pass_through():
    """Lifespan/websocket scopes must not be touched by the body guard."""
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    middleware = RequestBodySizeLimitMiddleware(app, max_body_bytes=LIMIT)

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        return None

    await middleware({"type": "lifespan"}, receive, send)

    assert seen == ["lifespan"]


@pytest.mark.parametrize("bad_length", ["not-a-number", ""])
def test_unparsable_content_length_falls_back_to_streaming_count(bad_length):
    """A malformed Content-Length must not skip the check entirely."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/rpc",
        "headers": [(b"content-length", bad_length.encode())],
    }
    middleware = RequestBodySizeLimitMiddleware(_app(), max_body_bytes=LIMIT)

    assert middleware._declared_length(scope) is None
