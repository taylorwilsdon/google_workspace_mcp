"""Tests for credential store file permissions and path safety."""

import json
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.credential_store import LocalDirectoryCredentialStore  # noqa: E402
from google.oauth2.credentials import Credentials  # noqa: E402


def _make_credentials():
    return Credentials(
        token="ya29.test-token",
        refresh_token="1//test-refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client-id.apps.googleusercontent.com",
        client_secret="test-client-secret",
        scopes=["https://www.googleapis.com/auth/drive.readonly"],
    )


class TestCredentialFilePermissions:
    def test_directory_created_with_0700(self, tmp_path):
        creds_dir = tmp_path / "new_creds"
        store = LocalDirectoryCredentialStore(base_dir=str(creds_dir))
        store.store_credential("user@example.com", _make_credentials())

        dir_mode = stat.S_IMODE(os.stat(creds_dir).st_mode)
        assert dir_mode == 0o700, f"Expected 0o700, got {oct(dir_mode)}"

    def test_credential_file_created_with_0600(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        store.store_credential("user@example.com", _make_credentials())

        cred_file = tmp_path / "user@example.com.json"
        file_mode = stat.S_IMODE(os.stat(cred_file).st_mode)
        assert file_mode == 0o600, f"Expected 0o600, got {oct(file_mode)}"

    def test_credential_file_contains_valid_json(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        creds = _make_credentials()
        store.store_credential("user@example.com", creds)

        cred_file = tmp_path / "user@example.com.json"
        data = json.loads(cred_file.read_text())
        assert data["token"] == "ya29.test-token"
        assert data["refresh_token"] == "1//test-refresh-token"
        assert data["client_id"] == "test-client-id.apps.googleusercontent.com"

    def test_roundtrip_store_and_get(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        creds = _make_credentials()
        store.store_credential("user@example.com", creds)

        loaded = store.get_credential("user@example.com")
        assert loaded is not None
        assert loaded.token == "ya29.test-token"
        assert loaded.refresh_token == "1//test-refresh-token"


class TestPathTraversal:
    def test_slash_in_email_uses_basename(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        store.store_credential("../../etc/passwd", _make_credentials())

        # Should NOT create a file outside base_dir
        assert not os.path.exists("/etc/passwd.json")
        # Should create file using basename only
        assert (tmp_path / "passwd.json").exists()

    def test_dotdot_path_raises_or_stays_contained(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        # Even with traversal attempts, file must stay within base_dir
        store.store_credential("../evil", _make_credentials())
        for f in tmp_path.rglob("*.json"):
            real = os.path.realpath(f)
            assert real.startswith(str(tmp_path.resolve()))

    def test_empty_email_uses_fallback(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        store.store_credential("", _make_credentials())
        assert (tmp_path / "_invalid.json").exists()

    def test_dot_prefixed_email_uses_fallback(self, tmp_path):
        store = LocalDirectoryCredentialStore(base_dir=str(tmp_path))
        store.store_credential(".hidden", _make_credentials())
        assert (tmp_path / "_invalid.json").exists()
