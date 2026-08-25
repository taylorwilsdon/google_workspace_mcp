"""Scoped helpers for oauthlib insecure-transport (localhost only)."""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ENV_KEY = "OAUTHLIB_INSECURE_TRANSPORT"
_lock = threading.Lock()
_active_scopes = 0
_saved_env_value: Optional[str] = None
_had_preexisting = False


def _is_loopback_redirect(redirect_uri: str) -> bool:
    try:
        host = (urlparse(redirect_uri).hostname or "").lower()
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


@contextmanager
def oauthlib_insecure_transport_scope(redirect_uri: str) -> Iterator[None]:
    """Temporarily allow HTTP OAuth redirects for loopback only.

    Overlapping scopes are serialized with a lock and reference-counted so the
    env var is set once on first enter and restored (or removed) only when the
    last scope exits. Pre-existing operator values are preserved.
    """
    if not _is_loopback_redirect(redirect_uri):
        yield
        return

    global _active_scopes, _saved_env_value, _had_preexisting

    with _lock:
        if _active_scopes == 0:
            _had_preexisting = _ENV_KEY in os.environ
            _saved_env_value = os.environ.get(_ENV_KEY) if _had_preexisting else None
            if not _had_preexisting:
                os.environ[_ENV_KEY] = "1"
                logger.debug(
                    "Temporarily enabling %s for loopback OAuth redirect", _ENV_KEY
                )
        _active_scopes += 1

    try:
        yield
    finally:
        with _lock:
            _active_scopes -= 1
            if _active_scopes == 0:
                if _had_preexisting:
                    if _saved_env_value is not None:
                        os.environ[_ENV_KEY] = _saved_env_value
                else:
                    os.environ.pop(_ENV_KEY, None)
                _saved_env_value = None
                _had_preexisting = False
