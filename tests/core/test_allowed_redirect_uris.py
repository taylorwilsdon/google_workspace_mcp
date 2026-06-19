"""Tests for _parse_allowed_redirect_uris in core/server.py.

Covers parsing of WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS into the
list[str] | None shape that FastMCP's GoogleProvider expects.
"""

from core.server import _parse_allowed_redirect_uris


class TestParseAllowedRedirectUris:
    def test_none_returns_none(self):
        assert _parse_allowed_redirect_uris(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_allowed_redirect_uris("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_allowed_redirect_uris("   ") is None

    def test_single_uri(self):
        assert _parse_allowed_redirect_uris(
            "https://claude.ai/api/mcp/auth_callback"
        ) == ["https://claude.ai/api/mcp/auth_callback"]

    def test_multiple_uris_comma_separated(self):
        result = _parse_allowed_redirect_uris(
            "https://claude.ai/api/mcp/auth_callback,"
            "https://claude.com/api/mcp/auth_callback"
        )
        assert result == [
            "https://claude.ai/api/mcp/auth_callback",
            "https://claude.com/api/mcp/auth_callback",
        ]

    def test_whitespace_around_entries_is_stripped(self):
        result = _parse_allowed_redirect_uris(
            "  https://a.example/callback  ,  https://b.example/callback  "
        )
        assert result == [
            "https://a.example/callback",
            "https://b.example/callback",
        ]

    def test_empty_entries_are_filtered(self):
        # Trailing comma or double comma should not produce empty strings
        result = _parse_allowed_redirect_uris(
            "https://a.example/callback,,https://b.example/callback,"
        )
        assert result == [
            "https://a.example/callback",
            "https://b.example/callback",
        ]

    def test_only_commas_returns_none(self):
        assert _parse_allowed_redirect_uris(",,,") is None

    def test_wildcard_patterns_preserved(self):
        """Patterns pass through unchanged — FastMCP's matcher interprets them."""
        result = _parse_allowed_redirect_uris(
            "http://localhost:*/callback,http://127.0.0.1:*/callback"
        )
        assert result == [
            "http://localhost:*/callback",
            "http://127.0.0.1:*/callback",
        ]
