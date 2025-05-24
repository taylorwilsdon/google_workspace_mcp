import logging
import os
import threading
from typing import Dict, Any, Optional

from fastapi import Request, Header
from fastapi.responses import HTMLResponse

from mcp import types

from mcp.server.fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from starlette.requests import Request
from starlette.applications import Starlette

# Import our custom StreamableHTTP session manager
from core.streamable_http import SessionAwareStreamableHTTPManager, create_starlette_app

from google.auth.exceptions import RefreshError
from auth.google_auth import handle_auth_callback, start_auth_flow, CONFIG_CLIENT_SECRETS_PATH

# Import shared configuration
from config.google_config import (
    OAUTH_STATE_TO_SESSION_ID_MAP,
    USERINFO_EMAIL_SCOPE,
    OPENID_SCOPE,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_FILE_SCOPE,
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    BASE_SCOPES,
    CALENDAR_SCOPES,
    DRIVE_SCOPES,
    GMAIL_SCOPES,
    DOCS_READONLY_SCOPE,
    DOCS_WRITE_SCOPE,
    SCOPES
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

WORKSPACE_MCP_PORT = int(os.getenv("WORKSPACE_MCP_PORT", 8000))
WORKSPACE_MCP_BASE_URI = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")

# Basic MCP server instance
server = FastMCP(
    name="google_workspace",
    server_url=f"{WORKSPACE_MCP_BASE_URI}:{WORKSPACE_MCP_PORT}/gworkspace",  # Add absolute URL for Gemini native function calling
    host="0.0.0.0",      # Listen on all interfaces
    port=WORKSPACE_MCP_PORT,   # Default port for HTTP server
    stateless_http=False # Enable stateful sessions (default)
)

# Container for session manager
class SessionManagerContainer:
    """
    Thread-safe container for the session manager instance.

    This encapsulates the session manager to improve testability and thread safety
    by avoiding direct global variable access.
    """

    def __init__(self):
        self._session_manager = None
        self._lock = threading.Lock()

    def set_session_manager(self, manager) -> None:
        """Set the session manager instance in a thread-safe manner."""
        with self._lock:
            self._session_manager = manager

    def get_session_manager(self):
        """Get the current session manager instance in a thread-safe manner."""
        with self._lock:
            return self._session_manager

session_manager_container = SessionManagerContainer()

def get_session_manager():
    """
    Get the current session manager instance.

    Returns:
        The session manager instance if initialized, None otherwise
    """
    return session_manager_container.get_session_manager()

def log_all_active_sessions():
    """
    Log information about all active sessions for debugging purposes.
    """
    session_manager = session_manager_container.get_session_manager()
    if session_manager is None:
        logger.debug("Cannot log sessions: session_manager is not initialized")
        return

    active_sessions = session_manager.get_active_sessions()
    session_count = len(active_sessions)

    logger.debug(f"Active sessions: {session_count}")
    for session_id, info in active_sessions.items():
        logger.debug(f"Session ID: {session_id}, Created: {info.get('created_at')}, Last Active: {info.get('last_active')}")

def create_application(base_path="/gworkspace") -> Starlette:
    """
    Create a Starlette application with the MCP server and session manager.

    Args:
        base_path: The base path to mount the MCP server at

    Returns:
        A Starlette application
    """
    logger.info(f"Creating Starlette application with MCP server mounted at {base_path}")
    app, manager = create_starlette_app(server._mcp_server, base_path)
    session_manager_container.set_session_manager(manager)

    # Add the OAuth callback route to the Starlette application
    from starlette.routing import Route

    # Add the OAuth callback route
    app.routes.append(
        Route("/oauth2callback", endpoint=oauth2_callback, methods=["GET"])
    )

    return app

# Configure OAuth redirect URI to use the MCP server's base uri and port
OAUTH_REDIRECT_URI = f"{WORKSPACE_MCP_BASE_URI}:{WORKSPACE_MCP_PORT}/oauth2callback"

# Register OAuth callback as a custom route
@server.custom_route("/oauth2callback", methods=["GET"])
async def oauth2_callback(request: Request) -> HTMLResponse:
    """
    Handle OAuth2 callback from Google via a custom route.
    This endpoint exchanges the authorization code for credentials and saves them.
    It then displays a success or error page to the user.
    """
    # State is used by google-auth-library for CSRF protection and should be present.
    # We don't need to track it ourselves in this simplified flow.
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        error_message = f"Authentication failed: Google returned an error: {error}. State: {state}."
        logger.error(error_message)
        return HTMLResponse(content=f"""
            <html><head><title>Authentication Error</title></head>
            <body><h2>Authentication Error</h2><p>{error_message}</p>
            <p>Please ensure you grant the requested permissions. You can close this window and try again.</p></body></html>
        """, status_code=400)

    if not code:
        error_message = "Authentication failed: No authorization code received from Google."
        logger.error(error_message)
        return HTMLResponse(content=f"""
            <html><head><title>Authentication Error</title></head>
            <body><h2>Authentication Error</h2><p>{error_message}</p><p>You can close this window and try again.</p></body></html>
        """, status_code=400)

    try:
        # Use the centralized CONFIG_CLIENT_SECRETS_PATH
        client_secrets_path = CONFIG_CLIENT_SECRETS_PATH
        if not os.path.exists(client_secrets_path):
            logger.error(f"OAuth client secrets file not found at {client_secrets_path}")
            # This is a server configuration error, should not happen in a deployed environment.
            return HTMLResponse(content="Server Configuration Error: Client secrets not found.", status_code=500)

        logger.info(f"OAuth callback: Received code (state: {state}). Attempting to exchange for tokens.")

        mcp_session_id: Optional[str] = OAUTH_STATE_TO_SESSION_ID_MAP.pop(state, None)
        if mcp_session_id:
            logger.info(f"OAuth callback: Retrieved MCP session ID '{mcp_session_id}' for state '{state}'.")
        else:
            logger.warning(f"OAuth callback: No MCP session ID found for state '{state}'. Auth will not be tied to a specific session directly via this callback.")

        # Exchange code for credentials. handle_auth_callback will save them.
        # The user_id returned here is the Google-verified email.
        verified_user_id, credentials = handle_auth_callback(
            client_secrets_path=client_secrets_path,
            scopes=SCOPES, # Ensure all necessary scopes are requested
            authorization_response=str(request.url),
            redirect_uri=OAUTH_REDIRECT_URI,
            session_id=mcp_session_id # Pass session_id if available
        )

        log_session_part = f" (linked to session: {mcp_session_id})" if mcp_session_id else ""
        logger.info(f"OAuth callback: Successfully authenticated user: {verified_user_id} (state: {state}){log_session_part}.")

        # Return a more informative success page
        success_page_content = f"""
            <html>
            <head>
                <title>Authentication Successful</title>
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; text-align: center; color: #333; border: 1px solid #ddd; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                    .status {{ color: #4CAF50; font-size: 24px; margin-bottom: 15px; }}
                    .message {{ margin-bottom: 20px; line-height: 1.6; }}
                    .user-id {{ font-weight: bold; color: #2a2a2a; }}
                    .button {{ background-color: #4CAF50; color: white; padding: 12px 25px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; text-decoration: none; display: inline-block; margin-top: 10px; }}
                    .note {{ font-size: 0.9em; color: #555; margin-top: 25px; }}
                </style>
                <script> setTimeout(function() {{ window.close(); }}, 10000); </script>
            </head>
            <body>
                <div class="status">✅ Authentication Successful</div>
                <div class="message">
                    You have successfully authenticated as <span class="user-id">{verified_user_id}</span>.
                    Credentials have been saved.
                </div>
                <div class="message">
                    You can now close this window and **retry your original command** in the application.
                </div>
                <button class="button" onclick="window.close()">Close Window</button>
            </body>
            </html>
        """
        return HTMLResponse(content=success_page_content)

    except Exception as e:
        error_message_detail = f"Error processing OAuth callback (state: {state}): {str(e)}"
        logger.error(error_message_detail, exc_info=True)
        # Generic error page for any other issues during token exchange or credential saving
        return HTMLResponse(content=f"""
            <html>
            <head><title>Authentication Processing Error</title></head>
            <body>
                <h2 style="color: #d32f2f;">Authentication Processing Error</h2>
                <p>An unexpected error occurred while processing your authentication: {str(e)}</p>
                <p>Please try again. You can close this window.</p>
            </body>
            </html>
        """, status_code=500)

@server.tool()
async def start_google_auth(
    user_google_email: str,
    service_name: str,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id")
) -> types.CallToolResult:
    """
    Initiates the Google OAuth 2.0 authentication flow for the specified user email and service.
    This is the primary method to establish credentials when no valid session exists or when targeting a specific account for a particular service.
    It generates an authorization URL that the LLM must present to the user.
    The authentication attempt is linked to the current MCP session via `mcp_session_id`.

    LLM Guidance:
    - Use this tool when you need to authenticate a user for a specific Google service (e.g., "Google Calendar", "Google Docs", "Gmail", "Google Drive")
      and don't have existing valid credentials for the session or specified email.
    - You MUST provide the `user_google_email` and the `service_name`. If you don't know the email, ask the user first.
    - Valid `service_name` values typically include "Google Calendar", "Google Docs", "Gmail", "Google Drive".
    - After calling this tool, present the returned authorization URL clearly to the user and instruct them to:
        1. Click the link and complete the sign-in/consent process in their browser.
        2. Note the authenticated email displayed on the success page.
        3. Provide that email back to you (the LLM).
        4. Retry their original request, including the confirmed `user_google_email`.

    Args:
        user_google_email (str): The user's full Google email address (e.g., 'example@gmail.com'). This is REQUIRED.
        service_name (str): The name of the Google service for which authentication is being requested (e.g., "Google Calendar", "Google Docs"). This is REQUIRED.
        mcp_session_id (Optional[str]): The active MCP session ID (automatically injected by FastMCP from the Mcp-Session-Id header). Links the OAuth flow state to the session.

    Returns:
        types.CallToolResult: An error result (`isError=True`) containing:
                               - A detailed message for the LLM with the authorization URL and instructions to guide the user through the authentication process.
                               - An error message if `user_google_email` or `service_name` is invalid or missing.
                               - An error message if the OAuth flow initiation fails.
    """
    if not user_google_email or not isinstance(user_google_email, str) or '@' not in user_google_email:
        error_msg = "Invalid or missing 'user_google_email'. This parameter is required and must be a valid email address. LLM, please ask the user for their Google email address."
        logger.error(f"[start_google_auth] {error_msg}")
        return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=error_msg)])

    if not service_name or not isinstance(service_name, str):
        error_msg = "Invalid or missing 'service_name'. This parameter is required (e.g., 'Google Calendar', 'Google Docs'). LLM, please specify the service name."
        logger.error(f"[start_google_auth] {error_msg}")
        return types.CallToolResult(isError=True, content=[types.TextContent(type="text", text=error_msg)])

    logger.info(f"Tool 'start_google_auth' invoked for user_google_email: '{user_google_email}', service: '{service_name}', session: '{mcp_session_id}'.")
    # Use the centralized start_auth_flow from auth.google_auth
    # OAUTH_REDIRECT_URI is already defined in this file
    return await start_auth_flow(
        mcp_session_id=mcp_session_id,
        user_google_email=user_google_email,
        service_name=service_name,
        redirect_uri=OAUTH_REDIRECT_URI
    )

@server.tool()
async def get_active_sessions() -> Dict[str, Any]:
    """
    Retrieve information about all active MCP sessions.

    LLM Guidance:
    - Use this tool to get information about currently active sessions
    - This is useful for debugging or when you need to understand the active user sessions

    Returns:
        A dictionary mapping session IDs to session information
    """
    session_manager = session_manager_container.get_session_manager()
    if session_manager is None:
        logger.error("get_active_sessions called but session_manager is not initialized")
        return {"error": "Session manager not initialized"}

    active_sessions = session_manager.get_active_sessions()

    return active_sessions

@server.tool()
async def get_session_info(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve information about a specific MCP session.

    LLM Guidance:
    - Use this tool when you need details about a specific session
    - Provide the session_id parameter to identify which session to retrieve

    Args:
        session_id: The ID of the session to retrieve

    Returns:
        Session information if found, None otherwise
    """
    session_manager = session_manager_container.get_session_manager()
    if session_manager is None:
        logger.error(f"get_session_info({session_id}) called but session_manager is not initialized")
        return {"error": "Session manager not initialized"}

    session_info = session_manager.get_session(session_id)

    if session_info is None:
        logger.debug(f"Session {session_id} not found")
        return {"error": f"Session {session_id} not found"}
    return session_info

@server.tool()
async def debug_current_session(
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id")
) -> Dict[str, Any]:
    """
    Debug tool to show information about the current session.

    LLM Guidance:
    - Use this tool to verify that session tracking is working correctly
    - This tool will return information about the current session based on the Mcp-Session-Id header

    Args:
        mcp_session_id: The MCP session ID header (automatically injected)

    Returns:
        Information about the current session and all active sessions
    """
    session_manager = session_manager_container.get_session_manager()

    # Get the HTTP request to access headers
    req: Request = get_http_request()
    headers = dict(req.headers)

    # Log all active sessions for debugging
    log_all_active_sessions()

    result = {
        "current_session": {
            "session_id": mcp_session_id,
            "headers": headers
        },
        "session_info": None,
        "active_sessions_count": 0
    }

    # Get info for the current session if available
    if session_manager is not None and mcp_session_id:
        session_info = session_manager.get_session(mcp_session_id)
        result["session_info"] = session_info

        # Count active sessions
        active_sessions = session_manager.get_active_sessions()
        result["active_sessions_count"] = len(active_sessions)
        result["active_session_ids"] = list(active_sessions.keys())
    else:
        result["error"] = "Unable to retrieve session information"
        if session_manager is None:
            result["error_details"] = "Session manager not initialized"
        elif not mcp_session_id:
            result["error_details"] = "No session ID provided in request"

    return result