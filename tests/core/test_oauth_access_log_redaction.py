"""Request query strings, such as OAuth ``code``/``state``, never reach the uvicorn access log."""

import asyncio
import logging
import socket
from typing import Any

import httpx
import pytest
import uvicorn

from core.log_formatter import (
    RedactAccessLogQueryFilter,
    SuppressStatelessTransportTerminationFilter,
    install_noisy_log_filters,
)
from core.server import server


def _access_record(args: Any) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        args,
        None,
    )


@pytest.mark.parametrize(
    "target, expected",
    [
        ("/oauth2callback?state=s1&code=4/0Ab", "/oauth2callback?<query-redacted>"),
        ("/oauth2callback?x='&code=c1", "/oauth2callback?<query-redacted>"),
        ("/mcp", "/mcp"),
    ],
)
def test_filter_scrubs_only_the_query(target: str, expected: str) -> None:
    record = _access_record(("127.0.0.1:1", "GET", target, "1.1", 200))
    assert RedactAccessLogQueryFilter().filter(record) is True
    assert record.args == ("127.0.0.1:1", "GET", expected, "1.1", 200)


@pytest.mark.parametrize(
    "args",
    [None, ({"code": "x"},), ("127.0.0.1:1", "GET"), ("a", "GET", b"/cb?code=x")],
)
def test_filter_ignores_other_record_shapes(args: Any) -> None:
    record = _access_record(args)
    before = record.args
    assert RedactAccessLogQueryFilter().filter(record) is True
    assert record.args == before


@pytest.mark.parametrize(
    "logger_name, filter_type",
    [
        ("uvicorn.access", RedactAccessLogQueryFilter),
        ("mcp.server.streamable_http", SuppressStatelessTransportTerminationFilter),
    ],
)
def test_install_is_idempotent(
    logger_name: str, filter_type: type[logging.Filter]
) -> None:
    install_noisy_log_filters()
    install_noisy_log_filters()
    filters = [
        f for f in logging.getLogger(logger_name).filters if isinstance(f, filter_type)
    ]
    assert len(filters) == 1


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.mark.asyncio
async def test_real_uvicorn_access_log_never_contains_the_code() -> None:
    install_noisy_log_filters()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        server.http_app(),
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=True,
        lifespan="off",
    )
    capture = _Capture()
    # Config() has just applied uvicorn's logging setup; attach after it.
    logging.getLogger("uvicorn.access").addHandler(capture)
    srv = uvicorn.Server(config)
    task = asyncio.create_task(srv.serve(sockets=[sock]))
    try:
        for _ in range(100):
            if srv.started:
                break
            await asyncio.sleep(0.05)
        assert srv.started
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            await http.get("/oauth2callback?state=STATESECRET&code=CODESECRET")
    finally:
        srv.should_exit = True
        await task
        sock.close()
        logging.getLogger("uvicorn.access").removeHandler(capture)

    text = "\n".join(capture.lines)
    assert "CODESECRET" not in text and "STATESECRET" not in text
    assert "/oauth2callback?<query-redacted>" in text
