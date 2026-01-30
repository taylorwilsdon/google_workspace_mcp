import logging
import os
from typing import List, Optional
from importlib import metadata

from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.middleware import Middleware

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider

from auth.oauth21_session_store import get_oauth21_session_store, set_auth_provider
from auth.google_auth import handle_auth_callback, start_auth_flow, check_client_secrets
from auth.oauth_config import is_oauth21_enabled, is_external_oauth21_provider
from auth.mcp_session_middleware import MCPSessionMiddleware
from auth.oauth_responses import (
    create_error_response,
    create_success_response,
    create_server_error_response,
)
from auth.auth_info_middleware import AuthInfoMiddleware
from auth.scopes import SCOPES, get_current_scopes  # noqa
from core.config import (
    USER_GOOGLE_EMAIL,
    get_transport_mode,
    set_transport_mode as _set_transport_mode,
    get_oauth_redirect_uri as get_oauth_redirect_uri_for_current_mode,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_auth_provider: Optional[GoogleProvider] = None
_legacy_callback_registered = False

session_middleware = Middleware(MCPSessionMiddleware)


# Custom FastMCP that adds secure middleware stack for OAuth 2.1
class SecureFastMCP(FastMCP):
    def streamable_http_app(self) -> "Starlette":
        """Override to add secure middleware stack for OAuth 2.1."""
        app = super().streamable_http_app()

        # Add middleware in order (first added = outermost layer)
        # Session Management - extracts session info for MCP context
        app.user_middleware.insert(0, session_middleware)

        # Rebuild middleware stack
        app.middleware_stack = app.build_middleware_stack()
        logger.info("Added middleware stack: Session Management")
        return app


server = SecureFastMCP(
    name="google_workspace",
    auth=None,
)

# Add the AuthInfo middleware to inject authentication into FastMCP context
auth_info_middleware = AuthInfoMiddleware()
server.add_middleware(auth_info_middleware)


def _parse_bool_env(value: str) -> bool:
    """Parse environment variable string to boolean."""
    return value.lower() in ("1", "true", "yes", "on")


def set_transport_mode(mode: str):
    """Sets the transport mode for the server."""
    _set_transport_mode(mode)
    logger.info(f"Transport: {mode}")


def _ensure_legacy_callback_route() -> None:
    global _legacy_callback_registered
    if _legacy_callback_registered:
        return
    server.custom_route("/oauth2callback", methods=["GET"])(legacy_oauth2_callback)
    _legacy_callback_registered = True


def configure_server_for_http():
    """
    Configures the authentication provider for HTTP transport.
    This must be called BEFORE server.run().
    """
    global _auth_provider

    transport_mode = get_transport_mode()

    if transport_mode != "streamable-http":
        return

    # Use centralized OAuth configuration
    from auth.oauth_config import get_oauth_config

    config = get_oauth_config()

    # Check if OAuth 2.1 is enabled via centralized config
    oauth21_enabled = config.is_oauth21_enabled()

    if oauth21_enabled:
        if not config.is_configured():
            logger.warning("OAuth 2.1 enabled but OAuth credentials not configured")
            return

        def validate_and_derive_jwt_key(
            jwt_signing_key_override: str | None, client_secret: str
        ) -> bytes:
            """Validate JWT signing key override and derive the final JWT key."""
            if jwt_signing_key_override:
                if len(jwt_signing_key_override) < 12:
                    logger.warning(
                        "OAuth 2.1: FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY is less than 12 characters; "
                        "use a longer secret to improve key derivation strength."
                    )
                return derive_jwt_key(
                    low_entropy_material=jwt_signing_key_override,
                    salt="fastmcp-jwt-signing-key",
                )
            else:
                return derive_jwt_key(
                    high_entropy_material=client_secret,
                    salt="fastmcp-jwt-signing-key",
                )

        try:
            # Import common dependencies for storage backends
            from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
            from cryptography.fernet import Fernet
            from fastmcp.server.auth.jwt_issuer import derive_jwt_key

            required_scopes: List[str] = sorted(get_current_scopes())

            client_storage = None
            jwt_signing_key_override = (
                os.getenv("FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY", "").strip()
                or None
            )
            storage_backend = (
                os.getenv("WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND", "")
                .strip()
                .lower()
            )
            valkey_host = os.getenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST", "").strip()

            # Determine storage backend: valkey, disk, memory (default)
            use_valkey = storage_backend == "valkey" or bool(valkey_host)
            use_disk = storage_backend == "disk"

            if use_valkey:
                try:
                    from key_value.aio.stores.valkey import ValkeyStore

                    valkey_port_raw = os.getenv(
                        "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PORT", "6379"
                    ).strip()
                    valkey_db_raw = os.getenv(
                        "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_DB", "0"
                    ).strip()

                    valkey_port = int(valkey_port_raw)
                    valkey_db = int(valkey_db_raw)
                    valkey_use_tls_raw = os.getenv(
                        "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USE_TLS", ""
                    ).strip()
                    valkey_use_tls = (
                        _parse_bool_env(valkey_use_tls_raw)
                        if valkey_use_tls_raw
                        else valkey_port == 6380
                    )

                    valkey_request_timeout_ms_raw = os.getenv(
                        "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_REQUEST_TIMEOUT_MS", ""
                    ).strip()
                    valkey_connection_timeout_ms_raw = os.getenv(
                        "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_CONNECTION_TIMEOUT_MS", ""
                    ).strip()

                    valkey_request_timeout_ms = (
                        int(valkey_request_timeout_ms_raw)
                        if valkey_request_timeout_ms_raw
                        else None
                    )
                    valkey_connection_timeout_ms = (
                        int(valkey_connection_timeout_ms_raw)
                        if valkey_connection_timeout_ms_raw
                        else None
                    )

                    valkey_username = (
                        os.getenv(
                            "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USERNAME", ""
                        ).strip()
                        or None
                    )
                    valkey_password = (
                        os.getenv(
                            "WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PASSWORD", ""
                        ).strip()
                        or None
                    )

                    if not valkey_host:
                        valkey_host = "localhost"

                    client_storage = ValkeyStore(
                        host=valkey_host,
                        port=valkey_port,
                        db=valkey_db,
                        username=valkey_username,
                        password=valkey_password,
                    )

                    # Configure TLS and timeouts on the underlying Glide client config.
                    # ValkeyStore currently doesn't expose these settings directly.
                    glide_config = getattr(client_storage, "_client_config", None)
                    if glide_config is not None:
                        glide_config.use_tls = valkey_use_tls

                        is_remote_host = valkey_host not in {"localhost", "127.0.0.1"}
                        if valkey_request_timeout_ms is None and (
                            valkey_use_tls or is_remote_host
                        ):
                            # Glide defaults to 250ms if unset; increase for remote/TLS endpoints.
                            valkey_request_timeout_ms = 5000
                        if valkey_request_timeout_ms is not None:
                            glide_config.request_timeout = valkey_request_timeout_ms

                        if valkey_connection_timeout_ms is None and (
                            valkey_use_tls or is_remote_host
                        ):
                            valkey_connection_timeout_ms = 10000
                        if valkey_connection_timeout_ms is not None:
                            from glide_shared.config import (
                                AdvancedGlideClientConfiguration,
                            )

                            glide_config.advanced_config = (
                                AdvancedGlideClientConfiguration(
                                    connection_timeout=valkey_connection_timeout_ms
                                )
                            )

                    jwt_signing_key = validate_and_derive_jwt_key(
                        jwt_signing_key_override, config.client_secret
                    )

                    storage_encryption_key = derive_jwt_key(
                        high_entropy_material=jwt_signing_key.decode(),
                        salt="fastmcp-storage-encryption-key",
                    )

                    client_storage = FernetEncryptionWrapper(
                        key_value=client_storage,
                        fernet=Fernet(key=storage_encryption_key),
                    )
                    logger.info(
                        "OAuth 2.1: Using ValkeyStore for FastMCP OAuth proxy client_storage (host=%s, port=%s, db=%s, tls=%s)",
                        valkey_host,
                        valkey_port,
                        valkey_db,
                        valkey_use_tls,
                    )
                    if valkey_request_timeout_ms is not None:
                        logger.info(
                            "OAuth 2.1: Valkey request timeout set to %sms",
                            valkey_request_timeout_ms,
                        )
                    if valkey_connection_timeout_ms is not None:
                        logger.info(
                            "OAuth 2.1: Valkey connection timeout set to %sms",
                            valkey_connection_timeout_ms,
                        )
                    logger.info(
                        "OAuth 2.1: Applied Fernet encryption wrapper to Valkey client_storage (key derived from FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY or GOOGLE_OAUTH_CLIENT_SECRET)."
                    )
                except ImportError as exc:
                    logger.warning(
                        "OAuth 2.1: Valkey client_storage requested but Valkey dependencies are not installed (%s). "
                        "Install 'workspace-mcp[valkey]' (or 'py-key-value-aio[valkey]', which includes 'valkey-glide') "
                        "or unset WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND/WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST.",
                        exc,
                    )
                except ValueError as exc:
                    logger.warning(
                        "OAuth 2.1: Invalid Valkey configuration; falling back to default storage (%s).",
                        exc,
                    )
            elif use_disk:
                try:
                    from key_value.aio.stores.disk import DiskStore

                    disk_directory = os.getenv(
                        "WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY", ""
                    ).strip()
                    if not disk_directory:
                        # Default to FASTMCP_HOME/oauth-proxy or ~/.fastmcp/oauth-proxy
                        fastmcp_home = os.getenv("FASTMCP_HOME", "").strip()
                        if fastmcp_home:
                            disk_directory = os.path.join(fastmcp_home, "oauth-proxy")
                        else:
                            disk_directory = os.path.expanduser(
                                "~/.fastmcp/oauth-proxy"
                            )

                    client_storage = DiskStore(directory=disk_directory)

                    jwt_signing_key = validate_and_derive_jwt_key(
                        jwt_signing_key_override, config.client_secret
                    )

                    storage_encryption_key = derive_jwt_key(
                        high_entropy_material=jwt_signing_key.decode(),
                        salt="fastmcp-storage-encryption-key",
                    )

                    client_storage = FernetEncryptionWrapper(
                        key_value=client_storage,
                        fernet=Fernet(key=storage_encryption_key),
                    )
                    logger.info(
                        "OAuth 2.1: Using DiskStore for FastMCP OAuth proxy client_storage (directory=%s)",
                        disk_directory,
                    )
                except ImportError as exc:
                    logger.warning(
                        "OAuth 2.1: Disk storage requested but dependencies not available (%s). "
                        "Falling back to default storage.",
                        exc,
                    )
            elif storage_backend == "memory":
                from key_value.aio.stores.memory import MemoryStore

                client_storage = MemoryStore()
                logger.info(
                    "OAuth 2.1: Using MemoryStore for FastMCP OAuth proxy client_storage"
                )
            # else: client_storage remains None, FastMCP uses its default

            # Ensure JWT signing key is always derived for all storage backends
            if "jwt_signing_key" not in locals():
                jwt_signing_key = validate_and_derive_jwt_key(
                    jwt_signing_key_override, config.client_secret
                )

            # Check if external OAuth provider is configured
            if config.is_external_oauth21_provider():
                # External OAuth mode: use custom provider that handles ya29.* access tokens
                from auth.external_oauth_provider import ExternalOAuthProvider

                provider = ExternalOAuthProvider(
                    client_id=config.client_id,
                    client_secret=config.client_secret,
                    base_url=config.get_oauth_base_url(),
                    redirect_path=config.redirect_path,
                    required_scopes=required_scopes,
                    resource_server_url=config.get_oauth_base_url(),
                )
                server.auth = provider

                logger.info("OAuth 2.1 enabled with EXTERNAL provider mode")
                logger.info(
                    "Expecting Authorization bearer tokens in tool call headers"
                )
                logger.info(
                    "Protected resource metadata points to Google's authorization server"
                )
            else:
                # Standard OAuth 2.1 mode: use FastMCP's GoogleProvider
                provider = GoogleProvider(
                    client_id=config.client_id,
                    client_secret=config.client_secret,
                    base_url=config.get_oauth_base_url(),
                    redirect_path=config.redirect_path,
                    required_scopes=required_scopes,
                    client_storage=client_storage,
                    jwt_signing_key=jwt_signing_key,
                )
                # Enable protocol-level auth
                server.auth = provider
                logger.info(
                    "OAuth 2.1 enabled using FastMCP GoogleProvider with protocol-level auth"
                )

                # Explicitly mount well-known routes from the OAuth provider
                # These should be auto-mounted but we ensure they're available
                try:
                    well_known_routes = provider.get_well_known_routes()
                    for route in well_known_routes:
                        logger.info(f"Mounting OAuth well-known route: {route.path}")
                        server.custom_route(route.path, methods=list(route.methods))(
                            route.endpoint
                        )
                except Exception as e:
                    logger.warning(f"Could not mount well-known routes: {e}")

            # Always set auth provider for token validation in middleware
            set_auth_provider(provider)
            _auth_provider = provider
        except Exception as exc:
            logger.error(
                "Failed to initialize FastMCP GoogleProvider: %s", exc, exc_info=True
            )
            raise
    else:
        logger.info("OAuth 2.0 mode - Server will use legacy authentication.")
        server.auth = None
        _auth_provider = None
        set_auth_provider(None)
        _ensure_legacy_callback_route()


def get_auth_provider() -> Optional[GoogleProvider]:
    """Gets the global authentication provider instance."""
    return _auth_provider


@server.custom_route("/health", methods=["GET"])
async def health_check(request: Request):
    try:
        version = metadata.version("workspace-mcp")
    except metadata.PackageNotFoundError:
        version = "dev"
    return JSONResponse(
        {
            "status": "healthy",
            "service": "workspace-mcp",
            "version": version,
            "transport": get_transport_mode(),
        }
    )


@server.custom_route("/attachments/{file_id}", methods=["GET"])
async def serve_attachment(file_id: str):
    """Serve a stored attachment file."""
    from core.attachment_storage import get_attachment_storage

    storage = get_attachment_storage()
    metadata = storage.get_attachment_metadata(file_id)

    if not metadata:
        return JSONResponse(
            {"error": "Attachment not found or expired"}, status_code=404
        )

    file_path = storage.get_attachment_path(file_id)
    if not file_path:
        return JSONResponse({"error": "Attachment file not found"}, status_code=404)

    return FileResponse(
        path=str(file_path),
        filename=metadata["filename"],
        media_type=metadata["mime_type"],
    )


async def legacy_oauth2_callback(request: Request) -> HTMLResponse:
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        msg = (
            f"Authentication failed: Google returned an error: {error}. State: {state}."
        )
        logger.error(msg)
        return create_error_response(msg)

    if not code:
        msg = "Authentication failed: No authorization code received from Google."
        logger.error(msg)
        return create_error_response(msg)

    try:
        error_message = check_client_secrets()
        if error_message:
            return create_server_error_response(error_message)

        logger.info(f"OAuth callback: Received code (state: {state}).")

        mcp_session_id = None
        if hasattr(request, "state") and hasattr(request.state, "session_id"):
            mcp_session_id = request.state.session_id

        verified_user_id, credentials = handle_auth_callback(
            scopes=get_current_scopes(),
            authorization_response=str(request.url),
            redirect_uri=get_oauth_redirect_uri_for_current_mode(),
            session_id=mcp_session_id,
        )

        logger.info(
            f"OAuth callback: Successfully authenticated user: {verified_user_id}."
        )

        try:
            store = get_oauth21_session_store()

            store.store_session(
                user_email=verified_user_id,
                access_token=credentials.token,
                refresh_token=credentials.refresh_token,
                token_uri=credentials.token_uri,
                client_id=credentials.client_id,
                client_secret=credentials.client_secret,
                scopes=credentials.scopes,
                expiry=credentials.expiry,
                session_id=f"google-{state}",
                mcp_session_id=mcp_session_id,
            )
            logger.info(
                f"Stored Google credentials in OAuth 2.1 session store for {verified_user_id}"
            )
        except Exception as e:
            logger.error(f"Failed to store credentials in OAuth 2.1 store: {e}")

        return create_success_response(verified_user_id)
    except Exception as e:
        logger.error(f"Error processing OAuth callback: {str(e)}", exc_info=True)
        return create_server_error_response(str(e))


@server.tool()
async def start_google_auth(
    service_name: str, user_google_email: str = USER_GOOGLE_EMAIL
) -> str:
    """
    Manually initiate Google OAuth authentication flow.

    NOTE: This is a legacy OAuth 2.0 tool and is disabled when OAuth 2.1 is enabled.
    The authentication system automatically handles credential checks and prompts for
    authentication when needed. Only use this tool if:
    1. You need to re-authenticate with different credentials
    2. You want to proactively authenticate before using other tools
    3. The automatic authentication flow failed and you need to retry

    In most cases, simply try calling the Google Workspace tool you need - it will
    automatically handle authentication if required.
    """
    if is_oauth21_enabled():
        if is_external_oauth21_provider():
            return (
                "start_google_auth is disabled when OAuth 2.1 is enabled. "
                "Provide a valid OAuth 2.1 bearer token in the Authorization header "
                "and retry the original tool."
            )
        return (
            "start_google_auth is disabled when OAuth 2.1 is enabled. "
            "Authenticate through your MCP client's OAuth 2.1 flow and retry the "
            "original tool."
        )

    if not user_google_email:
        raise ValueError("user_google_email must be provided.")

    error_message = check_client_secrets()
    if error_message:
        return f"**Authentication Error:** {error_message}"

    try:
        auth_message = await start_auth_flow(
            user_google_email=user_google_email,
            service_name=service_name,
            redirect_uri=get_oauth_redirect_uri_for_current_mode(),
        )
        return auth_message
    except Exception as e:
        logger.error(f"Failed to start Google authentication flow: {e}", exc_info=True)
        return f"**Error:** An unexpected error occurred: {e}"
