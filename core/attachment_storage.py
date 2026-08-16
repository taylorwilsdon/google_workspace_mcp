"""
Temporary attachment storage for Gmail attachments.

Stores attachments to local disk and returns file paths for direct access.
Files are automatically cleaned up after expiration (default 1 hour).
"""

import base64
import logging
import os
import re
import shutil
import unicodedata
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

# Fallback extensions for attachments that arrive without a filename.
_MIME_TO_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "text/plain": ".txt",
    "text/html": ".html",
}

_WINDOWS_RESERVED_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _ensure_storage_dir() -> None:
    """Create the storage directory on first use, not at import time."""
    STORAGE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)


def sanitize_attachment_filename(filename: Optional[str]) -> str:
    """Return a filesystem-safe attachment filename."""
    if not filename:
        return "attachment"

    # Normalize Unicode space separators (category "Zs") to a plain ASCII space.
    filename = "".join(
        " " if unicodedata.category(ch) == "Zs" else ch for ch in filename
    )

    sanitized = _WINDOWS_RESERVED_FILENAME_CHARS.sub("_", filename).rstrip(" .")
    if not sanitized:
        return "attachment"

    stem = sanitized.split(".", 1)[0]
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized = f"_{sanitized}"

    return sanitized


def _build_save_name(
    file_id: str, filename: Optional[str], mime_type: Optional[str]
) -> str:
    """Build a collision-free on-disk name, keeping the original stem when given."""
    if filename:
        # The full file_id, not a prefix: a truncated one lets two distinct
        # attachments land on the same path, silently overwriting the first.
        safe_filename = Path(sanitize_attachment_filename(filename))
        return f"{safe_filename.stem}_{file_id}{safe_filename.suffix}"
    return f"{file_id}{_MIME_TO_EXTENSION.get(mime_type or '', '')}"


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

        save_name = _build_save_name(file_id, filename, mime_type)

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

        return self._record(
            file_id, file_path, save_name, filename, mime_type, len(file_bytes)
        )

    def save_attachment_from_path(
        self,
        src_path: str,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> SavedAttachment:
        """
        Adopt an already-downloaded file without loading it into memory.

        The counterpart to save_attachment() for payloads that were streamed
        straight to disk. Nothing here is proportional to the file size, so a
        multi-gigabyte Drive download costs no RAM (see #994).

        Args:
            src_path: Path to the downloaded file. Consumed - it is moved into
                storage, so the caller must not reuse it afterwards.
            filename: Original filename (optional)
            mime_type: MIME type (optional)

        Returns:
            SavedAttachment with file_id (UUID) and path (absolute file path)
        """
        _ensure_storage_dir()

        file_id = str(uuid.uuid4())
        save_name = _build_save_name(file_id, filename, mime_type)
        file_path = STORAGE_DIR / save_name

        try:
            # shutil.move degrades to a streamed copy across filesystems, so it
            # stays memory-safe when the temp dir is on another mount.
            shutil.move(str(src_path), str(file_path))
            os.chmod(file_path, 0o600)
            size = file_path.stat().st_size
            logger.info(
                f"Saved attachment file_id={file_id} filename={filename or save_name} "
                f"({size} bytes) to {file_path}"
            )
            return self._record(
                file_id, file_path, save_name, filename, mime_type, size
            )
        except Exception as e:
            # The move can land before a later finalization step fails, leaving a file in
            # storage that no metadata entry will ever expire. Drop it rather
            # than orphan it, but never let cleanup mask the original failure.
            try:
                file_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.warning(
                    f"Failed to remove partially saved attachment {file_path}: "
                    f"{cleanup_error}"
                )
            logger.error(
                f"Failed to save attachment file_id={file_id} "
                f"filename={filename or save_name} to {file_path}: {e}"
            )
            raise

    def _record(
        self,
        file_id: str,
        file_path: Path,
        save_name: str,
        filename: Optional[str],
        mime_type: Optional[str],
        size: int,
    ) -> SavedAttachment:
        """Record a stored file's metadata and return its handle."""
        self._metadata[file_id] = {
            "file_path": str(file_path),
            "filename": save_name,
            "original_filename": filename,
            "mime_type": mime_type or "application/octet-stream",
            "size": size,
            "created_at": datetime.now(),
            "expires_at": datetime.now() + timedelta(seconds=self.expiration_seconds),
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

    # In stdio mode the attachment route is served by the lazily-started callback
    # server; bring it up now so the URL we hand out is actually reachable. The
    # import is local to avoid pulling the FastAPI/uvicorn auth stack into this
    # lightweight, widely-imported module (matches every other call site, #832).
    from auth.oauth_callback_server import ensure_stdio_oauth_callback_available

    success, error_msg = ensure_stdio_oauth_callback_available()
    if not success:
        logger.warning(
            "Failed to start stdio attachment server; attachment URL may be "
            "unreachable: %s",
            error_msg,
        )

    # Use external URL if set (for reverse proxy scenarios)
    external_url = os.getenv("WORKSPACE_EXTERNAL_URL")
    if external_url:
        base_url = external_url.rstrip("/")
    else:
        base_url = f"{WORKSPACE_MCP_BASE_URI}:{WORKSPACE_MCP_PORT}"

    return f"{base_url}/attachments/{file_id}"
