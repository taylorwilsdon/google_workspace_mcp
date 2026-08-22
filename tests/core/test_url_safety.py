"""Finding 26: link URLs written into documents must pass a scheme allowlist.

Deciding on the raw string is not enough. Browsers and Docs' own link handling tolerate
whitespace, embedded control characters and mixed case around a scheme, so the value is
normalised before being matched. Percent-encoded schemes are checked too, because an
encoded scheme is invisible to a single-pass check but may be decoded before use.
"""

import pytest

from core.url_safety import (
    ALLOWED_LINK_SCHEMES,
    is_safe_link_url,
    sanitize_link_url,
    validate_link_url,
)


class TestAllowed:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/page",
            "http://example.com",
            "HTTPS://EXAMPLE.COM/Page",
            "https://example.com/a?b=c#d",
            "mailto:someone@example.com",
        ],
    )
    def test_accepted(self, url):
        assert is_safe_link_url(url) is True

    def test_none_means_no_link(self):
        assert validate_link_url(None) == (True, "")

    def test_allowlist_contents(self):
        assert ALLOWED_LINK_SCHEMES == {"http", "https", "mailto"}


class TestDangerousSchemes:
    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(document.cookie)",
            "JavaScript:alert(1)",
            "JAVASCRIPT:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
            "blob:https://example.com/uuid",
            "about:blank",
        ],
    )
    def test_rejected(self, url):
        is_valid, reason = validate_link_url(url)
        assert is_valid is False
        assert reason


class TestNormalisation:
    @pytest.mark.parametrize(
        "url",
        [
            " javascript:alert(1)",
            "\tjavascript:alert(1)",
            "\njavascript:alert(1)",
            # Browsers strip these from inside the scheme, so "java\tscript:" runs.
            "java\tscript:alert(1)",
            "java\nscript:alert(1)",
            "java\rscript:alert(1)",
            "javascript\t:alert(1)",
            "jav\x00ascript:alert(1)",
            "\u200bjavascript:alert(1)",
            "\ufeffjavascript:alert(1)",
        ],
    )
    def test_obfuscated_javascript_is_rejected(self, url):
        assert is_safe_link_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "%6aavascript:alert(1)",
            "%6A%61vascript:alert(1)",
            # Double-encoded: a single decode still hides the scheme.
            "%256a%2561vascript:alert(1)",
        ],
    )
    def test_percent_encoded_schemes_are_rejected(self, url):
        assert is_safe_link_url(url) is False

    def test_a_legitimate_url_with_encoded_path_is_still_allowed(self):
        """Normalisation must not reject ordinary escaping in the path or query."""
        assert is_safe_link_url("https://example.com/a%20b?q=%3D%26") is True


class TestMalformed:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "example.com/page",  # no scheme
            "https://",  # no host
            "mailto:",  # no address
            "ftp://example.com/f",  # not in the allowlist
        ],
    )
    def test_rejected(self, url):
        assert is_safe_link_url(url) is False

    def test_non_string_is_rejected(self):
        is_valid, reason = validate_link_url(42)
        assert is_valid is False
        assert "string" in reason


class TestSanitize:
    def test_safe_url_passes_through(self):
        assert sanitize_link_url("https://example.com") == "https://example.com"

    def test_unsafe_url_becomes_none(self, caplog):
        with caplog.at_level("WARNING", logger="core.url_safety"):
            assert sanitize_link_url("javascript:alert(1)") is None

        assert any("Dropping unsafe link" in r.getMessage() for r in caplog.records)

    def test_none_passes_through(self):
        assert sanitize_link_url(None) is None
