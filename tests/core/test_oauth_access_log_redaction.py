"""OAuth redirect ``code``/``state`` values must not reach the uvicorn access log."""

import asyncio
import logging
import socket

import httpx
import pytest
import uvicorn

from core.log_formatter import (
    RedactOAuthQueryParamsFilter,
    install_noisy_log_filters,
    redact_oauth_query_params,
)
from core.server import server


@pytest.mark.parametrize(
    "target, expected",
    [
        (
            "/oauth2callback?state=s1&iss=https://accounts.google.com&code=4/0Ab&scope=email",
            "/oauth2callback?state=<redacted>&iss=https://accounts.google.com&code=<redacted>&scope=email",
        ),
        (
            "/oauth2callback?error=access_denied&state=s1",
            "/oauth2callback?error=access_denied&state=<redacted>",
        ),
        (
            "/auth/callback?code=abc&state=xyz",
            "/auth/callback?code=<redacted>&state=<redacted>",
        ),
        # names are matched after percent-decoding, as the server parses them
        (
            "/oauth2callback?%63ode=c1&st%61te=s1&x=%63ode",
            "/oauth2callback?%63ode=<redacted>&st%61te=<redacted>&x=%63ode",
        ),
        ("/oauth2callback?xcode=1&codes=2", "/oauth2callback?xcode=1&codes=2"),
        ("/oauth2callback?code", "/oauth2callback?code"),
        ("/mcp", "/mcp"),
    ],
)
def test_redact_oauth_query_params(target: str, expected: str) -> None:
    assert redact_oauth_query_params(target) == expected


def test_filter_keeps_other_args() -> None:
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1", "GET", "/oauth2callback?code=secret", "1.1", 200),
        None,
    )
    assert RedactOAuthQueryParamsFilter().filter(record) is True
    assert record.args == (
        "127.0.0.1:1",
        "GET",
        "/oauth2callback?code=<redacted>",
        "1.1",
        200,
    )


def test_install_is_idempotent() -> None:
    install_noisy_log_filters()
    install_noisy_log_filters()
    filters = [
        f
        for f in logging.getLogger("uvicorn.access").filters
        if isinstance(f, RedactOAuthQueryParamsFilter)
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
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
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
    task = asyncio.create_task(srv.serve())
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
        logging.getLogger("uvicorn.access").removeHandler(capture)

    text = "\n".join(capture.lines)
    assert "CODESECRET" not in text and "STATESECRET" not in text
    assert "/oauth2callback?state=<redacted>&code=<redacted>" in text
