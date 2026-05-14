import errno

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


def test_ensure_oauth_callback_falls_back_to_alternative_port(monkeypatch):
    """When the preferred port is in use, the server should try alternative ports."""

    class _PortConflictServer(_DummyMinimalOAuthServer):
        def start(self):
            self.start_calls += 1
            if self.port == 8000:
                return False, "Port 8000 is already in use"
            self.running = True
            return True, ""

    _PortConflictServer.instances = []
    monkeypatch.setattr(oauth_callback_server, "_minimal_oauth_server", None)
    monkeypatch.setattr(
        oauth_callback_server,
        "MinimalOAuthServer",
        _PortConflictServer,
    )

    # Mock the oauth config so the redirect URI update doesn't fail
    class _FakeOAuthConfig:
        def __init__(self):
            self.port = 8000
            self.base_uri = "http://localhost"
            self.base_url = "http://localhost:8000"
            self.redirect_uri = "http://localhost:8000/oauth2callback"

    fake_config = _FakeOAuthConfig()
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: fake_config,
    )

    success, error = oauth_callback_server.ensure_oauth_callback_available(
        "stdio", 8000, "http://localhost"
    )

    assert success is True
    assert error == ""
    # First instance tried port 8000 and failed, second got 8001
    assert len(_PortConflictServer.instances) == 2
    assert _PortConflictServer.instances[0].port == 8000
    assert _PortConflictServer.instances[1].port == 8001
    assert fake_config.port == 8001
    assert fake_config.redirect_uri == "http://localhost:8001/oauth2callback"


def test_ensure_oauth_callback_fails_when_all_ports_exhausted(monkeypatch):
    """When all ports in the range are in use, the server should fail gracefully."""

    class _AllPortsBusyServer(_DummyMinimalOAuthServer):
        def start(self):
            self.start_calls += 1
            return False, f"Port {self.port} is already in use"

    _AllPortsBusyServer.instances = []
    monkeypatch.setattr(oauth_callback_server, "_minimal_oauth_server", None)
    monkeypatch.setattr(
        oauth_callback_server,
        "MinimalOAuthServer",
        _AllPortsBusyServer,
    )
    monkeypatch.setenv("WORKSPACE_MCP_PORT_RANGE", "3")

    success, error = oauth_callback_server.ensure_oauth_callback_available(
        "stdio", 8000, "http://localhost"
    )

    assert success is False
    assert "8000-8002" in error
    # Tried 8000 (original) + 8001, 8002 (alternatives) = created 3 instances
    assert len(_AllPortsBusyServer.instances) == 3


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
