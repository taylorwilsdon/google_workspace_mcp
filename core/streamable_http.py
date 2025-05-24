import contextlib
import logging
import os
from collections.abc import AsyncIterator
from typing import Optional, Dict, Any, Callable

import anyio
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send, ASGIApp

# Global variable to store the current session ID for the current request
# This will be used to pass the session ID to the FastMCP tools
CURRENT_SESSION_ID = None

logger = logging.getLogger(__name__)

class SessionAwareStreamableHTTPManager:
    """
    A wrapper around StreamableHTTPSessionManager that provides access to session information.
    This class enables retrieving active session data which can be useful for tools that need 
    to know about current sessions.
    """
    
    def __init__(self, app: Server, stateless: bool = False, event_store: Optional[Any] = None):
        """
        Initialize the session manager wrapper.
        
        Args:
            app: The MCP Server instance
            stateless: Whether to use stateless mode (default: False)
            event_store: Optional event store for storing session events
        """
        self.session_manager = StreamableHTTPSessionManager(
            app=app,
            event_store=event_store,
            stateless=stateless
        )
        self._sessions = {}
        
    async def handle_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Handle an incoming request by delegating to the underlying session manager.
        
        Args:
            scope: The ASGI scope
            receive: The ASGI receive function
            send: The ASGI send function
        """
        global CURRENT_SESSION_ID
        
        # Check for session ID in headers
        headers = dict(scope.get("headers", []))
        session_id = None
        for key, value in headers.items():
            if key.lower() == b'mcp-session-id':
                session_id = value.decode('utf-8')
                break
        
        # Extract session ID from StreamableHTTP if not in headers
        if not session_id and hasattr(self.session_manager, '_get_session_id_from_scope'):
            try:
                # Try to get session ID directly from StreamableHTTP manager
                session_id = self.session_manager._get_session_id_from_scope(scope)
            except Exception:
                pass
        
        # Set the global session ID for this request
        if session_id:
            CURRENT_SESSION_ID = session_id
            
            # Inject the session ID into the request headers
            # This allows FastMCP to access it via the Header mechanism
            new_headers = []
            has_session_header = False
            
            for k, v in scope.get("headers", []):
                if k.lower() == b'mcp-session-id':
                    new_headers.append((k, session_id.encode('utf-8')))
                    has_session_header = True
                else:
                    new_headers.append((k, v))
            
            # Add the header if it doesn't exist
            if not has_session_header:
                new_headers.append((b'mcp-session-id', session_id.encode('utf-8')))
            
            # Replace headers in scope
            scope["headers"] = new_headers
        else:
            CURRENT_SESSION_ID = None
        
        # Create a wrapper for the send function to capture the session ID
        # from the response if it's not in the request
        original_send = send
        
        async def wrapped_send(message):
            nonlocal session_id
            global CURRENT_SESSION_ID
            
            # If this is a response, check for session ID in headers
            if message.get("type") == "http.response.start" and not session_id:
                headers = message.get("headers", [])
                for k, v in headers:
                    if k.lower() == b'mcp-session-id':
                        new_session_id = v.decode('utf-8')
                        CURRENT_SESSION_ID = new_session_id
                        break
            
            await original_send(message)
        
        # Process the request with the wrapped send function
        await self.session_manager.handle_request(scope, receive, wrapped_send)
        
        # Clear the global session ID after the request is done
        CURRENT_SESSION_ID = None
    
    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """
        Context manager for running the session manager.
        
        Yields:
            None
        """
        async with self.session_manager.run():
            logger.debug("SessionAwareStreamableHTTPManager started")
            try:
                yield
            finally:
                logger.debug("SessionAwareStreamableHTTPManager shutting down")
    
    def get_active_sessions(self) -> Dict[str, Any]:
        """
        Get information about all active sessions.
        
        Returns:
            A dictionary mapping session IDs to session information
        """
        # Access the internal sessions dictionary from the session manager
        if hasattr(self.session_manager, '_sessions'):
            return {
                session_id: {
                    "created_at": session.created_at,
                    "last_active": session.last_active,
                    "client_id": session.client_id if hasattr(session, 'client_id') else None,
                }
                for session_id, session in self.session_manager._sessions.items()
            }
        return {}
    
    def get_session(self, session_id: str) -> Optional[Any]:
        """
        Get information about a specific session.
        
        Args:
            session_id: The ID of the session to retrieve
            
        Returns:
            Session information if found, None otherwise
        """
        if hasattr(self.session_manager, '_sessions') and session_id in self.session_manager._sessions:
            session = self.session_manager._sessions[session_id]
            return {
                "created_at": session.created_at,
                "last_active": session.last_active,
                "client_id": session.client_id if hasattr(session, 'client_id') else None,
            }
        return None

def create_starlette_app(mcp_server: Server, base_path: str = "/mcp") -> Starlette:
    """
    Create a Starlette application with a mounted StreamableHTTPSessionManager.
    
    Args:
        mcp_server: The MCP Server instance
        base_path: The base path to mount the session manager at
        
    Returns:
        A Starlette application
    """
    session_manager = SessionAwareStreamableHTTPManager(
        app=mcp_server,
        stateless=False,  # Use stateful sessions by default
    )
    
    async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
        # Log information about the incoming request
        path = scope.get("path", "unknown path")
        method = scope.get("method", "unknown method")
        logger.info(f"Incoming request: {method} {path}")
        
        # Process the request
        await session_manager.handle_request(scope, receive, send)
    
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Context manager for session manager."""
        async with session_manager.run():
            logger.info(f"Application started with StreamableHTTP session manager at {base_path}")
            try:
                yield
            finally:
                logger.info("Application shutting down...")
    
    app = Starlette(
        debug=True,
        routes=[
            Mount(base_path, app=handle_streamable_http),
        ],
        lifespan=lifespan,
    )
    
    # Create a middleware to set the FastMCP session ID header
    class SessionHeaderMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            # If we have a current session ID, add it to the request's headers
            # This makes it available to FastMCP via the Header injection
            if CURRENT_SESSION_ID:
                # Save the session ID in an environment variable that FastMCP can access
                os.environ["MCP_CURRENT_SESSION_ID"] = CURRENT_SESSION_ID
                
                # Since we can't modify request.headers directly,
                # we'll handle this in our SessionAwareStreamableHTTPManager
                logger.debug(f"SessionHeaderMiddleware: Set environment session ID to {CURRENT_SESSION_ID}")
            
            # Call the next middleware or endpoint
            response = await call_next(request)
            
            # Remove the environment variable after the request
            if "MCP_CURRENT_SESSION_ID" in os.environ:
                del os.environ["MCP_CURRENT_SESSION_ID"]
            
            return response
    
    # Add the middleware to the app
    app.add_middleware(SessionHeaderMiddleware)
    
    # Attach the session manager to the app for access elsewhere
    app.state.session_manager = session_manager
    
    return app, session_manager

# Function to get the current session ID (used by tools)
def get_current_session_id() -> Optional[str]:
    """
    Get the session ID for the current request context.
    
    Returns:
        The session ID if available, None otherwise
    """
    # First check the global variable (set during request handling)
    if CURRENT_SESSION_ID:
        return CURRENT_SESSION_ID
    
    # Then check environment variable (set by middleware)
    return os.environ.get("MCP_CURRENT_SESSION_ID")