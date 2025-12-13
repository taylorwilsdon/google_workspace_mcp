"""
Google Drive Helper Functions

Shared utilities for Google Drive operations including permission checking.
"""

import asyncio
import re
from typing import List, Dict, Any, Optional, Tuple


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
) -> Dict[str, Any]:
    """
    Helper function to build common list parameters for Drive API calls.

    Args:
        query: The search query string
        page_size: Maximum number of items to return
        drive_id: Optional shared drive ID
        include_items_from_all_drives: Whether to include items from all drives
        corpora: Optional corpus specification

    Returns:
        Dictionary of parameters for Drive API list calls
    """
    list_params = {
        "q": query,
        "pageSize": page_size,
        "fields": "nextPageToken, files(id, name, mimeType, webViewLink, iconLink, modifiedTime, size)",
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": include_items_from_all_drives,
    }

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
