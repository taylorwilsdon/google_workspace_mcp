"""
Authentication middleware to populate context state with user information
"""

import asyncio
import logging
import time

from fastmcp.server.dependencies import get_access_token, get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext

from auth.external_oauth_provider import get_session_time
from auth.gateway_identity import GatewayIdentityError, extract_email_from_assertion
from auth.oauth21_session_store import ensure_session_from_access_token
from auth.oauth_config import get_oauth_config, is_trust_gateway_identity
from auth.oauth_types import WorkspaceAccessToken
from auth.request_identity import (
    get_request_identity,
    reset_request_identity,
    set_request_identity,
)

# Configure logging
logger = logging.getLogger(__name__)


def _token_fingerprint(token: str) -> str:
    """Return a safe, short fingerprint of a bearer token for logging."""
    if not token:
        return "none"
    return f"{token[:8]}…(len={len(token)})"


class AuthInfoMiddleware(Middleware):
    """
    Middleware to extract authentication information from JWT tokens
    and populate the FastMCP context state for use in tools and prompts.
    """

    async def _process_request_for_auth(self, context: MiddlewareContext):
        """Helper to extract, verify, and store auth info from a request."""
        if not context.fastmcp_context:
            logger.warning("No fastmcp_context available")
            return

        # Shadow any identity inherited from session state before any auth path
        # runs. Paths below may fail and still fall through to the tool, so this
        # is what guarantees an unauthenticated request reads None rather than an
        # earlier request's principal.
        await reset_request_identity(context.fastmcp_context)

        authenticated_user = None
        auth_via = None

        # Trusted-gateway identity: verify the SIGNED assertion the fronting proxy injects
        # and use the asserted email as the principal. This is the highest-priority and only
        # trusted source in this mode (MCP_ENABLE_OAUTH21 is off — the proxy owns the handshake).
        if is_trust_gateway_identity():
            try:
                header_name = get_oauth_config().gateway_identity_header
                hdrs = get_http_headers(include={header_name}) or {}
                assertion = hdrs.get(header_name)
                if not assertion:
                    raise GatewayIdentityError(
                        f"Missing trusted-gateway identity header '{header_name}'"
                    )

                # Offload the (synchronous) JWKS fetch/verify off the event loop —
                # PyJWKClient can do network I/O on cold start / key rotation.
                verified_email = await asyncio.to_thread(
                    extract_email_from_assertion, assertion
                )
                if not verified_email:
                    raise GatewayIdentityError(
                        "Trusted-gateway identity assertion failed verification"
                    )

                await set_request_identity(
                    context.fastmcp_context,
                    email=verified_email,
                    via="gateway_assertion",
                )
                logger.info("✓ Authenticated via gateway_assertion: %s", verified_email)
                return
            except GatewayIdentityError:
                logger.warning(
                    "[AuthInfoMiddleware] Trusted-gateway authentication rejected"
                )
                raise
            except Exception as e:
                logger.error(
                    f"[AuthInfoMiddleware] Error processing gateway identity assertion: {e}"
                )
                raise GatewayIdentityError(
                    "Trusted-gateway identity verification failed"
                ) from e

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
                    await set_request_identity(
                        context.fastmcp_context,
                        email=user_email,
                        via="fastmcp_oauth",
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
                # Capture the full headers for diagnostics, then scope auth parsing to authorization.
                all_headers = get_http_headers()
                logger.info(
                    f"[AuthInfoMiddleware] get_http_headers() returned: {all_headers is not None}, keys: {list(all_headers.keys()) if all_headers else 'None'}"
                )
                headers = get_http_headers(include={"authorization"})
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

                                        mcp_session_id = getattr(
                                            context.fastmcp_context, "session_id", None
                                        )
                                        await ensure_session_from_access_token(
                                            access_token,
                                            user_email,
                                            mcp_session_id,
                                        )
                                        await set_request_identity(
                                            context.fastmcp_context,
                                            email=user_email,
                                            via="bearer_token",
                                        )
                                        authenticated_user = user_email
                                        auth_via = "bearer_token"
                                    else:
                                        logger.error(
                                            f"[AuthInfoMiddleware] Token verification returned None "
                                            f"reason=verify_token_returned_none "
                                            f"token={_token_fingerprint(token_str)} "
                                            f"provider={type(auth_provider).__name__}"
                                        )
                                except Exception as e:
                                    logger.error(
                                        f"[AuthInfoMiddleware] Token verification raised exception "
                                        f"reason=verify_token_exception "
                                        f"token={_token_fingerprint(token_str)} "
                                        f"exc_type={type(e).__name__}"
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
                            await set_request_identity(
                                context.fastmcp_context,
                                email=requested_user,
                                via="stdio_session",
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
                            await set_request_identity(
                                context.fastmcp_context,
                                email=single_user,
                                via="stdio_single_session",
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
                            await set_request_identity(
                                context.fastmcp_context,
                                email=bound_user,
                                via="mcp_session_binding",
                            )
                            authenticated_user = bound_user
                            auth_via = "mcp_session_binding"
                    except Exception as e:
                        logger.debug(f"Error checking MCP session binding: {e}")

        # Single exit point with logging
        if authenticated_user:
            logger.info(f"✓ Authenticated via {auth_via}: {authenticated_user}")
            identity = await get_request_identity(context.fastmcp_context)
            logger.debug(f"Request identity after auth: {identity}")
        else:
            try:
                auth_header = (get_http_headers() or {}).get("authorization", "")
            except Exception:
                auth_header = ""
            if auth_header[:7].lower() == "bearer ":
                bearer = auth_header[7:]
                token_fp = _token_fingerprint(bearer)
                token_kind = (
                    "google_oauth" if bearer.startswith("ya29.") else "jwt_or_other"
                )
            else:
                token_fp = "none"
                token_kind = "no_bearer"
            session_id = getattr(context.fastmcp_context, "session_id", None)
            expected_legacy_http_no_bearer = (
                transport_mode == "streamable-http"
                and token_kind == "no_bearer"
                and not get_oauth_config().is_oauth21_enabled()
                and not is_trust_gateway_identity()
            )
            if not expected_legacy_http_no_bearer:
                logger.warning(
                    f"[AuthInfoMiddleware] No authenticated user resolved "
                    f"reason=all_auth_paths_failed "
                    f"token={token_fp} "
                    f"token_kind={token_kind} "
                    f"session_id={session_id}"
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
            if (
                isinstance(e, GatewayIdentityError)
                or "GoogleAuthenticationError" in str(type(e))
                or "Access denied: Cannot retrieve credentials" in str(e)
            ):
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
            if (
                isinstance(e, GatewayIdentityError)
                or "GoogleAuthenticationError" in str(type(e))
                or "Access denied: Cannot retrieve credentials" in str(e)
            ):
                logger.info(f"Authentication check failed in prompt: {e}")
            else:
                logger.error(f"Error in on_get_prompt middleware: {e}", exc_info=True)
            raise
