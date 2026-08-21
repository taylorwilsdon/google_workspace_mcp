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
from core.startup_ui import StartupDisplay, collapse_home, wordmark_lines

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
    from core.log_formatter import (
        EnhancedLogFormatter,
        configure_file_logging,
        install_noisy_log_filters,
    )
    from core.utils import check_credentials_directory_permissions
    from core.server import server, set_transport_mode, configure_server_for_http
    from core.tool_tier_loader import resolve_tools_from_tier
    from core.tool_registry import (
        set_enabled_tools as set_enabled_tool_names,
        resolve_disabled_tools,
        set_disabled_tools,
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
        install_noisy_log_filters,
        check_credentials_directory_permissions,
        server,
        set_transport_mode,
        configure_server_for_http,
        resolve_tools_from_tier,
        set_enabled_tool_names,
        resolve_disabled_tools,
        set_disabled_tools,
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
    install_noisy_log_filters,
    check_credentials_directory_permissions,
    server,
    set_transport_mode,
    configure_server_for_http,
    resolve_tools_from_tier,
    set_enabled_tool_names,
    resolve_disabled_tools,
    set_disabled_tools,
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

install_noisy_log_filters()
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


# Advisories raised while resolving configuration. They are queued rather than
# logged so the startup screen can present them as one block instead of letting
# them race ahead of the banner on stderr.
STARTUP_NOTICES: list[str] = []


def add_startup_notice(message: str) -> None:
    """Queue an advisory for the startup screen, keeping it in the debug log."""
    STARTUP_NOTICES.append(message)
    logger.debug(message)


def resolve_bind_host_for_transport(transport: str) -> str:
    """Choose a safe default bind host for the selected transport/auth mode."""
    configured_host = os.getenv("WORKSPACE_MCP_HOST")
    host = configured_host or "0.0.0.0"
    if transport != "streamable-http":
        return host

    config = get_oauth_config()
    if config.is_oauth21_enabled():
        return host

    if configured_host:
        if configured_host not in {"localhost", "127.0.0.1", "::1"}:
            add_startup_notice(
                f"Legacy streamable-http mode has no MCP-level auth provider and is "
                f"bound to {configured_host} because WORKSPACE_MCP_HOST was explicitly "
                f"set. Use MCP_ENABLE_OAUTH21=true for remotely reachable HTTP "
                f"deployments."
            )
        return configured_host

    add_startup_notice(
        "Legacy streamable-http mode has no MCP-level auth provider; binding to "
        "127.0.0.1 by default. Set WORKSPACE_MCP_HOST explicitly only for trusted "
        "networks, or use MCP_ENABLE_OAUTH21=true for remote HTTP deployments."
    )
    return "127.0.0.1"


def validate_streamable_http_auth(transport: str) -> None:
    """Reject misconfigured OAuth 2.1 HTTP before starting."""
    if transport != "streamable-http":
        return

    config = get_oauth_config()
    if config.is_oauth21_enabled() and not config.is_configured():
        print(
            "Error: streamable-http transport with MCP_ENABLE_OAUTH21=true requires "
            "GOOGLE_OAUTH_CLIENT_ID so OAuth 2.1 protocol authentication can be "
            "configured.",
            file=sys.stderr,
        )
        sys.exit(1)


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

# Every icon is a double-width emoji with no variation selector, so the startup
# service grid stays aligned across terminals.
SERVICE_ICONS = {
    "gmail": "📧",
    "drive": "📁",
    "calendar": "📅",
    "docs": "📄",
    "sheets": "📊",
    "chat": "💬",
    "forms": "📝",
    "slides": "🎥",
    "tasks": "📋",
    "contacts": "👤",
    "search": "🔍",
    "appscript": "📜",
}


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


def _optional_field(name: str, *, path: bool = False) -> tuple[str, str, str]:
    """Describe an optional env var as a (label, value, state) display row."""
    value = os.getenv(name)
    if not value:
        return name, "not set", "off"
    return name, collapse_home(os.path.expanduser(value)) if path else value, "on"


def _flag_field(name: str, *, warn_when_true: bool = False) -> tuple[str, str, str]:
    """Describe a boolean env var as a (label, value, state) display row."""
    value = os.getenv(name, "false")
    if value.strip().lower() not in {"true", "1", "yes"}:
        return name, value, "off"
    return name, value, "warn" if warn_when_true else "on"


def _disabled_tools_field(disabled_tools: set[str]) -> tuple[str, str, str]:
    """Describe the resolved per-tool block list as a display row."""
    name = "WORKSPACE_MCP_DISABLED_TOOLS"
    if not disabled_tools:
        return name, "not set", "off"
    return name, ", ".join(sorted(disabled_tools)), "on"


def _client_secret_field() -> tuple[str, str, str]:
    """Describe the OAuth client secret without revealing it."""
    name = "GOOGLE_OAUTH_CLIENT_SECRET"
    secret = os.getenv(name)
    if not secret:
        return name, "not set", "off"
    if len(secret) <= 8:
        return name, "set · unexpectedly short", "warn"
    return name, f"{secret[:4]}…{secret[-4:]}", "on"


def _credentials_dir_field() -> tuple[str, str, str]:
    """Describe the credentials directory, mirroring credential_store resolution."""
    for name in ("WORKSPACE_MCP_CREDENTIALS_DIR", "GOOGLE_MCP_CREDENTIALS_DIR"):
        value = os.getenv(name)
        if value:
            return name, collapse_home(os.path.expanduser(value)), "on"
    default = os.path.join(
        os.path.expanduser("~"), ".google_workspace_mcp", "credentials"
    )
    return "WORKSPACE_MCP_CREDENTIALS_DIR", collapse_home(default), "off"


def describe_credential_config() -> list[tuple[str, str, str]]:
    """Build the credential rows shown in the startup configuration section."""
    return [
        _optional_field("GOOGLE_OAUTH_CLIENT_ID"),
        _client_secret_field(),
        _optional_field("GOOGLE_CLIENT_SECRET_PATH", path=True),
        _optional_field("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", path=True),
        _optional_field("USER_GOOGLE_EMAIL"),
        _credentials_dir_field(),
    ]


def describe_mode_config(
    disabled_tools: set[str] = frozenset(),
) -> list[tuple[str, str, str]]:
    """Build the mode rows shown in the startup configuration section."""
    return [
        _flag_field("MCP_SINGLE_USER_MODE"),
        _flag_field("MCP_ENABLE_OAUTH21"),
        _flag_field("WORKSPACE_MCP_STATELESS_MODE"),
        _flag_field("OAUTHLIB_INSECURE_TRANSPORT", warn_when_true=True),
        _disabled_tools_field(disabled_tools),
    ]


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

    # Enable OpenTelemetry tracing when an OTLP endpoint is configured.
    from core.telemetry import configure_telemetry

    configure_telemetry()

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
        "--disabled-tools",
        nargs="+",
        metavar="TOOL_NAME",
        help=(
            "Block individual tools by name regardless of tier or permission selection. "
            "Composes with every other filtering option. "
            "Env var: WORKSPACE_MCP_DISABLED_TOOLS (comma-separated)."
        ),
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
    # Subtractive, so it needs no conflict handling against the allowlist flags.
    disabled_tools = resolve_disabled_tools(args.disabled_tools)
    set_disabled_tools(disabled_tools)
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

    validate_streamable_http_auth(args.transport)
    resolve_callback_port_for_transport(args.transport)

    # Set port and base URI once for reuse throughout the function
    if os.getenv("WORKSPACE_MCP_RESOLVED_PORT") == "1":
        port = int(os.getenv("WORKSPACE_MCP_PORT", os.getenv("PORT", "8000")))
    else:
        port = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", "8000")))
    base_uri = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
    host = resolve_bind_host_for_transport(args.transport)
    external_url = os.getenv("WORKSPACE_EXTERNAL_URL")
    display_url = external_url if external_url else f"{base_uri}:{port}"

    try:
        version = metadata.version("workspace-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"

    mode = "single-user" if args.single_user else "multi-user"
    pyver = sys.version.split()[0]

    flags = []
    if args.read_only:
        flags.append("read-only")
    if args.permissions:
        flags.append("granular permissions")

    ui = StartupDisplay(safe_print)
    ui.blank()
    ui.rule()
    ui.blank()
    ui.banner(
        wordmark_lines(
            ui,
            version=version,
            transport=args.transport,
            mode=mode,
            python_version=pyver,
            url=display_url if args.transport == "streamable-http" else None,
            flags=flags,
        )
    )

    ui.section("Configuration")
    ui.fields(
        [
            ("Credentials", describe_credential_config()),
            ("Modes", describe_mode_config(disabled_tools)),
        ]
    )

    # Import tool modules to register them with the MCP server via decorators.
    tool_imports = {
        svc: partial(import_module, mod) for svc, mod in SERVICE_MODULES.items()
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

    # Filter tools based on tier configuration (if tier-based loading is enabled)
    tools_removed = filter_server_tools(server)

    ui.section("Services")
    ui.grid([(SERVICE_ICONS.get(t, "🔧"), t.title()) for t in loaded])
    ui.blank()

    summary = f"{len(loaded)} of {len(tool_imports)} services loaded"
    if args.tool_tier is not None:
        summary += f" · tier {args.tool_tier}"
    if tools_removed:
        summary += f" · {tools_removed} tools filtered out"
    ui.step(summary)
    for tool, exc in failed:
        ui.step(f"{tool.title()} failed to load", state="fail")
        ui.detail(str(exc))

    if perms:
        ui.blank()
        ui.heading("Permissions")
        ui.grid(
            [
                (SERVICE_ICONS.get(service, "🔧"), f"{service}:{level}")
                for service, level in sorted(perms.items())
            ],
            columns=3,
        )

    ui.section("Startup")

    # Set global single-user mode flag
    if args.single_user:
        # Check for incompatible OAuth 2.1 mode
        if os.getenv("MCP_ENABLE_OAUTH21", "false").lower() == "true":
            ui.step(
                "Single-user mode is incompatible with OAuth 2.1 mode", state="fail"
            )
            ui.detail("Single-user mode is for legacy clients that pass user emails")
            ui.detail("OAuth 2.1 mode is for multi-user scenarios with bearer tokens")
            ui.detail("Choose one: --single-user OR MCP_ENABLE_OAUTH21=true")
            sys.exit(1)

        if is_stateless_mode():
            ui.step(
                "Single-user mode is incompatible with stateless mode", state="fail"
            )
            ui.detail("Stateless mode requires OAuth 2.1, which is multi-user")
            sys.exit(1)

        if is_service_account_enabled():
            ui.step(
                "Single-user mode is incompatible with service account mode",
                state="fail",
            )
            ui.detail("Service account mode handles auth via domain-wide delegation")
            ui.detail("Choose one: --single-user OR GOOGLE_SERVICE_ACCOUNT_KEY_FILE")
            sys.exit(1)

        os.environ["MCP_SINGLE_USER_MODE"] = "1"
        ui.step("Single-user mode enabled")

    # Service account mode startup validation
    if is_service_account_enabled():
        user_email = os.getenv("USER_GOOGLE_EMAIL")
        if not user_email:
            ui.step("Service account mode requires USER_GOOGLE_EMAIL", state="fail")
            ui.detail("Set USER_GOOGLE_EMAIL to the domain user to impersonate")
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
                ui.step("Service account key is missing required fields", state="fail")
                ui.detail(", ".join(sorted(missing)))
                sys.exit(1)
            if key_data.get("type") != "service_account":
                ui.step("Service account key has unexpected type", state="fail")
                ui.detail(repr(key_data.get("type")))
                sys.exit(1)
        except FileNotFoundError as e:
            ui.step("Service account key file not found", state="fail")
            ui.detail(str(e))
            sys.exit(1)
        except json.JSONDecodeError as e:
            ui.step("Service account key contains invalid JSON", state="fail")
            ui.detail(str(e))
            sys.exit(1)
        except (IOError, OSError) as e:
            ui.step("Failed to read service account key", state="fail")
            ui.detail(str(e))
            sys.exit(1)
        ui.step("Service account mode enabled", "domain-wide delegation")
        ui.detail(f"impersonating {user_email}")

    backend = get_selected_backend()

    # Check local credentials directory permissions only when using the local backend.
    if (
        not is_stateless_mode()
        and not is_service_account_enabled()
        and backend != "gcs"
    ):
        try:
            check_credentials_directory_permissions()
            ui.step("Credentials directory verified")
        except (PermissionError, OSError) as e:
            ui.step("Credentials directory permission check failed", state="fail")
            ui.detail(str(e))
            ui.detail(
                "Ensure the service can create and write to the credentials directory"
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
        ui.step(f"Credentials directory check skipped ({skip_reason})", state="skip")

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
                credential_store.verify_cmek()
                ui.step("GCS credential store verified")
            else:
                ui.step(
                    "GCS credential store verification skipped",
                    "require_cmek=False",
                    state="skip",
                )
        except Exception as e:
            ui.step("GCS credential store verification failed", state="fail")
            ui.detail(str(e))
            sys.exit(1)

    try:
        # Set transport mode for OAuth callback handling
        set_transport_mode(args.transport)

        # Configure auth initialization for FastMCP lifecycle events
        if args.transport == "streamable-http":
            configure_server_for_http()
            ui.step("HTTP server", f"{base_uri}:{port}")
            if external_url:
                ui.detail(f"external URL {external_url}")
        else:
            ui.step("STDIO server")
            # The OAuth callback / attachment server is started lazily — only when
            # an auth flow is initiated or an attachment URL is handed out — so
            # short-lived spawns (e.g. client health checks) never bind a port and
            # cannot exhaust the 8000-8004 fallback range (see issue #832).
            if not is_service_account_enabled():
                ui.detail(
                    f"OAuth callback starts on demand at {display_url}/oauth2callback"
                )

        ui.step("Ready for MCP connections")

        if args.transport == "streamable-http" and _env_http_port:
            add_startup_notice(
                "WORKSPACE_MCP_HTTP_PORT is ignored when transport is "
                "'streamable-http'; the primary server already serves HTTP on "
                "WORKSPACE_MCP_PORT/PORT."
            )

        if STARTUP_NOTICES:
            ui.section("Notices")
            for message in STARTUP_NOTICES:
                ui.notice(message)

        ui.blank()
        ui.rule()
        ui.blank()

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
                            app,
                            host=http_host,
                            port=http_port,
                            log_level="warning",
                            # Match FastMCP's own uvicorn config: uvicorn's "auto"
                            # default resolves to the deprecated legacy-websockets
                            # implementation whenever `websockets` is installed.
                            ws="websockets-sansio",
                        )
                        http_srv = uvicorn.Server(config)
                        http_task = asyncio.create_task(http_srv.serve())
                        logger.info(
                            "workspace-cli endpoint: http://%s:%d/mcp",
                            http_host,
                            http_port,
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
