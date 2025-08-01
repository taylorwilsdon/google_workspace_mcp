import argparse
import logging
import os
import sys
from importlib import metadata

# Local imports
from core.server import server, set_transport_mode
from core.utils import check_credentials_directory_permissions

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

try:
    root_logger = logging.getLogger()
    log_file_dir = os.path.dirname(os.path.abspath(__file__))
    log_file_path = os.path.join(log_file_dir, 'mcp_server_debug.log')

    file_handler = logging.FileHandler(log_file_path, mode='a')
    file_handler.setLevel(logging.DEBUG)

    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(process)d - %(threadName)s '
        '[%(module)s.%(funcName)s:%(lineno)d] - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)

    logger.debug(f"Detailed file logging configured to: {log_file_path}")
except Exception as e:
    sys.stderr.write(f"CRITICAL: Failed to set up file logging to '{log_file_path}': {e}\n")

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
        print(text.encode('ascii', errors='replace').decode(), file=sys.stderr)

def main():
    """
    Main entry point for the Google Workspace MCP server.
    Uses FastMCP's native streamable-http transport.
    """
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Google Workspace MCP Server')
    parser.add_argument('--single-user', action='store_true',
                        help='Run in single-user mode - bypass session mapping and use any credentials from the credentials directory')
    parser.add_argument('--tools', nargs='*',
                        choices=['gmail', 'drive', 'calendar', 'docs', 'sheets', 'chat', 'forms', 'slides', 'tasks', 'search'],
                        help='Specify which tools to register. If not provided, all tools are registered.')
    parser.add_argument('--transport', choices=['stdio', 'streamable-http'], default='stdio',
                        help='Transport mode: stdio (default) or streamable-http')
    args = parser.parse_args()

    # Set port and base URI once for reuse throughout the function
    port = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", 8000)))
    base_uri = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")

    safe_print("🔧 Google Workspace MCP Server")
    safe_print("=" * 35)
    safe_print("📋 Server Information:")
    try:
        version = metadata.version("workspace-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"
    safe_print(f"   📦 Version: {version}")
    safe_print(f"   🌐 Transport: {args.transport}")
    if args.transport == 'streamable-http':
        safe_print(f"   🔗 URL: {base_uri}:{port}")
        safe_print(f"   🔐 OAuth Callback: {base_uri}:{port}/oauth2callback")
    safe_print(f"   👤 Mode: {'Single-user' if args.single_user else 'Multi-user'}")
    safe_print(f"   🐍 Python: {sys.version.split()[0]}")
    safe_print("")

    # Import tool modules to register them with the MCP server via decorators
    tool_imports = {
        'gmail': lambda: __import__('gmail.gmail_tools'),
        'drive': lambda: __import__('gdrive.drive_tools'),
        'calendar': lambda: __import__('gcalendar.calendar_tools'),
        'docs': lambda: __import__('gdocs.docs_tools'),
        'sheets': lambda: __import__('gsheets.sheets_tools'),
        'chat': lambda: __import__('gchat.chat_tools'),
        'forms': lambda: __import__('gforms.forms_tools'),
        'slides': lambda: __import__('gslides.slides_tools'),
        'tasks': lambda: __import__('gtasks.tasks_tools'),
        'search': lambda: __import__('gsearch.search_tools')
    }

    tool_icons = {
        'gmail': '📧',
        'drive': '📁',
        'calendar': '📅',
        'docs': '📄',
        'sheets': '📊',
        'chat': '💬',
        'forms': '📝',
        'slides': '🖼️',
        'tasks': '✓',
        'search': '🔍'
    }

    # Import specified tools or all tools if none specified
    tools_to_import = args.tools if args.tools is not None else tool_imports.keys()
    safe_print(f"🛠️  Loading {len(tools_to_import)} tool module{'s' if len(tools_to_import) != 1 else ''}:")
    for tool in tools_to_import:
        tool_imports[tool]()
        safe_print(f"   {tool_icons[tool]} {tool.title()} - Google {tool.title()} API integration")
    safe_print("")

    safe_print("📊 Configuration Summary:")
    safe_print(f"   🔧 Tools Enabled: {len(tools_to_import)}/{len(tool_imports)}")
    safe_print("   🔑 Auth Method: OAuth 2.0 with PKCE")
    safe_print(f"   📝 Log Level: {logging.getLogger().getEffectiveLevel()}")
    safe_print("")

    # Set global single-user mode flag
    if args.single_user:
        os.environ['MCP_SINGLE_USER_MODE'] = '1'
        safe_print("🔐 Single-user mode enabled")
        safe_print("")

    # Check credentials directory permissions before starting
    try:
        safe_print("🔍 Checking credentials directory permissions...")
        check_credentials_directory_permissions()
        safe_print("✅ Credentials directory permissions verified")
        safe_print("")
    except (PermissionError, OSError) as e:
        safe_print(f"❌ Credentials directory permission check failed: {e}")
        safe_print("   Please ensure the service has write permissions to create/access the credentials directory")
        logger.error(f"Failed credentials directory permission check: {e}")
        sys.exit(1)

    try:
        # Set transport mode for OAuth callback handling
        set_transport_mode(args.transport)

        if args.transport == 'streamable-http':
            safe_print(f"🚀 Starting server on {base_uri}:{port}")
        else:
            safe_print("🚀 Starting server in stdio mode")
            # Start minimal OAuth callback server for stdio mode
            from auth.oauth_callback_server import ensure_oauth_callback_available
            success, error_msg = ensure_oauth_callback_available('stdio', port, base_uri)
            if success:
                safe_print(f"   OAuth callback server started on {base_uri}:{port}/oauth2callback")
            else:
                warning_msg = f"   ⚠️  Warning: Failed to start OAuth callback server"
                if error_msg:
                    warning_msg += f": {error_msg}"
                safe_print(warning_msg)

        safe_print("   Ready for MCP connections!")
        safe_print("")

        if args.transport == 'streamable-http':
            # The server is already configured with port and server_url in core/server.py
            server.run(transport="streamable-http")
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
