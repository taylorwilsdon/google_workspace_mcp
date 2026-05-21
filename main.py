import io
import argparse
import json
import logging
import os
import socket
import sys
from functools import partial
from importlib import metadata, import_module
from dotenv import load_dotenv

# Prevent any stray startup output on macOS (e.g. platform identifiers) from
# corrupting the MCP JSON-RPC handshake on stdout. We capture anything written
# to stdout during module-level initialisation and replay it to stderr so that
# diagnostic information is not lost.
_original_stdout = sys.stdout
if sys.platform == "darwin":
    sys.stdout = io.StringIO()


def _load_startup_dependencies():
    from auth.credential_store import get_credential_store, get_selected_backend
    from auth.oauth_config import (
        get_oauth_config,
        reload_oauth_config,
        is_stateless_mode,
        is_service_account_enabled,
    )
    from core.log_formatter import EnhancedLogFormatter, configure_file_logging
    from core.utils import check_credentials_directory_permissions
    from core.server import server, set_transport_mode, configure_server_for_http
    from core.tool_tier_loader import resolve_tools_from_tier
    from core.tool_registry import (
        set_enabled_tools as set_enabled_tool_names,
        wrap_server_tool_method,
        filter_server_tools,
    )

    return (
        get_selected_backend,
        get_credential_store,
        get_oauth_config,
        reload_oauth_config,
        is_stateless_mode,
        is_service_account_enabled,
        EnhancedLogFormatter,
        configure_file_logging,
        check_credentials_directory_permissions,
        server,
        set_transport_mode,
        configure_server_for_http,
        resolve_tools_from_tier,
        set_enabled_tool_names,
        wrap_server_tool_method,
        filter_server_tools,
    )


(
    get_selected_backend,
    get_credential_store,
    get_oauth_config,
    reload_oauth_config,
    is_stateless_mode,
    is_service_account_enabled,
    EnhancedLogFormatter,
    configure_file_logging,
    check_credentials_directory_permissions,
    server,
    set_transport_mode,
    configure_server_for_http,
    resolve_tools_from_tier,
    set_enabled_tool_names,
    wrap_server_tool_method,
    filter_server_tools,
) = _load_startup_dependencies()

dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=dotenv_path)

# Suppress googleapiclient discovery cache warning
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)

# Suppress httpx/httpcore INFO logs that leak access tokens in URLs
# (e.g. tokeninfo?access_token=ya29.xxx)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

reload_oauth_config()

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

configure_file_logging()


def resolve_stdio_callback_port() -> None:
    """
    Late-bind the legacy stdio OAuth callback port.

    Streamable HTTP/OAuth 2.1 owns its main HTTP port directly and must keep the
    normal PORT/WORKSPACE_MCP_PORT semantics. The fallback range only exists for
    the standalone stdio callback listener.
    """
    from auth.port_resolver import resolve_port, NoAvailablePortError, PortConfigError

    try:
        resolve_port()
    except (NoAvailablePortError, PortConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    reload_oauth_config()


def resolve_callback_port_for_transport(transport: str) -> None:
    """Apply callback port fallback only to legacy stdio transport."""
    if transport == "stdio":
        resolve_stdio_callback_port()
    else:
        os.environ.pop("WORKSPACE_MCP_RESOLVED_PORT", None)


# Single source of truth: service name -> module path.
# VALID_SERVICES is derived from this mapping.
SERVICE_MODULES = {
    "gmail": "gmail.gmail_tools",
    "drive": "gdrive.drive_tools",
    "calendar": "gcalendar.calendar_tools",
    "docs": "gdocs.docs_tools",
    "sheets": "gsheets.sheets_tools",
    "chat": "gchat.chat_tools",
    "forms": "gforms.forms_tools",
    "slides": "gslides.slides_tools",
    "tasks": "gtasks.tasks_tools",
    "contacts": "gcontacts.contacts_tools",
    "search": "gsearch.search_tools",
    "appscript": "gappsscript.apps_script_tools",
}
VALID_SERVICES = frozenset(SERVICE_MODULES)


def safe_print(text):
    """Print to stderr, falling back to debug logging when running as an MCP server."""
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
    """Replace console handlers with ASCII-safe formatters for Windows compatibility."""

    class SafeEnhancedFormatter(EnhancedLogFormatter):
        """Enhanced ASCII formatter with additional Windows safety."""

        def format(self, record):
            """Format a log record, falling back to ASCII if encoding fails."""
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


def resolve_permissions_mode_selection(
    permission_services: list[str], tool_tier: str | None
) -> tuple[list[str], set[str] | None]:
    """
    Resolve service imports and optional tool-name filtering for --permissions mode.

    When a tier is specified, both:
    - imported services are narrowed to services with tier-matched tools
    - registered tools are narrowed to the resolved tool names
    """
    if tool_tier is None:
        return permission_services, None

    tier_tools, tier_services = resolve_tools_from_tier(tool_tier, permission_services)
    return tier_services, set(tier_tools)


def narrow_permissions_to_services(
    permissions: dict[str, str], services: list[str]
) -> dict[str, str]:
    """Restrict permission entries to the provided service list order."""
    return {
        service: permissions[service] for service in services if service in permissions
    }


def _restore_stdout() -> None:
    """Restore the real stdout and replay any captured output to stderr."""
    captured_stdout = sys.stdout

    # Idempotent: if already restored, nothing to do.
    if captured_stdout is _original_stdout:
        return

    captured = ""
    required_stringio_methods = ("getvalue", "write", "flush")
    try:
        if all(
            callable(getattr(captured_stdout, method_name, None))
            for method_name in required_stringio_methods
        ):
            captured = captured_stdout.getvalue()
    finally:
        sys.stdout = _original_stdout

    if captured:
        print(captured, end="", file=sys.stderr)


def main():
    """
    Main entry point for the Google Workspace MCP server.
    Uses FastMCP's native streamable-http transport.
    """
    _restore_stdout()

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
        choices=sorted(VALID_SERVICES),
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
        default=None,
        help="Transport mode: stdio (default; overridable via WORKSPACE_MCP_TRANSPORT) or streamable-http",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="Run in read-only mode - requests only read-only scopes and disables tools requiring write permissions",
    )
    parser.add_argument(
        "--permissions",
        nargs="+",
        metavar="SERVICE:LEVEL",
        help=(
            "Granular per-service permission levels. Format: service:level. "
            "Example: --permissions gmail:organize drive:readonly. "
            "Gmail levels: readonly, organize, drafts, send, full (cumulative). "
            "Other services: readonly, full. "
            "Mutually exclusive with --read-only and --tools."
        ),
    )
    args = parser.parse_args()

    # Env var fallbacks for plugin users who configure via userConfig.
    # Non-empty but invalid values fail closed to prevent silent access widening.
    # Skip env fallbacks for mutually exclusive flags that were set on the CLI
    # to avoid conflicts (e.g. WORKSPACE_MCP_READ_ONLY=true + --permissions).
    _cli_has_tools = args.tools is not None
    _cli_has_permissions = args.permissions is not None
    _cli_has_read_only = args.read_only

    def _exit_with_env_error(name: str, value: str, expected: str) -> None:
        print(f"Error: invalid {name} {value!r}; expected {expected}.", file=sys.stderr)
        sys.exit(1)

    if args.tools is None and not _cli_has_permissions:
        _env_tools = os.getenv("WORKSPACE_MCP_TOOLS", "").strip()
        if _env_tools:
            _parsed = [t.strip().lower() for t in _env_tools.split(",")]
            _invalid = [t for t in _parsed if not t or t not in VALID_SERVICES]
            if _invalid:
                _exit_with_env_error(
                    "WORKSPACE_MCP_TOOLS",
                    _env_tools,
                    "comma-separated valid service names",
                )
            args.tools = _parsed
    elif _cli_has_permissions and os.getenv("WORKSPACE_MCP_TOOLS", "").strip():
        logger.info(
            "WORKSPACE_MCP_TOOLS ignored because --permissions was provided on the CLI"
        )
    if args.tool_tier is None:
        _env_tier = os.getenv("WORKSPACE_MCP_TOOL_TIER", "").strip().lower()
        if _env_tier:
            if _env_tier not in {"core", "extended", "complete"}:
                _exit_with_env_error(
                    "WORKSPACE_MCP_TOOL_TIER", _env_tier, "core, extended, or complete"
                )
            args.tool_tier = _env_tier
    if not args.read_only and not _cli_has_permissions:
        _env_ro = os.getenv("WORKSPACE_MCP_READ_ONLY", "").strip().lower()
        if _env_ro:
            if _env_ro in {"true", "1", "yes"}:
                args.read_only = True
            elif _env_ro not in {"false", "0", "no"}:
                _exit_with_env_error(
                    "WORKSPACE_MCP_READ_ONLY", _env_ro, "true/1/yes or false/0/no"
                )
    elif _cli_has_permissions and os.getenv("WORKSPACE_MCP_READ_ONLY", "").strip():
        logger.info(
            "WORKSPACE_MCP_READ_ONLY ignored because --permissions was provided on the CLI"
        )
    if args.permissions is None and not _cli_has_read_only and not _cli_has_tools:
        _env_perms = os.getenv("WORKSPACE_MCP_PERMISSIONS", "").strip()
        if _env_perms:
            args.permissions = [p.lower() for p in _env_perms.split()]
    elif (_cli_has_read_only or _cli_has_tools) and os.getenv(
        "WORKSPACE_MCP_PERMISSIONS", ""
    ).strip():
        _conflicts = [
            name
            for name, present in (
                ("--read-only", _cli_has_read_only),
                ("--tools", _cli_has_tools),
            )
            if present
        ]
        logger.info(
            "WORKSPACE_MCP_PERMISSIONS ignored because %s was provided on the CLI",
            " and ".join(_conflicts),
        )
    if args.transport is None:
        _env_transport = os.getenv("WORKSPACE_MCP_TRANSPORT", "").strip().lower()
        if _env_transport:
            if _env_transport not in {"stdio", "streamable-http"}:
                _exit_with_env_error(
                    "WORKSPACE_MCP_TRANSPORT",
                    _env_transport,
                    "stdio or streamable-http",
                )
            args.transport = _env_transport
        else:
            args.transport = "stdio"

    _env_http_port = os.getenv("WORKSPACE_MCP_HTTP_PORT", "").strip()
    http_port = None
    if _env_http_port:
        try:
            http_port = int(_env_http_port)
            if not 1 <= http_port <= 65535:
                raise ValueError("must be between 1 and 65535")
        except ValueError as exc:
            print(
                f"Error: invalid WORKSPACE_MCP_HTTP_PORT '{_env_http_port}': {exc}.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Validate mutually exclusive flags (settings can come from CLI flags or WORKSPACE_MCP_* env vars).
    if args.permissions and args.read_only:
        print(
            "Error: --permissions and --read-only are mutually exclusive "
            "(via CLI flag or WORKSPACE_MCP_PERMISSIONS / WORKSPACE_MCP_READ_ONLY env var). "
            "Use service:readonly within --permissions instead.",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.permissions and args.tools is not None:
        print(
            "Error: --permissions and --tools cannot be combined "
            "(via CLI flag or WORKSPACE_MCP_PERMISSIONS / WORKSPACE_MCP_TOOLS env var). "
            "Select services via --permissions (optionally with --tool-tier).",
            file=sys.stderr,
        )
        sys.exit(1)

    resolve_callback_port_for_transport(args.transport)

    # Set port and base URI once for reuse throughout the function
    if os.getenv("WORKSPACE_MCP_RESOLVED_PORT") == "1":
        port = int(os.getenv("WORKSPACE_MCP_PORT", os.getenv("PORT", "8000")))
    else:
        port = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", "8000")))
    base_uri = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
    host = os.getenv("WORKSPACE_MCP_HOST", "0.0.0.0")
    external_url = os.getenv("WORKSPACE_EXTERNAL_URL")
    display_url = external_url if external_url else f"{base_uri}:{port}"

    try:
        version = metadata.version("workspace-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"

    mode = "single-user" if args.single_user else "multi-user"
    pyver = sys.version.split()[0]

    # ANSI color codes for Google brand colors
    B = "\033[1;34m"  # Blue
    R = "\033[1;31m"  # Red
    Y = "\033[1;33m"  # Yellow
    G = "\033[1;32m"  # Green
    W = "\033[1;37m"  # White
    C = "\033[0;36m"  # Cyan
    D = "\033[0;90m"  # Dim
    RST = "\033[0m"  # Reset

    info_lines = [f"{C}{args.transport}  ·  {mode}{RST}"]
    if args.transport == "streamable-http":
        info_lines.append(f"{C}{display_url}{RST}")
    if args.read_only:
        info_lines.append(f"{Y}read-only{RST}")
    if args.permissions:
        info_lines.append(f"{Y}granular permissions{RST}")

    banner = (
        f"\n{D}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RST}\n"
        f"\n"
        f"     {B}██████{R}╗{RST}       {W}Google Workspace{RST}\n"
        f"     {B}██{RST}╔════╝       {W}MCP Server{RST}  {C}v{version}{RST}\n"
        f"     {B}██{RST}║  {Y}███{RST}╗\n"
        f"     {B}██{RST}║   {Y}██{RST}║      {info_lines[0]}\n"
        f"     {B}╚█████{G}█╔╝{RST}      {C}Python {pyver}{RST}\n"
        f"      {B}╚════{G}═╝{RST}"
    )
    for line in info_lines[1:]:
        banner += f"\n                       {line}"
    banner += (
        f"\n\n{D}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RST}\n"
    )
    safe_print(banner)

    # Active Configuration
    safe_print("⚙️ Active Configuration:")

    # Redact client secret for security
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "Not Set")
    redacted_secret = (
        f"{client_secret[:4]}...{client_secret[-4:]}"
        if len(client_secret) > 8
        else "Invalid or too short"
    )

    # Determine credentials directory (same logic as credential_store.py)
    workspace_creds_dir = os.getenv("WORKSPACE_MCP_CREDENTIALS_DIR")
    google_creds_dir = os.getenv("GOOGLE_MCP_CREDENTIALS_DIR")
    if workspace_creds_dir:
        creds_dir_display = os.path.expanduser(workspace_creds_dir)
        creds_dir_source = "WORKSPACE_MCP_CREDENTIALS_DIR"
    elif google_creds_dir:
        creds_dir_display = os.path.expanduser(google_creds_dir)
        creds_dir_source = "GOOGLE_MCP_CREDENTIALS_DIR"
    else:
        creds_dir_display = os.path.join(
            os.path.expanduser("~"), ".google_workspace_mcp", "credentials"
        )
        creds_dir_source = "default"

    config_vars = {
        "GOOGLE_OAUTH_CLIENT_ID": os.getenv("GOOGLE_OAUTH_CLIENT_ID", "Not Set"),
        "GOOGLE_OAUTH_CLIENT_SECRET": redacted_secret,
        "USER_GOOGLE_EMAIL": os.getenv("USER_GOOGLE_EMAIL", "Not Set"),
        "CREDENTIALS_DIR": f"{creds_dir_display} ({creds_dir_source})",
        "MCP_SINGLE_USER_MODE": os.getenv("MCP_SINGLE_USER_MODE", "false"),
        "MCP_ENABLE_OAUTH21": os.getenv("MCP_ENABLE_OAUTH21", "false"),
        "WORKSPACE_MCP_STATELESS_MODE": os.getenv(
            "WORKSPACE_MCP_STATELESS_MODE", "false"
        ),
        "OAUTHLIB_INSECURE_TRANSPORT": os.getenv(
            "OAUTHLIB_INSECURE_TRANSPORT", "false"
        ),
        "GOOGLE_CLIENT_SECRET_PATH": os.getenv("GOOGLE_CLIENT_SECRET_PATH", "Not Set"),
        "GOOGLE_SERVICE_ACCOUNT_KEY_FILE": os.getenv(
            "GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "Not Set"
        ),
    }

    for key, value in config_vars.items():
        safe_print(f"   - {key}: {value}")
    safe_print("")

    # Import tool modules to register them with the MCP server via decorators.
    tool_imports = {
        svc: partial(import_module, mod) for svc, mod in SERVICE_MODULES.items()
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
        "contacts": "👤",
        "search": "🔍",
        "appscript": "📜",
    }

    # Determine which tools to import based on arguments
    perms = None
    if args.permissions:
        # Granular permissions mode — parse and activate before tool selection
        from auth.permissions import parse_permissions_arg, set_permissions

        try:
            perms = parse_permissions_arg(args.permissions)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        # Permissions implicitly defines which services to load
        tools_to_import = list(perms.keys())
        set_enabled_tool_names(None)

        if args.tool_tier is not None:
            # Combine with tier filtering within the permission-selected services
            try:
                tools_to_import, tier_tool_filter = resolve_permissions_mode_selection(
                    tools_to_import, args.tool_tier
                )
                set_enabled_tool_names(tier_tool_filter)
                perms = narrow_permissions_to_services(perms, tools_to_import)
            except Exception as e:
                print(
                    f"Error loading tools for tier '{args.tool_tier}': {e}",
                    file=sys.stderr,
                )
                sys.exit(1)
        set_permissions(perms)
    elif args.tool_tier is not None:
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

    from auth.scopes import set_enabled_tools, set_read_only

    set_enabled_tools(list(tools_to_import))
    if args.read_only:
        set_read_only(True)

    loaded = []
    failed = []
    for tool in tools_to_import:
        try:
            tool_imports[tool]()
            loaded.append(tool)
        except ModuleNotFoundError as exc:
            logger.error("Failed to import tool '%s': %s", tool, exc, exc_info=True)
            failed.append((tool, exc))

    tool_summary = " ".join(f"{tool_icons.get(t, '🔧')} {t.title()}" for t in loaded)
    safe_print(f"🛠️  Loaded {len(loaded)} services: {tool_summary}")
    for tool, exc in failed:
        safe_print(f"   ⚠️ Failed: {tool.title()} ({exc})")

    if perms:
        perm_summary = " | ".join(
            f"{tool_icons.get(svc, ' ')}{svc}:{lvl}"
            for svc, lvl in sorted(perms.items())
        )
        safe_print(f"🔒 Permissions: {perm_summary}")
    safe_print("")

    # Filter tools based on tier configuration (if tier-based loading is enabled)
    filter_server_tools(server)

    summary_parts = [f"{len(loaded)}/{len(tool_imports)} services"]
    if args.tool_tier is not None:
        tier_desc = f"tier={args.tool_tier}"
        if args.tools is not None:
            tier_desc += f" ({', '.join(args.tools)})"
        summary_parts.append(tier_desc)
    safe_print(f"📊 {' | '.join(summary_parts)}")
    safe_print("")

    # Set global single-user mode flag
    if args.single_user:
        # Check for incompatible OAuth 2.1 mode
        if os.getenv("MCP_ENABLE_OAUTH21", "false").lower() == "true":
            safe_print("❌ Single-user mode is incompatible with OAuth 2.1 mode")
            safe_print(
                "   Single-user mode is for legacy clients that pass user emails"
            )
            safe_print(
                "   OAuth 2.1 mode is for multi-user scenarios with bearer tokens"
            )
            safe_print(
                "   Please choose one mode: either --single-user OR MCP_ENABLE_OAUTH21=true"
            )
            sys.exit(1)

        if is_stateless_mode():
            safe_print("❌ Single-user mode is incompatible with stateless mode")
            safe_print("   Stateless mode requires OAuth 2.1 which is multi-user")
            sys.exit(1)

        if is_service_account_enabled():
            safe_print("❌ Single-user mode is incompatible with service account mode")
            safe_print(
                "   Service account mode handles auth via domain-wide delegation"
            )
            safe_print(
                "   Please choose one mode: either --single-user OR GOOGLE_SERVICE_ACCOUNT_KEY_FILE"
            )
            sys.exit(1)

        os.environ["MCP_SINGLE_USER_MODE"] = "1"
        safe_print("🔐 Single-user mode enabled")
        safe_print("")

    # Service account mode startup validation
    if is_service_account_enabled():
        user_email = os.getenv("USER_GOOGLE_EMAIL")
        if not user_email:
            safe_print("❌ Service account mode requires USER_GOOGLE_EMAIL to be set")
            safe_print("   Set USER_GOOGLE_EMAIL to the domain user to impersonate")
            sys.exit(1)
        # Validate service account key material before advertising readiness
        sa_config = get_oauth_config()
        try:
            if sa_config.service_account_key_file:
                with open(sa_config.service_account_key_file) as f:
                    key_data = json.load(f)
            else:
                key_data = json.loads(sa_config.service_account_key_json)
            required_fields = {"type", "project_id", "private_key", "client_email"}
            missing = required_fields - set(key_data.keys())
            if missing:
                safe_print(
                    f"❌ Service account key missing required fields: "
                    f"{', '.join(sorted(missing))}"
                )
                sys.exit(1)
            if key_data.get("type") != "service_account":
                safe_print(
                    f"❌ Service account key has unexpected type: "
                    f"{key_data.get('type')!r}"
                )
                sys.exit(1)
        except FileNotFoundError as e:
            safe_print(f"❌ Service account key file not found: {e}")
            sys.exit(1)
        except json.JSONDecodeError as e:
            safe_print(f"❌ Service account key contains invalid JSON: {e}")
            sys.exit(1)
        except (IOError, OSError) as e:
            safe_print(f"❌ Failed to read service account key: {e}")
            sys.exit(1)
        safe_print("🔐 Service account mode enabled (domain-wide delegation)")
        safe_print(f"   Impersonating: {user_email}")
        safe_print("")

    backend = get_selected_backend()

    # Check local credentials directory permissions only when using the local backend.
    if (
        not is_stateless_mode()
        and not is_service_account_enabled()
        and backend != "gcs"
    ):
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
        if is_stateless_mode():
            skip_reason = "stateless mode"
        elif is_service_account_enabled():
            skip_reason = "service account mode"
        else:
            skip_reason = "gcs backend"
        safe_print(f"🔍 Skipping credentials directory check ({skip_reason})")
        safe_print("")

    if (
        backend == "gcs"
        and not is_stateless_mode()
        and not is_service_account_enabled()
    ):
        try:
            from auth.credential_store import GCSCredentialStore

            credential_store = get_credential_store()
            if not isinstance(credential_store, GCSCredentialStore):
                raise TypeError(
                    "Configured credential store backend is 'gcs' but the store instance is not GCSCredentialStore"
                )

            if credential_store.require_cmek:
                safe_print("🔍 Verifying GCS credential store configuration...")
                credential_store.verify_cmek()
                safe_print("✅ GCS credential store configuration verified")
            else:
                safe_print(
                    "ℹ️ GCS credential store verification skipped (require_cmek=False)"
                )
            safe_print("")
        except Exception as e:
            safe_print(f"❌ GCS credential store verification failed: {e}")
            sys.exit(1)

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
            # Start minimal OAuth callback server for stdio mode (not needed for service accounts)
            if not is_service_account_enabled():
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

        if args.transport == "streamable-http" and _env_http_port:
            logger.warning(
                "WORKSPACE_MCP_HTTP_PORT is ignored when transport is 'streamable-http'; "
                "the primary server already serves HTTP on WORKSPACE_MCP_PORT/PORT."
            )

        if args.transport == "streamable-http":
            # Check port availability before starting HTTP server
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind((host, port))
            except OSError as e:
                safe_print(f"Socket error: {e}")
                safe_print(
                    f"❌ Port {port} is already in use. Cannot start HTTP server."
                )
                sys.exit(1)

            server.run(
                transport="streamable-http",
                host=host,
                port=port,
                stateless_http=is_stateless_mode(),
                show_banner=False,
            )
        else:
            if http_port is not None:
                # Dual transport: stdio for MCP client + HTTP for workspace-cli
                import asyncio
                import uvicorn

                # Bind sidecar to loopback only — auth provider is not initialized
                # in stdio mode, so exposing this on 0.0.0.0 would allow unauthenticated access.
                http_host = "127.0.0.1"

                async def _run_dual() -> None:
                    """Run stdio and HTTP transports concurrently."""
                    http_available = True
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.bind((http_host, http_port))
                    except OSError:
                        logger.warning(
                            "Port %d in use, workspace-cli HTTP endpoint unavailable",
                            http_port,
                        )
                        http_available = False

                    http_srv = None
                    http_task = None
                    if http_available:
                        app = server.http_app(path="/mcp")
                        config = uvicorn.Config(
                            app, host=http_host, port=http_port, log_level="warning"
                        )
                        http_srv = uvicorn.Server(config)
                        http_task = asyncio.create_task(http_srv.serve())
                        safe_print(
                            f"   workspace-cli endpoint: http://{http_host}:{http_port}/mcp"
                        )

                    try:
                        await server.run_stdio_async(show_banner=False)
                    finally:
                        if http_srv:
                            http_srv.should_exit = True
                        if http_task:
                            try:
                                await asyncio.wait_for(http_task, timeout=5.0)
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "HTTP sidecar did not exit within 5s; cancelled"
                                )
                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                logger.warning(
                                    "HTTP sidecar ended with exception: %s", exc
                                )

                asyncio.run(_run_dual())
            else:
                server.run(show_banner=False)
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
