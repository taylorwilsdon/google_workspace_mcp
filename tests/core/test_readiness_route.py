import asyncio
import json

import pytest
from key_value.aio.stores.memory import MemoryStore
from starlette.requests import Request

import core.server as server_module
from core.server import health_check, readiness_check


def _request(path: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
    }
    return Request(scope)


async def _ready(monkeypatch, storage):
    monkeypatch.setattr(server_module, "_oauth_client_storage", storage)
    response = await readiness_check(_request("/health/ready"))
    return response.status_code, json.loads(response.body)


class RaisingStore:
    async def get(self, *, key, collection):
        raise ConnectionRefusedError("valkey:6379 refused connection")


class HangingStore:
    def __init__(self):
        self.cancelled = False

    async def get(self, *, key, collection):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.mark.asyncio
async def test_ready_without_explicit_storage(monkeypatch):
    status, body = await _ready(monkeypatch, None)

    assert status == 200
    assert body == {"status": "ready", "storage": "default"}


@pytest.mark.asyncio
async def test_ready_with_memory_store(monkeypatch):
    status, body = await _ready(monkeypatch, MemoryStore())

    assert status == 200
    assert body == {"status": "ready", "storage": "ok"}


@pytest.mark.asyncio
async def test_unavailable_when_store_raises(monkeypatch, caplog):
    status, body = await _ready(monkeypatch, RaisingStore())

    assert status == 503
    assert body == {
        "status": "unavailable",
        "storage": "unavailable",
        "error": "ConnectionRefusedError",
    }
    assert "ConnectionRefusedError" in caplog.text
    assert "refused connection" not in caplog.text


@pytest.mark.asyncio
async def test_unavailable_when_store_hangs(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_READINESS_TIMEOUT_SECONDS", "0.05")
    store = HangingStore()

    status, body = await _ready(monkeypatch, store)

    assert status == 503
    assert body["storage"] == "unavailable"
    assert body["error"] == "storage probe timed out after 0.05s"
    assert store.cancelled


@pytest.mark.parametrize("raw", ["", "abc", "0", "-1", "inf", "nan"])
def test_readiness_timeout_falls_back_to_default(monkeypatch, raw):
    monkeypatch.setenv("WORKSPACE_MCP_READINESS_TIMEOUT_SECONDS", raw)
    assert server_module._readiness_timeout_seconds() == 1.0


@pytest.mark.asyncio
async def test_liveness_ignores_storage(monkeypatch):
    monkeypatch.setattr(server_module, "_oauth_client_storage", HangingStore())

    response = await asyncio.wait_for(health_check(_request("/health")), timeout=1)

    assert response.status_code == 200
