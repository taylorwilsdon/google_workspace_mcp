"""
OAuth Configuration Management

This module centralizes OAuth-related configuration to eliminate hardcoded values
scattered throughout the codebase. It provides environment variable support and
sensible defaults for all OAuth-related settings.

Supports both OAuth 2.0 and OAuth 2.1 with automatic client capability detection.
"""

import os
from urllib.parse import urlparse
from typing import List, Optional, Dict, Any


class OAuthConfig:
    """
    Centralized OAuth configuration management.

    This class eliminates the hardcoded configuration anti-pattern identified
    in the challenge review by providing a single source of truth for all
    OAuth-related configuration values.
    """

    def __init__(self):
        # Base server configuration
        self.base_uri = os.getenv("WORKSPACE_MCP_BASE_URI", "http://localhost")
        self.port = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", "8000")))
        self.base_url = f"{self.base_uri}:{self.port}"

        # External URL for reverse proxy scenarios
        self.external_url = os.getenv("WORKSPACE_EXTERNAL_URL")

        # OAuth client configuration
        self.client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

        # OAuth 2.1 configuration
        self.oauth21_enabled = (
            os.getenv("MCP_ENABLE_OAUTH21", "false").lower() == "true"
        )
        self.pkce_required = self.oauth21_enabled  # PKCE is mandatory in OAuth 2.1
        self.supported_code_challenge_methods = (
            ["S256", "plain"] if not self.oauth21_enabled else ["S256"]
        )

        # External OAuth 2.1 provider configuration
        self.external_oauth21_provider = (
            os.getenv("EXTERNAL_OAUTH21_PROVIDER", "false").lower() == "true"
        )
        if self.external_oauth21_provider and not self.oauth21_enabled:
            raise ValueError(
                "EXTERNAL_OAUTH21_PROVIDER requires MCP_ENABLE_OAUTH21=true"
            )

        # Stateless mode configuration
        self.stateless_mode = (
            os.getenv("WORKSPACE_MCP_STATELESS_MODE", "false").lower() == "true"
        )
        if self.stateless_mode and not self.oauth21_enabled:
            raise ValueError(
                "WORKSPACE_MCP_STATELESS_MODE requires MCP_ENABLE_OAUTH21=true"
            )

        # Transport mode (will be set at runtime)
        self._transport_mode = "stdio"  # Default

        # Redirect URI configuration
        self.redirect_uri = self._get_redirect_uri()
        self.redirect_path = self._get_redirect_path(self.redirect_uri)

        # Ensure FastMCP's Google provider picks up our existing configuration
        self._apply_fastmcp_google_env()

    def _get_redirect_uri(self) -> str:
        """
        Get the OAuth redirect URI, supporting reverse proxy configurations.

        Returns:
            The configured redirect URI
        """
        explicit_uri = os.getenv("GOOGLE_OAUTH_REDIRECT_URI")
        if explicit_uri:
            return explicit_uri
        return f"{self.base_url}/oauth2callback"

    @staticmethod
    def _get_redirect_path(uri: str) -> str:
        """Extract the redirect path from a full redirect URI."""
        parsed = urlparse(uri)
        if parsed.scheme or parsed.netloc:
            path = parsed.path or "/oauth2callback"
        else:
            # If the value was already a path, ensure it starts with '/'
            path = uri if uri.startswith("/") else f"/{uri}"
        return path or "/oauth2callback"

    def _apply_fastmcp_google_env(self) -> None:
        """Mirror legacy GOOGLE_* env vars into FastMCP Google provider settings."""
        if not self.client_id:
            return

        def _set_if_absent(key: str, value: Optional[str]) -> None:
            if value and key not in os.environ:
                os.environ[key] = value

        # Don't set FASTMCP_SERVER_AUTH if using external OAuth provider
        # (external OAuth means protocol-level auth is disabled, only tool-level auth)
        if not self.external_oauth21_provider:
            _set_if_absent(
                "FASTMCP_SERVER_AUTH",
                "fastmcp.server.auth.providers.google.GoogleProvider"
                if self.oauth21_enabled
                else None,
            )

        _set_if_absent("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID", self.client_id)
        _set_if_absent("FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET", self.client_secret)
        _set_if_absent("FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL", self.get_oauth_base_url())
        _set_if_absent("FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH", self.redirect_path)

    def get_redirect_uris(self) -> List[str]:
        """
        Get all valid OAuth redirect URIs.

        Returns:
            List of all supported redirect URIs
        """
        uris = []

        # Primary redirect URI
        uris.append(self.redirect_uri)

        # Custom redirect URIs from environment
        custom_uris = os.getenv("OAUTH_CUSTOM_REDIRECT_URIS")
        if custom_uris:
            uris.extend([uri.strip() for uri in custom_uris.split(",")])

        # Remove duplicates while preserving order
        return list(dict.fromkeys(uris))

    def get_allowed_origins(self) -> List[str]:
        """
        Get allowed CORS origins for OAuth endpoints.

        Returns:
            List of allowed origins for CORS
        """
        origins = []

        # Server's own origin
        origins.append(self.base_url)

        # VS Code and development origins
        origins.extend(
            [
                "vscode-webview://",
                "https://vscode.dev",
                "https://github.dev",
            ]
        )

        # Custom origins from environment
        custom_origins = os.getenv("OAUTH_ALLOWED_ORIGINS")
        if custom_origins:
            origins.extend([origin.strip() for origin in custom_origins.split(",")])

        return list(dict.fromkeys(origins))

    def is_configured(self) -> bool:
        """
        Check if OAuth is properly configured.

        Returns:
            True if OAuth client credentials are available
        """
        return bool(self.client_id and self.client_secret)

    def get_oauth_base_url(self) -> str:
        """
        Get OAuth base URL for constructing OAuth endpoints.

        Uses WORKSPACE_EXTERNAL_URL if set (for reverse proxy scenarios),
        otherwise falls back to constructed base_url with port.

        Returns:
            Base URL for OAuth endpoints
        """
        if self.external_url:
            return self.external_url
        return self.base_url

    def validate_redirect_uri(self, uri: str) -> bool:
        """
        Validate if a redirect URI is allowed.

        Args:
            uri: The redirect URI to validate

        Returns:
            True if the URI is allowed, False otherwise
        """
        allowed_uris = self.get_redirect_uris()
        return uri in allowed_uris

    def get_environment_summary(self) -> dict:
        """
        Get a summary of the current OAuth configuration.

        Returns:
            Dictionary with configuration summary (excluding secrets)
        """
        return {
            "base_url": self.base_url,
            "external_url": self.external_url,
            "effective_oauth_url": self.get_oauth_base_url(),
            "redirect_uri": self.redirect_uri,
            "redirect_path": self.redirect_path,
            "client_configured": bool(self.client_id),
            "oauth21_enabled": self.oauth21_enabled,
            "external_oauth21_provider": self.external_oauth21_provider,
            "pkce_required": self.pkce_required,
            "transport_mode": self._transport_mode,
            "total_redirect_uris": len(self.get_redirect_uris()),
            "total_allowed_origins": len(self.get_allowed_origins()),
        }

    def set_transport_mode(self, mode: str) -> None:
        """
        Set the current transport mode for OAuth callback handling.

        Args:
            mode: Transport mode ("stdio", "streamable-http", etc.)
        """
        self._transport_mode = mode

    def get_transport_mode(self) -> str:
        """
        Get the current transport mode.

        Returns:
            Current transport mode
        """
        return self._transport_mode

    def is_oauth21_enabled(self) -> bool:
        """
        Check if OAuth 2.1 mode is enabled.

        Returns:
            True if OAuth 2.1 is enabled
        """
        return self.oauth21_enabled

    def is_external_oauth21_provider(self) -> bool:
        """
        Check if external OAuth 2.1 provider mode is enabled.

        When enabled, the server expects external OAuth flow with bearer tokens
        in Authorization headers for tool calls. Protocol-level auth is disabled.

        Returns:
            True if external OAuth 2.1 provider is enabled
        """
        return self.external_oauth21_provider

    def detect_oauth_version(self, request_params: Dict[str, Any]) -> str:
        """
        Detect OAuth version based on request parameters.

        This method implements a conservative detection strategy:
        - Only returns "oauth21" when we have clear indicators
        - Defaults to "oauth20" for backward compatibility
        - Respects the global oauth21_enabled flag

        Args:
            request_params: Request parameters from authorization or token request

        Returns:
            "oauth21" or "oauth20" based on detection
        """
        # If OAuth 2.1 is not enabled globally, always return OAuth 2.0
        if not self.oauth21_enabled:
            return "oauth20"

        # Use the structured type for cleaner detection logic
        from auth.oauth_types import OAuthVersionDetectionParams

        params = OAuthVersionDetectionParams.from_request(request_params)

        # Clear OAuth 2.1 indicator: PKCE is present
        if params.has_pkce:
            return "oauth21"

        # Additional detection: Check if we have an active OAuth 2.1 session
        # This is important for tool calls where PKCE params aren't available
        authenticated_user = request_params.get("authenticated_user")
        if authenticated_user:
            try:
                from auth.oauth21_session_store import get_oauth21_session_store

                store = get_oauth21_session_store()
                if store.has_session(authenticated_user):
                    return "oauth21"
            except (ImportError, AttributeError, RuntimeError):
                pass  # Fall back to OAuth 2.0 if session check fails

        # For public clients in OAuth 2.1 mode, we require PKCE
        # But since they didn't send PKCE, fall back to OAuth 2.0
        # This ensures backward compatibility

        # Default to OAuth 2.0 for maximum compatibility
        return "oauth20"

    def get_authorization_server_metadata(
        self, scopes: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Get OAuth authorization server metadata per RFC 8414.

        Args:
            scopes: Optional list of supported scopes to include in metadata

        Returns:
            Authorization server metadata dictionary
        """
        oauth_base = self.get_oauth_base_url()
        metadata = {
            "issuer": "https://accounts.google.com",
            "authorization_endpoint": f"{oauth_base}/oauth2/authorize",
            "token_endpoint": f"{oauth_base}/oauth2/token",
            "registration_endpoint": f"{oauth_base}/oauth2/register",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
            "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
            "response_types_supported": ["code", "token"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post",
                "client_secret_basic",
            ],
            "code_challenge_methods_supported": self.supported_code_challenge_methods,
        }

        # Include scopes if provided
        if scopes is not None:
            metadata["scopes_supported"] = scopes

        # Add OAuth 2.1 specific metadata
        if self.oauth21_enabled:
            metadata["pkce_required"] = True
            # OAuth 2.1 deprecates implicit flow
            metadata["response_types_supported"] = ["code"]
            # OAuth 2.1 requires exact redirect URI matching
            metadata["require_exact_redirect_uri"] = True

        return metadata


# Global configuration instance
_oauth_config = None


def get_oauth_config() -> OAuthConfig:
    """
    Get the global OAuth configuration instance.

    Returns:
        The singleton OAuth configuration instance
    """
    global _oauth_config
    if _oauth_config is None:
        _oauth_config = OAuthConfig()
    return _oauth_config


def reload_oauth_config() -> OAuthConfig:
    """
    Reload the OAuth configuration from environment variables.

    This is useful for testing or when environment variables change.

    Returns:
        The reloaded OAuth configuration instance
    """
    global _oauth_config
    _oauth_config = OAuthConfig()
    return _oauth_config


# Convenience functions for backward compatibility
def get_oauth_base_url() -> str:
    """Get OAuth base URL."""
    return get_oauth_config().get_oauth_base_url()


def get_redirect_uris() -> List[str]:
    """Get all valid OAuth redirect URIs."""
    return get_oauth_config().get_redirect_uris()


def get_allowed_origins() -> List[str]:
    """Get allowed CORS origins."""
    return get_oauth_config().get_allowed_origins()


def is_oauth_configured() -> bool:
    """Check if OAuth is properly configured."""
    return get_oauth_config().is_configured()


def set_transport_mode(mode: str) -> None:
    """Set the current transport mode."""
    get_oauth_config().set_transport_mode(mode)


def get_transport_mode() -> str:
    """Get the current transport mode."""
    return get_oauth_config().get_transport_mode()


def is_oauth21_enabled() -> bool:
    """Check if OAuth 2.1 is enabled."""
    return get_oauth_config().is_oauth21_enabled()


def get_oauth_redirect_uri() -> str:
    """Get the primary OAuth redirect URI."""
    return get_oauth_config().redirect_uri


def is_stateless_mode() -> bool:
    """Check if stateless mode is enabled."""
    return get_oauth_config().stateless_mode


def is_external_oauth21_provider() -> bool:
    """Check if external OAuth 2.1 provider mode is enabled."""
    return get_oauth_config().is_external_oauth21_provider()
