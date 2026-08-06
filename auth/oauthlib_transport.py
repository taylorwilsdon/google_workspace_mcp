"""Scoped helpers for oauthlib insecure-transport (localhost only)."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_ENV_KEY = "OAUTHLIB_INSECURE_TRANSPORT"


def _is_loopback_redirect(redirect_uri: str) -> bool:
    try:
        host = (urlparse(redirect_uri).hostname or "").lower()
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


@contextmanager
def oauthlib_insecure_transport_scope(redirect_uri: str) -> Iterator[None]:
    """Temporarily allow HTTP OAuth redirects for loopback only.

    Does not permanently mutate process-wide env. If the operator already set
    ``OAUTHLIB_INSECURE_TRANSPORT``, that value is left unchanged. For non-loopback
    redirects, insecure transport is never enabled here.
    """
    if not _is_loopback_redirect(redirect_uri):
        yield
        return

    already_set = _ENV_KEY in os.environ
    if already_set:
        yield
        return

    os.environ[_ENV_KEY] = "1"
    logger.debug(
        "Temporarily enabling %s for loopback OAuth redirect", _ENV_KEY
    )
    try:
        yield
    finally:
        os.environ.pop(_ENV_KEY, None)
