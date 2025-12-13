import argparse
import logging
import os
import socket
import sys
from importlib import metadata, import_module
from dotenv import load_dotenv

from auth.oauth_config import reload_oauth_config, is_stateless_mode
from core.log_formatter import EnhancedLogFormatter, configure_file_logging
from core.utils import check_credentials_directory_permissions
from core.server import server, set_transport_mode, configure_server_for_http
from core.tool_tier_loader import resolve_tools_from_tier
from core.tool_registry import (
    set_enabled_tools as set_enabled_tool_names,
    wrap_server_tool_method,
    filter_server_tools,
)

dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=dotenv_path)

# Suppress googleapiclient discovery cache warning
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

reload_oauth_config()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

configure_file_logging()


def safe_print(text):
    # Don't print to stderr when running as MCP server via uvx to avoid JSON parsing errors
    # Check if we're running as MCP server (no TTY and uvx in process name)
    if not sys.stderr.isatty():
        # Running as MCP server, suppress output to avoid JSON parsing errors
        logger.debug(f"[MCP Server] {text}")
        return

    try:
        print(text, file=sys.stderr)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode(), file=sys.stderr)


def configure_safe_logging():
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


def main():
    """
    Main entry point for the Google Workspace MCP server.
    Uses FastMCP's native streamable-http transport.
    """
    # Configure safe logging for Windows Unicode handling
    configure_safe_logging()

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Google Workspace MCP Server")
    parser.add_argument(
        "--single-user",
        action="store_true",
        help="Run in single-user mode - bypass session mapping and use any credentials from the credentials directory",
    )
    parser.add_argument(
        "--tools",
        nargs="*",
        choices=[
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
        ],
        help="Specify which tools to register. If not provided, all tools are registered.",
    )
    parser.add_argument(
        "--tool-tier",
        choices=["core", "extended", "complete"],
        help="Load tools based on tier level. Can be combined with --tools to filter services.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport mode: stdio (default) or streamable-http",
    )
    args = parser.parse_args()

    # Set port and base URI once for reuse throughout the function
    port = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", 8000)))
    base_uri = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
    external_url = os.getenv("WORKSPACE_EXTERNAL_URL")
    display_url = external_url if external_url else f"{base_uri}:{port}"

    safe_print("🔧 Google Workspace MCP Server")
    safe_print("=" * 35)
    safe_print("📋 Server Information:")
    try:
        version = metadata.version("workspace-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"
    safe_print(f"   📦 Version: {version}")
    safe_print(f"   🌐 Transport: {args.transport}")
    if args.transport == "streamable-http":
        safe_print(f"   🔗 URL: {display_url}")
        safe_print(f"   🔐 OAuth Callback: {display_url}/oauth2callback")
    safe_print(f"   👤 Mode: {'Single-user' if args.single_user else 'Multi-user'}")
    safe_print(f"   🐍 Python: {sys.version.split()[0]}")
    safe_print("")

    # Active Configuration
    safe_print("⚙️ Active Configuration:")

    # Redact client secret for security
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "Not Set")
    redacted_secret = (
        f"{client_secret[:4]}...{client_secret[-4:]}"
        if len(client_secret) > 8
        else "Invalid or too short"
    )

    config_vars = {
        "GOOGLE_OAUTH_CLIENT_ID": os.getenv("GOOGLE_OAUTH_CLIENT_ID", "Not Set"),
        "GOOGLE_OAUTH_CLIENT_SECRET": redacted_secret,
        "USER_GOOGLE_EMAIL": os.getenv("USER_GOOGLE_EMAIL", "Not Set"),
        "MCP_SINGLE_USER_MODE": os.getenv("MCP_SINGLE_USER_MODE", "false"),
        "MCP_ENABLE_OAUTH21": os.getenv("MCP_ENABLE_OAUTH21", "false"),
        "WORKSPACE_MCP_STATELESS_MODE": os.getenv(
            "WORKSPACE_MCP_STATELESS_MODE", "false"
        ),
        "OAUTHLIB_INSECURE_TRANSPORT": os.getenv(
            "OAUTHLIB_INSECURE_TRANSPORT", "false"
        ),
        "GOOGLE_CLIENT_SECRET_PATH": os.getenv("GOOGLE_CLIENT_SECRET_PATH", "Not Set"),
    }

    for key, value in config_vars.items():
        safe_print(f"   - {key}: {value}")
    safe_print("")

    # Import tool modules to register them with the MCP server via decorators
    tool_imports = {
        "gmail": lambda: import_module("gmail.gmail_tools"),
        "drive": lambda: import_module("gdrive.drive_tools"),
        "calendar": lambda: import_module("gcalendar.calendar_tools"),
        "docs": lambda: import_module("gdocs.docs_tools"),
        "sheets": lambda: import_module("gsheets.sheets_tools"),
        "chat": lambda: import_module("gchat.chat_tools"),
        "forms": lambda: import_module("gforms.forms_tools"),
        "slides": lambda: import_module("gslides.slides_tools"),
        "tasks": lambda: import_module("gtasks.tasks_tools"),
        "search": lambda: import_module("gsearch.search_tools"),
    }

    tool_icons = {
        "gmail": "📧",
        "drive": "📁",
        "calendar": "📅",
        "docs": "📄",
        "sheets": "📊",
        "chat": "💬",
        "forms": "📝",
        "slides": "🖼️",
        "tasks": "✓",
        "search": "🔍",
    }

    # Determine which tools to import based on arguments
    if args.tool_tier is not None:
        # Use tier-based tool selection, optionally filtered by services
        try:
            tier_tools, suggested_services = resolve_tools_from_tier(
                args.tool_tier, args.tools
            )

            # If --tools specified, use those services; otherwise use all services that have tier tools
            if args.tools is not None:
                tools_to_import = args.tools
            else:
                tools_to_import = suggested_services

            # Set the specific tools that should be registered
            set_enabled_tool_names(set(tier_tools))
        except Exception as e:
            safe_print(f"❌ Error loading tools for tier '{args.tool_tier}': {e}")
            sys.exit(1)
    elif args.tools is not None:
        # Use explicit tool list without tier filtering
        tools_to_import = args.tools
        # Don't filter individual tools when using explicit service list only
        set_enabled_tool_names(None)
    else:
        # Default: import all tools
        tools_to_import = tool_imports.keys()
        # Don't filter individual tools when importing all
        set_enabled_tool_names(None)

    wrap_server_tool_method(server)

    from auth.scopes import set_enabled_tools

    set_enabled_tools(list(tools_to_import))

    safe_print(
        f"🛠️  Loading {len(tools_to_import)} tool module{'s' if len(tools_to_import) != 1 else ''}:"
    )
    for tool in tools_to_import:
        try:
            tool_imports[tool]()
            safe_print(
                f"   {tool_icons[tool]} {tool.title()} - Google {tool.title()} API integration"
            )
        except ModuleNotFoundError as exc:
            logger.error("Failed to import tool '%s': %s", tool, exc, exc_info=True)
            safe_print(f"   ⚠️ Failed to load {tool.title()} tool module ({exc}).")
    safe_print("")

    # Filter tools based on tier configuration (if tier-based loading is enabled)
    filter_server_tools(server)

    safe_print("📊 Configuration Summary:")
    safe_print(f"   🔧 Services Loaded: {len(tools_to_import)}/{len(tool_imports)}")
    if args.tool_tier is not None:
        if args.tools is not None:
            safe_print(
                f"   📊 Tool Tier: {args.tool_tier} (filtered to {', '.join(args.tools)})"
            )
        else:
            safe_print(f"   📊 Tool Tier: {args.tool_tier}")
    safe_print(f"   📝 Log Level: {logging.getLogger().getEffectiveLevel()}")
    safe_print("")

    # Set global single-user mode flag
    if args.single_user:
        if is_stateless_mode():
            safe_print("❌ Single-user mode is incompatible with stateless mode")
            safe_print("   Stateless mode requires OAuth 2.1 which is multi-user")
            sys.exit(1)
        os.environ["MCP_SINGLE_USER_MODE"] = "1"
        safe_print("🔐 Single-user mode enabled")
        safe_print("")

    # Check credentials directory permissions before starting (skip in stateless mode)
    if not is_stateless_mode():
        try:
            safe_print("🔍 Checking credentials directory permissions...")
            check_credentials_directory_permissions()
            safe_print("✅ Credentials directory permissions verified")
            safe_print("")
        except (PermissionError, OSError) as e:
            safe_print(f"❌ Credentials directory permission check failed: {e}")
            safe_print(
                "   Please ensure the service has write permissions to create/access the credentials directory"
            )
            logger.error(f"Failed credentials directory permission check: {e}")
            sys.exit(1)
    else:
        safe_print("🔍 Skipping credentials directory check (stateless mode)")
        safe_print("")

    try:
        # Set transport mode for OAuth callback handling
        set_transport_mode(args.transport)

        # Configure auth initialization for FastMCP lifecycle events
        if args.transport == "streamable-http":
            configure_server_for_http()
            safe_print("")
            safe_print(f"🚀 Starting HTTP server on {base_uri}:{port}")
            if external_url:
                safe_print(f"   External URL: {external_url}")
        else:
            safe_print("")
            safe_print("🚀 Starting STDIO server")
            # Start minimal OAuth callback server for stdio mode
            from auth.oauth_callback_server import ensure_oauth_callback_available

            success, error_msg = ensure_oauth_callback_available(
                "stdio", port, base_uri
            )
            if success:
                safe_print(
                    f"   OAuth callback server started on {display_url}/oauth2callback"
                )
            else:
                warning_msg = "   ⚠️  Warning: Failed to start OAuth callback server"
                if error_msg:
                    warning_msg += f": {error_msg}"
                safe_print(warning_msg)

        safe_print("✅ Ready for MCP connections")
        safe_print("")

        if args.transport == "streamable-http":
            # Check port availability before starting HTTP server
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("", port))
            except OSError as e:
                safe_print(f"Socket error: {e}")
                safe_print(
                    f"❌ Port {port} is already in use. Cannot start HTTP server."
                )
                sys.exit(1)

            server.run(transport="streamable-http", host="0.0.0.0", port=port)
        else:
            server.run()
    except KeyboardInterrupt:
        safe_print("\n👋 Server shutdown requested")
        # Clean up OAuth callback server if running
        from auth.oauth_callback_server import cleanup_oauth_callback_server

        cleanup_oauth_callback_server()
        sys.exit(0)
    except Exception as e:
        safe_print(f"\n❌ Server error: {e}")
        logger.error(f"Unexpected error running server: {e}", exc_info=True)
        # Clean up OAuth callback server if running
        from auth.oauth_callback_server import cleanup_oauth_callback_server

        cleanup_oauth_callback_server()
        sys.exit(1)


if __name__ == "__main__":
    main()
