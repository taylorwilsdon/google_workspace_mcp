"""
Transport-aware OAuth callback handling.

In streamable-http mode: Uses the existing FastAPI server
In stdio mode: Starts a minimal HTTP server just for OAuth callbacks
"""

import asyncio
import errno
import logging
import os
import threading
import time
import socket
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from auth.scopes import SCOPES, get_current_scopes  # noqa
from auth.oauth_responses import (
    create_error_response,
    create_success_response,
    create_server_error_response,
)
from auth.google_auth import handle_auth_callback, check_client_secrets
from auth.oauth_config import (
    get_oauth_redirect_uri,
    get_oauth_config,
    get_transport_mode,
    reload_oauth_config,
)

logger = logging.getLogger(__name__)

# How long an ephemeral stdio callback listener stays bound waiting for the
# browser redirect before it gives up and frees the port (abandoned auth flow).
EPHEMERAL_CALLBACK_TIMEOUT_SECONDS = 300


# Snapshot of whether the operator pinned the callback port, captured from the
# launch environment at import time. This must be read BEFORE any ephemeral
# acquisition mutates WORKSPACE_MCP_PORT, otherwise the second auth flow would
# mistake our own ephemeral value for an operator pin.
_OPERATOR_PINNED_CALLBACK_PORT = (
    "WORKSPACE_MCP_PORT" in os.environ or "PORT" in os.environ
)


def _callback_port_is_pinned() -> bool:
    """Return True when the operator explicitly pinned the callback port.

    A pinned port (``WORKSPACE_MCP_PORT`` or ``PORT`` present in the launch
    environment) is required for Web/confidential OAuth clients, whose redirect
    URI must exactly match a registered port. When neither is set, stdio mode
    uses an ephemeral port (Desktop/PKCE clients accept any loopback port).
    """
    return _OPERATOR_PINNED_CALLBACK_PORT


def _acquire_ephemeral_port(host: str) -> tuple[socket.socket, int]:
    """Bind a fresh OS-assigned port and return the (still-open) socket + port.

    Binding to port 0 lets the kernel hand back a guaranteed-free port with no
    pick-then-check race. The socket is returned still bound so it can be handed
    directly to uvicorn (``serve(sockets=[sock])``), eliminating any window in
    which another process could steal the port between selection and listen.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
    except OSError:
        sock.close()
        raise
    return sock, sock.getsockname()[1]


class MinimalOAuthServer:
    """
    Minimal HTTP server for OAuth callbacks in stdio mode.
    Only starts when needed and uses the same port (8000) as streamable-http mode.
    """

    def __init__(
        self,
        port: int = 8000,
        base_uri: str = "http://localhost",
        *,
        prebound_socket: Optional[socket.socket] = None,
        ephemeral: bool = False,
        idle_timeout: Optional[int] = None,
    ):
        self.port = port
        self.base_uri = base_uri
        self.app = FastAPI()
        self.server = None
        self.server_thread = None
        self.is_running = False
        self._reusing_external_listener = False
        # Ephemeral (stdio Desktop-client) listeners bind a throwaway port and
        # release it the moment the callback resolves; a watchdog frees it if the
        # browser flow is abandoned. A pre-bound socket is handed to uvicorn so
        # the port chosen at acquisition is the exact one served (no TOCTOU).
        self._prebound_socket = prebound_socket
        self._ephemeral = ephemeral
        self._idle_timeout = idle_timeout
        self._shutdown_requested = False
        self._watchdog = None

        # Setup the callback route
        self._setup_callback_route()
        # Setup attachment serving route
        self._setup_attachment_route()

    def _setup_callback_route(self):
        """Setup the OAuth callback route."""

        @self.app.get("/oauth2callback")
        async def oauth_callback(request: Request):
            """Handle OAuth callback - same logic as in core/server.py"""
            try:
                return await self._resolve_callback(request)
            finally:
                # The callback has resolved (success or error) — an ephemeral
                # listener has served its single purpose, so free the port.
                if self._ephemeral:
                    self._schedule_shutdown()

    async def _resolve_callback(self, request: Request):
        """Exchange the authorization code and render the result page."""
        code = request.query_params.get("code")
        error = request.query_params.get("error")

        if error:
            error_message = f"Authentication failed: Google returned an error: {error}."
            logger.error(error_message)
            return create_error_response(error_message)

        if not code:
            error_message = (
                "Authentication failed: No authorization code received from Google."
            )
            logger.error(error_message)
            return create_error_response(error_message)

        try:
            # Check if we have credentials available (environment variables or file)
            error_message = check_client_secrets()
            if error_message:
                return create_server_error_response(error_message)

            logger.info(
                "OAuth callback: Received authorization code. Attempting to exchange for tokens."
            )

            # Session ID tracking removed - not needed

            # Exchange code for credentials
            redirect_uri = get_oauth_redirect_uri()
            verified_user_id, credentials = await handle_auth_callback(
                scopes=get_current_scopes(),
                authorization_response=str(request.url),
                redirect_uri=redirect_uri,
                session_id=None,
                allow_missing_state_fallback=os.getenv("MCP_SINGLE_USER_MODE") == "1",
            )

            logger.info(
                f"OAuth callback: Successfully authenticated user: {verified_user_id}."
            )

            # Return success page using shared template
            return create_success_response(verified_user_id)

        except Exception as e:
            error_message_detail = f"Error processing OAuth callback: {str(e)}"
            logger.error(error_message_detail, exc_info=True)
            return create_server_error_response(str(e))

    def _schedule_shutdown(self) -> None:
        """Signal the running uvicorn server to exit so the port is released.

        Safe to call from within a request handler: uvicorn finishes the
        in-flight response before honouring ``should_exit``. Idempotent.
        """
        self._shutdown_requested = True
        if self._watchdog is not None:
            self._watchdog.cancel()
            self._watchdog = None
        if self.server is not None:
            self.server.should_exit = True

    def _arm_watchdog(self) -> None:
        """Start a timer that frees the port if the auth flow is abandoned."""
        timeout = self._idle_timeout or EPHEMERAL_CALLBACK_TIMEOUT_SECONDS
        self._watchdog = threading.Timer(timeout, self._schedule_shutdown)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _setup_attachment_route(self):
        """Setup the attachment serving route."""
        from core.attachment_storage import get_attachment_storage

        @self.app.get("/attachments/{file_id}")
        async def serve_attachment(file_id: str, request: Request):
            """Serve a stored attachment file."""
            storage = get_attachment_storage()
            metadata = storage.get_attachment_metadata(file_id)

            if not metadata:
                return JSONResponse(
                    {"error": "Attachment not found or expired"}, status_code=404
                )

            file_path = storage.get_attachment_path(file_id)
            if not file_path:
                return JSONResponse(
                    {"error": "Attachment file not found"}, status_code=404
                )

            return FileResponse(
                path=str(file_path),
                filename=metadata["filename"],
                media_type=metadata["mime_type"],
            )

    def is_actually_running(self) -> bool:
        """
        Check whether *this* server instance owns the callback port.

        Returns False immediately if we have never called ``start()`` — a foreign
        process listening on the same port must not be mistaken for our OAuth server.
        Only after we have successfully started do we probe the port to confirm the
        server thread is still alive and responding.
        """
        # We never started — don't probe the port at all.  A foreign listener (e.g.
        # another local web server) would make a raw TCP connect_ex succeed and cause
        # is_actually_running() to return True, silently skipping OAuth setup.
        if not self.is_running and self.server_thread is None:
            return False

        if self.server_thread and not self.server_thread.is_alive():
            return False
        try:
            parsed_uri = urlparse(self.base_uri)
            hostname = parsed_uri.hostname or "localhost"
        except Exception:
            hostname = "localhost"
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                if s.connect_ex((hostname, self.port)) == 0:
                    return True
        except Exception:
            return False

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((hostname, self.port))
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                return True
            return False
        except Exception:
            return False

        return False

    def matches_endpoint(self, port: int, base_uri: str) -> bool:
        """Return True when this server instance matches the requested callback endpoint."""
        if self.port != port:
            return False
        self_parsed = urlparse(self.base_uri)
        other_parsed = urlparse(base_uri)
        if self_parsed.scheme.lower() != other_parsed.scheme.lower():
            return False
        if (self_parsed.hostname or "").lower() != (
            other_parsed.hostname or ""
        ).lower():
            return False
        default_port = 443 if self_parsed.scheme.lower() == "https" else 80
        self_port = self_parsed.port or default_port
        other_port = other_parsed.port or default_port
        if self_port != other_port:
            return False
        self_path = self_parsed.path.rstrip("/") or ""
        other_path = other_parsed.path.rstrip("/") or ""
        return self_path == other_path

    def _callback_endpoint_looks_like_workspace(self, hostname: str) -> bool:
        """
        Probe an occupied callback port for this app's OAuth callback handler.

        This is intentionally only used after bind returns EADDRINUSE in stdio
        mode. A no-code request to our handler returns the shared
        "Authentication Error" page with HTTP 400, which is enough to distinguish
        the boot-time workspace callback listener from a random local process.
        """
        try:
            parsed_uri = urlparse(self.base_uri)
            scheme = parsed_uri.scheme or "http"
            if scheme not in {"http", "https"}:
                return False
            probe_host = hostname
            if probe_host in {"0.0.0.0", "::"}:
                probe_host = "127.0.0.1"
            if ":" in probe_host and not probe_host.startswith("["):
                probe_host = f"[{probe_host}]"
            base_path = parsed_uri.path.rstrip("/")
            probe_url = f"{scheme}://{probe_host}:{self.port}{base_path}/oauth2callback"

            try:
                with urlopen(probe_url, timeout=1.0) as response:
                    status_code = response.status
                    body = response.read(4096).decode("utf-8", errors="replace")
            except HTTPError as exc:
                status_code = exc.code
                body = exc.read(4096).decode("utf-8", errors="replace")

            return status_code in {200, 400} and (
                "Authentication Error" in body or "Authentication Successful" in body
            )
        except (OSError, URLError, ValueError):
            return False

    def start(self) -> tuple[bool, str]:
        """
        Start the minimal OAuth server.

        Returns:
            Tuple of (success: bool, error_message: str)
        """
        if self.is_running:
            if self.is_actually_running():
                logger.info("Minimal OAuth server is already running")
                return True, ""
            else:
                logger.warning(
                    "Minimal OAuth server was marked running but port is not responding. Restarting."
                )
                self.stop()
                self.server = None
                self.server_thread = None

        # Extract hostname from base_uri (e.g., "http://localhost" -> "localhost")
        try:
            parsed_uri = urlparse(self.base_uri)
            hostname = parsed_uri.hostname or "localhost"
        except Exception:
            hostname = "localhost"

        # A pre-bound (ephemeral) socket already owns the port, so skip the
        # availability probe and hand the socket straight to uvicorn.
        if self._prebound_socket is None:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind((hostname, self.port))
            except OSError as exc:
                if (
                    exc.errno == errno.EADDRINUSE
                    and self._callback_endpoint_looks_like_workspace(hostname)
                ):
                    logger.info(
                        "OAuth callback server already available on %s:%s; reusing existing listener",
                        hostname,
                        self.port,
                    )
                    self.is_running = True
                    self._reusing_external_listener = True
                    return True, ""
                error_msg = f"Port {self.port} is already in use on {hostname}. Cannot start minimal OAuth server."
                logger.error(error_msg)
                return False, error_msg

        self._reusing_external_listener = False

        def run_server():
            """Run the server in a separate thread."""
            try:
                config_kwargs = dict(
                    host=hostname,
                    port=self.port,
                    log_level="warning",
                    access_log=False,
                    # Match FastMCP's own uvicorn config: uvicorn's "auto"
                    # default resolves to the deprecated legacy-websockets
                    # implementation whenever `websockets` is installed.
                    ws="websockets-sansio",
                )
                if self._ephemeral:
                    # Cap graceful shutdown so a lingering keep-alive connection
                    # (e.g. the browser holding the success page open) cannot pin
                    # the ephemeral port after should_exit is signalled.
                    config_kwargs["timeout_graceful_shutdown"] = 5
                config = uvicorn.Config(self.app, **config_kwargs)
                self.server = uvicorn.Server(config)
                if self._prebound_socket is not None:
                    # Shutdown signalled via should_exit is polled between
                    # requests; serving the pre-bound socket avoids any rebind.
                    if self._shutdown_requested:
                        self.server.should_exit = True
                    asyncio.run(self.server.serve(sockets=[self._prebound_socket]))
                else:
                    asyncio.run(self.server.serve())

            except Exception as e:
                logger.error(f"Minimal OAuth server error: {e}", exc_info=True)
                self.is_running = False

        # Start server in background thread
        self.server_thread = threading.Thread(target=run_server, daemon=True)
        self.server_thread.start()

        # Wait for server to start
        max_wait = 3.0
        start_time = time.time()
        while time.time() - start_time < max_wait:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    result = s.connect_ex((hostname, self.port))
                    if result == 0:
                        self.is_running = True
                        logger.info(
                            f"Minimal OAuth server started on {hostname}:{self.port}"
                        )
                        if self._ephemeral:
                            self._arm_watchdog()
                        return True, ""
            except Exception:
                pass
            time.sleep(0.1)

        error_msg = f"Failed to start minimal OAuth server on {hostname}:{self.port} - server did not respond within {max_wait}s"
        logger.error(error_msg)
        return False, error_msg

    def stop(self):
        """Stop the minimal OAuth server."""
        if not self.is_running:
            return

        if self._reusing_external_listener:
            self.is_running = False
            self._reusing_external_listener = False
            logger.info("Minimal OAuth server external listener reuse released")
            return

        try:
            if self.server:
                if hasattr(self.server, "should_exit"):
                    self.server.should_exit = True

            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=3.0)

            self.is_running = False
            self._reusing_external_listener = False
            logger.info("Minimal OAuth server stopped")

        except Exception as e:
            logger.error(f"Error stopping minimal OAuth server: {e}", exc_info=True)


# Global instance for stdio mode
_minimal_oauth_server: Optional[MinimalOAuthServer] = None
_minimal_oauth_server_lock = threading.Lock()
# Serializes the ephemeral acquire-port → publish-env → reload-config → start
# handoff, which mutates process-global state and must not interleave.
_stdio_ephemeral_callback_lock = threading.Lock()


def ensure_oauth_callback_available(
    transport_mode: str = "stdio",
    port: int = 8000,
    base_uri: str = "http://localhost",
    *,
    prebound_socket: Optional[socket.socket] = None,
    ephemeral: bool = False,
    idle_timeout: Optional[int] = None,
) -> tuple[bool, str]:
    """
    Ensure OAuth callback endpoint is available for the given transport mode.

    For streamable-http: Assumes the main server is already running
    For stdio: Starts a minimal server if needed

    Args:
        transport_mode: "stdio" or "streamable-http"
        port: Port number (default 8000)
        base_uri: Base URI (default "http://localhost")
        prebound_socket: Pre-bound listening socket to hand to uvicorn (ephemeral).
        ephemeral: When True, release the port on callback resolution / timeout.
        idle_timeout: Watchdog timeout (seconds) for an abandoned ephemeral flow.

    Returns:
        Tuple of (success: bool, error_message: str)
    """
    global _minimal_oauth_server

    if transport_mode == "streamable-http":
        # In streamable-http mode, the main FastAPI server should handle callbacks
        logger.debug(
            "Using existing FastAPI server for OAuth callbacks (streamable-http mode)"
        )
        return True, ""

    elif transport_mode == "stdio":
        with _minimal_oauth_server_lock:
            # In stdio mode, start or refresh the minimal callback server as needed.
            if _minimal_oauth_server and not _minimal_oauth_server.matches_endpoint(
                port, base_uri
            ):
                logger.info(
                    "OAuth callback endpoint changed from %s:%s to %s:%s; recreating minimal OAuth server",
                    _minimal_oauth_server.base_uri,
                    _minimal_oauth_server.port,
                    base_uri,
                    port,
                )
                _minimal_oauth_server.stop()
                _minimal_oauth_server = None

            # Ephemeral flows hand in a fresh prebound socket each time. Never
            # reuse a prior instance even if the OS reassigns the same port —
            # its socket/shutdown state is stale and the new socket would leak.
            if ephemeral and _minimal_oauth_server is not None:
                _minimal_oauth_server.stop()
                _minimal_oauth_server = None

            if _minimal_oauth_server is None:
                logger.info(
                    f"Creating minimal OAuth server instance for {base_uri}:{port}"
                )
                if ephemeral:
                    _minimal_oauth_server = MinimalOAuthServer(
                        port,
                        base_uri,
                        prebound_socket=prebound_socket,
                        ephemeral=True,
                        idle_timeout=idle_timeout,
                    )
                else:
                    _minimal_oauth_server = MinimalOAuthServer(port, base_uri)

            if not _minimal_oauth_server.is_actually_running():
                logger.info("Starting minimal OAuth server for stdio mode")
                success, error_msg = _minimal_oauth_server.start()
                if success:
                    logger.info(
                        f"Minimal OAuth server successfully started on {base_uri}:{port}"
                    )
                    return True, ""
                else:
                    logger.error(
                        f"Failed to start minimal OAuth server on {base_uri}:{port}: {error_msg}"
                    )
                    return False, error_msg
            else:
                logger.info("Minimal OAuth server is already running")
                return True, ""

    else:
        error_msg = f"Unknown transport mode: {transport_mode}"
        logger.error(error_msg)
        return False, error_msg


def ensure_stdio_oauth_callback_available() -> tuple[bool, str]:
    """
    Lazily start the stdio OAuth-callback / attachment listener on demand.

    Binding the callback port is deferred until it is actually needed — an auth
    flow starting or an attachment URL being handed out — so short-lived stdio
    spawns that do neither never occupy a port in the fallback range (issue #832).

    No-op (returns success) outside stdio transport, where the main HTTP server
    already serves these routes. Uses the active OAuth config so the listener
    binds the same port the redirect and attachment URLs are composed from.

    When the port is not explicitly pinned (the documented Desktop/PKCE client
    case, where Google accepts any loopback port), a fresh ephemeral port is
    bound per flow and released on callback resolution — so concurrent stdio
    sessions never contend for a single fixed port. An explicitly-pinned
    ``WORKSPACE_MCP_PORT``/``PORT`` (Web/confidential clients, whose redirect URI
    must match a registered port) keeps the legacy fixed-port behavior.

    Concurrency semantics (ephemeral path):

    * Inter-session: each stdio process binds its own OS-assigned port (``bind``
      to port 0), so separate sessions get distinct ports and never collide. A
      session that abandons a flow holds its port only until its own watchdog
      reclaims it; it cannot block another session.
    * Intra-session: there is a single module-global listener
      (``_minimal_oauth_server``). Re-triggering auth before the prior flow's
      timeout acquires a new ephemeral port; because the endpoint port differs,
      :func:`ensure_oauth_callback_available` stops the old listener (freeing its
      port), starts the new one, and arms a fresh watchdog. The old port is not
      reused, its timer is not reset in place, and the two listeners never run in
      parallel — the superseded flow is abandoned in favor of the new URL.
    """
    if get_transport_mode() != "stdio":
        return True, ""

    if _callback_port_is_pinned():
        config = get_oauth_config()
        return ensure_oauth_callback_available("stdio", config.port, config.base_uri)

    # Ephemeral path: bind a free port now and publish it so the redirect URI
    # (composed by the caller right after this returns) and the callback listener
    # agree on the same port. The whole acquire→publish→reload→start handoff is
    # serialized so concurrent callers can't pair one socket with another's port.
    with _stdio_ephemeral_callback_lock:
        host = urlparse(get_oauth_config().base_uri).hostname or "localhost"
        try:
            sock, port = _acquire_ephemeral_port(host)
        except OSError as exc:
            return False, f"Could not acquire an ephemeral OAuth callback port: {exc}"

        os.environ["WORKSPACE_MCP_PORT"] = str(port)
        os.environ["WORKSPACE_MCP_RESOLVED_PORT"] = "1"
        reload_oauth_config()
        config = get_oauth_config()
        try:
            success, error = ensure_oauth_callback_available(
                "stdio",
                config.port,
                config.base_uri,
                prebound_socket=sock,
                ephemeral=True,
                idle_timeout=EPHEMERAL_CALLBACK_TIMEOUT_SECONDS,
            )
            if not success:
                sock.close()
            return success, error
        except Exception:
            sock.close()
            raise


def cleanup_oauth_callback_server():
    """Clean up the minimal OAuth server if it was started."""
    global _minimal_oauth_server
    with _minimal_oauth_server_lock:
        if _minimal_oauth_server:
            _minimal_oauth_server.stop()
            _minimal_oauth_server = None
