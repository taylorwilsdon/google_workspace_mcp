"""Tests for core.valkey_storage against a fake glide client."""

import asyncio
import logging

import pytest

pytest.importorskip("glide")

from glide.glide_client import BaseClient  # noqa: E402
from glide_shared.config import GlideClientConfiguration  # noqa: E402
from glide_shared.exceptions import (  # noqa: E402
    ClosingError,
    ConnectionError as GlideConnectionError,
    RequestError,
    TimeoutError as GlideTimeoutError,
)

import core.valkey_storage as valkey_storage  # noqa: E402
from core.valkey_storage import (  # noqa: E402
    ResilientValkeyStore,
    build_valkey_client_config,
    configure_glide_logging,
    describe_valkey_client_config,
)

PASSWORD = "hunter2-valkey-password"


class FakeGlideClient(BaseClient):
    """Dict-backed stand-in for the glide calls ValkeyStore makes."""

    def __init__(self, data, failures=()):
        self.data = data
        self.failures = list(failures)
        self.closed = False
        self.commands = 0

    async def _command(self):
        self.commands += 1
        if self.closed:
            raise ClosingError("client closed")
        if self.failures:
            failure = self.failures.pop(0)
            if callable(failure) and not isinstance(failure, BaseException):
                failure = await failure()
            raise failure

    async def get(self, key):
        await self._command()
        return self.data.get(key)

    async def mget(self, keys):
        await self._command()
        return [self.data.get(key) for key in keys]

    async def set(self, key, value, expiry=None):
        await self._command()
        self.data[key] = value.encode() if isinstance(value, str) else value
        return "OK"

    async def delete(self, keys):
        await self._command()
        return sum(self.data.pop(key, None) is not None for key in keys)

    async def close(self):
        self.closed = True


class ClientFactory:
    def __init__(self, *failure_plans, create_errors=()):
        self.data = {}
        self.failure_plans = list(failure_plans)
        self.create_errors = list(create_errors)
        self.created = []
        self.configs = []

    async def __call__(self, config):
        self.configs.append(config)
        if self.create_errors:
            raise self.create_errors.pop(0)
        failures = self.failure_plans.pop(0) if self.failure_plans else ()
        client = FakeGlideClient(self.data, failures)
        self.created.append(client)
        return client


def _store(monkeypatch, factory, env=None):
    monkeypatch.setattr(valkey_storage, "_create_client", factory)
    config = build_valkey_client_config(env or {})
    return ResilientValkeyStore(config=config)


@pytest.mark.asyncio
async def test_get_reconnects_once_after_closing_error(monkeypatch, caplog):
    factory = ClientFactory()
    store = _store(monkeypatch, factory)
    await store.put(key="k", value={"v": 1}, collection="c")

    with caplog.at_level(logging.WARNING, logger="core.valkey_storage"):
        factory.created[0].failures.append(ClosingError("connection closed by peer"))
        assert await store.get(key="k", collection="c") == {"v": 1}

    assert len(factory.created) == 2
    assert factory.created[0].closed
    assert store._connected_client is factory.created[1]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "connection lost" in m and "ClosingError" in m and "closed by peer" in m
        for m in warnings
    )


@pytest.mark.asyncio
async def test_error_after_reconnect_propagates_without_second_reconnect(monkeypatch):
    factory = ClientFactory(
        [ClosingError("first")],
        [ClosingError("second")],
    )
    store = _store(monkeypatch, factory)

    with pytest.raises(ClosingError, match="second"):
        await store.get(key="k", collection="c")

    assert len(factory.created) == 2


@pytest.mark.asyncio
async def test_concurrent_failures_share_one_reconnect(monkeypatch):
    release = asyncio.Event()

    async def fail_after_release():
        await release.wait()
        return ClosingError("connection reset")

    factory = ClientFactory([fail_after_release] * 10)
    store = _store(monkeypatch, factory)
    await store.setup()
    first_client = factory.created[0]

    tasks = [
        asyncio.create_task(store.get(key=f"k{i}", collection="c")) for i in range(10)
    ]
    while first_client.commands < 10:
        await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    assert results == [None] * 10
    assert len(factory.created) == 2
    assert first_client.closed
    assert factory.created[1].commands == 10


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [GlideTimeoutError("timed out"), RequestError("WRONGTYPE")],
    ids=["timeout", "request-error"],
)
async def test_ambiguous_errors_are_not_retried(monkeypatch, error):
    factory = ClientFactory([error])
    store = _store(monkeypatch, factory)

    with pytest.raises(type(error)):
        await store.put(key="k", value={"v": 1}, collection="c")

    assert len(factory.created) == 1
    assert factory.created[0].commands == 1


@pytest.mark.asyncio
async def test_connection_error_is_retried(monkeypatch):
    factory = ClientFactory([GlideConnectionError("connection refused")])
    store = _store(monkeypatch, factory)

    await store.put(key="k", value={"v": 1}, collection="c")

    assert len(factory.created) == 2
    assert await store.get(key="k", collection="c") == {"v": 1}


@pytest.mark.asyncio
async def test_all_primitives_reconnect(monkeypatch):
    factory = ClientFactory()
    store = _store(monkeypatch, factory)
    await store.put_many(keys=["a", "b"], values=[{"v": 1}, {"v": 2}], collection="c")

    calls = [
        lambda: store.get(key="a", collection="c"),
        lambda: store.get_many(keys=["a", "b"], collection="c"),
        lambda: store.put(key="d", value={"v": 4}, collection="c"),
        lambda: store.delete(key="d", collection="c"),
        lambda: store.delete_many(keys=["a"], collection="c"),
    ]
    for call in calls:
        store._connected_client.failures.append(ClosingError("gone"))
        await call()

    assert len(factory.created) == 1 + len(calls)
    assert await store.get(key="b", collection="c") == {"v": 2}
    assert await store.get(key="a", collection="c") is None


@pytest.mark.asyncio
async def test_failed_reconnect_is_retried_on_next_call(monkeypatch):
    factory = ClientFactory([ClosingError("gone")])
    store = _store(monkeypatch, factory)
    await store.setup()
    factory.create_errors.append(ClosingError("Valkey still down"))

    with pytest.raises(ClosingError, match="still down"):
        await store.get(key="k", collection="c")
    assert store._connected_client is None

    assert await store.get(key="k", collection="c") is None
    assert len(factory.created) == 2


@pytest.mark.asyncio
async def test_close_closes_the_current_client(monkeypatch):
    factory = ClientFactory([ClosingError("gone")])
    store = _store(monkeypatch, factory)
    await store.get(key="k", collection="c")

    await store.close()

    assert all(client.closed for client in factory.created)
    assert store._connected_client is None


@pytest.mark.asyncio
async def test_reconnect_warning_redacts_credentials(monkeypatch, caplog):
    factory = ClientFactory(
        [ClosingError(f"AUTH failed for password {PASSWORD}")],
    )
    store = _store(
        monkeypatch,
        factory,
        env={
            "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PASSWORD": PASSWORD,
            "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USERNAME": "svc-user",
        },
    )
    await store.setup()
    factory.create_errors.append(ClosingError(f"svc-user {PASSWORD} rejected"))

    with caplog.at_level(logging.DEBUG, logger="core.valkey_storage"):
        with pytest.raises(ClosingError):
            await store.get(key="k", collection="c")

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert "connection lost" in log_text and "reconnect failed" in log_text
    assert "<redacted>" in log_text
    assert PASSWORD not in log_text
    assert "svc-user" not in log_text


P = "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_"


def test_config_loopback_keeps_glide_defaults():
    config = build_valkey_client_config({})

    assert isinstance(config, GlideClientConfiguration)
    assert (config.addresses[0].host, config.addresses[0].port) == ("localhost", 6379)
    assert config.use_tls is False
    assert config.request_timeout is None
    assert config.advanced_config.connection_timeout is None
    assert config.credentials is None
    assert config.database_id == 0


def test_config_remote_plain_gets_generous_timeouts():
    config = build_valkey_client_config(
        {P + "HOST": "cache.internal", P + "PORT": "6379", P + "DB": "2"}
    )

    assert config.use_tls is False
    assert config.request_timeout == 5000
    assert config.advanced_config.connection_timeout == 10000
    assert config.database_id == 2


def test_config_tls_defaults_on_for_port_6380():
    config = build_valkey_client_config(
        {
            P + "HOST": "cache.example.com",
            P + "PORT": "6380",
            P + "USERNAME": "svc",
            P + "PASSWORD": PASSWORD,
            P + "REQUEST_TIMEOUT_MS": "750",
        }
    )

    assert config.use_tls is True
    assert config.request_timeout == 750
    assert config.advanced_config.connection_timeout == 10000
    assert config.credentials.username == "svc"
    assert config.credentials.password == PASSWORD


def test_config_tls_can_be_disabled_on_6380():
    config = build_valkey_client_config({P + "PORT": "6380", P + "USE_TLS": "false"})
    assert config.use_tls is False


def test_config_tls_on_loopback_gets_generous_timeouts():
    config = build_valkey_client_config({P + "USE_TLS": "true"})
    assert config.request_timeout == 5000


@pytest.mark.parametrize(
    "name, value, message",
    [
        ("REQUEST_TIMEOUT_MS", "5s", "REQUEST_TIMEOUT_MS must be an integer, got '5s'"),
        ("CONNECTION_TIMEOUT_MS", "0", "CONNECTION_TIMEOUT_MS must be >= 1, got 0"),
        ("PORT", "abc", "PORT must be an integer"),
    ],
)
def test_config_rejects_invalid_integers(name, value, message):
    with pytest.raises(ValueError, match=message):
        build_valkey_client_config({P + name: value})


def test_describe_config_omits_credentials():
    config = build_valkey_client_config(
        {
            P + "HOST": "cache.example.com",
            P + "USERNAME": "svc",
            P + "PASSWORD": PASSWORD,
        }
    )

    description = describe_valkey_client_config(config)

    assert "host=cache.example.com" in description
    assert "request_timeout_ms=5000" in description
    assert PASSWORD not in description
    assert "svc" not in description


@pytest.mark.parametrize(
    "python_level, expected",
    [
        (logging.DEBUG, "DEBUG"),
        (logging.INFO, "WARN"),
        (logging.WARNING, "WARN"),
        (logging.ERROR, "ERROR"),
        (logging.CRITICAL, "ERROR"),
    ],
)
def test_glide_log_level_follows_python_logging(monkeypatch, python_level, expected):
    levels = []
    monkeypatch.setattr(
        valkey_storage.GlideLogger,
        "set_logger_config",
        lambda level: levels.append(level),
    )
    monkeypatch.setattr(valkey_storage.logger, "level", python_level)

    configure_glide_logging()

    assert levels == [getattr(valkey_storage.LogLevel, expected)]
