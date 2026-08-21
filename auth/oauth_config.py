"""
OAuth Configuration Management

This module centralizes OAuth-related configuration to eliminate hardcoded values
scattered throughout the codebase. It provides environment variable support and
sensible defaults for all OAuth-related settings.

Supports both OAuth 2.0 and OAuth 2.1 with automatic client capability detection.
"""

import os
from ipaddress import ip_address
from threading import RLock
from urllib.parse import urlparse
from typing import List, Optional, Dict, Any


_ASYMMETRIC_JWT_ALGORITHM_FAMILIES = {
    "ES": frozenset({"ES256", "ES256K", "ES384", "ES512", "ES521"}),
    "EdDSA": frozenset({"EdDSA"}),
    "PS": frozenset({"PS256", "PS384", "PS512"}),
    "RS": frozenset({"RS256", "RS384", "RS512"}),
}


def _is_loopback_host(hostname: Optional[str]) -> bool:
    """Return whether a JWKS hostname is explicitly local-only."""
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _is_secure_gateway_jwks_url(url: str) -> bool:
    """Require HTTPS except for explicit HTTP loopback development endpoints."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError:
        return False
    return bool(
        hostname
        and (
            parsed.scheme == "https"
            or (parsed.scheme == "http" and _is_loopback_host(hostname))
        )
    )


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
        if os.getenv("WORKSPACE_MCP_RESOLVED_PORT") == "1":
            self.port = int(os.getenv("WORKSPACE_MCP_PORT", os.getenv("PORT", "8000")))
        else:
            self.port = int(os.getenv("PORT", os.getenv("WORKSPACE_MCP_PORT", "8000")))
        self.base_url = f"{self.base_uri}:{self.port}"

        # External URL for reverse proxy scenarios
        self.external_url = os.getenv("WORKSPACE_EXTERNAL_URL")

        # OAuth client configuration
        self.client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")

        # Branding for the OAuth consent page. FastMCP's OAuth proxy renders the
        # server's name / icon / website on the consent screen; these env vars feed
        # those server fields. All optional — unset leaves the upstream defaults.
        self.brand_name = os.getenv("WORKSPACE_MCP_BRAND_NAME")
        self.brand_icon_url = os.getenv("WORKSPACE_MCP_BRAND_ICON_URL")
        self.brand_website_url = os.getenv("WORKSPACE_MCP_BRAND_WEBSITE_URL")

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

        # Trusted-gateway identity (provider-agnostic).
        # An MCP-aware reverse proxy (e.g. Pomerium, oauth2-proxy, Cloudflare Access,
        # Istio/Envoy, Traefik ForwardAuth) authenticates the user and injects a SIGNED
        # identity assertion (a JWT) on every upstream request. This server verifies that
        # assertion against the proxy's JWKS and uses the asserted email as the per-request
        # principal — WITHOUT terminating MCP OAuth itself (MCP_ENABLE_OAUTH21 stays off), so
        # it composes with the proxy instead of fighting it for the single MCP auth handshake.
        # Credentials are still the legacy per-user Google grants (keyed by email); the asserted
        # identity just selects/locks which user's grant a request may use (true per-user isolation).
        # Defaults target Pomerium; override the header/algorithm for other providers.
        self.trust_gateway_identity = (
            os.getenv("TRUST_GATEWAY_IDENTITY", "false").lower() == "true"
        )
        self.gateway_identity_jwks_url = (
            os.getenv("GATEWAY_IDENTITY_JWKS_URL", "").strip() or None
        )
        # Header carrying the signed assertion (default: Pomerium's x-pomerium-jwt-assertion;
        # e.g. cf-access-jwt-assertion for Cloudflare Access).
        self.gateway_identity_header = (
            os.getenv("GATEWAY_IDENTITY_HEADER", "x-pomerium-jwt-assertion")
            .strip()
            .lower()
        )
        # Allowed signing algorithm(s), comma-separated (default ES256 — Pomerium; use
        # RS256 for Cloudflare Access, etc.). Pinned to block alg-confusion/none attacks.
        self.gateway_identity_algorithms = [
            a.strip()
            for a in os.getenv("GATEWAY_IDENTITY_ALGORITHMS", "ES256").split(",")
            if a.strip()
        ]
        # Audience binds assertions to this relying party and is mandatory. Issuer
        # pinning remains optional for gateways whose JWKS URL is the trust boundary.
        self.gateway_identity_issuer = (
            os.getenv("GATEWAY_IDENTITY_ISSUER", "").strip() or None
        )
        self.gateway_identity_audience = (
            os.getenv("GATEWAY_IDENTITY_AUDIENCE", "").strip() or None
        )
        if self.trust_gateway_identity:
            if self.oauth21_enabled:
                raise ValueError(
                    "TRUST_GATEWAY_IDENTITY is incompatible with MCP_ENABLE_OAUTH21=true. "
                    "In trusted-gateway mode the proxy owns the MCP handshake; keep "
                    "MCP_ENABLE_OAUTH21=false."
                )
            if not self.gateway_identity_jwks_url:
                raise ValueError(
                    "TRUST_GATEWAY_IDENTITY=true requires GATEWAY_IDENTITY_JWKS_URL "
                    "(the gateway's JWKS endpoint used to verify the identity assertion)."
                )
            if not _is_secure_gateway_jwks_url(self.gateway_identity_jwks_url):
                raise ValueError(
                    "GATEWAY_IDENTITY_JWKS_URL must use HTTPS; HTTP is permitted "
                    "only for loopback development endpoints."
                )
            if not self.gateway_identity_algorithms:
                raise ValueError(
                    "TRUST_GATEWAY_IDENTITY=true requires GATEWAY_IDENTITY_ALGORITHMS "
                    "to list at least one signing algorithm (e.g. ES256)."
                )
            algorithm_families = {
                family
                for family, algorithms in _ASYMMETRIC_JWT_ALGORITHM_FAMILIES.items()
                if any(
                    algorithm in algorithms
                    for algorithm in self.gateway_identity_algorithms
                )
            }
            allowed_algorithms = set().union(
                *_ASYMMETRIC_JWT_ALGORITHM_FAMILIES.values()
            )
            invalid_algorithms = [
                algorithm
                for algorithm in self.gateway_identity_algorithms
                if algorithm not in allowed_algorithms
            ]
            if invalid_algorithms:
                raise ValueError(
                    "GATEWAY_IDENTITY_ALGORITHMS must contain only supported "
                    "asymmetric JWT algorithms."
                )
            if len(algorithm_families) != 1:
                raise ValueError(
                    "GATEWAY_IDENTITY_ALGORITHMS must use a single asymmetric "
                    "JWT algorithm family."
                )
            if not self.gateway_identity_header:
                raise ValueError(
                    "TRUST_GATEWAY_IDENTITY=true requires a non-empty "
                    "GATEWAY_IDENTITY_HEADER."
                )
            if not self.gateway_identity_audience:
                raise ValueError(
                    "TRUST_GATEWAY_IDENTITY=true requires GATEWAY_IDENTITY_AUDIENCE "
                    "(the audience that identifies this MCP deployment)."
                )

        # Stateless mode configuration
        self.stateless_mode = (
            os.getenv("WORKSPACE_MCP_STATELESS_MODE", "false").lower() == "true"
        )
        if self.stateless_mode and not self.oauth21_enabled:
            raise ValueError(
                "WORKSPACE_MCP_STATELESS_MODE requires MCP_ENABLE_OAUTH21=true"
            )

        # Service account (domain-wide delegation) configuration
        self.service_account_key_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_FILE")
        self.service_account_key_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON")
        if self.service_account_key_file and self.service_account_key_json:
            raise ValueError(
                "Only one service account key source may be provided. "
                "Set either GOOGLE_SERVICE_ACCOUNT_KEY_FILE or "
                "GOOGLE_SERVICE_ACCOUNT_KEY_JSON, not both."
            )
        self.service_account_enabled = bool(
            self.service_account_key_file or self.service_account_key_json
        )
        if self.service_account_enabled and self.oauth21_enabled:
            raise ValueError(
                "Service account mode is incompatible with OAuth 2.1 mode. "
                "Set GOOGLE_SERVICE_ACCOUNT_KEY_FILE or GOOGLE_SERVICE_ACCOUNT_KEY_JSON, "
                "but not MCP_ENABLE_OAUTH21=true."
            )

        # Optional per-request impersonation domain allowlist for service accounts.
        _raw_domains = os.getenv("DWD_ALLOWED_DOMAINS", "")
        self.dwd_allowed_domains: List[str] = (
            [d.strip().lower() for d in _raw_domains.split(",") if d.strip()]
            if self.service_account_enabled and _raw_domains
            else []
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
        if self.client_secret:
            _set_if_absent(
                "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET", self.client_secret
            )
        _set_if_absent("FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL", self.get_oauth_base_url())
        _set_if_absent("FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH", self.redirect_path)

    def is_public_client(self) -> bool:
        """Return True when only a client_id is configured (no client_secret)."""
        return bool(self.client_id and not self.client_secret)

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
        return bool(self.client_id)

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
            "client_secret_configured": bool(self.client_secret),
            "public_client": self.is_public_client(),
            "oauth21_enabled": self.oauth21_enabled,
            "external_oauth21_provider": self.external_oauth21_provider,
            "pkce_required": self.pkce_required,
            "service_account_enabled": self.service_account_enabled,
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

    def is_service_account_enabled(self) -> bool:
        """
        Check if service account (domain-wide delegation) mode is enabled.

        Returns:
            True if service account mode is enabled
        """
        return self.service_account_enabled

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
            "token_endpoint_auth_methods_supported": (
                ["none"]
                if self.is_public_client()
                else ["client_secret_post", "client_secret_basic"]
            ),
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


# Global configuration instance with thread-safe access
_oauth_config = None
_oauth_config_lock = RLock()


def get_oauth_config() -> OAuthConfig:
    """
    Get the global OAuth configuration instance.

    Thread-safe singleton accessor.

    Returns:
        The singleton OAuth configuration instance
    """
    global _oauth_config
    with _oauth_config_lock:
        if _oauth_config is None:
            _oauth_config = OAuthConfig()
        return _oauth_config


def reload_oauth_config() -> OAuthConfig:
    """
    Reload the OAuth configuration from environment variables.

    Thread-safe reload that prevents races with concurrent access.

    Returns:
        The reloaded OAuth configuration instance
    """
    global _oauth_config
    with _oauth_config_lock:
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


def is_trust_gateway_identity() -> bool:
    """Check if trusted-gateway identity (signed assertion) mode is enabled."""
    return get_oauth_config().trust_gateway_identity


def is_service_account_enabled() -> bool:
    """Check if service account (domain-wide delegation) mode is enabled."""
    return get_oauth_config().is_service_account_enabled()
