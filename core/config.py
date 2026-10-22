"""
Shared configuration for Google Workspace MCP server.
This module holds configuration values that need to be shared across modules
to avoid circular imports.

NOTE: OAuth configuration has been moved to auth.oauth_config for centralization.
This module now imports from there for backward compatibility.
"""

import os
from typing import TYPE_CHECKING

from auth.oauth_config import (
    get_oauth_base_url,
    get_oauth_redirect_uri,
    set_transport_mode,
    get_transport_mode,
    is_oauth21_enabled,
)

# Server configuration. WORKSPACE_MCP_PORT is resolved lazily via PEP 562
# __getattr__ so that the value reflects the current env at access time.
# main.py mutates WORKSPACE_MCP_PORT in os.environ at startup via the port
# resolver (auth.port_resolver.resolve_port); consumers that do
# `from core.config import WORKSPACE_MCP_PORT` inside a function will see the
# late-bound port instead of a frozen-at-module-import 8000.
WORKSPACE_MCP_BASE_URI = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
WORKSPACE_EXTERNAL_URL = os.getenv("WORKSPACE_EXTERNAL_URL")

if TYPE_CHECKING:
    WORKSPACE_MCP_PORT: int


def is_extended_name_lookup_enabled() -> bool:
    """Whether Gmail compose may resolve names from Other contacts and the directory.

    Reads GMAIL_EXTENDED_NAME_LOOKUP (``1``/``true``/``yes``/``on``, case-insensitive).
    Off by default: the contacts.other.readonly and directory.readonly scopes are
    then never requested, and recipient names come only from the conversation and
    saved contacts.
    """
    return os.getenv("GMAIL_EXTENDED_NAME_LOOKUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def __getattr__(name: str) -> int:
    if name == "WORKSPACE_MCP_PORT":
        if os.getenv("WORKSPACE_MCP_RESOLVED_PORT") == "1":
            return int(os.getenv("WORKSPACE_MCP_PORT", os.getenv("PORT", "8000")))
        return int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", "8000")))
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Disable USER_GOOGLE_EMAIL in OAuth 2.1 multi-user mode
USER_GOOGLE_EMAIL = (
    None if is_oauth21_enabled() else os.getenv("USER_GOOGLE_EMAIL", None)
)

# Re-export OAuth functions for backward compatibility
__all__ = [
    "WORKSPACE_MCP_PORT",
    "WORKSPACE_MCP_BASE_URI",
    "WORKSPACE_EXTERNAL_URL",
    "USER_GOOGLE_EMAIL",
    "is_extended_name_lookup_enabled",
    "get_oauth_base_url",
    "get_oauth_redirect_uri",
    "set_transport_mode",
    "get_transport_mode",
]
