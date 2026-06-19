"""Tests for credential store security hardening.

Covers file permissions, directory permissions, and path traversal prevention.
"""

import json
import os
import stat
from unittest.mock import MagicMock

import pytest

from auth.credential_store import CredentialStore, LocalDirectoryCredentialStore


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

        cred_path = os.path.join(
            cred_store.base_dir, f"user@example.com{CredentialStore.FILE_EXTENSION}"
        )
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

        cred_path = os.path.join(
            cred_store.base_dir, f"user@example.com{CredentialStore.FILE_EXTENSION}"
        )
        with open(cred_path) as f:
            data = json.load(f)

        assert data["token"] == "access_token_value"
        assert data["refresh_token"] == "refresh_token_value"
        assert data["client_id"] == "client_id_value"


class TestPathTraversal:
    """user_email must be sanitized before use in file paths."""

    def test_traversal_chars_sanitized(self, cred_store):
        """Path separators and traversal sequences are percent-encoded."""
        path = cred_store._get_credential_path("../../etc/evil@gmail.com")
        filename = os.path.basename(path)
        assert (
            filename
            == f"..%2F..%2Fetc%2Fevil@gmail.com{CredentialStore.FILE_EXTENSION}"
        )

    def test_slash_in_email_sanitized(self, cred_store):
        """Forward slashes in email are percent-encoded."""
        path = cred_store._get_credential_path("user/admin@gmail.com")
        filename = os.path.basename(path)
        assert filename == f"user%2Fadmin@gmail.com{CredentialStore.FILE_EXTENSION}"

    def test_backslash_in_email_sanitized(self, cred_store):
        """Backslashes in email are percent-encoded."""
        path = cred_store._get_credential_path("user\\admin@gmail.com")
        filename = os.path.basename(path)
        assert filename == f"user%5Cadmin@gmail.com{CredentialStore.FILE_EXTENSION}"

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
        assert filename == f"alice@example.com{CredentialStore.FILE_EXTENSION}"

    def test_email_with_dots_and_hyphens(self, cred_store):
        """Dots and hyphens are allowed in email addresses."""
        path = cred_store._get_credential_path("first.last-name@my-domain.co.uk")
        filename = os.path.basename(path)
        assert (
            filename
            == f"first.last-name@my-domain.co.uk{CredentialStore.FILE_EXTENSION}"
        )

    def test_null_bytes_sanitized(self, cred_store):
        """Null bytes in email are percent-encoded."""
        path = cred_store._get_credential_path("user\x00@gmail.com")
        filename = os.path.basename(path)
        assert "\x00" not in filename
        assert filename == f"user%00@gmail.com{CredentialStore.FILE_EXTENSION}"

    def test_plus_sign_prevents_collision(self, cred_store):
        """Distinct emails must not collapse to the same filename."""
        path1 = cred_store._get_credential_path("user+admin@example.com")
        path2 = cred_store._get_credential_path("user_admin@example.com")

        assert (
            os.path.basename(path1)
            == f"user%2Badmin@example.com{CredentialStore.FILE_EXTENSION}"
        )
        assert (
            os.path.basename(path2)
            == f"user_admin@example.com{CredentialStore.FILE_EXTENSION}"
        )
        assert path1 != path2

    def test_list_users_decodes_percent_encoded_email(self, cred_store):
        """User enumeration returns the original email, not the encoded key."""
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        mock_creds.refresh_token = "rtok"
        mock_creds.token_uri = "https://oauth2.googleapis.com/token"
        mock_creds.client_id = "cid"
        mock_creds.client_secret = "csec"
        mock_creds.scopes = ["openid"]
        mock_creds.expiry = None

        cred_store.store_credential("user+admin@example.com", mock_creds)

        assert cred_store.list_users() == ["user+admin@example.com"]

    def test_get_credential_path_falls_back_to_legacy_filename(self, cred_store):
        """Existing legacy filenames remain readable after URL-encoding rollout."""
        legacy_path = os.path.join(
            cred_store.base_dir,
            f"user_admin@example.com{CredentialStore.FILE_EXTENSION}",
        )
        os.makedirs(cred_store.base_dir, exist_ok=True)
        with open(legacy_path, "w") as f:
            json.dump({}, f)

        resolved = cred_store._get_credential_path("user+admin@example.com")

        assert resolved == legacy_path

    def test_list_users_includes_legacy_filename_variants(self, cred_store):
        """Legacy sanitized filenames remain discoverable during migration."""
        os.makedirs(cred_store.base_dir, exist_ok=True)

        encoded_path = os.path.join(
            cred_store.base_dir,
            f"user%2Badmin@example.com{CredentialStore.FILE_EXTENSION}",
        )
        legacy_path = os.path.join(
            cred_store.base_dir,
            f"user_admin@example.com{CredentialStore.FILE_EXTENSION}",
        )
        for path in (encoded_path, legacy_path):
            with open(path, "w") as f:
                json.dump({}, f)

        assert cred_store.list_users() == [
            "user+admin@example.com",
            "user_admin@example.com",
        ]
