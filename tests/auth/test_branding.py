"""Tests for OAuth consent-page branding config (WORKSPACE_MCP_BRAND_*).

These env vars feed the FastMCP server's name / icon / website, which FastMCP's
OAuth proxy renders on the consent screen (see core/server.py).
"""

from mcp.types import Icon

from auth.oauth_config import OAuthConfig


class TestBrandingConfig:
    def test_brand_env_vars_are_read(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_BRAND_NAME", "Example Workspace")
        monkeypatch.setenv(
            "WORKSPACE_MCP_BRAND_ICON_URL", "https://example.com/logo.png"
        )
        monkeypatch.setenv("WORKSPACE_MCP_BRAND_WEBSITE_URL", "https://example.com")

        cfg = OAuthConfig()

        assert cfg.brand_name == "Example Workspace"
        assert cfg.brand_icon_url == "https://example.com/logo.png"
        assert cfg.brand_website_url == "https://example.com"

    def test_brand_defaults_to_none_when_unset(self, monkeypatch):
        for var in (
            "WORKSPACE_MCP_BRAND_NAME",
            "WORKSPACE_MCP_BRAND_ICON_URL",
            "WORKSPACE_MCP_BRAND_WEBSITE_URL",
        ):
            monkeypatch.delenv(var, raising=False)

        cfg = OAuthConfig()

        assert cfg.brand_name is None
        assert cfg.brand_icon_url is None
        assert cfg.brand_website_url is None

    def test_icon_built_from_brand_icon_url(self, monkeypatch):
        # Mirrors core/server.py: an Icon is constructed from the configured URL (or
        # data URI) and handed to FastMCP, replacing the default consent-page logo.
        monkeypatch.setenv(
            "WORKSPACE_MCP_BRAND_ICON_URL", "data:image/svg+xml;base64,ABC123"
        )
        cfg = OAuthConfig()

        icons = [Icon(src=cfg.brand_icon_url)] if cfg.brand_icon_url else None

        assert icons is not None
        assert icons[0].src == "data:image/svg+xml;base64,ABC123"
