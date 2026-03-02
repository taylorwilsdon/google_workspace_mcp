"""
Authentication middleware to populate context state with user information
"""

import logging
import time

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.dependencies import get_http_headers

from auth.external_oauth_provider import get_session_time
from auth.oauth21_session_store import ensure_session_from_access_token
from auth.oauth_types import WorkspaceAccessToken

# Configure logging
logger = logging.getLogger(__name__)


class AuthInfoMiddleware(Middleware):
    """
    Middleware to extract authentication information from JWT tokens
    and populate the FastMCP context state for use in tools and prompts.
    """

    def __init__(self):
        super().__init__()
        self.auth_provider_type = "GoogleProvider"

    async def _process_request_for_auth(self, context: MiddlewareContext):
        """Helper to extract, verify, and store auth info from a request."""
        if not context.fastmcp_context:
            logger.warning("No fastmcp_context available")
            return

        authenticated_user = None
        auth_via = None

        # First check if FastMCP has already validated an access token
        try:
            access_token = get_access_token()
            if access_token:
                logger.info("[AuthInfoMiddleware] FastMCP access_token found")
                user_email = getattr(access_token, "email", None)
                if not user_email and hasattr(access_token, "claims"):
                    user_email = access_token.claims.get("email")

                if user_email:
                    logger.info(
                        f"✓ Using FastMCP validated token for user: {user_email}"
                    )
                    await context.fastmcp_context.set_state(
                        "authenticated_user_email", user_email
                    )
                    await context.fastmcp_context.set_state(
                        "authenticated_via", "fastmcp_oauth"
                    )
                    await context.fastmcp_context.set_state(
                        "access_token", access_token, serializable=False
                    )
                    authenticated_user = user_email
                    auth_via = "fastmcp_oauth"
                else:
                    logger.warning(
                        f"FastMCP access_token found but no email. Type: {type(access_token).__name__}"
                    )
        except Exception as e:
            logger.debug(f"Could not get FastMCP access_token: {e}")

        # Try to get the HTTP request to extract Authorization header
        if not authenticated_user:
            try:
                # Use the new FastMCP method to get HTTP headers
                headers = get_http_headers()
                logger.info(
                    f"[AuthInfoMiddleware] get_http_headers() returned: {headers is not None}, keys: {list(headers.keys()) if headers else 'None'}"
                )
                if headers:
                    logger.debug("Processing HTTP headers for authentication")

                    # Get the Authorization header
                    auth_header = headers.get("authorization", "")
                    if auth_header.startswith("Bearer "):
                        token_str = auth_header[7:]  # Remove "Bearer " prefix
                        logger.info("Found Bearer token in request")

                        # For Google OAuth tokens (ya29.*), we need to verify them differently
                        if token_str.startswith("ya29."):
                            logger.debug("Detected Google OAuth access token format")

                            # Verify the token to get user info
                            from core.server import get_auth_provider

                            auth_provider = get_auth_provider()

                            if auth_provider:
                                try:
                                    # Verify the token
                                    verified_auth = await auth_provider.verify_token(
                                        token_str
                                    )
                                    if verified_auth:
                                        # Extract user email from verified token
                                        user_email = getattr(
                                            verified_auth, "email", None
                                        )
                                        if not user_email and hasattr(
                                            verified_auth, "claims"
                                        ):
                                            user_email = verified_auth.claims.get(
                                                "email"
                                            )

                                        if isinstance(
                                            verified_auth, WorkspaceAccessToken
                                        ):
                                            # ExternalOAuthProvider returns a fully-formed WorkspaceAccessToken
                                            access_token = verified_auth
                                        else:
                                            # Standard GoogleProvider returns a base AccessToken;
                                            # wrap it in WorkspaceAccessToken for identical downstream handling
                                            verified_expires = getattr(
                                                verified_auth, "expires_at", None
                                            )
                                            access_token = WorkspaceAccessToken(
                                                token=token_str,
                                                client_id=getattr(
                                                    verified_auth, "client_id", None
                                                )
                                                or "google",
                                                scopes=getattr(
                                                    verified_auth, "scopes", []
                                                )
                                                or [],
                                                session_id=f"google_oauth_{token_str[:8]}",
                                                expires_at=verified_expires
                                                if verified_expires is not None
                                                else int(time.time())
                                                + get_session_time(),
                                                claims=getattr(
                                                    verified_auth, "claims", {}
                                                )
                                                or {},
                                                sub=getattr(verified_auth, "sub", None)
                                                or user_email,
                                                email=user_email,
                                            )

                                        # Store in context state - this is the authoritative authentication state
                                        await context.fastmcp_context.set_state(
                                            "access_token",
                                            access_token,
                                            serializable=False,
                                        )
                                        mcp_session_id = getattr(
                                            context.fastmcp_context, "session_id", None
                                        )
                                        ensure_session_from_access_token(
                                            access_token,
                                            user_email,
                                            mcp_session_id,
                                        )
                                        await context.fastmcp_context.set_state(
                                            "auth_provider_type",
                                            self.auth_provider_type,
                                        )
                                        await context.fastmcp_context.set_state(
                                            "token_type", "google_oauth"
                                        )
                                        await context.fastmcp_context.set_state(
                                            "user_email", user_email
                                        )
                                        await context.fastmcp_context.set_state(
                                            "username", user_email
                                        )
                                        # Set the definitive authentication state
                                        await context.fastmcp_context.set_state(
                                            "authenticated_user_email", user_email
                                        )
                                        await context.fastmcp_context.set_state(
                                            "authenticated_via", "bearer_token"
                                        )
                                        authenticated_user = user_email
                                        auth_via = "bearer_token"
                                    else:
                                        logger.error(
                                            "Failed to verify Google OAuth token"
                                        )
                                except Exception as e:
                                    logger.error(
                                        f"Error verifying Google OAuth token: {e}"
                                    )
                            else:
                                logger.warning(
                                    "No auth provider available to verify Google token"
                                )

                        else:
                            # Non-Google JWT tokens require verification
                            # SECURITY: Never set authenticated_user_email from unverified tokens
                            logger.debug(
                                "Unverified JWT token rejected - only verified tokens accepted"
                            )
                    else:
                        logger.debug("No Bearer token in Authorization header")
                else:
                    logger.debug(
                        "No HTTP headers available (might be using stdio transport)"
                    )
            except Exception as e:
                logger.debug(f"Could not get HTTP request: {e}")

        # After trying HTTP headers, check for other authentication methods
        # This consolidates all authentication logic in the middleware
        if not authenticated_user:
            logger.debug(
                "No authentication found via bearer token, checking other methods"
            )

            # Check transport mode
            from core.config import get_transport_mode

            transport_mode = get_transport_mode()

            if transport_mode == "stdio":
                # In stdio mode, check if there's a session with credentials
                # This is ONLY safe in stdio mode because it's single-user
                logger.debug("Checking for stdio mode authentication")

                # Get the requested user from the context if available
                requested_user = None
                if hasattr(context, "request") and hasattr(context.request, "params"):
                    requested_user = context.request.params.get("user_google_email")
                elif hasattr(context, "arguments"):
                    # FastMCP may store arguments differently
                    requested_user = context.arguments.get("user_google_email")

                if requested_user:
                    try:
                        from auth.oauth21_session_store import get_oauth21_session_store

                        store = get_oauth21_session_store()

                        # Check if user has a recent session
                        if store.has_session(requested_user):
                            logger.debug(
                                f"Using recent stdio session for {requested_user}"
                            )
                            # In stdio mode, we can trust the user has authenticated recently
                            await context.fastmcp_context.set_state(
                                "authenticated_user_email", requested_user
                            )
                            await context.fastmcp_context.set_state(
                                "authenticated_via", "stdio_session"
                            )
                            await context.fastmcp_context.set_state(
                                "auth_provider_type", "oauth21_stdio"
                            )
                            authenticated_user = requested_user
                            auth_via = "stdio_session"
                    except Exception as e:
                        logger.debug(f"Error checking stdio session: {e}")

                # If no requested user was provided but exactly one session exists, assume it in stdio mode
                if not authenticated_user:
                    try:
                        from auth.oauth21_session_store import get_oauth21_session_store

                        store = get_oauth21_session_store()
                        single_user = store.get_single_user_email()
                        if single_user:
                            logger.debug(
                                f"Defaulting to single stdio OAuth session for {single_user}"
                            )
                            await context.fastmcp_context.set_state(
                                "authenticated_user_email", single_user
                            )
                            await context.fastmcp_context.set_state(
                                "authenticated_via", "stdio_single_session"
                            )
                            await context.fastmcp_context.set_state(
                                "auth_provider_type", "oauth21_stdio"
                            )
                            await context.fastmcp_context.set_state(
                                "user_email", single_user
                            )
                            await context.fastmcp_context.set_state(
                                "username", single_user
                            )
                            authenticated_user = single_user
                            auth_via = "stdio_single_session"
                    except Exception as e:
                        logger.debug(
                            f"Error determining stdio single-user session: {e}"
                        )

            # Check for MCP session binding
            if not authenticated_user and hasattr(
                context.fastmcp_context, "session_id"
            ):
                mcp_session_id = context.fastmcp_context.session_id
                if mcp_session_id:
                    try:
                        from auth.oauth21_session_store import get_oauth21_session_store

                        store = get_oauth21_session_store()

                        # Check if this MCP session is bound to a user
                        bound_user = store.get_user_by_mcp_session(mcp_session_id)
                        if bound_user:
                            logger.debug(f"MCP session bound to {bound_user}")
                            await context.fastmcp_context.set_state(
                                "authenticated_user_email", bound_user
                            )
                            await context.fastmcp_context.set_state(
                                "authenticated_via", "mcp_session_binding"
                            )
                            await context.fastmcp_context.set_state(
                                "auth_provider_type", "oauth21_session"
                            )
                            authenticated_user = bound_user
                            auth_via = "mcp_session_binding"
                    except Exception as e:
                        logger.debug(f"Error checking MCP session binding: {e}")

        # Single exit point with logging
        if authenticated_user:
            logger.info(f"✓ Authenticated via {auth_via}: {authenticated_user}")
            auth_email = await context.fastmcp_context.get_state(
                "authenticated_user_email"
            )
            logger.debug(
                f"Context state after auth: authenticated_user_email={auth_email}"
            )

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Extract auth info from token and set in context state"""
        logger.debug("Processing tool call authentication")

        try:
            await self._process_request_for_auth(context)

            logger.debug("Passing to next handler")
            result = await call_next(context)
            logger.debug("Handler completed")
            return result

        except Exception as e:
            # Check if this is an authentication error - don't log traceback for these
            if "GoogleAuthenticationError" in str(
                type(e)
            ) or "Access denied: Cannot retrieve credentials" in str(e):
                logger.info(f"Authentication check failed: {e}")
            else:
                logger.error(f"Error in on_call_tool middleware: {e}", exc_info=True)
            raise

    async def on_get_prompt(self, context: MiddlewareContext, call_next):
        """Extract auth info for prompt requests too"""
        logger.debug("Processing prompt authentication")

        try:
            await self._process_request_for_auth(context)

            logger.debug("Passing prompt to next handler")
            result = await call_next(context)
            logger.debug("Prompt handler completed")
            return result

        except Exception as e:
            # Check if this is an authentication error - don't log traceback for these
            if "GoogleAuthenticationError" in str(
                type(e)
            ) or "Access denied: Cannot retrieve credentials" in str(e):
                logger.info(f"Authentication check failed in prompt: {e}")
            else:
                logger.error(f"Error in on_get_prompt middleware: {e}", exc_info=True)
            raise
