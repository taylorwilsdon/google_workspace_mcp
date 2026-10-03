"""Tests for the GCS-backed attachment storage.

The GCS client is replaced by a small in-memory fake, so these tests exercise
the storage logic (object naming, metadata, delegation, signed URLs) without
touching Google Cloud Storage.
"""

import base64

import pytest

import core.attachment_storage as attachment_storage
import core.gcs_attachment_storage as gcs_module
from core.gcs_attachment_storage import GCSAttachmentStorage, gcs_files_enabled

BUCKET = "test-bucket"


class FakeBlob:
    """Records what was uploaded and hands out a predictable signed URL."""

    def __init__(self, name: str, bucket: "FakeBucket"):
        self.name = name
        self._bucket = bucket
        self.content: bytes = b""
        self.content_type: str | None = None

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self.content = data
        self.content_type = content_type
        self._bucket.blobs[self.name] = self

    def upload_from_filename(self, path: str, content_type: str | None = None) -> None:
        with open(path, "rb") as fh:
            self.content = fh.read()
        self.content_type = content_type
        self._bucket.blobs[self.name] = self

    def generate_signed_url(self, **kwargs):
        return f"https://signed.example/{self.name}"


class FakeBucket:
    def __init__(self, name: str):
        self.name = name
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        return self.blobs.get(name) or FakeBlob(name, self)


class FakeClient:
    def __init__(self):
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
    monkeypatch.delenv("WORKSPACE_MCP_FILES_GCS_PREFIX", raising=False)
    store = GCSAttachmentStorage()
    client = FakeClient()
    monkeypatch.setattr(store, "_get_client", lambda: client)
    return store


class TestEnablement:
    def test_disabled_without_bucket(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_FILES_GCS_BUCKET", raising=False)
        assert gcs_files_enabled() is False

    def test_enabled_with_bucket(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
        assert gcs_files_enabled() is True


class TestSaveAttachment:
    def test_bytes_are_uploaded_unchanged(self, storage):
        payload = b"%PDF-1.7 not really a pdf"
        saved = storage.save_attachment_bytes(payload, "report.pdf", "application/pdf")

        blob = (
            storage._get_client()
            .bucket(BUCKET)
            .blobs[storage._metadata[saved.file_id]["blob_name"]]
        )
        assert blob.content == payload
        assert blob.content_type == "application/pdf"
        assert saved.path.startswith(f"gs://{BUCKET}/")

    def test_base64_entry_point_delegates(self, storage):
        payload = b"hello world"
        saved = storage.save_attachment(
            base64.urlsafe_b64encode(payload).decode(), "note.txt", "text/plain"
        )
        meta = storage.get_attachment_metadata(saved.file_id)
        assert meta["size"] == len(payload)
        assert meta["mime_type"] == "text/plain"

    def test_invalid_base64_raises(self, storage):
        with pytest.raises(ValueError):
            storage.save_attachment("!!!not base64!!!", "x.bin", None)

    def test_from_path_consumes_the_source(self, storage, tmp_path):
        src = tmp_path / "download.bin"
        src.write_bytes(b"streamed payload")

        saved = storage.save_attachment_from_path(str(src), "download.bin", None)

        assert not src.exists(), "source file must be handed over, not copied"
        assert storage.get_attachment_metadata(saved.file_id)["size"] == len(
            b"streamed payload"
        )

    def test_filename_is_sanitized_into_the_object_name(self, storage):
        saved = storage.save_attachment_bytes(b"x", "a/b:c.png", "image/png")
        assert "a_b_c.png" in storage._metadata[saved.file_id]["blob_name"]


class TestLookups:
    def test_no_local_path(self, storage):
        saved = storage.save_attachment_bytes(b"x", "x.bin", None)
        assert storage.get_attachment_path(saved.file_id) is None

    def test_metadata_for_unknown_id(self, storage):
        assert storage.get_attachment_metadata("does-not-exist") is None

    def test_signed_url_for_unknown_id_raises(self, storage):
        with pytest.raises(Exception):
            storage.get_signed_url("does-not-exist")


class TestDelegation:
    """get_attachment_storage/get_attachment_url switch backends via env."""

    def test_storage_factory_returns_gcs_backend(self, monkeypatch, storage):
        monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
        monkeypatch.setattr(gcs_module, "get_gcs_attachment_storage", lambda: storage)
        assert attachment_storage.get_attachment_storage() is storage

    def test_url_factory_returns_signed_url(self, monkeypatch, storage):
        monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
        monkeypatch.setattr(gcs_module, "get_gcs_attachment_storage", lambda: storage)
        saved = storage.save_attachment_bytes(b"x", "x.bin", None)
        assert attachment_storage.get_attachment_url(saved.file_id).startswith(
            "https://signed.example/"
        )

    def test_local_backend_when_disabled(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_FILES_GCS_BUCKET", raising=False)
        assert isinstance(
            attachment_storage.get_attachment_storage(),
            attachment_storage.AttachmentStorage,
        )
