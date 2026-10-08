import asyncio
import gc
import threading

import pytest
from fastmcp import Client, FastMCP

from core.server import (
    _freeze_startup_heap,
    _google_api_executor,
    get_google_api_workers,
)

_ENV = "WORKSPACE_MCP_GOOGLE_API_WORKERS"


@pytest.mark.parametrize(("raw", "expected"), [(None, None), ("", None), (" 8 ", 8)])
def test_get_google_api_workers(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(_ENV, raising=False)
    else:
        monkeypatch.setenv(_ENV, raw)

    assert get_google_api_workers() == expected


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "1.5"])
def test_get_google_api_workers_rejects_invalid(monkeypatch, raw):
    monkeypatch.setenv(_ENV, raw)

    with pytest.raises(ValueError, match=_ENV):
        get_google_api_workers()


async def _worker_thread_name() -> str:
    probe_server = FastMCP("probe", lifespan=_google_api_executor)

    @probe_server.tool()
    async def worker_thread_name() -> str:
        return await asyncio.to_thread(lambda: threading.current_thread().name)

    async with Client(probe_server) as client:
        result = await client.call_tool("worker_thread_name", {})
    return result.data


@pytest.mark.asyncio
async def test_lifespan_keeps_default_executor_when_unset(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)

    assert not (await _worker_thread_name()).startswith("google-api")


@pytest.mark.asyncio
async def test_lifespan_sizes_executor_when_configured(monkeypatch):
    monkeypatch.setenv(_ENV, "8")

    assert (await _worker_thread_name()).startswith("google-api")


@pytest.mark.asyncio
async def test_freeze_startup_heap_exempts_startup_objects_from_gc():
    try:
        async with _freeze_startup_heap(FastMCP("probe")):
            assert gc.get_freeze_count() > 0
    finally:
        gc.unfreeze()


@pytest.mark.asyncio
async def test_lifespan_rejects_invalid_google_api_timeout(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_GOOGLE_API_TIMEOUT_SECONDS", "abc")

    with pytest.raises(ValueError, match="WORKSPACE_MCP_GOOGLE_API_TIMEOUT_SECONDS"):
        async with _google_api_executor(FastMCP("probe")):
            pass
