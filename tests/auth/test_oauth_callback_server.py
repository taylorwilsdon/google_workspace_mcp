import errno

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


class _AliveThread:
    def is_alive(self):
        return True


def test_start_reuses_server_when_thread_alive_and_port_responding():
    """Reproduce the re-auth bug: is_running is False but the server thread is
    still alive and holding the port.  start() should detect this and reuse
    instead of failing with 'port in use'."""
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")
    server.is_running = False  # Stale flag — simulates error handler resetting it
    server.server_thread = _AliveThread()

    # is_actually_running returns True (port is responding)
    server.is_actually_running = lambda: True

    success, error = server.start()

    assert success is True
    assert error == ""
    assert server.is_running is True


def test_start_recovers_when_port_in_use_by_own_server(monkeypatch):
    """When the socket bind check fails but is_actually_running() confirms the
    port is held by our own server, start() should succeed instead of erroring."""
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")
    server.is_running = False
    server.server_thread = None  # No thread reference — e.g. garbage collected

    call_count = {"bind": 0, "running": 0}

    class _BindFailSocket:
        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):  # noqa: ARG002
            return False

        def settimeout(self, timeout):  # noqa: ARG002
            pass

        def connect_ex(self, address):  # noqa: ARG002
            return 0  # Port is accepting connections

        def bind(self, address):  # noqa: ARG002
            call_count["bind"] += 1
            raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr(oauth_callback_server.socket, "socket", _BindFailSocket)

    # is_actually_running will use our patched socket and see connect_ex=0
    success, error = server.start()

    assert success is True
    assert error == ""
    assert server.is_running is True


def test_stop_cleans_up_even_when_is_running_false():
    """stop() must still signal the server thread to exit even when is_running
    is False — that stale flag is exactly the scenario that causes the bug."""
    server = oauth_callback_server.MinimalOAuthServer(8000, "http://localhost")
    server.is_running = False

    class _MockUvicornServer:
        def __init__(self):
            self.should_exit = False

    mock_uvicorn = _MockUvicornServer()
    server.server = mock_uvicorn

    class _JoinableThread:
        def __init__(self):
            self.joined = False
            self._alive = True

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):  # noqa: ARG002
            self.joined = True
            self._alive = False

    mock_thread = _JoinableThread()
    server.server_thread = mock_thread

    server.stop()

    assert mock_uvicorn.should_exit is True
    assert mock_thread.joined is True
    assert server.is_running is False
