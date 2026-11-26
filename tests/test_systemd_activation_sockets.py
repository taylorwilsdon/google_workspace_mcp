"""Tests for systemd socket activation of the streamable-http server in main.py."""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Importing main loads .env, and OAuth 2.1 mode changes tool schemas at decoration time.
os.environ["MCP_ENABLE_OAUTH21"] = "false"
os.environ["WORKSPACE_MCP_STATELESS_MODE"] = "false"

import main  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Stands in for systemd: puts the listener on fd 3 and execs main.py under the same pid.
_ACTIVATE = """
import os, sys
os.dup2(int(sys.argv[1]), 3)
os.environ["LISTEN_PID"] = str(os.getpid())
os.environ["LISTEN_FDS"] = "1"
os.execv(sys.executable, [sys.executable, "main.py", *sys.argv[2:]])
"""

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="sd_listen_fds(3) is a POSIX fd handoff"
)


def _listener(host: str = "127.0.0.1") -> socket.socket:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, 0))
    srv.listen()
    return srv


def _serve_activated(
    tmp_path: Path, bind_host: str, workspace_host: str
) -> tuple[int | None, int, str]:
    """Hand main.py a listener on bind_host; return (health status, exit code, stderr)."""
    srv = _listener(bind_host)
    port = srv.getsockname()[1]
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _ACTIVATE,
            str(srv.fileno()),
            "--tools",
            "gmail",
            "--transport",
            "streamable-http",
        ],
        cwd=REPO_ROOT,
        env={
            **{k: v for k, v in os.environ.items() if k != "PORT"},
            "MCP_ENABLE_OAUTH21": "false",
            "WORKSPACE_MCP_CREDENTIALS_DIR": str(tmp_path),
            "WORKSPACE_MCP_HOST": workspace_host,
            "WORKSPACE_MCP_PORT": str(port),
        },
        pass_fds=(srv.fileno(),),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        status = None
        deadline = time.monotonic() + 60
        while status is None and proc.poll() is None and time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=5
                ) as response:
                    status = response.status
            except OSError:
                time.sleep(0.2)
    finally:
        srv.close()
        proc.terminate()
        _, stderr = proc.communicate(timeout=30)
    return status, proc.returncode, stderr


@pytest.fixture
def fd3() -> Iterator[socket.socket]:
    """Put a listening socket on fd 3 and restore whatever was there afterwards."""
    srv = _listener()
    try:
        saved = os.dup(3)
    except OSError:
        saved = None
    os.dup2(srv.fileno(), 3)
    try:
        yield srv
    finally:
        srv.close()
        if saved is None:
            os.close(3)
        else:
            os.dup2(saved, 3)
            os.close(saved)


@pytest.mark.parametrize(
    ("listen_pid", "listen_fds"),
    [
        (None, None),
        (None, "1"),
        ("self", None),
        ("other", "1"),
        ("not-a-pid", "1"),
        ("self", "not-a-number"),
        ("self", "0"),
        ("self", "-1"),
    ],
)
def test_no_activation_sockets_without_a_valid_handoff(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    listen_pid: str | None,
    listen_fds: str | None,
) -> None:
    pids = {"self": str(os.getpid()), "other": str(os.getpid() + 1)}
    env = {
        "LISTEN_PID": pids.get(listen_pid, listen_pid),
        "LISTEN_FDS": listen_fds,
    }
    for name, value in env.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)

    with caplog.at_level("WARNING", logger=main.logger.name):
        assert main._systemd_activation_sockets() is None
    assert {name: os.environ.get(name) for name in env} == env
    assert ("Ignoring systemd socket activation" in caplog.text) == (
        listen_pid == "other"
    )


def test_matching_pid_returns_the_inherited_socket(
    monkeypatch: pytest.MonkeyPatch, fd3: socket.socket
) -> None:
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "1")

    sockets = main._systemd_activation_sockets()

    assert sockets is not None
    try:
        assert [sock.fileno() for sock in sockets] == [3]
        assert sockets[0].getsockname() == fd3.getsockname()
        assert not sockets[0].get_inheritable()
        assert "LISTEN_PID" not in os.environ
        assert "LISTEN_FDS" not in os.environ
    finally:
        for sock in sockets:
            sock.detach()


@pytest.mark.parametrize(
    ("bind", "expected"),
    [("127.0.0.1", []), ("0.0.0.0", ["0.0.0.0"]), ("unix", [])],
)
def test_non_loopback_addresses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bind: str, expected: list[str]
) -> None:
    if bind == "unix":
        # A relative path keeps sun_path under the macOS 104-byte limit.
        monkeypatch.chdir(tmp_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind("s")
        srv.listen()
    else:
        srv = _listener(bind)
    try:
        assert main._non_loopback_addresses([srv]) == expected
    finally:
        srv.close()


def test_streamable_http_serves_on_the_activated_socket(tmp_path: Path) -> None:
    status, _, stderr = _serve_activated(tmp_path, "127.0.0.1", "127.0.0.1")

    assert status == 200, stderr


def test_legacy_mode_refuses_a_non_loopback_activated_socket(tmp_path: Path) -> None:
    # Set but empty, so a developer .env cannot fill WORKSPACE_MCP_HOST in.
    status, returncode, stderr = _serve_activated(tmp_path, "0.0.0.0", "")

    assert status is None, stderr
    assert returncode == 1, stderr
    assert "Refusing socket activation on 0.0.0.0" in stderr


def test_explicit_host_serves_a_non_loopback_activated_socket(tmp_path: Path) -> None:
    status, _, stderr = _serve_activated(tmp_path, "0.0.0.0", "0.0.0.0")

    assert status == 200, stderr
