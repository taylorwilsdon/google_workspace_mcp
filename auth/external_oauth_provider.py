"""
External OAuth Provider for Google Workspace MCP

Extends FastMCP's GoogleProvider to support external OAuth flows where
access tokens (ya29.*) are issued by external systems and need validation.
"""

import logging
import time
from typing import Optional

import httpx

from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.auth import AccessToken

logger = logging.getLogger(__name__)

# Google's userinfo endpoint for token validation
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

# Reusable HTTP client — avoids creating a new SSL context per request
_http_client: Optional[httpx.Client] = None


def _get_http_client() -> httpx.Client:
    """Get or create a reusable httpx client for token validation."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(timeout=10.0)
    return _http_client


class ExternalOAuthProvider(GoogleProvider):
    """
    Extended GoogleProvider that supports validating external Google OAuth access tokens.

    This provider handles ya29.* access tokens by calling Google's userinfo API,
    while maintaining compatibility with standard JWT ID tokens.
    """

    def __init__(self, client_id: str, client_secret: str, **kwargs):
        """Initialize and store client credentials for token validation."""
        super().__init__(client_id=client_id, client_secret=client_secret, **kwargs)
        # Store credentials as they're not exposed by parent class
        self._client_id = client_id
        self._client_secret = client_secret

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        """
        Verify a token - supports both JWT ID tokens and ya29.* access tokens.

        For ya29.* access tokens (issued externally), validates by calling
        Google's userinfo API directly via httpx (lightweight, no
        googleapiclient.discovery overhead). For JWT tokens, delegates to
        parent class.

        Args:
            token: Token string to verify (JWT or ya29.* access token)

        Returns:
            AccessToken object if valid, None otherwise
        """
        # For ya29.* access tokens, validate using Google's userinfo API
        if token.startswith("ya29."):
            logger.debug("Validating external Google OAuth access token")

            try:
                # Validate token by calling userinfo API directly.
                # Previously this used googleapiclient.discovery.build("oauth2", "v2")
                # which created heavy httplib2.Http + SSL context + discovery doc
                # objects on every call that were never closed, causing memory leaks.
                client = _get_http_client()
                response = client.get(
                    _GOOGLE_USERINFO_URL,
                    headers={"Authorization": f"Bearer {token}"},
                )

                if response.status_code != 200:
                    logger.error(
                        f"Google userinfo API returned {response.status_code}"
                    )
                    return None

                user_info = response.json()

                if user_info and user_info.get("email"):
                    # Token is valid - create AccessToken object
                    logger.info(
                        f"Validated external access token for: {user_info['email']}"
                    )

                    from types import SimpleNamespace

                    scope_list = list(getattr(self, "required_scopes", []) or [])
                    access_token = SimpleNamespace(
                        token=token,
                        scopes=scope_list,
                        expires_at=int(time.time()) + 3600,
                        claims={
                            "email": user_info["email"],
                            "sub": user_info.get("sub"),
                        },
                        client_id=self._client_id,
                        email=user_info["email"],
                        sub=user_info.get("sub"),
                    )
                    return access_token
                else:
                    logger.error("Could not get user info from access token")
                    return None

            except Exception as e:
                logger.error(f"Error validating external access token: {e}")
                return None

        # For JWT tokens, use parent class implementation
        return await super().verify_token(token)
