"""
MCP Session Middleware

This middleware intercepts MCP requests and sets the session context
for use by tool functions.
"""

import logging
from typing import Optional

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from auth.oauth21_session_store import (
    SessionContext,
    SessionContextManager,
    extract_session_from_headers,
)
# OAuth 2.1 is now handled by FastMCP auth

logger = logging.getLogger(__name__)


def _build_session_context(request: Request) -> Optional[SessionContext]:
    headers = dict(request.headers)
    session_id = extract_session_from_headers(headers)

    auth_context = None
    user_email = None
    mcp_session_id = None
    if hasattr(request.state, "auth"):
        auth_context = request.state.auth
        # SECURITY: user_email must come from verified FastMCP auth, never a raw JWT.
        if hasattr(auth_context, "claims") and auth_context.claims:
            user_email = auth_context.claims.get("email")

    if hasattr(request.state, "session_id"):
        mcp_session_id = request.state.session_id
        logger.debug(f"Found FastMCP session ID: {mcp_session_id}")

    if not (session_id or auth_context or user_email or mcp_session_id):
        return None

    # Session ID hierarchy: explicit session_id > Google user session > FastMCP session
    effective_session_id = session_id
    if not effective_session_id and user_email:
        effective_session_id = f"google_{user_email}"
    elif not effective_session_id and mcp_session_id:
        effective_session_id = mcp_session_id

    session_context = SessionContext(
        session_id=effective_session_id,
        user_id=user_email or (auth_context.user_id if auth_context else None),
        auth_context=auth_context,
        request=request,
        metadata={
            "path": request.url.path,
            "method": request.method,
            "user_email": user_email,
            "mcp_session_id": mcp_session_id,
        },
    )
    logger.debug(
        f"MCP request with session: session_id={session_context.session_id}, "
        f"user_id={session_context.user_id}, path={request.url.path}"
    )
    return session_context


class MCPSessionMiddleware:
    """
    Middleware that extracts session information from requests and makes it
    available to MCP tool functions via context variables.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        try:
            request = Request(scope, receive)
            logger.debug(
                f"MCPSessionMiddleware processing request: {request.method} {request.url.path}"
            )
            session_context = _build_session_context(request)
        except Exception as e:
            logger.error(f"Error in MCP session middleware: {e}")
            session_context = None

        with SessionContextManager(session_context):
            await self.app(scope, receive, send)
