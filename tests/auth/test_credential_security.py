"""Tests for credential store security hardening.

Covers file permissions, directory permissions, and path traversal prevention.
"""

import json
import os
import stat
from unittest.mock import MagicMock

import pytest

from auth.credential_store import LocalDirectoryCredentialStore


@pytest.fixture
def cred_store(tmp_path):
    """Create a LocalDirectoryCredentialStore with a temp base directory."""
    return LocalDirectoryCredentialStore(base_dir=str(tmp_path / "creds"))


class TestDirectoryPermissions:
    """Credential directory must be created with 0700."""

    def test_directory_created_with_0700(self, cred_store):
        """_get_credential_path creates base_dir with mode 0700."""
        cred_store._get_credential_path("test@example.com")
        mode = stat.S_IMODE(os.stat(cred_store.base_dir).st_mode)
        assert mode == 0o700, f"Expected 0700, got {oct(mode)}"


class TestFilePermissions:
    """Credential files must be written with 0600."""

    def test_credential_file_created_with_0600(self, cred_store):
        """store_credential writes JSON with mode 0600."""
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        mock_creds.refresh_token = "rtok"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "cid"
        mock_creds.client_secret = "csec"
        mock_creds.scopes = ["openid"]
        mock_creds.expiry = None

        result = cred_store.store_credential("user@example.com", mock_creds)
        assert result is True

        cred_path = os.path.join(cred_store.base_dir, "user@example.com.json")
        mode = stat.S_IMODE(os.stat(cred_path).st_mode)
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_credential_file_content_valid(self, cred_store):
        """Stored credential file contains valid JSON with expected keys."""
        mock_creds = MagicMock()
        mock_creds.token = "access_token_value"
        mock_creds.refresh_token = "refresh_token_value"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "client_id_value"
        mock_creds.client_secret = "client_secret_value"
        mock_creds.scopes = ["openid", "email"]
        mock_creds.expiry = None

        cred_store.store_credential("user@example.com", mock_creds)

        cred_path = os.path.join(cred_store.base_dir, "user@example.com.json")
        with open(cred_path) as f:
            data = json.load(f)

        assert data["token"] == "access_token_value"
        assert data["refresh_token"] == "refresh_token_value"
        assert data["client_id"] == "client_id_value"


class TestPathTraversal:
    """user_email must be sanitized before use in file paths."""

    def test_traversal_chars_sanitized(self, cred_store):
        """Path separators and traversal sequences are replaced with underscores."""
        path = cred_store._get_credential_path("../../etc/evil@gmail.com")
        filename = os.path.basename(path)
        # Dots are kept (valid in emails), slashes become underscores
        assert filename == ".._.._etc_evil@gmail.com.json"

    def test_slash_in_email_sanitized(self, cred_store):
        """Forward slashes in email are replaced."""
        path = cred_store._get_credential_path("user/admin@gmail.com")
        filename = os.path.basename(path)
        assert filename == "user_admin@gmail.com.json"

    def test_backslash_in_email_sanitized(self, cred_store):
        """Backslashes in email are replaced."""
        path = cred_store._get_credential_path("user\\admin@gmail.com")
        filename = os.path.basename(path)
        assert filename == "user_admin@gmail.com.json"

    def test_resolved_path_under_base_dir(self, cred_store):
        """Resolved path must remain within base_dir."""
        # Even after sanitization, verify the path stays under base_dir
        path = cred_store._get_credential_path("normal@gmail.com")
        resolved = os.path.realpath(path)
        assert resolved.startswith(os.path.realpath(cred_store.base_dir))

    def test_normal_email_unchanged(self, cred_store):
        """Normal email addresses pass through sanitization unchanged."""
        path = cred_store._get_credential_path("alice@example.com")
        filename = os.path.basename(path)
        assert filename == "alice@example.com.json"

    def test_email_with_dots_and_hyphens(self, cred_store):
        """Dots and hyphens are allowed in email addresses."""
        path = cred_store._get_credential_path("first.last-name@my-domain.co.uk")
        filename = os.path.basename(path)
        assert filename == "first.last-name@my-domain.co.uk.json"

    def test_null_bytes_sanitized(self, cred_store):
        """Null bytes in email are replaced."""
        path = cred_store._get_credential_path("user\x00@gmail.com")
        filename = os.path.basename(path)
        assert "\x00" not in filename
        assert filename == "user_@gmail.com.json"
