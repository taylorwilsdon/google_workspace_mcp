import errno
from types import TracebackType
from typing import Any, Optional, Tuple, Type

import pytest
from starlette.testclient import TestClient

from auth import oauth_callback_server


class _DummyMinimalOAuthServer:
    instances = []

    def __init__(self, port, base_uri):
        self.port = port
        self.base_uri = base_uri
        self.running = False
        self.start_calls = 0
        self.stop_calls = 0
        self.__class__.instances.append(self)

    def matches_endpoint(self, port, base_uri):
        return self.port == port and self.base_uri == base_uri

    def is_actually_running(self):
        return self.running

    def start(self):
        self.start_calls += 1
        self.running = True
        return True, ""

    def stop(self):
        self.stop_calls += 1
        self.running = False


class _DeadThread:
    def is_alive(self):
        return False


class _AliveThread:
    def is_alive(self):
        return True


def test_ensure_oauth_callback_recreates_server_when_endpoint_changes(monkeypatch):
    _DummyMinimalOAuthServer.instances = []
    monkeypatch.setattr(
        oauth_callback_server,
        "MinimalOAuthServer",
        _DummyMinimalOAuthServer,
    )
    monkeypatch.setattr(oauth_callback_server, "_minimal_oauth_server", None)

    success, error = oauth_callback_server.ensure_oauth_callback_available(
        "stdio", 8000, "http://localhost"
    )

    assert success is True
    assert error == ""
    assert len(_DummyMinimalOAuthServer.instances) == 1

    first_server = _DummyMinimalOAuthServer.instances[0]

    success, error = oauth_callback_server.ensure_oauth_callback_available(
        "stdio", 9000, "http://127.0.0.1"
    )

    assert success is True
    assert error == ""
    assert len(_DummyMinimalOAuthServer.instances) == 2
    assert first_server.stop_calls == 1

    replacement_server = _DummyMinimalOAuthServer.instances[1]
    assert replacement_server.port == 9000
    assert replacement_server.base_uri == "http://127.0.0.1"
    assert replacement_server.start_calls == 1


def test_is_actually_running_returns_false_when_server_thread_is_dead(monkeypatch):
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")
    server.is_running = True
    server.server_thread = _DeadThread()

    def fail_if_socket_used(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("dead server thread should short-circuit health check")

    monkeypatch.setattr(oauth_callback_server.socket, "socket", fail_if_socket_used)

    assert server.is_actually_running() is False


def test_is_actually_running_treats_eaddrinuse_as_callback_port_in_use(monkeypatch):
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")
    server.is_running = True
    server.server_thread = _AliveThread()

    class _FakeSocket:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            self.bind_calls = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ARG002
            return False

        def settimeout(self, timeout):  # noqa: ARG002
            return None

        def connect_ex(self, address):  # noqa: ARG002
            return 111

        def bind(self, address):  # noqa: ARG002
            raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(oauth_callback_server.socket, "socket", _FakeSocket)

    assert server.is_actually_running() is True


def test_ensure_oauth_callback_skips_start_when_other_instance_owns_port(monkeypatch):
    _DummyMinimalOAuthServer.instances = []
    monkeypatch.setattr(oauth_callback_server, "_minimal_oauth_server", None)

    class _PortInUseServer(_DummyMinimalOAuthServer):
        def is_actually_running(self):
            return True

    monkeypatch.setattr(
        oauth_callback_server,
        "MinimalOAuthServer",
        _PortInUseServer,
    )

    success, error = oauth_callback_server.ensure_oauth_callback_available(
        "stdio", 8000, "http://localhost"
    )

    assert success is True
    assert error == ""
    assert len(_PortInUseServer.instances) == 1
    assert _PortInUseServer.instances[0].start_calls == 0


def test_start_reuses_existing_workspace_callback_on_eaddrinuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")

    class _FakeSocket:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        def __enter__(self) -> "_FakeSocket":
            return self

        def __exit__(  # noqa: ARG002
            self,
            exc_type: Optional[Type[BaseException]],
            exc: Optional[BaseException],
            tb: Optional[TracebackType],
        ) -> bool:
            return False

        def bind(self, address: Tuple[str, int]) -> None:  # noqa: ARG002
            raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(oauth_callback_server.socket, "socket", _FakeSocket)
    monkeypatch.setattr(
        server,
        "_callback_endpoint_looks_like_workspace",
        lambda hostname: hostname == "localhost",
    )

    success, error = server.start()

    assert success is True
    assert error == ""
    assert server.is_running is True


def test_start_rejects_eaddrinuse_when_callback_probe_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")

    class _FakeSocket:
        def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
            pass

        def __enter__(self) -> "_FakeSocket":
            return self

        def __exit__(  # noqa: ARG002
            self,
            exc_type: Optional[Type[BaseException]],
            exc: Optional[BaseException],
            tb: Optional[TracebackType],
        ) -> bool:
            return False

        def bind(self, address: Tuple[str, int]) -> None:  # noqa: ARG002
            raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(oauth_callback_server.socket, "socket", _FakeSocket)
    monkeypatch.setattr(
        server,
        "_callback_endpoint_looks_like_workspace",
        lambda hostname: False,  # noqa: ARG005
    )

    success, error = server.start()

    assert success is False
    assert "already in use" in error
    assert server.is_running is False


def test_oauth_callback_missing_state_fallback_follows_single_user_mode(monkeypatch):
    calls = []

    async def fake_handle_auth_callback(**kwargs):
        calls.append(kwargs)
        return "user@example.com", object()

    monkeypatch.setattr(oauth_callback_server, "check_client_secrets", lambda: None)
    monkeypatch.setattr(oauth_callback_server, "get_current_scopes", lambda: ["scope"])
    monkeypatch.setattr(
        oauth_callback_server,
        "get_oauth_redirect_uri",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr(
        oauth_callback_server,
        "handle_auth_callback",
        fake_handle_auth_callback,
    )

    monkeypatch.delenv("MCP_SINGLE_USER_MODE", raising=False)
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")
    response = TestClient(server.app).get("/oauth2callback?code=code123")

    assert response.status_code == 200
    assert calls[-1]["allow_missing_state_fallback"] is False

    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")
    response = TestClient(server.app).get("/oauth2callback?code=code123")

    assert response.status_code == 200
    assert calls[-1]["allow_missing_state_fallback"] is True
