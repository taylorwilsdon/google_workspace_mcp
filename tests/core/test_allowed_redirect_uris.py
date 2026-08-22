"""Tests for _parse_allowed_redirect_uris in core/server.py.

Findings 23, 28, 51: an unset WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS used to
produce ``None``, which FastMCP reads as "accept any client-supplied redirect URI"
during Dynamic Client Registration. These tests pin the replacement contract: the
allowlist is mandatory, and every entry must be exact enough that a registered
client cannot steer authorization codes somewhere else.
"""

import pytest

from core.server import _parse_allowed_redirect_uris


class TestAllowlistIsMandatory:
    """No allowlist means no safe default, so startup must fail."""

    @pytest.mark.parametrize("value", [None, "", "   ", ",,,", " , , "])
    def test_missing_or_empty_raises(self, value):
        with pytest.raises(
            ValueError, match="WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS"
        ):
            _parse_allowed_redirect_uris(value)


class TestAcceptedEntries:
    def test_single_https_uri(self):
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
        result = _parse_allowed_redirect_uris(
            "https://a.example/callback,,https://b.example/callback,"
        )
        assert result == [
            "https://a.example/callback",
            "https://b.example/callback",
        ]

    def test_duplicates_are_collapsed_in_order(self):
        result = _parse_allowed_redirect_uris(
            "https://b.example/cb,https://a.example/cb,https://b.example/cb"
        )
        assert result == ["https://b.example/cb", "https://a.example/cb"]

    @pytest.mark.parametrize(
        "uri",
        [
            # RFC 8252 §7.3: a native client picks an ephemeral loopback port, so the
            # port wildcard is required and keeps the code on the user's own machine.
            "http://localhost:*/callback",
            "http://127.0.0.1:*/callback",
            "http://[::1]:*/callback",
            "http://localhost:8080/callback",
            "http://127.0.0.1/callback",
            "https://localhost:*/callback",
        ],
    )
    def test_loopback_forms_are_allowed(self, uri):
        assert _parse_allowed_redirect_uris(uri) == [uri]


class TestRejectedEntries:
    @pytest.mark.parametrize(
        ("uri", "reason"),
        [
            # A subdomain wildcard lets anyone who controls one subdomain collect codes.
            ("https://*.example.com/callback", "host wildcard"),
            ("https://*/callback", "host wildcard"),
            # fnmatch path globs match across "/", so /auth/* also covers /auth/../x.
            ("https://app.example.com/auth/*", "path wildcard"),
            # Remote http would expose the code to network observers.
            ("http://app.example.com/callback", "https"),
            # A remote port wildcard widens the allowlist for no RFC 8252 benefit.
            ("https://app.example.com:*/callback", "port wildcard"),
            # Classic allowlist bypass: the real host is evil.example.
            ("https://localhost@evil.example/callback", "userinfo"),
            # Browser-executable schemes must never be registrable.
            ("javascript:alert(1)", "unsafe scheme"),
            ("data:text/html,x", "unsafe scheme"),
            ("file:///etc/passwd", "unsafe scheme"),
            # Non-absolute or hostless entries cannot be matched exactly.
            ("/callback", "scheme"),
            ("https:///callback", "host"),
            ("ftp://example.com/callback", "unsupported scheme"),
        ],
    )
    def test_entry_is_rejected(self, uri, reason):
        with pytest.raises(ValueError, match=reason):
            _parse_allowed_redirect_uris(uri)

    def test_one_bad_entry_rejects_the_whole_allowlist(self):
        """Partial acceptance would silently keep a wildcard in the effective list."""
        with pytest.raises(ValueError, match="host wildcard"):
            _parse_allowed_redirect_uris(
                "https://good.example/callback,https://*.evil.example/callback"
            )
