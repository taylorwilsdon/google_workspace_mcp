"""
Port resolver for late-binding the workspace-mcp OAuth callback server.

Probes a preferred port plus a small fallback range at process start;
the first available port is bound and surfaced through startup logs so
redirect URIs are composed from the actual bound port.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PREFERRED_PORT = 8000
DEFAULT_FALLBACK_COUNT = 4
RESOLVED_PORT_ENV = "WORKSPACE_MCP_RESOLVED_PORT"


class NoAvailablePortError(RuntimeError):
    """Raised when no port in the preferred + fallback range is free."""


class PortConfigError(RuntimeError):
    """Raised when a port-related env var contains a non-integer value."""


def _candidate_ports(preferred: int, fallback_count: int) -> List[int]:
    return [preferred] + [preferred + i for i in range(1, max(0, fallback_count) + 1)]


def _is_port_free(host: str, port: int) -> bool:
    """
    Test whether a TCP port is genuinely free for the OAuth callback server.

    Two-stage probe:
      1. CONNECT to 127.0.0.1:port -- detects an existing listener regardless of
         which interface they bound to (on macOS, SO_REUSEADDR lets a 0.0.0.0
         bind succeed even when 127.0.0.1 is already listening, so a bind probe
         alone gives a false-negative). Since the OAuth callback URI is always
         http://localhost:port, anything listening on loopback is a collision.
      2. BIND with SO_REUSEADDR=0 (default) on the requested host -- catches
         TIME_WAIT and BSD-style local conflicts.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return False
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
        return True
    except OSError:
        return False


def resolve_port(
    preferred: Optional[int] = None,
    fallback_count: Optional[int] = None,
    host: Optional[str] = None,
) -> int:
    """
    Resolve the first available port in [preferred, preferred+1, ..., preferred+fallback_count].

    Reads defaults from env when args are None:
      WORKSPACE_MCP_PORT (default 8000)                  -- preferred port
      WORKSPACE_MCP_PORT_FALLBACK_COUNT (default 4)      -- fallback slots
      WORKSPACE_MCP_HOST (default 0.0.0.0)               -- bind host

    Side effect: mutates os.environ["WORKSPACE_MCP_PORT"] to the resolved port,
    so every downstream reader (auth.oauth_config singleton on next reload,
    core.config.WORKSPACE_MCP_PORT after explicit refresh, attachment_storage)
    sees the bound port. Callers should call reload_oauth_config() after this
    function to pick up the new value in the OAuthConfig singleton.

    Returns the first-available port. Raises NoAvailablePortError if every
    candidate is in use, or PortConfigError if a port env var is invalid.
    """
    if preferred is None:
        raw = os.getenv(
            "PORT", os.getenv("WORKSPACE_MCP_PORT", str(DEFAULT_PREFERRED_PORT))
        )
        try:
            preferred = int(raw)
        except ValueError as exc:
            env_name = "PORT" if os.getenv("PORT") else "WORKSPACE_MCP_PORT"
            raise PortConfigError(
                f"{env_name} must be an integer, got {raw!r}"
            ) from exc
    if fallback_count is None:
        raw = os.getenv(
            "WORKSPACE_MCP_PORT_FALLBACK_COUNT", str(DEFAULT_FALLBACK_COUNT)
        )
        try:
            fallback_count = int(raw)
        except ValueError as exc:
            raise PortConfigError(
                f"WORKSPACE_MCP_PORT_FALLBACK_COUNT must be an integer, got {raw!r}"
            ) from exc
    if host is None:
        host = os.getenv("WORKSPACE_MCP_HOST", "0.0.0.0")

    candidates = _candidate_ports(preferred, fallback_count)
    for port in candidates:
        if _is_port_free(host, port):
            if port == preferred:
                logger.info("Port resolver: bound preferred port %d", port)
            else:
                logger.warning(
                    "Port resolver: preferred port %d unavailable; falling back to %d "
                    "(checked range %s)",
                    preferred,
                    port,
                    candidates,
                )
            os.environ["WORKSPACE_MCP_PORT"] = str(port)
            os.environ[RESOLVED_PORT_ENV] = "1"
            return port

    raise NoAvailablePortError(
        f"No available port in range {candidates}; all in use. "
        f"Set WORKSPACE_MCP_PORT to a different preferred port, or "
        f"increase WORKSPACE_MCP_PORT_FALLBACK_COUNT."
    )
