"""
Temporary attachment storage for Gmail attachments.

Stores attachments to local disk and returns file paths for direct access.
Files are automatically cleaned up after expiration (default 1 hour).
"""

import base64
import logging
import os
import uuid
from pathlib import Path
from typing import NamedTuple, Optional, Dict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Default expiration: 1 hour
DEFAULT_EXPIRATION_SECONDS = 3600

# Storage directory - configurable via WORKSPACE_ATTACHMENT_DIR env var
# Uses absolute path to avoid creating tmp/ in arbitrary working directories (see #327)
_default_dir = str(Path.home() / ".workspace-mcp" / "attachments")
STORAGE_DIR = (
    Path(os.getenv("WORKSPACE_ATTACHMENT_DIR", _default_dir)).expanduser().resolve()
)


def _ensure_storage_dir() -> None:
    """Create the storage directory on first use, not at import time."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


class SavedAttachment(NamedTuple):
    """Result of saving an attachment: provides both the UUID and the absolute file path."""

    file_id: str
    path: str


class AttachmentStorage:
    """Manages temporary storage of email attachments."""

    def __init__(self, expiration_seconds: int = DEFAULT_EXPIRATION_SECONDS):
        self.expiration_seconds = expiration_seconds
        self._metadata: Dict[str, Dict] = {}

    def save_attachment(
        self,
        base64_data: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """
        Save an attachment to local disk.

        Args:
            base64_data: Base64-encoded attachment data
            filename: Original filename (optional)
            mime_type: MIME type (optional)

        Returns:
            SavedAttachment with file_id (UUID) and path (absolute file path)
        """
        _ensure_storage_dir()

        # Generate unique file ID for metadata tracking
        file_id = str(uuid.uuid4())

        # Decode base64 data
        try:
            file_bytes = base64.urlsafe_b64decode(base64_data)
        except Exception as e:
            logger.error(f"Failed to decode base64 attachment data: {e}")
            raise ValueError(f"Invalid base64 data: {e}")

        # Determine file extension from filename or mime type
        extension = ""
        if filename:
            extension = Path(filename).suffix
        elif mime_type:
            # Basic mime type to extension mapping
            mime_to_ext = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "application/pdf": ".pdf",
                "application/zip": ".zip",
                "text/plain": ".txt",
                "text/html": ".html",
            }
            extension = mime_to_ext.get(mime_type, "")

        # Use original filename if available, with UUID suffix for uniqueness
        if filename:
            stem = Path(filename).stem
            ext = Path(filename).suffix
            save_name = f"{stem}_{file_id[:8]}{ext}"
        else:
            save_name = f"{file_id}{extension}"

        # Save file with restrictive permissions (sensitive email/drive content)
        file_path = STORAGE_DIR / save_name
        try:
            fd = os.open(
                file_path,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0),
                0o600,
            )
            try:
                total_written = 0
                data_len = len(file_bytes)
                while total_written < data_len:
                    written = os.write(fd, file_bytes[total_written:])
                    if written == 0:
                        raise OSError(
                            "os.write returned 0 bytes; could not write attachment data"
                        )
                    total_written += written
            finally:
                os.close(fd)
            logger.info(
                f"Saved attachment file_id={file_id} filename={filename or save_name} "
                f"({len(file_bytes)} bytes) to {file_path}"
            )
        except Exception as e:
            logger.error(
                f"Failed to save attachment file_id={file_id} "
                f"filename={filename or save_name} to {file_path}: {e}"
            )
            raise

        # Store metadata
        expires_at = datetime.now() + timedelta(seconds=self.expiration_seconds)
        self._metadata[file_id] = {
            "file_path": str(file_path),
            "filename": filename or f"attachment{extension}",
            "mime_type": mime_type or "application/octet-stream",
            "size": len(file_bytes),
            "created_at": datetime.now(),
            "expires_at": expires_at,
        }

        return SavedAttachment(file_id=file_id, path=str(file_path))

    def get_attachment_path(self, file_id: str) -> Optional[Path]:
        """
        Get the file path for an attachment ID.

        Args:
            file_id: Unique file ID

        Returns:
            Path object if file exists and not expired, None otherwise
        """
        if file_id not in self._metadata:
            logger.warning(f"Attachment {file_id} not found in metadata")
            return None

        metadata = self._metadata[file_id]
        file_path = Path(metadata["file_path"])

        # Check if expired
        if datetime.now() > metadata["expires_at"]:
            logger.info(f"Attachment {file_id} has expired, cleaning up")
            self._cleanup_file(file_id)
            return None

        # Check if file exists
        if not file_path.exists():
            logger.warning(f"Attachment file {file_path} does not exist")
            del self._metadata[file_id]
            return None

        return file_path

    def get_attachment_metadata(self, file_id: str) -> Optional[Dict]:
        """
        Get metadata for an attachment.

        Args:
            file_id: Unique file ID

        Returns:
            Metadata dict if exists and not expired, None otherwise
        """
        if file_id not in self._metadata:
            return None

        metadata = self._metadata[file_id].copy()

        # Check if expired
        if datetime.now() > metadata["expires_at"]:
            self._cleanup_file(file_id)
            return None

        return metadata

    def _cleanup_file(self, file_id: str) -> None:
        """Remove file and metadata."""
        if file_id in self._metadata:
            file_path = Path(self._metadata[file_id]["file_path"])
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"Deleted expired attachment file: {file_path}")
            except Exception as e:
                logger.warning(f"Failed to delete attachment file {file_path}: {e}")
            del self._metadata[file_id]

    def cleanup_expired(self) -> int:
        """
        Clean up expired attachments.

        Returns:
            Number of files cleaned up
        """
        now = datetime.now()
        expired_ids = [
            file_id
            for file_id, metadata in self._metadata.items()
            if now > metadata["expires_at"]
        ]

        for file_id in expired_ids:
            self._cleanup_file(file_id)

        return len(expired_ids)


# Global instance
_attachment_storage: Optional[AttachmentStorage] = None


def get_attachment_storage() -> AttachmentStorage:
    """Get the global attachment storage instance."""
    global _attachment_storage
    if _attachment_storage is None:
        _attachment_storage = AttachmentStorage()
    return _attachment_storage


def get_attachment_url(file_id: str) -> str:
    """
    Generate a URL for accessing an attachment.

    Args:
        file_id: Unique file ID

    Returns:
        Full URL to access the attachment
    """
    from core.config import WORKSPACE_MCP_PORT, WORKSPACE_MCP_BASE_URI

    # Use external URL if set (for reverse proxy scenarios)
    external_url = os.getenv("WORKSPACE_EXTERNAL_URL")
    if external_url:
        base_url = external_url.rstrip("/")
    else:
        base_url = f"{WORKSPACE_MCP_BASE_URI}:{WORKSPACE_MCP_PORT}"

    return f"{base_url}/attachments/{file_id}"
