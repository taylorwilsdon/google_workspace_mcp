"""Tests for the streamable-http startup port probe in main.py."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import main  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="TIME_WAIT bind semantics differ on Windows"
)


def _listener(port: int = 0, address: str = "127.0.0.1") -> socket.socket:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    srv = socket.socket(family, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((address, port))
    srv.listen()
    return srv


def _ipv6_listener() -> socket.socket:
    try:
        return _listener(address="::1")
    except OSError:
        pytest.skip("IPv6 loopback unavailable")


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


@pytest.mark.parametrize("host", ["", "::", "::1"])
def test_port_with_live_ipv6_listener_is_not_free(host: str) -> None:
    srv = _ipv6_listener()
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict(host, port) == (
            f"a server is already listening on [::1]:{port}"
        )
    finally:
        srv.close()


def test_ipv4_only_probe_ignores_an_ipv6_listener() -> None:
    srv = _ipv6_listener()
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict("127.0.0.1", port) is None
    finally:
        srv.close()


def test_ipv6_wildcard_probe_ignores_an_ipv4_wildcard_listener() -> None:
    _ipv6_listener().close()
    srv = _listener(address="0.0.0.0")
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict("::", port) is None
    finally:
        srv.close()


def test_default_host_probe_sees_an_ipv4_listener() -> None:
    srv = _listener()
    try:
        port = srv.getsockname()[1]
        assert main._http_port_conflict("", port) == (
            f"a server is already listening on 127.0.0.1:{port}"
        )
    finally:
        srv.close()


@pytest.mark.parametrize("host", ["", "0.0.0.0", "::", "::1", "localhost"])
def test_unused_port_is_free_on_every_host_form(host: str) -> None:
    srv = _ipv6_listener()
    port = srv.getsockname()[1]
    srv.close()
    assert main._http_port_conflict(host, port) is None


def test_default_host_probe_skips_a_family_the_host_cannot_bind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    srv = _listener()
    port = srv.getsockname()[1]
    srv.close()
    real_getaddrinfo = socket.getaddrinfo

    def fake_getaddrinfo(host: str | None, *args: object, **kwargs: object) -> list:
        infos = real_getaddrinfo(host, *args, **kwargs)
        if host is not None:
            return infos
        # 2001:db8::/32 is documentation space, so bind fails EADDRNOTAVAIL.
        unbindable = (
            socket.AF_INET6,
            socket.SOCK_STREAM,
            0,
            "",
            ("2001:db8::1", port, 0, 0),
        )
        return [unbindable, *(i for i in infos if i[0] == socket.AF_INET)]

    monkeypatch.setattr(main.socket, "getaddrinfo", fake_getaddrinfo)
    assert main._http_port_conflict("", port) is None


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


_OAUTH21_DEFAULT_HOST_ENV = {
    "MCP_ENABLE_OAUTH21": "true",
    "GOOGLE_OAUTH_CLIENT_ID": "test.apps.googleusercontent.com",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-secret",
    "WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND": "memory",
    "WORKSPACE_MCP_HOST": "",
}


def _server_command(*args: str) -> list[str]:
    return [sys.executable, "main.py", "--tools", "gmail", *args]


def _server_env(tmp_path, **env: str) -> dict[str, str]:
    return {
        **{k: v for k, v in os.environ.items() if k != "PORT"},
        "MCP_ENABLE_OAUTH21": "false",
        "WORKSPACE_MCP_CREDENTIALS_DIR": str(tmp_path),
        **env,
    }


def _start_server(tmp_path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        _server_command(*args),
        cwd=REPO_ROOT,
        env=_server_env(tmp_path, **env),
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


def test_oauth21_default_host_reports_an_ipv6_port_conflict(tmp_path) -> None:
    srv = _ipv6_listener()
    try:
        port = srv.getsockname()[1]
        result = _start_server(
            tmp_path,
            "--transport",
            "streamable-http",
            **_OAUTH21_DEFAULT_HOST_ENV,
            WORKSPACE_MCP_PORT=str(port),
        )
    finally:
        srv.close()

    assert result.returncode == 1
    assert f"Cannot start HTTP server on *:{port}" in result.stderr
    assert f"a server is already listening on [::1]:{port}" in result.stderr


def test_oauth21_default_host_serves_ipv4_and_ipv6(tmp_path: Path) -> None:
    srv = _ipv6_listener()
    port = srv.getsockname()[1]
    srv.close()
    proc = subprocess.Popen(
        _server_command("--transport", "streamable-http"),
        cwd=REPO_ROOT,
        env=_server_env(
            tmp_path, **_OAUTH21_DEFAULT_HOST_ENV, WORKSPACE_MCP_PORT=str(port)
        ),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pending = ["127.0.0.1", "::1"]
        deadline = time.monotonic() + 30
        while pending:
            assert proc.poll() is None
            assert time.monotonic() < deadline, f"not listening on {pending}"
            try:
                socket.create_connection((pending[0], port), timeout=0.5).close()
            except OSError:
                time.sleep(0.1)
                continue
            pending.pop(0)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


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
