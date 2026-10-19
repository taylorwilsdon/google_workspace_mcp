"""Valkey-backed storage for the FastMCP OAuth proxy (requires the ``valkey`` extra)."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, TypeVar

from glide import Logger as GlideLogger, LogLevel
from glide.glide_client import GlideClient
from glide_shared.config import (
    AdvancedGlideClientConfiguration,
    GlideClientConfiguration,
    NodeAddress,
    ServerCredentials,
)
from glide_shared.exceptions import (
    ClosingError,
    ConnectionError as GlideConnectionError,
)
from key_value.aio.stores.valkey import ValkeyStore

if TYPE_CHECKING:
    from key_value.aio._utils.managed_entry import ManagedEntry

logger = logging.getLogger(__name__)

T = TypeVar("T")

ENV_PREFIX = "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_"

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
_TLS_DEFAULT_PORT = 6380

DEFAULT_REMOTE_REQUEST_TIMEOUT_MS = 5000
DEFAULT_REMOTE_CONNECTION_TIMEOUT_MS = 10000

_RECOVERABLE_ERRORS = (ClosingError, GlideConnectionError)


def _env_str(env: Mapping[str, str], name: str) -> str:
    return env.get(ENV_PREFIX + name, "").strip()


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _env_str(env, name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_int(
    env: Mapping[str, str], name: str, default: int | None, *, minimum: int = 0
) -> int | None:
    raw = _env_str(env, name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"{ENV_PREFIX}{name} must be an integer, got {raw!r}"
        ) from None
    if value < minimum:
        raise ValueError(f"{ENV_PREFIX}{name} must be >= {minimum}, got {value}")
    return value


def build_valkey_client_config(
    env: Mapping[str, str] | None = None,
) -> GlideClientConfiguration:
    """Build the glide client configuration from ``WORKSPACE_MCP_OAUTH_PROXY_VALKEY_*``.

    Raises:
        ValueError: if a numeric setting is not a valid integer.
    """
    if env is None:
        env = os.environ

    host = _env_str(env, "HOST") or "localhost"
    port = _env_int(env, "PORT", 6379, minimum=1)
    use_tls = _env_bool(env, "USE_TLS", default=port == _TLS_DEFAULT_PORT)

    needs_generous_timeouts = use_tls or host not in _LOOPBACK_HOSTS
    request_timeout_ms = _env_int(
        env,
        "REQUEST_TIMEOUT_MS",
        DEFAULT_REMOTE_REQUEST_TIMEOUT_MS if needs_generous_timeouts else None,
        minimum=1,
    )
    connection_timeout_ms = _env_int(
        env,
        "CONNECTION_TIMEOUT_MS",
        DEFAULT_REMOTE_CONNECTION_TIMEOUT_MS if needs_generous_timeouts else None,
        minimum=1,
    )

    username = _env_str(env, "USERNAME") or None
    password = _env_str(env, "PASSWORD") or None
    credentials = (
        ServerCredentials(password=password, username=username) if password else None
    )

    return GlideClientConfiguration(
        addresses=[NodeAddress(host=host, port=port)],
        use_tls=use_tls,
        credentials=credentials,
        database_id=_env_int(env, "DB", 0),
        request_timeout=request_timeout_ms,
        advanced_config=AdvancedGlideClientConfiguration(
            connection_timeout=connection_timeout_ms
        ),
    )


def describe_valkey_client_config(config: GlideClientConfiguration) -> str:
    """Summarize a client configuration for logs, without credentials."""
    address = config.addresses[0]
    return (
        f"host={address.host}, port={address.port}, db={config.database_id}, "
        f"tls={config.use_tls}, "
        f"request_timeout_ms={config.request_timeout or 'glide default'}, "
        f"connection_timeout_ms="
        f"{config.advanced_config.connection_timeout or 'glide default'}"
    )


def configure_glide_logging() -> None:
    """Set glide's log level from this module's effective Python log level."""
    level = logger.getEffectiveLevel()
    if level <= logging.DEBUG:
        glide_level = LogLevel.DEBUG
    elif level <= logging.WARNING:
        glide_level = LogLevel.WARN
    else:
        glide_level = LogLevel.ERROR
    GlideLogger.set_logger_config(glide_level)


async def _create_client(config: GlideClientConfiguration) -> GlideClient:
    return await GlideClient.create(config)


class ResilientValkeyStore(ValkeyStore):
    """``ValkeyStore`` that replaces a closed or disconnected glide client and retries once."""

    def __init__(
        self, *, config: GlideClientConfiguration, default_collection: str | None = None
    ) -> None:
        super().__init__(default_collection=default_collection)
        self._client_config = config
        self._reconnect_lock = asyncio.Lock()
        credentials = config.credentials
        self._secrets = (
            tuple(s for s in (credentials.password, credentials.username) if s)
            if credentials
            else ()
        )

    async def _setup(self) -> None:
        self._connected_client = await _create_client(self._client_config)
        self._exit_stack.push_async_callback(self._close_current_client)

    async def _close_current_client(self) -> None:
        client, self._connected_client = self._connected_client, None
        if client is not None:
            await client.close()

    def _redact(self, message: str) -> str:
        for secret in self._secrets:
            message = message.replace(secret, "<redacted>")
        return message

    async def _reconnect(
        self, failed_client: GlideClient | None, reason: BaseException | None
    ) -> None:
        async with self._reconnect_lock:
            if self._connected_client is not failed_client:
                return

            if reason is None:
                logger.warning(
                    "Valkey OAuth storage has no connected client (previous "
                    "reconnect failed); reconnecting"
                )
            else:
                logger.warning(
                    "Valkey OAuth storage connection lost (%s: %s); reconnecting",
                    type(reason).__name__,
                    self._redact(str(reason)),
                )

            self._connected_client = None
            if failed_client is not None:
                try:
                    await failed_client.close()
                except Exception:
                    logger.debug(
                        "Ignoring error closing failed Valkey client", exc_info=True
                    )

            try:
                self._connected_client = await _create_client(self._client_config)
            except Exception as exc:
                logger.warning(
                    "Valkey OAuth storage reconnect failed (%s: %s)",
                    type(exc).__name__,
                    self._redact(str(exc)),
                )
                raise
            logger.info("Valkey OAuth storage reconnected")

    async def _with_reconnect(self, operation: Callable[[], Awaitable[T]]) -> T:
        client = self._connected_client
        if client is None:
            await self._reconnect(failed_client=None, reason=None)
            client = self._connected_client
        try:
            return await operation()
        except _RECOVERABLE_ERRORS as exc:
            await self._reconnect(failed_client=client, reason=exc)
        return await operation()

    async def _get_managed_entry(
        self, *, key: str, collection: str
    ) -> ManagedEntry | None:
        return await self._with_reconnect(
            functools.partial(
                super()._get_managed_entry, key=key, collection=collection
            )
        )

    async def _get_managed_entries(
        self, *, collection: str, keys: Sequence[str]
    ) -> list[ManagedEntry | None]:
        return await self._with_reconnect(
            functools.partial(
                super()._get_managed_entries, collection=collection, keys=keys
            )
        )

    async def _put_managed_entry(
        self, *, key: str, collection: str, managed_entry: ManagedEntry
    ) -> None:
        return await self._with_reconnect(
            functools.partial(
                super()._put_managed_entry,
                key=key,
                collection=collection,
                managed_entry=managed_entry,
            )
        )

    async def _delete_managed_entry(self, *, key: str, collection: str) -> bool:
        return await self._with_reconnect(
            functools.partial(
                super()._delete_managed_entry, key=key, collection=collection
            )
        )

    async def _delete_managed_entries(
        self, *, keys: Sequence[str], collection: str
    ) -> int:
        return await self._with_reconnect(
            functools.partial(
                super()._delete_managed_entries, keys=keys, collection=collection
            )
        )
