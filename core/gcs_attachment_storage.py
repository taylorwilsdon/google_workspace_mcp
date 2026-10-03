"""
GCS-backed temporary attachment storage.

Drop-in alternative to the local-disk ``AttachmentStorage`` for horizontally
scaled / scale-to-zero deployments (e.g. Cloud Run), where instance-local disk
is unreliable and the built-in ``/attachments/{id}`` route may hit a different
instance than the one that stored the file.

Files are staged in a GCS bucket and handed out as **V4 signed URLs** so the
client downloads straight from GCS — no server state required. This also works
in stateless mode (``WORKSPACE_MCP_STATELESS_MODE=true``): stateless refers to
credential/session state, while file staging is delegated entirely to GCS.

Enable by setting:
    WORKSPACE_MCP_FILES_GCS_BUCKET=<bucket-name>          (required)
    WORKSPACE_MCP_FILES_GCS_PREFIX=<object-prefix>        (optional, default "attachments")
    WORKSPACE_MCP_FILES_SIGNED_URL_SECONDS=<seconds>      (optional, default 3600)

Bucket lifecycle (auto-delete) should be configured on the bucket itself
(recommended: delete objects after a few hours).

Signed URLs on keyless environments (Cloud Run default service account without
an exported private key) use the IAM signBlob API; the runtime service account
then needs ``roles/iam.serviceAccountTokenCreator`` on itself.
"""

import base64
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, Optional

from core.attachment_storage import SavedAttachment, sanitize_attachment_filename

logger = logging.getLogger(__name__)

DEFAULT_SIGNED_URL_SECONDS = 3600


def gcs_files_enabled() -> bool:
    """True when GCS file staging is configured via env."""
    return bool(os.getenv("WORKSPACE_MCP_FILES_GCS_BUCKET"))


class GCSAttachmentStorage:
    """Stores attachments as GCS objects and serves them via signed URLs.

    Mirrors the public interface of ``AttachmentStorage`` (``save_attachment``,
    ``get_attachment_metadata``) and adds ``get_signed_url``.
    """

    def __init__(self) -> None:
        self.bucket_name = os.environ["WORKSPACE_MCP_FILES_GCS_BUCKET"]
        self.prefix = os.getenv("WORKSPACE_MCP_FILES_GCS_PREFIX", "attachments").strip(
            "/"
        )
        self.url_expiration_seconds = int(
            os.getenv(
                "WORKSPACE_MCP_FILES_SIGNED_URL_SECONDS",
                str(DEFAULT_SIGNED_URL_SECONDS),
            )
        )
        # file_id -> metadata; only needed within the request that stored the
        # file (URL is generated right after save on the same instance).
        self._metadata: Dict[str, Dict] = {}
        self._client = None

    # -- internals ---------------------------------------------------------

    def _get_client(self):
        if self._client is None:
            from google.cloud import storage  # provided by the [gcs] extra

            self._client = storage.Client()
        return self._client

    def _blob_name(self, file_id: str, save_name: str) -> str:
        return f"{self.prefix}/{file_id}/{save_name}"

    # -- AttachmentStorage-compatible interface -----------------------------

    def save_attachment(
        self,
        base64_data: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """Upload an attachment to GCS. Returns ``SavedAttachment`` whose
        ``path`` is the ``gs://`` object URI."""
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
        """Upload raw bytes to GCS (counterpart to the local-disk backend)."""
        file_id = str(uuid.uuid4())

        safe_filename = sanitize_attachment_filename(filename) if filename else file_id
        blob_name = self._blob_name(file_id, safe_filename)

        bucket = self._get_client().bucket(self.bucket_name)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            file_bytes, content_type=mime_type or "application/octet-stream"
        )

        expires_at = datetime.now() + timedelta(seconds=self.url_expiration_seconds)
        self._metadata[file_id] = {
            "blob_name": blob_name,
            "filename": safe_filename,
            "original_filename": filename,
            "mime_type": mime_type or "application/octet-stream",
            "size": len(file_bytes),
            "expires_at": expires_at.isoformat(),
        }

        logger.info(
            f"Saved attachment file_id={file_id} filename={filename or safe_filename} "
            f"({len(file_bytes)} bytes) to gs://{self.bucket_name}/{blob_name}"
        )
        return SavedAttachment(
            file_id=file_id, path=f"gs://{self.bucket_name}/{blob_name}"
        )

    def save_attachment_from_path(
        self,
        src_path: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """Adopt an already-downloaded file without loading it into memory.

        Counterpart to the local backend's method of the same name: the source
        file is streamed into GCS and then removed, so a multi-gigabyte Drive
        download never has to be held in RAM.
        """
        file_id = str(uuid.uuid4())
        safe_filename = sanitize_attachment_filename(filename) if filename else file_id
        blob_name = self._blob_name(file_id, safe_filename)

        size = os.path.getsize(src_path)
        bucket = self._get_client().bucket(self.bucket_name)
        blob = bucket.blob(blob_name)
        try:
            blob.upload_from_filename(
                src_path, content_type=mime_type or "application/octet-stream"
            )
        finally:
            # The caller hands the file over; it must not be reused afterwards.
            try:
                os.unlink(src_path)
            except OSError:
                logger.debug("Could not remove staged source file %s", src_path)

        expires_at = datetime.now() + timedelta(seconds=self.url_expiration_seconds)
        self._metadata[file_id] = {
            "blob_name": blob_name,
            "filename": safe_filename,
            "original_filename": filename,
            "mime_type": mime_type or "application/octet-stream",
            "size": size,
            "expires_at": expires_at.isoformat(),
        }

        logger.info(
            f"Saved attachment file_id={file_id} filename={filename or safe_filename} "
            f"({size} bytes) to gs://{self.bucket_name}/{blob_name}"
        )
        return SavedAttachment(
            file_id=file_id, path=f"gs://{self.bucket_name}/{blob_name}"
        )

    def get_attachment_path(self, file_id: str) -> None:
        """Always ``None``: the bytes live in GCS, not on local disk.

        Callers treat ``None`` as "no local copy" and fall back to the URL
        (``core/server.py`` answers its local attachment route with 404, which
        is correct here — the client is handed a signed GCS URL instead).
        """
        return None

    def get_attachment_metadata(self, file_id: str) -> Optional[Dict]:
        return self._metadata.get(file_id)

    # -- signed URL ----------------------------------------------------------

    def get_signed_url(self, file_id: str) -> str:
        """Generate a V4 signed download URL for a previously saved attachment."""
        meta = self._metadata.get(file_id)
        if not meta:
            raise KeyError(f"Unknown attachment file_id: {file_id}")

        bucket = self._get_client().bucket(self.bucket_name)
        blob = bucket.blob(meta["blob_name"])
        expiration = timedelta(seconds=self.url_expiration_seconds)

        try:
            # Works when credentials carry a private key (service account key file).
            return blob.generate_signed_url(version="v4", expiration=expiration)
        except (AttributeError, TypeError) as key_err:
            # Keyless environment (e.g. Cloud Run default credentials): sign via
            # the IAM signBlob API. Requires roles/iam.serviceAccountTokenCreator
            # for the runtime service account on itself.
            logger.debug(
                f"Falling back to IAM-based signing (no local private key): {key_err}"
            )
            import google.auth
            from google.auth.transport.requests import Request

            credentials, _ = google.auth.default()
            credentials.refresh(Request())
            service_account_email = getattr(credentials, "service_account_email", None)
            if not service_account_email or service_account_email == "default":
                # Resolve the actual SA email from the metadata server if needed.
                import requests as _requests

                service_account_email = _requests.get(
                    "http://metadata.google.internal/computeMetadata/v1/instance/"
                    "service-accounts/default/email",
                    headers={"Metadata-Flavor": "Google"},
                    timeout=5,
                ).text.strip()
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                service_account_email=service_account_email,
                access_token=credentials.token,
            )


# Global instance
_gcs_attachment_storage: Optional[GCSAttachmentStorage] = None


def get_gcs_attachment_storage() -> GCSAttachmentStorage:
    """Get the global GCS attachment storage instance."""
    global _gcs_attachment_storage
    if _gcs_attachment_storage is None:
        _gcs_attachment_storage = GCSAttachmentStorage()
    return _gcs_attachment_storage
