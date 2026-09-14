"""Tests for atomic credential persistence (issue #1104).

LocalDirectoryCredentialStore.store_credential must write via a temp file +
fsync + os.replace() so readers never observe a partially-written or
truncated credential file.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from auth.credential_store import LocalDirectoryCredentialStore


@pytest.fixture
def cred_store(tmp_path):
    return LocalDirectoryCredentialStore(base_dir=str(tmp_path / "creds"))


def _make_credentials(token="access-token", refresh_token="refresh-token"):
    creds = MagicMock()
    creds.token = token
    creds.refresh_token = refresh_token
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.client_id = "client-id"
    creds.client_secret = "client-secret"
    creds.scopes = ["openid"]
    creds.expiry = None
    return creds


def test_store_credential_leaves_no_temp_file_on_success(cred_store):
    result = cred_store.store_credential("user@example.com", _make_credentials())
    assert result is True

    entries = os.listdir(cred_store.base_dir)
    assert entries == ["user@example.com.json"]
    assert not any(name.startswith(".tmp-") for name in entries)


def test_store_credential_writes_fully_readable_content(cred_store):
    cred_store.store_credential(
        "user@example.com", _make_credentials(token="tok-1", refresh_token="ref-1")
    )

    creds_path = cred_store._get_credential_path("user@example.com")
    with open(creds_path) as f:
        data = json.load(f)

    assert data["token"] == "tok-1"
    assert data["refresh_token"] == "ref-1"


def test_store_credential_uses_replace_not_in_place_write(cred_store, monkeypatch):
    """Verify the atomic-rename call is actually made, not a plain write."""
    replace_calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        replace_calls.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy_replace)

    cred_store.store_credential("user@example.com", _make_credentials())

    assert len(replace_calls) == 1
    src, dst = replace_calls[0]
    assert dst == cred_store._get_credential_path("user@example.com")
    # The temp file must live in the same directory as the destination so the
    # rename is atomic (no cross-filesystem copy).
    assert os.path.dirname(src) == os.path.dirname(dst)
    assert src != dst


def test_store_credential_cleans_up_temp_file_on_write_failure(cred_store, monkeypatch):
    """A failure between temp-file write and rename must not leave debris behind."""

    def boom(src, dst):  # noqa: ARG001
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", boom)

    result = cred_store.store_credential("user@example.com", _make_credentials())

    assert result is False
    # No stray temp files, and no corrupted destination file either.
    entries = os.listdir(cred_store.base_dir)
    assert entries == []


def test_store_credential_overwrite_never_exposes_partial_content(cred_store):
    """A second store_credential call replaces content atomically, never truncating in place."""
    cred_store.store_credential(
        "user@example.com", _make_credentials(token="tok-1", refresh_token="ref-1")
    )
    cred_store.store_credential(
        "user@example.com", _make_credentials(token="tok-2", refresh_token="ref-2")
    )

    creds_path = cred_store._get_credential_path("user@example.com")
    with open(creds_path) as f:
        data = json.load(f)

    assert data["token"] == "tok-2"
    assert data["refresh_token"] == "ref-2"
    assert not any(name.startswith(".tmp-") for name in os.listdir(cred_store.base_dir))
