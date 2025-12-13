"""
Transport-aware OAuth callback handling.

In streamable-http mode: Uses the existing FastAPI server
In stdio mode: Starts a minimal HTTP server just for OAuth callbacks
"""

import asyncio
import logging
import threading
import time
import socket
import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional
from urllib.parse import urlparse

from auth.scopes import SCOPES, get_current_scopes  # noqa
from auth.oauth_responses import (
    create_error_response,
    create_success_response,
    create_server_error_response,
)
from auth.google_auth import handle_auth_callback, check_client_secrets
from auth.oauth_config import get_oauth_redirect_uri

logger = logging.getLogger(__name__)


class MinimalOAuthServer:
    """
    Minimal HTTP server for OAuth callbacks in stdio mode.
    Only starts when needed and uses the same port (8000) as streamable-http mode.
    """

    def __init__(self, port: int = 8000, base_uri: str = "http://localhost"):
        self.port = port
        self.base_uri = base_uri
        self.app = FastAPI()
        self.server = None
        self.server_thread = None
        self.is_running = False

        # Setup the callback route
        self._setup_callback_route()
        # Setup attachment serving route
        self._setup_attachment_route()

    def _setup_callback_route(self):
        """Setup the OAuth callback route."""

        @self.app.get("/oauth2callback")
        async def oauth_callback(request: Request):
            """Handle OAuth callback - same logic as in core/server.py"""
            state = request.query_params.get("state")
            code = request.query_params.get("code")
            error = request.query_params.get("error")

            if error:
                error_message = f"Authentication failed: Google returned an error: {error}. State: {state}."
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
                    f"OAuth callback: Received code (state: {state}). Attempting to exchange for tokens."
                )

                # Session ID tracking removed - not needed

                # Exchange code for credentials
                redirect_uri = get_oauth_redirect_uri()
                verified_user_id, credentials = handle_auth_callback(
                    scopes=get_current_scopes(),
                    authorization_response=str(request.url),
                    redirect_uri=redirect_uri,
                    session_id=None,
                )

                logger.info(
                    f"OAuth callback: Successfully authenticated user: {verified_user_id} (state: {state})."
                )

                # Return success page using shared template
                return create_success_response(verified_user_id)

            except Exception as e:
                error_message_detail = (
                    f"Error processing OAuth callback (state: {state}): {str(e)}"
                )
                logger.error(error_message_detail, exc_info=True)
                return create_server_error_response(str(e))

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

    def start(self) -> tuple[bool, str]:
        """
        Start the minimal OAuth server.

        Returns:
            Tuple of (success: bool, error_message: str)
        """
        if self.is_running:
            logger.info("Minimal OAuth server is already running")
            return True, ""

        # Check if port is available
        # Extract hostname from base_uri (e.g., "http://localhost" -> "localhost")
        try:
            parsed_uri = urlparse(self.base_uri)
            hostname = parsed_uri.hostname or "localhost"
        except Exception:
            hostname = "localhost"

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((hostname, self.port))
        except OSError:
            error_msg = f"Port {self.port} is already in use on {hostname}. Cannot start minimal OAuth server."
            logger.error(error_msg)
            return False, error_msg

        def run_server():
            """Run the server in a separate thread."""
            try:
                config = uvicorn.Config(
                    self.app,
                    host=hostname,
                    port=self.port,
                    log_level="warning",
                    access_log=False,
                )
                self.server = uvicorn.Server(config)
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

        try:
            if self.server:
                if hasattr(self.server, "should_exit"):
                    self.server.should_exit = True

            if self.server_thread and self.server_thread.is_alive():
                self.server_thread.join(timeout=3.0)

            self.is_running = False
            logger.info("Minimal OAuth server stopped")

        except Exception as e:
            logger.error(f"Error stopping minimal OAuth server: {e}", exc_info=True)


# Global instance for stdio mode
_minimal_oauth_server: Optional[MinimalOAuthServer] = None


def ensure_oauth_callback_available(
    transport_mode: str = "stdio", port: int = 8000, base_uri: str = "http://localhost"
) -> tuple[bool, str]:
    """
    Ensure OAuth callback endpoint is available for the given transport mode.

    For streamable-http: Assumes the main server is already running
    For stdio: Starts a minimal server if needed

    Args:
        transport_mode: "stdio" or "streamable-http"
        port: Port number (default 8000)
        base_uri: Base URI (default "http://localhost")

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
        # In stdio mode, start minimal server if not already running
        if _minimal_oauth_server is None:
            logger.info(f"Creating minimal OAuth server instance for {base_uri}:{port}")
            _minimal_oauth_server = MinimalOAuthServer(port, base_uri)

        if not _minimal_oauth_server.is_running:
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


def cleanup_oauth_callback_server():
    """Clean up the minimal OAuth server if it was started."""
    global _minimal_oauth_server
    if _minimal_oauth_server:
        _minimal_oauth_server.stop()
        _minimal_oauth_server = None
