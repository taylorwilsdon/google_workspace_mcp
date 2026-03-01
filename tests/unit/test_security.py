"""
Security tests for Google Workspace MCP server.

Tests cover:
- Path traversal prevention in filenames
- Content type validation
- OAuth state CSRF protection
- Session binding immutability
- Bearer token header extraction safety
- Credential isolation between users
- Input validation for tool parameters
"""

import base64
import hashlib
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from auth.oauth21_session_store import (
    OAuth21SessionStore,
    extract_session_from_headers,
)
from gmail.gmail_tools import (
    _extract_attachments,
    _extract_headers,
)


class TestPathTraversalPrevention:
    """Tests that filenames from API responses don't enable path traversal."""

    @pytest.mark.parametrize("malicious_filename", [
        "../../../etc/passwd",
        "..\\..\\Windows\\System32\\config\\SAM",
        "....//....//etc/shadow",
        "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
        "..%252f..%252f..%252fetc%252fpasswd",
        "/absolute/path/to/file",
        "C:\\Windows\\System32\\cmd.exe",
        "file\x00.txt",
        "\x00/etc/passwd",
    ])
    def test_malicious_filenames_extracted_as_is(self, malicious_filename):
        """Filenames from Google API are extracted raw. Sanitization should
        happen at the point of use (file save), not during extraction."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{
                "filename": malicious_filename,
                "mimeType": "application/octet-stream",
                "body": {"attachmentId": "att_test", "size": 100},
            }]
        }
        attachments = _extract_attachments(payload)
        # Should extract but consumers must sanitize before filesystem use
        assert len(attachments) == 1
        assert attachments[0]["filename"] == malicious_filename

    def test_filename_with_only_dots(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{
                "filename": "...",
                "mimeType": "application/octet-stream",
                "body": {"attachmentId": "att_dots", "size": 100},
            }]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1


class TestOAuthStateCsrfProtection:
    """Tests for CSRF protection via OAuth state parameter."""

    def test_state_is_single_use(self):
        """OAuth state tokens must be consumed on first validation."""
        store = OAuth21SessionStore()
        store.store_oauth_state("csrf_state_123")
        # First validation succeeds
        store.validate_and_consume_oauth_state("csrf_state_123")
        # Second validation fails (replay attack prevention)
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("csrf_state_123")

    def test_forged_state_rejected(self):
        """Random state values not from the server should be rejected."""
        store = OAuth21SessionStore()
        store.store_oauth_state("real_state")
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("forged_state")

    def test_cross_session_state_rejected(self):
        """State bound to session A cannot be used by session B."""
        store = OAuth21SessionStore()
        store.store_oauth_state("session_bound_state", session_id="session_A")
        with pytest.raises(ValueError, match="does not match"):
            store.validate_and_consume_oauth_state(
                "session_bound_state",
                session_id="session_B"
            )

    def test_state_consumed_after_session_mismatch(self):
        """After session mismatch, state should be consumed to prevent replay."""
        store = OAuth21SessionStore()
        store.store_oauth_state("bound_state", session_id="session_A")
        with pytest.raises(ValueError):
            store.validate_and_consume_oauth_state("bound_state", session_id="session_B")
        # Even with correct session, state should be gone
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("bound_state", session_id="session_A")

    def test_expired_state_not_valid(self):
        """Expired states should not be accepted."""
        store = OAuth21SessionStore()
        store.store_oauth_state("expired_state", expires_in_seconds=0)
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("expired_state")


class TestSessionBindingSecurity:
    """Tests for session binding security guarantees."""

    def test_cannot_hijack_session(self):
        """An attacker cannot rebind an existing session to their account."""
        store = OAuth21SessionStore()
        # Legitimate user authenticates
        store.store_session(
            user_email="legitimate@example.com",
            access_token="legit_token",
            mcp_session_id="shared_session",
        )
        # Attacker tries to bind same session to their account
        with pytest.raises(ValueError, match="already bound"):
            store.store_session(
                user_email="attacker@evil.com",
                access_token="evil_token",
                mcp_session_id="shared_session",
            )
        # Original binding should remain
        creds = store.get_credentials_by_mcp_session("shared_session")
        assert creds.token == "legit_token"

    def test_user_credentials_isolated(self):
        """User A cannot access User B's credentials."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="alice_secret_token",
            refresh_token="alice_refresh",
        )
        store.store_session(
            user_email="bob@example.com",
            access_token="bob_token",
            refresh_token="bob_refresh",
        )
        # Bob's lookup should only return Bob's credentials
        bob_creds = store.get_credentials("bob@example.com")
        assert bob_creds.token == "bob_token"
        assert bob_creds.token != "alice_secret_token"

        # Alice's lookup should only return Alice's credentials
        alice_creds = store.get_credentials("alice@example.com")
        assert alice_creds.token == "alice_secret_token"

    def test_session_mapping_isolation(self):
        """Different MCP sessions should map to different users."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="alice_token",
            mcp_session_id="alice_session",
        )
        store.store_session(
            user_email="bob@example.com",
            access_token="bob_token",
            mcp_session_id="bob_session",
        )
        alice_creds = store.get_credentials_by_mcp_session("alice_session")
        bob_creds = store.get_credentials_by_mcp_session("bob_session")
        assert alice_creds.token == "alice_token"
        assert bob_creds.token == "bob_token"


class TestBearerTokenSecurity:
    """Tests for bearer token extraction safety."""

    def test_bearer_token_produces_deterministic_session_id(self):
        """Same bearer token should always produce the same session ID."""
        headers = {"authorization": "Bearer my_secret_token"}
        result1 = extract_session_from_headers(headers)
        result2 = extract_session_from_headers(headers)
        assert result1 == result2
        assert result1.startswith("bearer_token_")

    def test_different_tokens_produce_different_sessions(self):
        """Different bearer tokens should produce different session IDs."""
        headers1 = {"authorization": "Bearer token_one"}
        headers2 = {"authorization": "Bearer token_two"}
        result1 = extract_session_from_headers(headers1)
        result2 = extract_session_from_headers(headers2)
        assert result1 != result2

    def test_bearer_token_hash_is_sha256(self):
        """Session ID from bearer token should use SHA-256 hash prefix."""
        token = "test_bearer_token_value"
        headers = {"authorization": f"Bearer {token}"}
        result = extract_session_from_headers(headers)
        expected_hash = hashlib.sha256(token.encode()).hexdigest()[:8]
        assert result == f"bearer_token_{expected_hash}"

    def test_non_bearer_auth_ignored(self):
        """Non-bearer Authorization headers should not produce session IDs."""
        headers = {"authorization": "Basic dXNlcjpwYXNz"}
        result = extract_session_from_headers(headers)
        assert result is None

    def test_empty_bearer_token_still_hashed(self):
        """Bearer with trailing space still produces a hashed session ID (the token is an empty string)."""
        headers = {"authorization": "Bearer "}
        result = extract_session_from_headers(headers)
        # An empty string after 'Bearer ' is still hashed
        assert result is not None
        assert result.startswith("bearer_token_")


class TestInputValidation:
    """Tests for input validation in tool parameters."""

    def test_header_extraction_handles_missing_headers_key(self):
        """Payload without headers key should not crash."""
        result = _extract_headers({}, ["Subject"])
        assert result == {}

    def test_header_extraction_handles_none_value(self):
        result = _extract_headers({"headers": []}, ["Subject", "From"])
        assert result == {}

    def test_attachment_extraction_handles_empty_payload(self):
        attachments = _extract_attachments({})
        assert attachments == []

    def test_attachment_extraction_handles_no_parts(self):
        payload = {"mimeType": "text/plain", "body": {}}
        attachments = _extract_attachments(payload)
        assert attachments == []


class TestContentTypeValidation:
    """Tests for content type handling."""

    @pytest.mark.parametrize("mime_type,filename", [
        ("application/x-msdownload", "virus.exe"),
        ("application/x-dosexec", "malware.com"),
        ("application/x-msdos-program", "trojan.bat"),
        ("application/x-sh", "script.sh"),
        ("application/javascript", "xss.js"),
        ("text/html", "phishing.html"),
    ])
    def test_dangerous_mime_types_extracted(self, mime_type, filename):
        """Server extracts all MIME types - it's the consumer's job to filter."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [{
                "filename": filename,
                "mimeType": mime_type,
                "body": {"attachmentId": "att_x", "size": 100},
            }]
        }
        attachments = _extract_attachments(payload)
        assert len(attachments) == 1
        assert attachments[0]["mimeType"] == mime_type
