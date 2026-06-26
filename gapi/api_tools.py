"""
Generic Google API MCP Tool

Provides a single `api_call` escape hatch that issues an authenticated request to
an arbitrary Google API endpoint using the user's existing credentials. This covers
the long tail of endpoints, parameters, and services that do not (yet) have a
dedicated tool.

The call is bounded entirely by the scopes the user has already granted: it reuses
the same OAuth credentials the typed service clients use, so it cannot do anything
the user has not consented to.
"""

import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlsplit, urlunsplit

from mcp.types import ToolAnnotations

from auth.scopes import USERINFO_EMAIL_SCOPE
from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

# The credentials are Google OAuth tokens; sending them anywhere other than
# Google's own API hosts would leak them, so api_call is restricted to the
# *.googleapis.com surface.
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

# Response bodies are returned inline to the model; cap to avoid flooding context.
_MAX_RESPONSE_CHARS = 100_000


def _validate_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise UserInputError(
            f"url must be https (got scheme '{parts.scheme or 'none'}')."
        )
    host = parts.hostname or ""
    if not (host.endswith(".googleapis.com") or host == "www.googleapis.com"):
        raise UserInputError(
            f"url host '{host}' is not permitted. api_call may only target "
            "*.googleapis.com endpoints (the user's Google credentials must not "
            "be sent elsewhere)."
        )
    return url


def _prepare_request(
    method: str, url: str, query: Optional[Dict[str, Any]], body: Optional[Any]
) -> tuple:
    """Validate inputs and build the (method, url, body, headers) for the request."""
    method = (method or "").upper()
    if method not in _ALLOWED_METHODS:
        raise UserInputError(
            f"method must be one of {sorted(_ALLOWED_METHODS)} (got '{method}')."
        )

    _validate_url(url)

    if query:
        parts = urlsplit(url)
        encoded = urlencode(query, doseq=True)
        merged = f"{parts.query}&{encoded}" if parts.query else encoded
        url = urlunsplit(
            (parts.scheme, parts.netloc, parts.path, merged, parts.fragment)
        )

    headers = {}
    request_body = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        request_body = body if isinstance(body, str) else json.dumps(body)

    return method, url, request_body, headers


def _format_response(status: int, content: Optional[bytes]) -> str:
    """Format an HTTP response into the status line + (pretty) body string."""
    text = content.decode("utf-8", errors="replace") if content else ""

    # Pretty-print JSON responses for readability; fall back to raw text.
    try:
        text = json.dumps(json.loads(text), indent=2)
    except (ValueError, TypeError):
        pass

    if len(text) > _MAX_RESPONSE_CHARS:
        text = text[:_MAX_RESPONSE_CHARS] + f"\n...[truncated, {len(text)} chars total]"

    return f"HTTP {status}\n{text}"


async def _api_call_impl(
    service: Any,
    user_google_email: str,
    method: str,
    url: str,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
) -> str:
    """Internal implementation for api_call (see the tool's docstring)."""
    method, url, request_body, headers = _prepare_request(method, url, query, body)

    logger.info(f"[api_call] {user_google_email} -> {method} {url}")

    # Borrow the authorized transport from the injected service. It carries the
    # user's OAuth credentials and handles token refresh; we just point it at an
    # arbitrary endpoint. The decorator closes `service` (and thus this http) on exit.
    authorized_http = service._http
    response, content = authorized_http.request(
        url, method=method, body=request_body, headers=headers
    )

    return _format_response(response.status, content)


@server.tool(
    title="Google API Call",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
# api_call is not bound to any one service: it only needs a valid credential to
# borrow the authorized transport from. We authenticate against the base identity
# scope (userinfo.email), which every authenticated user already holds, rather than
# requesting an unrelated service scope like Drive. Actual access is bounded by
# whatever scopes the user has separately granted.
@handle_http_errors("api_call", is_read_only=False, service_type="people")
@require_google_service("people", USERINFO_EMAIL_SCOPE)
async def api_call(
    service: Any,
    user_google_email: str,
    method: str,
    url: str,
    query: Optional[Dict[str, Any]] = None,
    body: Optional[Any] = None,
) -> str:
    """
    Make an authenticated request to an arbitrary Google API endpoint.

    Escape hatch for any Google API operation that does not have a dedicated tool.
    The request is authenticated with the user's existing Google credentials and is
    therefore bounded by the scopes they have already granted.

    Args:
        service: Injected Google API service client (used only for its credentials).
        user_google_email: User's email address.
        method: HTTP method — one of GET, POST, PUT, PATCH, DELETE.
        url: Full https URL of a *.googleapis.com endpoint, e.g.
            "https://gmail.googleapis.com/gmail/v1/users/me/labels".
        query: Optional query-string parameters as a dict.
        body: Optional JSON request body (dict/list); serialized as application/json.

    Returns:
        str: The HTTP status line followed by the response body (JSON pretty-printed
        when possible, otherwise raw text).
    """
    return await _api_call_impl(service, user_google_email, method, url, query, body)
