"""Tests for streamable-http / dual-transport TCP bind pre-flight probes."""

import logging
import os
import socket
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("MCP_ENABLE_OAUTH21", "false")
os.environ.setdefault("WORKSPACE_MCP_STATELESS_MODE", "false")

import main


@contextmanager
def _hold_listening_port(host: str = "127.0.0.1"):
    """Hold an exclusive listening socket so a SO_REUSEADDR probe still fails."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
        # Windows: prevent SO_REUSEADDR peers from sharing the bind.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    s.bind((host, 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        yield host, port
    finally:
        s.close()


def _free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def test_probe_tcp_bind_succeeds_on_free_port():
    host = "127.0.0.1"
    port = _free_port(host)
    main.probe_tcp_bind(host, port)


def test_probe_tcp_bind_sets_so_reuseaddr():
    """Probe must enable SO_REUSEADDR to match uvicorn/asyncio bind semantics."""
    recorded: list[tuple[int, int, int]] = []
    real_socket = socket.socket

    class TrackingSocket:
        def __init__(self, *args, **kwargs):
            self._sock = real_socket(*args, **kwargs)

        def setsockopt(self, level, optname, value, *args, **kwargs):
            recorded.append((level, optname, value))
            return self._sock.setsockopt(level, optname, value, *args, **kwargs)

        def bind(self, address):
            return self._sock.bind(address)

        def close(self):
            return self._sock.close()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.close()
            return False

    host = "127.0.0.1"
    port = _free_port(host)
    with patch("main.socket.socket", TrackingSocket):
        main.probe_tcp_bind(host, port)

    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in recorded


def test_probe_tcp_bind_raises_when_port_held():
    with _hold_listening_port() as (host, port):
        with pytest.raises(OSError):
            main.probe_tcp_bind(host, port)


def test_report_preflight_bind_failure_logs_at_error(caplog):
    """Bind failure must be visible at ERROR when stderr is non-TTY (containers)."""
    error = OSError(98, "Address already in use")
    with caplog.at_level(logging.DEBUG, logger="main"):
        main.report_preflight_bind_failure(8123, error)

    error_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert any("Socket error during pre-flight bind" in m for m in error_messages)
    assert any("8123" in m and "already in use" in m for m in error_messages)
    # Must not be debug-only: at least one ERROR record for the failure.
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_outer_except_exception_does_not_catch_systemexit():
    """Regression for #946: SystemExit is BaseException, not Exception."""

    def raises_system_exit():
        try:
            raise SystemExit(1)
        except Exception:
            return "swallowed"
        except BaseException as e:
            return type(e).__name__

    assert raises_system_exit() == "SystemExit"
