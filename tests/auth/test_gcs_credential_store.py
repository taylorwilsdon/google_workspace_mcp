"""Tests for GCSCredentialStore.

Covers round-trip, path sanitisation, CMEK verification flag, atomic
read-modify-write retry, backend selection, and dependency error handling.

GCS is mocked at the client level — these tests do not hit real GCS.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from google.cloud.exceptions import NotFound, PreconditionFailed

from auth import credential_store as cs_module
from auth.credential_store import (
    CredentialStore,
    GCSCredentialStore,
    LocalDirectoryCredentialStore,
    _parse_bool_env,
    get_credential_store,
)


@pytest.fixture
def mock_storage_client():
    """Patch google.cloud.storage.Client; yield the bucket mock for configuration."""
    with patch("google.cloud.storage.Client") as client_cls:
        bucket = MagicMock(name="bucket")
        client_cls.return_value.bucket.return_value = bucket
        # Default: CMEK not configured (None). Tests that need it set it explicitly.
        bucket.default_kms_key_name = None
        yield bucket


@pytest.fixture
def cred_store(mock_storage_client):
    """A GCSCredentialStore wired up to the mocked bucket."""
    return GCSCredentialStore(bucket_name="test-bucket")


@pytest.fixture
def mock_creds():
    creds = MagicMock()
    creds.token = "access_token_value"
    creds.refresh_token = "refresh_token_value"
    creds.token_uri = "https://oauth2.googleapis.com/token"
    creds.client_id = "client_id_value"
    creds.client_secret = "client_secret_value"
    creds.scopes = ["openid", "email"]
    creds.expiry = None
    return creds


def _serialized_payload(creds) -> bytes:
    """The bytes GCSCredentialStore would upload for this credential."""
    return json.dumps(
        {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
            "expiry": None,
        }
    ).encode()


class TestRoundTrip:
    """store/get/delete round-trip correctness against the mocked bucket."""

    def test_get_missing_user_returns_none(self, cred_store, mock_storage_client):
        blob = MagicMock()
        blob.download_as_bytes.side_effect = NotFound("gone")
        mock_storage_client.blob.return_value = blob

        assert cred_store.get_credential("nobody@example.com") is None

    def test_get_returns_credentials(self, cred_store, mock_storage_client, mock_creds):
        blob = MagicMock()
        blob.download_as_bytes.return_value = _serialized_payload(mock_creds)
        mock_storage_client.blob.return_value = blob

        loaded = cred_store.get_credential("user@example.com")
        assert loaded is not None
        assert loaded.token == "access_token_value"
        assert loaded.refresh_token == "refresh_token_value"
        assert loaded.scopes == ["openid", "email"]

    def test_get_propagates_non_notfound_download_error(
        self, cred_store, mock_storage_client
    ):
        blob = MagicMock()
        blob.download_as_bytes.side_effect = RuntimeError("permission denied")
        mock_storage_client.blob.return_value = blob

        with pytest.raises(RuntimeError, match="permission denied"):
            cred_store.get_credential("user@example.com")

    def test_store_uploads_serialized_payload(
        self, cred_store, mock_storage_client, mock_creds
    ):
        blob = MagicMock()
        blob.reload.side_effect = NotFound("new blob")
        mock_storage_client.blob.return_value = blob

        assert cred_store.store_credential("user@example.com", mock_creds) is True

        blob.upload_from_string.assert_called_once()
        args, kwargs = blob.upload_from_string.call_args
        assert args[0] == _serialized_payload(mock_creds)
        assert kwargs["content_type"] == "application/json"
        # New blob → generation 0 precondition
        assert kwargs["if_generation_match"] == 0

    def test_delete_removes_blob(self, cred_store, mock_storage_client):
        blob = MagicMock()
        mock_storage_client.blob.return_value = blob

        assert cred_store.delete_credential("user@example.com") is True
        blob.delete.assert_called_once()

    def test_delete_missing_user_is_idempotent(self, cred_store, mock_storage_client):
        blob = MagicMock()
        blob.delete.side_effect = NotFound("already gone")
        mock_storage_client.blob.return_value = blob

        assert cred_store.delete_credential("nobody@example.com") is True

    def test_list_users_raises_not_implemented(self, cred_store):
        with pytest.raises(NotImplementedError, match="does not support listing users"):
            cred_store.list_users()


class TestAtomicWrite:
    """Store path must use generation preconditions and fail closed on contention."""

    def test_existing_blob_uses_current_generation(
        self, cred_store, mock_storage_client, mock_creds
    ):
        blob = MagicMock()
        blob.reload.return_value = None
        blob.generation = 12345
        mock_storage_client.blob.return_value = blob

        assert cred_store.store_credential("user@example.com", mock_creds) is True
        _, kwargs = blob.upload_from_string.call_args
        assert kwargs["if_generation_match"] == 12345

    def test_precondition_failed_returns_false_without_retry(
        self, cred_store, mock_storage_client, mock_creds
    ):
        """On 412 (concurrent writer), fail fast rather than overwrite with
        stale payload. Caller must re-read and re-derive for the next write."""
        blob = MagicMock()
        blob.reload.return_value = None
        blob.generation = 1
        blob.upload_from_string.side_effect = PreconditionFailed("racing writer")
        mock_storage_client.blob.return_value = blob

        assert cred_store.store_credential("user@example.com", mock_creds) is False
        # Exactly one upload attempt — no retry with the same (potentially stale) payload
        assert blob.upload_from_string.call_count == 1

    def test_reload_non_notfound_error_returns_false(
        self, cred_store, mock_storage_client, mock_creds
    ):
        """Permission / network / quota errors from reload() must not propagate."""
        blob = MagicMock()
        blob.reload.side_effect = Exception("permission denied")
        mock_storage_client.blob.return_value = blob

        # Should return False (error path), not raise
        assert cred_store.store_credential("user@example.com", mock_creds) is False
        blob.upload_from_string.assert_not_called()


class TestCMEKVerification:
    """WORKSPACE_MCP_GCS_REQUIRE_CMEK gates on bucket.default_kms_key_name."""

    def test_require_cmek_passes_when_key_is_set(self, mock_storage_client):
        mock_storage_client.default_kms_key_name = (
            "projects/p/locations/l/keyRings/r/cryptoKeys/k"
        )
        store = GCSCredentialStore(bucket_name="b", require_cmek=True)
        mock_storage_client.reload.assert_not_called()
        store.verify_cmek()
        mock_storage_client.reload.assert_called_once()

    def test_require_cmek_raises_when_key_is_missing(self, mock_storage_client):
        mock_storage_client.default_kms_key_name = None
        store = GCSCredentialStore(bucket_name="b", require_cmek=True)
        with pytest.raises(ValueError, match="no default KMS key"):
            store.verify_cmek()

    def test_require_cmek_raises_when_key_is_empty_string(self, mock_storage_client):
        mock_storage_client.default_kms_key_name = ""
        store = GCSCredentialStore(bucket_name="b", require_cmek=True)
        with pytest.raises(ValueError, match="no default KMS key"):
            store.verify_cmek()

    def test_no_flag_does_not_call_reload(self, mock_storage_client):
        # When require_cmek is False, we should not pay for bucket metadata fetch
        store = GCSCredentialStore(bucket_name="b", require_cmek=False)
        store.verify_cmek()
        mock_storage_client.reload.assert_not_called()

    def test_env_var_truthy_enables_cmek_check(self, mock_storage_client, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_GCS_REQUIRE_CMEK", "true")
        mock_storage_client.default_kms_key_name = None
        store = GCSCredentialStore(bucket_name="b")
        with pytest.raises(ValueError, match="no default KMS key"):
            store.verify_cmek()

    def test_env_var_falsy_skips_cmek_check(self, mock_storage_client, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_GCS_REQUIRE_CMEK", "false")
        mock_storage_client.default_kms_key_name = None
        store = GCSCredentialStore(bucket_name="b")
        store.verify_cmek()
        mock_storage_client.reload.assert_not_called()

    def test_invalid_require_cmek_env_value_raises(
        self, mock_storage_client, monkeypatch
    ):
        monkeypatch.setenv("WORKSPACE_MCP_GCS_REQUIRE_CMEK", "treu")
        with pytest.raises(ValueError, match="Invalid boolean env var"):
            GCSCredentialStore(bucket_name="b")


class TestConfiguration:
    """Bucket and prefix configuration via arg or env var."""

    def test_missing_bucket_raises(self, mock_storage_client, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_GCS_BUCKET", raising=False)
        with pytest.raises(ValueError, match="bucket name"):
            GCSCredentialStore()

    def test_env_bucket_used(self, mock_storage_client, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_GCS_BUCKET", "from-env")
        store = GCSCredentialStore()
        assert store.bucket_name == "from-env"

    def test_prefix_applied_to_blob_names(self, mock_storage_client):
        store = GCSCredentialStore(bucket_name="b", prefix="creds")
        assert (
            store._blob_name("a@example.com")
            == f"creds/a@example.com{CredentialStore.FILE_EXTENSION}"
        )

    def test_prefix_trailing_slash_normalised(self, mock_storage_client):
        store = GCSCredentialStore(bucket_name="b", prefix="creds/")
        assert (
            store._blob_name("a@example.com")
            == f"creds/a@example.com{CredentialStore.FILE_EXTENSION}"
        )

    def test_empty_prefix_produces_flat_names(self, mock_storage_client):
        store = GCSCredentialStore(bucket_name="b", prefix="")
        assert (
            store._blob_name("a@example.com")
            == f"a@example.com{CredentialStore.FILE_EXTENSION}"
        )


class TestPathSanitisation:
    """Email URL-encoding prevents collisions and path traversal."""

    def test_traversal_chars_encoded(self, cred_store):
        assert (
            cred_store._blob_name("../../etc/evil@gmail.com")
            == f"..%2F..%2Fetc%2Fevil@gmail.com{CredentialStore.FILE_EXTENSION}"
        )

    def test_slash_encoded(self, cred_store):
        assert (
            cred_store._blob_name("user/admin@gmail.com")
            == f"user%2Fadmin@gmail.com{CredentialStore.FILE_EXTENSION}"
        )

    def test_normal_email_unchanged(self, cred_store):
        assert (
            cred_store._blob_name("alice@example.com")
            == f"alice@example.com{CredentialStore.FILE_EXTENSION}"
        )

    def test_plus_sign_prevents_collision(self, cred_store):
        """Verify that user+admin@example.com and user_admin@example.com don't collide."""
        blob1 = cred_store._blob_name("user+admin@example.com")
        blob2 = cred_store._blob_name("user_admin@example.com")
        assert blob1 == f"user%2Badmin@example.com{CredentialStore.FILE_EXTENSION}"
        assert blob2 == f"user_admin@example.com{CredentialStore.FILE_EXTENSION}"
        assert blob1 != blob2, "Different users must not map to the same blob"


class TestBackendSelection:
    """get_credential_store() dispatches on WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND."""

    @pytest.fixture(autouse=True)
    def _reset_singleton(self):
        cs_module._credential_store = None
        yield
        cs_module._credential_store = None

    def test_default_backend_is_local_directory(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", raising=False)
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIALS_DIR", str(tmp_path))
        assert isinstance(get_credential_store(), LocalDirectoryCredentialStore)

    def test_gcs_backend_selected(self, monkeypatch, mock_storage_client):
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "gcs")
        monkeypatch.setenv("WORKSPACE_MCP_GCS_BUCKET", "test-bucket")
        monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
        monkeypatch.delenv("WORKSPACE_MCP_GCS_REQUIRE_CMEK", raising=False)
        assert isinstance(get_credential_store(), GCSCredentialStore)

    def test_gcs_backend_requires_oauth21(self, monkeypatch, mock_storage_client):
        """GCS backend must reject single-user mode (no OAuth 2.1) since list_users() is not supported."""
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "gcs")
        monkeypatch.setenv("WORKSPACE_MCP_GCS_BUCKET", "test-bucket")
        monkeypatch.delenv("MCP_ENABLE_OAUTH21", raising=False)
        with pytest.raises(ValueError, match="MCP_ENABLE_OAUTH21=true"):
            get_credential_store()

    def test_gcs_backend_rejects_invalid_oauth21_flag(
        self, monkeypatch, mock_storage_client
    ):
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "gcs")
        monkeypatch.setenv("WORKSPACE_MCP_GCS_BUCKET", "test-bucket")
        monkeypatch.setenv("MCP_ENABLE_OAUTH21", "treu")
        with pytest.raises(ValueError, match="Invalid boolean env var"):
            get_credential_store()

    def test_unknown_backend_raises(self, monkeypatch, tmp_path):
        """Typos/invalid values must not silently fall back — they raise."""
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "gibberish")
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIALS_DIR", str(tmp_path))
        with pytest.raises(ValueError, match="Unsupported.*BACKEND"):
            get_credential_store()

    def test_empty_backend_uses_local_directory(self, monkeypatch, tmp_path):
        """Empty env var defaults to local_directory."""
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "")
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIALS_DIR", str(tmp_path))
        assert isinstance(get_credential_store(), LocalDirectoryCredentialStore)

    def test_whitespace_around_backend_is_stripped(
        self, monkeypatch, mock_storage_client
    ):
        """Trailing whitespace on the env var doesn't cause a misclassification."""
        monkeypatch.setenv("WORKSPACE_MCP_CREDENTIAL_STORE_BACKEND", "  gcs  ")
        monkeypatch.setenv("WORKSPACE_MCP_GCS_BUCKET", "test-bucket")
        monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
        monkeypatch.delenv("WORKSPACE_MCP_GCS_REQUIRE_CMEK", raising=False)
        assert isinstance(get_credential_store(), GCSCredentialStore)


class TestParseBoolEnv:
    """The strict bool parser used for security-relevant flags.

    Fails loud on unrecognised input to prevent typos silently disabling
    a flag like WORKSPACE_MCP_GCS_REQUIRE_CMEK.
    """

    @pytest.mark.parametrize(
        "value", ["1", "true", "TRUE", "yes", "on", " true ", "Yes"]
    )
    def test_truthy_values(self, value):
        assert _parse_bool_env(value) is True

    @pytest.mark.parametrize(
        "value", ["0", "false", "FALSE", "no", "off", "", " ", None]
    )
    def test_falsy_values(self, value):
        assert _parse_bool_env(value) is False

    @pytest.mark.parametrize("value", ["treu", "ye", "maybe", "2", "enabled"])
    def test_unrecognised_values_raise(self, value):
        with pytest.raises(ValueError, match="Invalid boolean env var"):
            _parse_bool_env(value)
