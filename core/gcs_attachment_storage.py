"""
GCS-backed temporary attachment storage.

Alternative to the local-disk ``AttachmentStorage`` for horizontally scaled or
scale-to-zero HTTP deployments (e.g. Cloud Run), where the built-in
``/attachments/{id}`` route may hit a different instance than the one that
stored the file. Files are staged in a GCS bucket and handed out as V4 signed
URLs, so the client downloads straight from GCS. This also works in stateless
mode: stateless refers to credential and session state, not file staging.

Enable by setting:
    WORKSPACE_MCP_FILES_GCS_BUCKET=<bucket-name>          (required)
    WORKSPACE_MCP_FILES_GCS_PREFIX=<object-prefix>        (optional, default "attachments")

Object lifetime is left to a bucket lifecycle rule. Without an exported
private key (e.g. Cloud Run default credentials), URLs are signed through the
IAM signBlob API, which needs ``roles/iam.serviceAccountTokenCreator`` for the
runtime service account on itself.
"""

import base64
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

import google.auth
from google.auth.transport.requests import Request

from auth.oauth_config import get_transport_mode
from core.attachment_storage import (
    DEFAULT_EXPIRATION_SECONDS,
    SavedAttachment,
    sanitize_attachment_filename,
)

logger = logging.getLogger(__name__)

_BUCKET_ENV = "WORKSPACE_MCP_FILES_GCS_BUCKET"
_PREFIX_ENV = "WORKSPACE_MCP_FILES_GCS_PREFIX"


def gcs_files_enabled() -> bool:
    """True when GCS file staging is configured and the transport is not stdio.

    A stdio client shares the server's filesystem and reads the local path the
    tools hand back, so local disk stays the backend there.
    """
    return bool(os.getenv(_BUCKET_ENV)) and get_transport_mode() != "stdio"


class GCSAttachmentStorage:
    """Stores attachments as GCS objects and serves them via signed URLs.

    Covers the parts of ``AttachmentStorage`` the tools call, and adds
    ``get_signed_url``. Two methods are deliberately absent: ``sweep_expired``
    and ``cleanup_expired`` delete files from local disk, while GCS objects are
    removed by the bucket's lifecycle rule. ``get_attachment_storage`` only
    sweeps the local backend, so nothing asks this one for them.

    ``get_attachment_path`` returns ``None`` here: there is no local file. The
    ``/attachments/{id}`` route answers 404 for that, which is correct — with
    GCS the client follows the signed URL instead.
    """

    def __init__(self) -> None:
        self.bucket_name = os.environ[_BUCKET_ENV]
        prefix = os.getenv(_PREFIX_ENV, "attachments").strip("/")
        self.prefix = f"{prefix}/" if prefix else ""
        self.expiration_seconds = DEFAULT_EXPIRATION_SECONDS
        # file_id -> metadata, kept only until the signed URL lapses: the URL
        # is generated right after save, on the same instance.
        self._metadata: Dict[str, Dict] = {}
        self._metadata_lock = threading.Lock()
        self._client = None

    def _get_client(self):
        if self._client is None:
            # The [gcs] extra is optional, so import it only once enabled.
            from google.cloud import storage

            self._client = storage.Client()
        return self._client

    def _upload_target(self, filename: Optional[str]):
        """Return a fresh file_id, its sanitized name, and the blob to write."""
        file_id = str(uuid.uuid4())
        safe_filename = sanitize_attachment_filename(filename) if filename else file_id
        blob_name = f"{self.prefix}{file_id}/{safe_filename}"
        blob = self._get_client().bucket(self.bucket_name).blob(blob_name)
        # Objects carry user data and are reachable by anyone holding the signed
        # URL, so keep caches out of the path: GCS otherwise defaults unset
        # Cache-Control to "public, max-age=3600", which lets a browser or an
        # intermediary keep the bytes after the URL has expired. Set here rather
        # than at each call site so every upload path inherits it.
        blob.cache_control = "no-store"
        return file_id, safe_filename, blob

    def _record(
        self,
        file_id: str,
        blob_name: str,
        safe_filename: str,
        filename: Optional[str],
        mime_type: Optional[str],
        size: int,
    ) -> SavedAttachment:
        """Record an uploaded object, dropping entries whose URL has lapsed."""
        now = datetime.now()
        with self._metadata_lock:
            expired = [
                key for key, meta in self._metadata.items() if meta["expires_at"] <= now
            ]
            for key in expired:
                del self._metadata[key]
            self._metadata[file_id] = {
                "blob_name": blob_name,
                "filename": safe_filename,
                "original_filename": filename,
                "mime_type": mime_type or "application/octet-stream",
                "size": size,
                "expires_at": now + timedelta(seconds=self.expiration_seconds),
            }
        path = f"gs://{self.bucket_name}/{blob_name}"
        logger.info(
            f"Saved attachment file_id={file_id} filename={filename or safe_filename} "
            f"({size} bytes) to {path}"
        )
        return SavedAttachment(file_id=file_id, path=path)

    def save_attachment(
        self,
        base64_data: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """Upload a base64-encoded attachment; ``path`` is the ``gs://`` URI."""
        try:
            file_bytes = base64.urlsafe_b64decode(base64_data)
        except Exception as e:
            logger.error(f"Failed to decode base64 attachment data: {e}")
            raise ValueError(f"Invalid base64 data: {e}")

        return self.save_attachment_bytes(file_bytes, filename, mime_type)

    def save_attachment_bytes(
        self,
        file_bytes: bytes,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """Upload already-decoded bytes."""
        file_id, safe_filename, blob = self._upload_target(filename)
        blob.upload_from_string(
            file_bytes, content_type=mime_type or "application/octet-stream"
        )
        return self._record(
            file_id, blob.name, safe_filename, filename, mime_type, len(file_bytes)
        )

    def save_attachment_from_path(
        self,
        src_path: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """Stream an already-downloaded file into GCS, then remove the source.

        The caller hands the file over, so it is removed even if the upload
        fails and must not be reused afterwards.
        """
        file_id, safe_filename, blob = self._upload_target(filename)
        size = os.path.getsize(src_path)
        try:
            blob.upload_from_filename(
                src_path, content_type=mime_type or "application/octet-stream"
            )
        finally:
            try:
                os.unlink(src_path)
            except OSError:
                logger.debug("Could not remove staged source file %s", src_path)
        return self._record(
            file_id, blob.name, safe_filename, filename, mime_type, size
        )

    def get_attachment_path(self, file_id: str) -> None:
        """Always ``None``: the bytes live in GCS, so the local route answers 404."""
        return None

    def get_attachment_metadata(self, file_id: str) -> Optional[Dict]:
        """Metadata for an attachment, or None once its URL lifetime has run out.

        Returns a copy: the caller must not be able to mutate the shared dict,
        and the entry can be evicted between calls. Mirrors the local
        ``AttachmentStorage``, which copies and expires the same way.
        """
        with self._metadata_lock:
            meta = self._metadata.get(file_id)
            if meta and meta["expires_at"] <= datetime.now():
                del self._metadata[file_id]
                return None
            return dict(meta) if meta else None

    def get_signed_url(self, file_id: str) -> str:
        """Generate a V4 signed download URL for a previously saved attachment.

        An entry whose own lifetime has run out is dropped and treated as unknown.
        Metadata is only swept on save, so without this a caller holding a file_id
        could keep minting fresh one-hour URLs for an object long after its first
        URL lapsed — for as long as the bucket's lifecycle rule still keeps it.
        The built-in tools sign immediately and never hand out the id, so this
        guards callers of the storage API rather than the normal response path.
        """
        with self._metadata_lock:
            meta = self._metadata.get(file_id)
            if meta and meta["expires_at"] <= datetime.now():
                del self._metadata[file_id]
                meta = None
        if not meta:
            raise KeyError(f"Unknown attachment file_id: {file_id}")

        blob = self._get_client().bucket(self.bucket_name).blob(meta["blob_name"])
        expiration = timedelta(seconds=self.expiration_seconds)

        try:
            # Works when the credentials carry a private key.
            return blob.generate_signed_url(version="v4", expiration=expiration)
        except AttributeError as key_err:
            # Token-only credentials: sign through the IAM signBlob API.
            logger.debug(f"Falling back to IAM-based signing: {key_err}")
            credentials, _ = google.auth.default()
            # Refreshing also resolves a Compute Engine "default" account email.
            credentials.refresh(Request())
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                service_account_email=credentials.service_account_email,
                access_token=credentials.token,
            )


_gcs_attachment_storage: Optional[GCSAttachmentStorage] = None


def get_gcs_attachment_storage() -> GCSAttachmentStorage:
    """Get the global GCS attachment storage instance."""
    global _gcs_attachment_storage
    if _gcs_attachment_storage is None:
        _gcs_attachment_storage = GCSAttachmentStorage()
    return _gcs_attachment_storage
