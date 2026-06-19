"""
Google Drive Helper Functions

Shared utilities for Google Drive operations including permission checking,
remote content download, and import-time format conversion.
"""

import asyncio
import io
import logging
import re
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import List, Dict, Any, Awaitable, BinaryIO, Callable, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import url2pathname

import httpx
from googleapiclient.http import MediaIoBaseUpload

from core.http_utils import (
    redact_url as _redact_url,
    ssrf_safe_stream as _ssrf_safe_stream,
)
from core.utils import validate_file_path

logger = logging.getLogger(__name__)

VALID_SHARE_ROLES = {"reader", "commenter", "writer"}
VALID_SHARE_TYPES = {"user", "group", "domain", "anyone"}


def check_public_link_permission(permissions: List[Dict[str, Any]]) -> bool:
    """
    Check if file has 'anyone with the link' permission.

    Args:
        permissions: List of permission objects from Google Drive API

    Returns:
        bool: True if file has public link sharing enabled
    """
    return any(
        p.get("type") == "anyone" and p.get("role") in ["reader", "writer", "commenter"]
        for p in permissions
    )


def format_public_sharing_error(file_name: str, file_id: str) -> str:
    """
    Format error message for files without public sharing.

    Args:
        file_name: Name of the file
        file_id: Google Drive file ID

    Returns:
        str: Formatted error message
    """
    return (
        f"❌ Permission Error: '{file_name}' not shared publicly. "
        f"Set 'Anyone with the link' → 'Viewer' in Google Drive sharing. "
        f"File: https://drive.google.com/file/d/{file_id}/view"
    )


def get_drive_image_url(file_id: str) -> str:
    """
    Get the correct Drive URL format for publicly shared images.

    Args:
        file_id: Google Drive file ID

    Returns:
        str: URL for embedding Drive images
    """
    return f"https://drive.google.com/uc?export=view&id={file_id}"


def validate_share_role(role: str) -> None:
    """
    Validate that the role is valid for sharing.

    Args:
        role: The permission role to validate

    Raises:
        ValueError: If role is not reader, commenter, or writer
    """
    if role not in VALID_SHARE_ROLES:
        raise ValueError(
            f"Invalid role '{role}'. Must be one of: {', '.join(sorted(VALID_SHARE_ROLES))}"
        )


def validate_share_type(share_type: str) -> None:
    """
    Validate that the share type is valid.

    Args:
        share_type: The type of sharing to validate

    Raises:
        ValueError: If share_type is not user, group, domain, or anyone
    """
    if share_type not in VALID_SHARE_TYPES:
        raise ValueError(
            f"Invalid share_type '{share_type}'. Must be one of: {', '.join(sorted(VALID_SHARE_TYPES))}"
        )


RFC3339_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def validate_expiration_time(expiration_time: str) -> None:
    """
    Validate that expiration_time is in RFC 3339 format.

    Args:
        expiration_time: The expiration time string to validate

    Raises:
        ValueError: If expiration_time is not valid RFC 3339 format
    """
    if not RFC3339_PATTERN.match(expiration_time):
        raise ValueError(
            f"Invalid expiration_time '{expiration_time}'. "
            "Must be RFC 3339 format (e.g., '2025-01-15T00:00:00Z')"
        )


def format_permission_info(permission: Dict[str, Any]) -> str:
    """
    Format a permission object for display.

    Args:
        permission: Permission object from Google Drive API

    Returns:
        str: Human-readable permission description with ID
    """
    perm_type = permission.get("type", "unknown")
    role = permission.get("role", "unknown")
    perm_id = permission.get("id", "")

    if perm_type == "anyone":
        base = f"Anyone with the link ({role}) [id: {perm_id}]"
    elif perm_type == "user":
        email = permission.get("emailAddress", "unknown")
        base = f"User: {email} ({role}) [id: {perm_id}]"
    elif perm_type == "group":
        email = permission.get("emailAddress", "unknown")
        base = f"Group: {email} ({role}) [id: {perm_id}]"
    elif perm_type == "domain":
        domain = permission.get("domain", "unknown")
        base = f"Domain: {domain} ({role}) [id: {perm_id}]"
    else:
        base = f"{perm_type} ({role}) [id: {perm_id}]"

    extras = []
    if permission.get("expirationTime"):
        extras.append(f"expires: {permission['expirationTime']}")

    perm_details = permission.get("permissionDetails", [])
    if perm_details:
        for detail in perm_details:
            if detail.get("inherited") and detail.get("inheritedFrom"):
                extras.append(f"inherited from: {detail['inheritedFrom']}")
                break

    if extras:
        return f"{base} | {', '.join(extras)}"
    return base


# Precompiled regex patterns for Drive query detection
DRIVE_QUERY_PATTERNS = [
    re.compile(r'\b\w+\s*(=|!=|>|<)\s*[\'"].*?[\'"]', re.IGNORECASE),  # field = 'value'
    re.compile(r"\b\w+\s*(=|!=|>|<)\s*\d+", re.IGNORECASE),  # field = number
    re.compile(r"\bcontains\b", re.IGNORECASE),  # contains operator
    re.compile(r"\bin\s+parents\b", re.IGNORECASE),  # in parents
    re.compile(r"\bhas\s*\{", re.IGNORECASE),  # has {properties}
    re.compile(r"\btrashed\s*=\s*(true|false)\b", re.IGNORECASE),  # trashed=true/false
    re.compile(r"\bstarred\s*=\s*(true|false)\b", re.IGNORECASE),  # starred=true/false
    re.compile(
        r'[\'"][^\'"]+[\'"]\s+in\s+parents', re.IGNORECASE
    ),  # 'parentId' in parents
    re.compile(r"\bfullText\s+contains\b", re.IGNORECASE),  # fullText contains
    re.compile(r"\bname\s*(=|contains)\b", re.IGNORECASE),  # name = or name contains
    re.compile(r"\bmimeType\s*(=|!=)\b", re.IGNORECASE),  # mimeType operators
]


def build_drive_list_params(
    query: str,
    page_size: int,
    drive_id: Optional[str] = None,
    include_items_from_all_drives: bool = True,
    corpora: Optional[str] = None,
    page_token: Optional[str] = None,
    detailed: bool = True,
    include_permissions: bool = False,
    order_by: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Helper function to build common list parameters for Drive API calls.

    Args:
        query: The search query string
        page_size: Maximum number of items to return
        drive_id: Optional shared drive ID
        include_items_from_all_drives: Whether to include items from all drives
        corpora: Optional corpus specification
        page_token: Optional page token for pagination (from a previous nextPageToken)
        detailed: Whether to request size, modifiedTime, and webViewLink fields.
                  Defaults to True to preserve existing behavior.
        include_permissions: Whether detailed results should include file ACL fields.
        order_by: Optional sort order. Comma-separated list of sort keys.
                  Valid keys: 'createdTime', 'folder', 'modifiedByMeTime', 'modifiedTime',
                  'name', 'name_natural', 'quotaBytesUsed', 'recency', 'sharedWithMeTime',
                  'starred', 'viewedByMeTime'. Add 'desc' modifier to reverse (e.g., 'modifiedTime desc').
                  Example: 'folder,modifiedTime desc,name'

    Returns:
        Dictionary of parameters for Drive API list calls
    """
    if detailed:
        permission_fields = (
            ", permissions(id, type, role)" if include_permissions else ""
        )
        fields = (
            "nextPageToken, files(id, name, mimeType, webViewLink, iconLink,"
            " modifiedTime, createdTime, size, driveId,"
            " lastModifyingUser(displayName, emailAddress)"
            f"{permission_fields})"
        )
    else:
        fields = "nextPageToken, files(id, name, mimeType)"
    list_params = {
        "q": query,
        "pageSize": page_size,
        "fields": fields,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": include_items_from_all_drives,
    }

    if page_token:
        list_params["pageToken"] = page_token

    if order_by is not None:
        normalized_order_by = order_by.strip()
        if normalized_order_by:
            list_params["orderBy"] = normalized_order_by

    if drive_id:
        list_params["driveId"] = drive_id
        if corpora:
            list_params["corpora"] = corpora
        else:
            list_params["corpora"] = "drive"
    elif corpora:
        list_params["corpora"] = corpora

    return list_params


SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

# RFC 6838 token-style MIME type validation (safe for Drive query interpolation).
MIME_TYPE_PATTERN = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")

# Mapping from friendly type names to Google Drive MIME types.
# Raw MIME type strings (containing '/') are always accepted as-is.
FILE_TYPE_MIME_MAP: Dict[str, str] = {
    "folder": "application/vnd.google-apps.folder",
    "folders": "application/vnd.google-apps.folder",
    "document": "application/vnd.google-apps.document",
    "doc": "application/vnd.google-apps.document",
    "documents": "application/vnd.google-apps.document",
    "docs": "application/vnd.google-apps.document",
    "spreadsheet": "application/vnd.google-apps.spreadsheet",
    "sheet": "application/vnd.google-apps.spreadsheet",
    "spreadsheets": "application/vnd.google-apps.spreadsheet",
    "sheets": "application/vnd.google-apps.spreadsheet",
    "presentation": "application/vnd.google-apps.presentation",
    "presentations": "application/vnd.google-apps.presentation",
    "slide": "application/vnd.google-apps.presentation",
    "slides": "application/vnd.google-apps.presentation",
    "form": "application/vnd.google-apps.form",
    "forms": "application/vnd.google-apps.form",
    "drawing": "application/vnd.google-apps.drawing",
    "drawings": "application/vnd.google-apps.drawing",
    "pdf": "application/pdf",
    "pdfs": "application/pdf",
    "shortcut": "application/vnd.google-apps.shortcut",
    "shortcuts": "application/vnd.google-apps.shortcut",
    "script": "application/vnd.google-apps.script",
    "scripts": "application/vnd.google-apps.script",
    "site": "application/vnd.google-apps.site",
    "sites": "application/vnd.google-apps.site",
    "jam": "application/vnd.google-apps.jam",
    "jamboard": "application/vnd.google-apps.jam",
    "jamboards": "application/vnd.google-apps.jam",
}


def resolve_file_type_mime(file_type: str) -> str:
    """
    Resolve a friendly file type name or raw MIME type string to a Drive MIME type.

    If `file_type` contains '/' it is returned as-is (treated as a raw MIME type).
    Otherwise it is looked up in FILE_TYPE_MIME_MAP.

    Args:
        file_type: A friendly name ('folder', 'document', 'pdf', …) or a raw MIME
                   type string ('application/vnd.google-apps.document', …).

    Returns:
        str: The resolved MIME type string.

    Raises:
        ValueError: If the value is not a recognised friendly name and contains no '/'.
    """
    normalized = file_type.strip()
    if not normalized:
        raise ValueError("file_type cannot be empty.")

    if "/" in normalized:
        normalized_mime = normalized.lower()
        if not MIME_TYPE_PATTERN.fullmatch(normalized_mime):
            raise ValueError(
                f"Invalid MIME type '{file_type}'. Expected format like 'application/pdf'."
            )
        return normalized_mime
    lower = normalized.lower()
    if lower not in FILE_TYPE_MIME_MAP:
        valid = ", ".join(sorted(FILE_TYPE_MIME_MAP.keys()))
        raise ValueError(
            f"Unknown file_type '{file_type}'. Pass a MIME type directly (e.g. "
            f"'application/pdf') or use one of the friendly names: {valid}"
        )
    return FILE_TYPE_MIME_MAP[lower]


BASE_SHORTCUT_FIELDS = (
    "id, mimeType, parents, shortcutDetails(targetId, targetMimeType)"
)


async def resolve_drive_item(
    service,
    file_id: str,
    *,
    extra_fields: Optional[str] = None,
    max_depth: int = 5,
) -> Tuple[str, Dict[str, Any]]:
    """
    Resolve a Drive shortcut so downstream callers operate on the real item.

    Returns the resolved file ID and its metadata. Raises if shortcut targets loop
    or exceed max_depth to avoid infinite recursion.
    """
    current_id = file_id
    depth = 0
    fields = BASE_SHORTCUT_FIELDS
    if extra_fields:
        fields = f"{fields}, {extra_fields}"

    while True:
        metadata = await asyncio.to_thread(
            service.files()
            .get(fileId=current_id, fields=fields, supportsAllDrives=True)
            .execute
        )
        mime_type = metadata.get("mimeType")
        if mime_type != SHORTCUT_MIME_TYPE:
            return current_id, metadata

        shortcut_details = metadata.get("shortcutDetails") or {}
        target_id = shortcut_details.get("targetId")
        if not target_id:
            raise Exception(f"Shortcut '{current_id}' is missing target details.")

        depth += 1
        if depth > max_depth:
            raise Exception(
                f"Shortcut resolution exceeded {max_depth} hops starting from '{file_id}'."
            )
        current_id = target_id


async def resolve_folder_id(
    service,
    folder_id: str,
    *,
    max_depth: int = 5,
) -> str:
    """
    Resolve a folder ID that might be a shortcut and ensure the final target is a folder.
    """
    resolved_id, metadata = await resolve_drive_item(
        service,
        folder_id,
        max_depth=max_depth,
    )
    mime_type = metadata.get("mimeType")
    if mime_type != FOLDER_MIME_TYPE:
        raise Exception(
            f"Resolved ID '{resolved_id}' (from '{folder_id}') is not a folder; mimeType={mime_type}."
        )
    return resolved_id


DOWNLOAD_CHUNK_SIZE_BYTES = 256 * 1024  # 256 KB
UPLOAD_CHUNK_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB (Google recommended minimum)
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB safety limit for URL downloads


async def _stream_url_with_validation(
    url: str, write_chunk: Optional[Callable[[bytes], Awaitable[None]]] = None
) -> Tuple[int, Optional[str]]:
    """Stream a remote file with shared status and size validation."""
    total_bytes = 0
    redacted_url = _redact_url(url)

    async with _ssrf_safe_stream(url) as resp:
        if resp.status_code != 200:
            request = getattr(resp, "request", None)
            if request is None:
                parsed_url = urlparse(url)
                request = httpx.Request("GET", f"{parsed_url.scheme}://{redacted_url}")
            raise httpx.HTTPStatusError(
                f"Failed to fetch file from URL: {redacted_url} (status {resp.status_code})",
                request=request,
                response=resp,
            )

        content_type = resp.headers.get("Content-Type")
        async for chunk in resp.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE_BYTES):
            total_bytes += len(chunk)
            if total_bytes > MAX_DOWNLOAD_BYTES:
                raise ValueError(
                    f"Download from {redacted_url} exceeded {MAX_DOWNLOAD_BYTES} byte limit "
                    f"({total_bytes} bytes)"
                )
            if write_chunk is not None:
                await write_chunk(chunk)

    return total_bytes, content_type


async def _download_url_to_bytes(url: str) -> Tuple[BinaryIO, Optional[str]]:
    """Download a remote file into a spooled temporary file with bounded streaming."""
    spool = SpooledTemporaryFile(max_size=UPLOAD_CHUNK_SIZE_BYTES)
    try:

        async def _collect(chunk: bytes) -> None:
            await asyncio.to_thread(spool.write, chunk)

        _total_bytes, content_type = await _stream_url_with_validation(url, _collect)
        await asyncio.to_thread(spool.seek, 0)
        return spool, content_type
    except Exception:
        spool.close()
        raise


# Mapping of file extensions to source MIME types for Google Docs conversion
GOOGLE_DOCS_IMPORT_FORMATS = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".rtf": "application/rtf",
    ".odt": "application/vnd.oasis.opendocument.text",
}

# Mapping of file extensions to source MIME types for Google Slides conversion
GOOGLE_SLIDES_IMPORT_FORMATS = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".odp": "application/vnd.oasis.opendocument.presentation",
}

# Mapping of file extensions to source MIME types for Google Sheets conversion
GOOGLE_SHEETS_IMPORT_FORMATS = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
}

GOOGLE_DOCS_MIME_TYPE = "application/vnd.google-apps.document"
GOOGLE_SLIDES_MIME_TYPE = "application/vnd.google-apps.presentation"
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"

# Source MIME types safe to build from an in-memory `content` string. Binary
# Office/OpenDocument formats must come from file_path/file_url; UTF-8 encoding
# their bytes from a string would corrupt the upload and its conversion.
TEXT_BASED_IMPORT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/html",
    "text/csv",
    "text/tab-separated-values",
    "application/rtf",
}


def _detect_source_format(
    file_name: str,
    content: Optional[str] = None,
    format_map: Optional[Dict[str, str]] = None,
) -> str:
    """
    Detect the source MIME type from a file extension.

    Uses ``format_map`` (defaults to the Google Docs format map) and falls back to
    text/markdown for markdown-looking content, else text/plain.
    """
    if format_map is None:
        format_map = GOOGLE_DOCS_IMPORT_FORMATS

    ext = Path(file_name).suffix.lower()
    if ext in format_map:
        return format_map[ext]

    if content and (content.startswith("#") or "```" in content or "**" in content):
        return "text/markdown"

    return "text/plain"


async def _resolve_import_media(
    *,
    tool_name: str,
    file_name: str,
    content: Optional[str],
    file_path: Optional[str],
    file_url: Optional[str],
    source_format: Optional[str],
    format_map: Dict[str, str],
) -> Tuple[MediaIoBaseUpload, str, Optional[BinaryIO]]:
    """
    Resolve a content source into an upload ``MediaIoBaseUpload`` and source MIME type.

    Exactly one of ``content``, ``file_path``, or ``file_url`` must be provided.
    The source bytes are uploaded with their *source* MIME type so the Drive API can
    convert them into the destination Google Apps format. ``format_map`` is the
    extension → source MIME allowlist used for detection and validation.

    Returns ``(media, source_mime_type, closeable)``; when the source is a remote URL,
    ``closeable`` is the download stream the caller must close after upload (else None).
    """
    source_count = sum(1 for x in (content, file_path, file_url) if x is not None)
    if source_count == 0:
        raise ValueError(
            "You must provide one of: 'content', 'file_path', or 'file_url'."
        )
    if source_count > 1:
        raise ValueError("Provide only one of: 'content', 'file_path', or 'file_url'.")

    # Determine source MIME type from the explicit hint or auto-detection.
    if source_format:
        format_key = f".{source_format.lower().lstrip('.')}"
        if format_key not in format_map:
            raise ValueError(
                f"Unsupported source_format: '{source_format}'. "
                f"Supported: {', '.join(ext.lstrip('.') for ext in format_map.keys())}"
            )
        source_mime_type = format_map[format_key]
    else:
        detection_name = file_path or file_name
        if file_url is not None:
            detection_name = urlparse(file_url).path or file_url
        source_mime_type = _detect_source_format(detection_name, content, format_map)

    logger.info(f"[{tool_name}] Detected source MIME type: {source_mime_type}")

    file_data: bytes
    remote_file_data: Optional[BinaryIO] = None

    if content is not None:
        if source_mime_type not in TEXT_BASED_IMPORT_MIME_TYPES:
            raise ValueError(
                f"[{tool_name}] 'content' is only valid for text-based source formats, "
                f"but the source resolves to '{source_mime_type}' (a binary format). "
                f"Provide a 'file_path' or 'file_url' for binary formats instead."
            )
        file_data = content.encode("utf-8")
        logger.info(f"[{tool_name}] Using content: {len(file_data)} bytes")

    elif file_path is not None:
        parsed_url = urlparse(file_path)
        if parsed_url.scheme == "file":
            raw_path = parsed_url.path or ""
            netloc = parsed_url.netloc
            if netloc and netloc.lower() != "localhost":
                raw_path = f"//{netloc}{raw_path}"
            actual_path = url2pathname(raw_path)
        elif parsed_url.scheme == "":
            actual_path = file_path
        else:
            raise ValueError(
                f"file_path should be a local path or file:// URL, got: {file_path}"
            )

        path_obj = validate_file_path(actual_path)
        if not path_obj.exists():
            raise FileNotFoundError(f"File not found: {actual_path}")
        if not path_obj.is_file():
            raise ValueError(f"Path is not a file: {actual_path}")

        file_data = await asyncio.to_thread(path_obj.read_bytes)
        logger.info(f"[{tool_name}] Read local file: {len(file_data)} bytes")

        # Re-detect from the real file extension when no explicit hint was given.
        if not source_format:
            source_mime_type = _detect_source_format(actual_path, None, format_map)

    else:  # file_url is not None
        parsed_url = urlparse(file_url)
        if parsed_url.scheme not in ("http", "https"):
            raise ValueError(f"file_url must be http:// or https://, got: {file_url}")

        remote_file_data, remote_content_type = await _download_url_to_bytes(file_url)

        # Prefer the response Content-Type, falling back to URL-based detection.
        if not source_format:
            ct_base = (remote_content_type or "").split(";", 1)[0].strip()
            if ct_base and ct_base in format_map.values():
                source_mime_type = ct_base
            else:
                source_mime_type = _detect_source_format(
                    parsed_url.path or file_url, None, format_map
                )

    # Enforce the allowlist on the final resolved MIME type so auto-detection can't
    # upload an unsupported source (e.g. text/plain from an unknown extension).
    if source_mime_type not in format_map.values():
        if remote_file_data is not None:
            remote_file_data.close()
        raise ValueError(
            f"[{tool_name}] Detected source MIME type '{source_mime_type}' is not "
            f"supported by this tool. Supported source formats: "
            f"{', '.join(ext.lstrip('.') for ext in sorted(format_map.keys()))}."
        )

    media = MediaIoBaseUpload(
        remote_file_data if remote_file_data is not None else io.BytesIO(file_data),
        mimetype=source_mime_type,  # Source format drives Drive's auto-conversion
        resumable=True,
        chunksize=UPLOAD_CHUNK_SIZE_BYTES,
    )
    return media, source_mime_type, remote_file_data
