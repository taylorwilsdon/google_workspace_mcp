"""Tests for streamable-http / dual-transport TCP bind pre-flight probes."""

import errno
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


def test_probe_tcp_bind_succeeds_on_free_port():
    # Port 0 lets the OS allocate an ephemeral port atomically during the probe.
    host = "127.0.0.1"
    main.probe_tcp_bind(host, 0)


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
    with patch("main.socket.socket", TrackingSocket):
        main.probe_tcp_bind(host, 0)

    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in recorded


def test_probe_tcp_bind_raises_when_port_held():
    with _hold_listening_port() as (host, port):
        with pytest.raises(OSError):
            main.probe_tcp_bind(host, port)


def _leave_time_wait_on_port(host: str = "127.0.0.1") -> int:
    """Return a port whose only occupant is a server-side TIME_WAIT socket.

    The listener accepts one connection and closes it first, so the TIME_WAIT
    state lands on the server side (local port = the listener's port). Peers
    and listener are all closed before returning; only the lingering state is
    left, which is exactly what a fast restart of the HTTP server sees.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection((host, port))
    server_side, _ = listener.accept()
    server_side.close()  # active close on the server side -> TIME_WAIT there
    assert client.recv(1) == b""  # client sees EOF, then closes its end
    client.close()
    listener.close()
    return port


def test_probe_tcp_bind_accepts_port_with_only_time_wait_sockets():
    """Regression for #946: lingering TIME_WAIT must not read as a conflict.

    A bare bind (the pre-fix probe) fails here with EADDRINUSE; uvicorn's
    SO_REUSEADDR bind would succeed, so the probe must succeed too.
    """
    host = "127.0.0.1"
    port = _leave_time_wait_on_port(host)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bare:
        try:
            bare.bind((host, port))
        except OSError as exc:
            assert exc.errno == errno.EADDRINUSE
        else:
            pytest.skip("platform does not block a bare bind on TIME_WAIT")

    main.probe_tcp_bind(host, port)  # must not raise


def test_report_preflight_bind_failure_logs_at_error(caplog):
    """Bind failure must be visible at ERROR when stderr is non-TTY (containers)."""
    error = OSError(errno.EADDRINUSE, "Address already in use")
    with caplog.at_level(logging.DEBUG, logger="main"):
        main.report_preflight_bind_failure(8123, error)

    error_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert any("Socket error during pre-flight bind" in m for m in error_messages)
    assert any("8123" in m and "already in use" in m for m in error_messages)
    # Must not be debug-only: at least one ERROR record for the failure.
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_report_preflight_bind_failure_non_eaddrinuse_is_generic(caplog):
    """Non-EADDRINUSE bind errors must not claim the port is already in use."""
    error = OSError(errno.EACCES, "Permission denied")
    with caplog.at_level(logging.DEBUG, logger="main"):
        main.report_preflight_bind_failure(8123, error)

    error_messages = [
        r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR
    ]
    assert any("Socket error during pre-flight bind" in m for m in error_messages)
    assert any("Failed to bind HTTP server on port 8123" in m for m in error_messages)
    assert not any("already in use" in m for m in error_messages)


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
