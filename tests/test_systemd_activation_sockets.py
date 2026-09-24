import os
import socket
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep these tests independent of a developer's local .env, matching the
# pattern in tests/test_main_permissions_tier.py.
os.environ["MCP_ENABLE_OAUTH21"] = "false"
os.environ["WORKSPACE_MCP_STATELESS_MODE"] = "false"

import main  # noqa: E402


def _bind_loopback_socket() -> socket.socket:
    """Bind and listen on an ephemeral loopback port, returning the socket."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s


def test_systemd_activation_sockets_none_when_env_unset(monkeypatch):
    """No activation env vars means no activation sockets."""
    monkeypatch.delenv("LISTEN_PID", raising=False)
    monkeypatch.delenv("LISTEN_FDS", raising=False)

    assert main._systemd_activation_sockets() is None


def test_systemd_activation_sockets_none_on_pid_mismatch(monkeypatch):
    """A LISTEN_PID that isn't ours is ignored.

    LISTEN_PID must match our own pid; a stale/foreign value (as if the
    env were inherited by an unrelated child process) must be ignored.
    """
    monkeypatch.setenv("LISTEN_PID", "1")
    monkeypatch.setenv("LISTEN_FDS", "1")

    assert main._systemd_activation_sockets() is None


def test_systemd_activation_sockets_none_on_malformed_listen_pid(monkeypatch):
    """A non-numeric LISTEN_PID is ignored and the env vars are left intact."""
    monkeypatch.setenv("LISTEN_PID", "not-a-pid")
    monkeypatch.setenv("LISTEN_FDS", "1")

    assert main._systemd_activation_sockets() is None
    # A parse failure must not consume the env vars.
    assert os.environ.get("LISTEN_PID") == "not-a-pid"
    assert os.environ.get("LISTEN_FDS") == "1"


def test_systemd_activation_sockets_none_on_malformed_listen_fds(monkeypatch):
    """A non-numeric LISTEN_FDS is ignored and the env vars are left intact."""
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "not-a-number")

    assert main._systemd_activation_sockets() is None
    assert os.environ.get("LISTEN_PID") == str(os.getpid())
    assert os.environ.get("LISTEN_FDS") == "not-a-number"


def test_systemd_activation_sockets_none_on_zero_listen_fds(monkeypatch):
    """A LISTEN_FDS of 0 means no sockets were handed off."""
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "0")

    assert main._systemd_activation_sockets() is None


def test_systemd_activation_sockets_none_on_negative_listen_fds(monkeypatch):
    """A negative LISTEN_FDS is treated as invalid, not underflowed."""
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    monkeypatch.setenv("LISTEN_FDS", "-1")

    assert main._systemd_activation_sockets() is None


def test_systemd_activation_sockets_returns_inherited_sockets(monkeypatch):
    """A matching PID and positive LISTEN_FDS returns the fd-3 socket and clears the env vars."""
    bound = _bind_loopback_socket()
    expected_port = bound.getsockname()[1]

    # Preserve whatever (if anything) already occupies fd 3 in this
    # process, since sd_listen_fds(3) always hands off sockets starting
    # at fd 3 and we want to restore the test process afterward.
    saved_fd3 = None
    try:
        saved_fd3 = os.dup(3)
    except OSError:
        pass

    try:
        os.dup2(bound.fileno(), 3)
        monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
        monkeypatch.setenv("LISTEN_FDS", "1")

        sockets = main._systemd_activation_sockets()

        assert sockets is not None
        assert len(sockets) == 1
        assert sockets[0].getsockname()[1] == expected_port
        # Consumed and cleared, so a child process (if any) can't also
        # claim the same handed-off sockets.
        assert "LISTEN_PID" not in os.environ
        assert "LISTEN_FDS" not in os.environ

        for sock in sockets:
            sock.close()
    finally:
        bound.close()
        if saved_fd3 is not None:
            os.dup2(saved_fd3, 3)
            os.close(saved_fd3)
        else:
            try:
                os.close(3)
            except OSError:
                pass
