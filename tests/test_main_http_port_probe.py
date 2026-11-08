"""Tests for the streamable-http startup port probe in main.py."""

import os
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="TIME_WAIT bind semantics differ on Windows"
)


def _listener(port: int = 0) -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen()
    return srv


def test_port_with_live_listener_is_not_free():
    srv = _listener()
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict("127.0.0.1", port) is not None
    finally:
        srv.close()


def test_port_with_live_listener_is_not_free_when_probing_all_interfaces():
    srv = _listener()
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict("0.0.0.0", port) is not None
    finally:
        srv.close()


def test_wildcard_probe_sees_a_listener_on_a_non_loopback_address():
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except (OSError, UnicodeError):
        infos = []
    lan_addresses = [info[4][0] for info in infos if info[4][0] != "127.0.0.1"]
    if not lan_addresses:
        pytest.skip("no non-loopback IPv4 address on this machine")
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind((lan_addresses[0], 0))
    srv.listen()
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict("0.0.0.0", port) is not None
    finally:
        srv.close()


def test_port_left_in_time_wait_by_a_stopped_server_is_free():
    # Closing the accepted side first leaves TIME_WAIT on the listening port.
    srv = _listener()
    port = srv.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = srv.accept()
    conn.close()
    srv.close()
    time.sleep(0.1)
    client.close()

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as plain:
        with pytest.raises(OSError):
            plain.bind(("127.0.0.1", port))

    assert main._http_port_conflict("127.0.0.1", port) is None


def test_unused_port_is_free():
    srv = _listener()
    port = srv.getsockname()[1]
    srv.close()
    assert main._http_port_conflict("127.0.0.1", port) is None


@pytest.mark.parametrize("hostname", ["a" * 64, "bad..name"], ids=["long", "empty"])
def test_unencodable_hostname_falls_back_to_loopback(monkeypatch, hostname):
    monkeypatch.setattr(main.socket, "gethostname", lambda: hostname)
    assert main._local_ipv4_addresses() == ["127.0.0.1"]


def _start_server(tmp_path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "main.py", "--tools", "gmail", *args],
        cwd=REPO_ROOT,
        env={
            **{k: v for k, v in os.environ.items() if k != "PORT"},
            "MCP_ENABLE_OAUTH21": "false",
            "WORKSPACE_MCP_CREDENTIALS_DIR": str(tmp_path),
            **env,
        },
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_streamable_http_port_conflict_is_logged_at_error(tmp_path):
    srv = _listener()
    try:
        port = srv.getsockname()[1]
        result = _start_server(
            tmp_path,
            "--transport",
            "streamable-http",
            WORKSPACE_MCP_HOST="127.0.0.1",
            WORKSPACE_MCP_PORT=str(port),
        )
    finally:
        srv.close()

    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert f"Cannot start HTTP server on 127.0.0.1:{port}" in result.stderr
    assert f"a server is already listening on 127.0.0.1:{port}" in result.stderr


def test_streamable_http_bind_failure_is_not_reported_as_port_in_use(tmp_path):
    # 192.0.2.0/24 is TEST-NET-1, never assigned locally, so bind fails EADDRNOTAVAIL.
    result = _start_server(
        tmp_path,
        "--transport",
        "streamable-http",
        WORKSPACE_MCP_HOST="192.0.2.1",
        WORKSPACE_MCP_PORT="8000",
    )

    assert result.returncode == 1
    assert "Cannot start HTTP server on 192.0.2.1:8000" in result.stderr
    assert "assign requested address" in result.stderr
    assert "already in use" not in result.stderr


def test_sidecar_port_conflict_warning_includes_the_reason(tmp_path):
    srv = _listener()
    try:
        port = srv.getsockname()[1]
        result = _start_server(tmp_path, WORKSPACE_MCP_HTTP_PORT=str(port))
    finally:
        srv.close()

    assert (
        f"workspace-cli HTTP endpoint unavailable on port {port}: "
        f"a server is already listening on 127.0.0.1:{port}"
    ) in result.stderr
