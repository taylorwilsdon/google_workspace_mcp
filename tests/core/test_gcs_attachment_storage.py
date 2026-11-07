"""Tests for the GCS-backed attachment storage.

The GCS client is replaced by a small in-memory fake, so these tests exercise
the storage logic (object naming, metadata, delegation, signed URLs) without
touching Google Cloud Storage.
"""

import base64
import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

import core.attachment_storage as attachment_storage
import core.gcs_attachment_storage as gcs_module
from auth.oauth_config import get_transport_mode, set_transport_mode
from core.gcs_attachment_storage import GCSAttachmentStorage, gcs_files_enabled
from gchat.chat_tools import download_chat_attachment
from gdrive.drive_tools import get_drive_file_download_url
from gmail.gmail_tools import get_gmail_attachment_content, get_gmail_message_content

BUCKET = "test-bucket"


class FakeBlob:
    """Records what was uploaded and hands out a predictable signed URL."""

    def __init__(self, name: str, bucket: "FakeBucket"):
        self.name = name
        self._bucket = bucket
        self.content: bytes = b""
        self.content_type: str | None = None
        self.threads: list[threading.Thread] = []

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self.threads.append(threading.current_thread())
        self.content = data
        self.content_type = content_type
        self._bucket.blobs[self.name] = self

    def upload_from_filename(self, path: str, content_type: str | None = None) -> None:
        self.threads.append(threading.current_thread())
        with open(path, "rb") as fh:
            self.content = fh.read()
        self.content_type = content_type
        self._bucket.blobs[self.name] = self

    def generate_signed_url(self, **kwargs):
        self.threads.append(threading.current_thread())
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


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.fixture
def http_transport():
    previous = get_transport_mode()
    set_transport_mode("streamable-http")
    yield
    set_transport_mode(previous)


@pytest.fixture
def storage(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
    monkeypatch.delenv("WORKSPACE_MCP_FILES_GCS_PREFIX", raising=False)
    store = GCSAttachmentStorage()
    client = FakeClient()
    monkeypatch.setattr(store, "_get_client", lambda: client)
    return store


@pytest.fixture
def gcs_backend(monkeypatch, storage, http_transport):
    """Route the real storage factories to the fake-backed GCS store."""
    monkeypatch.setattr(gcs_module, "_gcs_attachment_storage", storage)
    return storage


class TestEnablement:
    def test_disabled_without_bucket(self, monkeypatch, http_transport):
        monkeypatch.delenv("WORKSPACE_MCP_FILES_GCS_BUCKET", raising=False)
        assert gcs_files_enabled() is False

    def test_enabled_with_bucket(self, monkeypatch, http_transport):
        monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
        assert gcs_files_enabled() is True

    def test_stdio_keeps_local_disk(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_BUCKET", BUCKET)
        previous = get_transport_mode()
        set_transport_mode("stdio")
        try:
            assert gcs_files_enabled() is False
            assert isinstance(
                attachment_storage.get_attachment_storage(),
                attachment_storage.AttachmentStorage,
            )
        finally:
            set_transport_mode(previous)


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

    @pytest.mark.parametrize("prefix", ["", "/"])
    def test_empty_prefix_has_no_leading_slash(self, monkeypatch, storage, prefix):
        monkeypatch.setenv("WORKSPACE_MCP_FILES_GCS_PREFIX", prefix)
        store = GCSAttachmentStorage()
        store._get_client = storage._get_client
        saved = store.save_attachment_bytes(b"x", "a.pdf", None)
        assert saved.path == f"gs://{BUCKET}/{saved.file_id}/a.pdf"

    def test_uploads_are_marked_no_store(self, storage, tmp_path):
        """Caches must not keep attachment bytes past the signed URL's lifetime.

        GCS defaults an unset Cache-Control to "public, max-age=3600", so a
        browser or intermediary could serve the file after the URL expired.
        """
        storage.save_attachment_bytes(b"secret", filename="a.txt")

        src = tmp_path / "b.txt"
        src.write_bytes(b"also secret")
        storage.save_attachment_from_path(str(src), filename="b.txt")

        blobs = storage._get_client().bucket(BUCKET).blobs.values()
        assert len(blobs) == 2
        assert {blob.cache_control for blob in blobs} == {"no-store"}

    def test_lapsed_metadata_is_evicted_on_save(self, storage):
        old = storage.save_attachment_bytes(b"x", "old.bin", None)
        storage._metadata[old.file_id]["expires_at"] = datetime.now() - timedelta(
            seconds=1
        )
        fresh = storage.save_attachment_bytes(b"y", "new.bin", None)
        assert set(storage._metadata) == {fresh.file_id}


class TestLookups:
    def test_no_local_path(self, storage):
        saved = storage.save_attachment_bytes(b"x", "x.bin", None)
        assert storage.get_attachment_path(saved.file_id) is None

    def test_metadata_for_unknown_id(self, storage):
        assert storage.get_attachment_metadata("does-not-exist") is None

    def test_signed_url_rejects_lapsed_metadata(self, storage):
        """A lapsed entry must not mint a fresh URL.

        Metadata is otherwise only swept on save, so a caller holding a file_id
        could keep extending access for as long as the bucket still holds the
        object.
        """
        saved = storage.save_attachment_bytes(b"x", filename="a.txt")
        with storage._metadata_lock:
            storage._metadata[saved.file_id]["expires_at"] = datetime.now() - timedelta(
                seconds=1
            )

        with pytest.raises(KeyError):
            storage.get_signed_url(saved.file_id)
        assert saved.file_id not in storage._metadata

    def test_metadata_is_a_copy(self, storage):
        """The caller must not be able to mutate the shared dict."""
        saved = storage.save_attachment_bytes(b"x", filename="a.txt")
        meta = storage.get_attachment_metadata(saved.file_id)
        meta["filename"] = "tampered"
        assert storage.get_attachment_metadata(saved.file_id)["filename"] != "tampered"

    def test_metadata_drops_lapsed_entries(self, storage):
        """A lapsed entry reads as unknown, like the local backend."""
        saved = storage.save_attachment_bytes(b"x", filename="a.txt")
        with storage._metadata_lock:
            storage._metadata[saved.file_id]["expires_at"] = datetime.now() - timedelta(
                seconds=1
            )

        assert storage.get_attachment_metadata(saved.file_id) is None
        assert saved.file_id not in storage._metadata

    def test_signed_url_for_unknown_id_raises(self, storage):
        with pytest.raises(KeyError):
            storage.get_signed_url("does-not-exist")


class TestDelegation:
    """get_attachment_storage/get_attachment_url switch backends via env."""

    def test_storage_factory_returns_gcs_backend(self, gcs_backend):
        assert attachment_storage.get_attachment_storage() is gcs_backend

    def test_url_factory_returns_signed_url(self, gcs_backend):
        storage = gcs_backend
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


def _assert_staged_off_event_loop(store):
    """The upload and the signing both ran, and neither on the event loop."""
    (blob,) = store._get_client().bucket(BUCKET).blobs.values()
    assert len(blob.threads) == 2
    assert threading.main_thread() not in blob.threads
    return blob


class TestStatelessToolsStageToGcs:
    """In stateless mode the tools hand out a signed URL instead of inlining."""

    @pytest.mark.asyncio
    async def test_drive_download(self, gcs_backend, tmp_path):
        download = tmp_path / "download.bin"
        download.write_bytes(b"video")
        service = Mock()
        with (
            patch(
                "gdrive.drive_tools.resolve_drive_item",
                return_value=("f1", {"name": "clip.mp4", "mimeType": "video/mp4"}),
            ),
            patch("gdrive.drive_tools.is_stateless_mode", return_value=True),
            patch(
                "gdrive.drive_tools._download_file_to_temp",
                new=AsyncMock(return_value=download),
            ),
        ):
            result = await _unwrap(get_drive_file_download_url)(
                service=service, user_google_email="u@example.com", file_id="f1"
            )

        assert "Download URL: https://signed.example/attachments/" in result
        assert _assert_staged_off_event_loop(gcs_backend).content == b"video"
        assert not download.exists()

    @pytest.mark.asyncio
    async def test_chat_download(self, gcs_backend):
        service = Mock()
        service.spaces().messages().get().execute.return_value = {
            "name": "spaces/S/messages/M",
            "attachment": [
                {
                    "contentName": "photo.png",
                    "contentType": "image/png",
                    "attachmentDataRef": {"resourceName": "spaces/S/attachments/A"},
                }
            ],
        }
        with (
            patch("auth.oauth_config.is_stateless_mode", return_value=True),
            patch(
                "gchat.chat_tools.download_http_url_bytes",
                new=AsyncMock(return_value=b"png"),
            ),
        ):
            result = await _unwrap(download_chat_attachment)(
                service=service,
                user_google_email="u@example.com",
                message_id="spaces/S/messages/M",
            )

        assert "Download URL: https://signed.example/attachments/" in result
        assert "Stateless mode" not in result
        _assert_staged_off_event_loop(gcs_backend)

    @pytest.mark.asyncio
    async def test_gmail_attachment(self, gcs_backend):
        service = Mock()
        service.users().messages().attachments().get().execute.return_value = {
            "size": 5,
            "data": base64.urlsafe_b64encode(b"hello").decode(),
        }
        with patch("auth.oauth_config.is_stateless_mode", return_value=True):
            result = await _unwrap(get_gmail_attachment_content)(
                service=service,
                message_id="m1",
                attachment_id="a1",
                user_google_email="u@example.com",
            )

        assert "Download URL: https://signed.example/attachments/" in result
        assert "Stateless mode" not in result
        assert _assert_staged_off_event_loop(gcs_backend).content == b"hello"

    @pytest.mark.asyncio
    async def test_gmail_full_message_export(self, gcs_backend):
        """Stateless + GCS stages the export instead of inlining it.

        Without a bucket, a stateless deployment has nowhere to put the file and
        inlines the whole message, which is what the export exists to avoid. The
        bucket is not instance-local, so staging works even there.
        """
        service = Mock()
        service.users().messages().get().execute.return_value = {
            "id": "m1",
            "sizeEstimate": 42,
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Quarterly report"},
                    {"name": "From", "value": "a@example.com"},
                ],
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"full body").decode()},
            },
        }
        with patch("gmail.gmail_tools.is_stateless_mode", return_value=True):
            result = await _unwrap(get_gmail_message_content)(
                service=service,
                message_id="m1",
                user_google_email="u@example.com",
                full=True,
            )

        assert "Download URL: https://signed.example/attachments/" in result
        assert "Stateless mode" not in result
        assert "BODY (COMPLETE, NOT TRUNCATED)" not in result
        _assert_staged_off_event_loop(gcs_backend)

        _assert_staged_off_event_loop(gcs_backend)
