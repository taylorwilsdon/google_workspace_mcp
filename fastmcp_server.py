# ruff: noqa
"""
FastMCP Cloud entrypoint for the Google Workspace MCP server.
Enforces OAuth 2.1 + stateless defaults required by FastMCP-hosted deployments.
"""

import logging
import os
import sys
from dotenv import load_dotenv

from auth.oauth_config import reload_oauth_config, is_stateless_mode
from core.log_formatter import EnhancedLogFormatter, configure_file_logging
from core.utils import check_credentials_directory_permissions
from core.server import server, set_transport_mode, configure_server_for_http
from core.tool_registry import (
    set_enabled_tools as set_enabled_tool_names,
    wrap_server_tool_method,
    filter_server_tools,
)
from auth.scopes import set_enabled_tools


def enforce_fastmcp_cloud_defaults():
    """Force FastMCP Cloud-compatible OAuth settings before initializing the server."""
    enforced = []

    required = {
        "MCP_ENABLE_OAUTH21": "true",
        "WORKSPACE_MCP_STATELESS_MODE": "true",
    }
    defaults = {
        "MCP_SINGLE_USER_MODE": "false",
    }

    for key, target in required.items():
        current = os.environ.get(key)
        normalized = (current or "").lower()
        if normalized != target:
            os.environ[key] = target
            enforced.append((key, current, target))

    for key, target in defaults.items():
        current = os.environ.get(key)
        if current != target:
            os.environ[key] = target
            enforced.append((key, current, target))

    return enforced


# Load environment variables
dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=dotenv_path)

_fastmcp_cloud_overrides = enforce_fastmcp_cloud_defaults()

# Suppress googleapiclient discovery cache warning
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

# Reload OAuth configuration after env vars loaded
reload_oauth_config()

# Configure basic logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

if _fastmcp_cloud_overrides:
    for key, previous, new_value in _fastmcp_cloud_overrides:
        if previous is None:
            logger.info("FastMCP Cloud: set %s=%s", key, new_value)
        else:
            logger.warning(
                "FastMCP Cloud: overriding %s from %s to %s", key, previous, new_value
            )
else:
    logger.info("FastMCP Cloud: OAuth 2.1 stateless defaults already satisfied")

# Configure file logging based on stateless mode
configure_file_logging()


def configure_safe_logging():
    """Configure safe Unicode handling for logging."""

    class SafeEnhancedFormatter(EnhancedLogFormatter):
        """Enhanced ASCII formatter with additional Windows safety."""

        def format(self, record):
            try:
                return super().format(record)
            except UnicodeEncodeError:
                # Fallback to ASCII-safe formatting
                service_prefix = self._get_ascii_prefix(record.name, record.levelname)
                safe_msg = (
                    str(record.getMessage())
                    .encode("ascii", errors="replace")
                    .decode("ascii")
                )
                return f"{service_prefix} {safe_msg}"

    # Replace all console handlers' formatters with safe enhanced ones
    for handler in logging.root.handlers:
        # Only apply to console/stream handlers, keep file handlers as-is
        if isinstance(handler, logging.StreamHandler) and handler.stream.name in [
            "<stderr>",
            "<stdout>",
        ]:
            safe_formatter = SafeEnhancedFormatter(use_colors=True)
            handler.setFormatter(safe_formatter)


# Configure safe logging
configure_safe_logging()

# Check credentials directory permissions (skip in stateless mode)
if not is_stateless_mode():
    try:
        logger.info("Checking credentials directory permissions...")
        check_credentials_directory_permissions()
        logger.info("Credentials directory permissions verified")
    except (PermissionError, OSError) as e:
        logger.error(f"Credentials directory permission check failed: {e}")
        logger.error(
            "   Please ensure the service has write permissions to create/access the credentials directory"
        )
        sys.exit(1)
else:
    logger.info("🔍 Skipping credentials directory check (stateless mode)")

# Set transport mode for HTTP (FastMCP CLI defaults to streamable-http)
set_transport_mode("streamable-http")

# Import all tool modules to register their @server.tool() decorators
import gmail.gmail_tools
import gdrive.drive_tools
import gcalendar.calendar_tools
import gdocs.docs_tools
import gsheets.sheets_tools
import gchat.chat_tools
import gforms.forms_tools
import gslides.slides_tools
import gtasks.tasks_tools
import gsearch.search_tools

# Configure tool registration
wrap_server_tool_method(server)

# Enable all tools and services by default
all_services = [
    "gmail",
    "drive",
    "calendar",
    "docs",
    "sheets",
    "chat",
    "forms",
    "slides",
    "tasks",
    "search",
]
set_enabled_tools(all_services)  # Set enabled services for scopes
set_enabled_tool_names(None)  # Don't filter individual tools - enable all

# Filter tools based on configuration
filter_server_tools(server)

# Configure authentication after scopes are known
configure_server_for_http()

# Export server instance for FastMCP CLI (looks for 'mcp', 'server', or 'app')
mcp = server
app = server
