"""
Google Keep MCP Tools

This module provides MCP tools for interacting with the Google Keep API.
"""

# API Reference: https://developers.google.com/workspace/keep/api/reference/rest

import asyncio
import base64
import logging
from typing import Any, Dict, List, Optional

from mcp import Resource

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors
from gkeep.keep_helpers import Note, build_note_body, format_note, format_note_content

logger = logging.getLogger(__name__)

LIST_NOTES_PAGE_SIZE_DEFAULT = 25
LIST_NOTES_PAGE_SIZE_MAX = 1000

def _redact_email(email: str) -> str:
    if "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    return f"{local[:2]}***@{domain}"

@server.tool()  # type: ignore
@require_google_service("keep", "keep_read")  # type: ignore
@handle_http_errors("list_notes", service_type="keep")  # type: ignore
async def list_notes(
    service: Resource,
    user_google_email: str,
    page_size: int = LIST_NOTES_PAGE_SIZE_DEFAULT,
    page_token: Optional[str] = None,
    filter_query: Optional[str] = None,
) -> str:
    """
    List notes from Google Keep.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Maximum number of notes to return (default: 25, max: 1000).
        page_token (Optional[str]): Token for pagination.
        filter_query (Optional[str]): Filter for list results. If no filter is supplied, the
            trashed filter is applied by default. Filterable fields: createTime,
            updateTime, trashTime, trashed.

    Returns:
        str: List of notes with their details.
    """
    logger.info(f"[list_notes] Invoked. Email: '{_redact_email(_redact_email(user_google_email))}'")

    params: Dict[str, Any] = {}
    if page_size is not None and page_size >= 1:
        params["pageSize"] = min(page_size, LIST_NOTES_PAGE_SIZE_MAX)
    if page_token:
        params["pageToken"] = page_token
    if filter_query:
        params["filter"] = filter_query

    result = await asyncio.to_thread(service.notes().list(**params).execute)

    raw_notes = result.get("notes", [])
    next_page_token = result.get("nextPageToken")

    if not raw_notes:
        return f"No notes found for {_redact_email(user_google_email)}."

    notes = [Note.from_api(n) for n in raw_notes]
    response = f"Notes for {_redact_email(user_google_email)}:\n\n"
    for note in notes:
        response += format_note(note) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}\n"

    logger.info(f"Found {len(notes)} notes for {_redact_email(user_google_email)}")
    return response


@server.tool()  # type: ignore
@require_google_service("keep", "keep_read")  # type: ignore
@handle_http_errors("get_note", service_type="keep")  # type: ignore
async def get_note(
    service: Resource,
    _redact_email(user_google_email): str,
    note_id: str,
) -> str:
    """
    Get metadata and a summary of a specific note.

    Returns note details including title, a preview of the body (truncated to 200
    characters for text notes), timestamps, attachments, and permissions. For the
    full note content, use read_note instead.

    Args:
        _redact_email(user_google_email) (str): The user's Google email address. Required.
        note_id (str): The ID of the note to retrieve (e.g., "notes/abc123" or just "abc123").

    Returns:
        str: Note summary including title, body preview, timestamps, and metadata.
    """
    logger.info(
        f"[get_note] Invoked. Email: '{_redact_email(user_google_email)}', Note ID: {note_id}"
    )

    name = note_id if note_id.startswith("notes/") else f"notes/{note_id}"
    result = await asyncio.to_thread(service.notes().get(name=name).execute)
    note = Note.from_api(result)

    response = f"Note Details for {name}:\n"
    response += format_note(note)

    logger.info(f"Retrieved note '{name}' for {_redact_email(user_google_email)}")
    return response


@server.tool()  # type: ignore
@require_google_service("keep", "keep_read")  # type: ignore
@handle_http_errors("read_note", service_type="keep")  # type: ignore
async def read_note(
    service: Resource,
    _redact_email(user_google_email): str,
    note_id: str,
) -> str:
    """
    Read the full contents of a specific note.

    Returns only the note's content (text body or checklist items) without
    truncation. Use get_note for metadata and summaries.

    Args:
        _redact_email(user_google_email) (str): The user's Google email address. Required.
        note_id (str): The ID of the note to read (e.g., "notes/abc123" or just "abc123").

    Returns:
        str: The full note content (text or checklist items).
    """
    logger.info(
        f"[read_note] Invoked. Email: '{_redact_email(user_google_email)}', Note ID: {note_id}"
    )

    name = note_id if note_id.startswith("notes/") else f"notes/{note_id}"
    result = await asyncio.to_thread(service.notes().get(name=name).execute)
    note = Note.from_api(result)

    response = format_note_content(note)

    logger.info(f"Read note content '{name}' for {_redact_email(user_google_email)}")
    return response


@server.tool()  # type: ignore
@require_google_service("keep", "keep_write")  # type: ignore
@handle_http_errors("create_note", service_type="keep")  # type: ignore
async def create_note(
    service: Resource,
    _redact_email(user_google_email): str,
    title: str,
    text: Optional[str] = None,
    list_items: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Create a new note in Google Keep.

    The note body can be either text or a list (checklist), but not both.
    If neither text nor list_items is provided, the note will be created with just a title.

    Args:
        _redact_email(user_google_email) (str): The user's Google email address. Required.
        title (str): The title of the note (max 1,000 characters).
        text (Optional[str]): Plain text content for the note body (max 20,000 characters).
        list_items (Optional[List[Dict[str, Any]]]): List/checklist items. Each item should
            have: {"text": {"text": "item text"}, "checked": false}. Items may include
            "childListItems" for one level of nesting. Max 1,000 items.

    Returns:
        str: Confirmation message with the new note details.
    """
    logger.info(
        f"[create_note] Invoked. Email: '{_redact_email(user_google_email)}', Title: '{title}'"
    )

    body = build_note_body(title, text=text, list_items=list_items)
    result = await asyncio.to_thread(service.notes().create(body=body).execute)
    note = Note.from_api(result)

    response = f"Note Created for {_redact_email(user_google_email)}:\n"
    response += format_note(note)

    logger.info(
        f"Created note '{title}' with name {note.name} for {_redact_email(user_google_email)}"
    )
    return response


@server.tool()  # type: ignore
@require_google_service("keep", "keep_write")  # type: ignore
@handle_http_errors("delete_note", service_type="keep")  # type: ignore
async def delete_note(
    service: Resource,
    _redact_email(user_google_email): str,
    note_id: str,
) -> str:
    """
    Delete a note from Google Keep.

    Args:
        _redact_email(user_google_email) (str): The user's Google email address. Required.
        note_id (str): The ID of the note to delete (e.g., "notes/abc123" or just "abc123").

    Returns:
        str: Confirmation message.
    """
    logger.info(
        f"[delete_note] Invoked. Email: '{_redact_email(user_google_email)}', Note ID: {note_id}"
    )

    name = note_id if note_id.startswith("notes/") else f"notes/{note_id}"

    await asyncio.to_thread(service.notes().delete(name=name).execute)

    response = f"Note '{name}' has been deleted for {_redact_email(user_google_email)}."

    logger.info(f"Deleted note '{name}' for {_redact_email(user_google_email)}")
    return response


@server.tool()  # type: ignore
@require_google_service("keep", "keep_read")  # type: ignore
@handle_http_errors("download_attachment", service_type="keep")  # type: ignore
async def download_attachment(
    service: Resource,
    _redact_email(user_google_email): str,
    attachment_name: str,
    mime_type: str,
) -> str:
    """
    Download an attachment from a Google Keep note.

    Use the attachment name from the note's attachments list (obtained via read_note).

    Args:
        _redact_email(user_google_email) (str): The user's Google email address. Required.
        attachment_name (str): The resource name of the attachment
            (e.g., "notes/abc123/attachments/def456").
        mime_type (str): The MIME type to download the attachment as. Must match one of
            the types listed in the attachment's mimeType field.

    Returns:
        str: Information about the downloaded attachment.
    """
    logger.info(
        f"[download_attachment] Invoked. Email: '{_redact_email(user_google_email)}', Attachment: {attachment_name}"
    )

    result = await asyncio.to_thread(
        service.media().download(name=attachment_name, mimeType=mime_type).execute
    )

    if isinstance(result, dict):
        return (
            f"Keep API returned metadata instead of binary data for '{attachment_name}'.\n"
            f"Response: {result}"
        )

    file_bytes: bytes = result
    size_bytes = len(file_bytes)
    size_kb = size_bytes / 1024

    # Derive a human-readable filename from the attachment resource name
    filename = attachment_name.rsplit("/", 1)[-1] or "attachment"

    from auth.oauth_config import is_stateless_mode

    if is_stateless_mode():
        b64_preview = base64.urlsafe_b64encode(file_bytes).decode("utf-8")[:100]
        return "\n".join(
            [
                f"Attachment downloaded: {attachment_name} ({mime_type})",
                f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
                "",
                "Stateless mode: File storage disabled.",
                f"Base64 preview: {b64_preview}...",
            ]
        )

    from core.attachment_storage import get_attachment_storage, get_attachment_url
    from core.config import get_transport_mode

    storage = get_attachment_storage()
    b64_data = base64.urlsafe_b64encode(file_bytes).decode("utf-8")
    saved = storage.save_attachment(
        base64_data=b64_data, filename=filename, mime_type=mime_type
    )

    result_lines = [
        f"Attachment downloaded: {attachment_name}",
        f"Type: {mime_type}",
        f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
    ]

    if get_transport_mode() == "stdio":
        result_lines.append(f"\nSaved to: {saved.path}")
        result_lines.append(
            "\nThe file has been saved to disk and can be accessed directly via the file path."
        )
    else:
        download_url = get_attachment_url(saved.file_id)
        result_lines.append(f"\nDownload URL: {download_url}")
        result_lines.append("\nThe file will expire after 1 hour.")

    logger.info(
        f"[download_attachment] Saved {size_kb:.1f} KB attachment to {saved.path}"
    )
    return "\n".join(result_lines)


@server.tool()  # type: ignore
@require_google_service("keep", "keep_write")  # type: ignore
@handle_http_errors("set_permissions", service_type="keep")  # type: ignore
async def set_permissions(
    service: Resource,
    _redact_email(user_google_email): str,
    note_id: str,
    emails: List[str],
    member_type: str = "user",
) -> str:
    """
    Set the WRITER permissions for a Google Keep note.

    This replaces all existing non-owner permissions with the specified list of members.
    It reads the current permissions, removes all existing WRITER permissions, then adds
    the new ones. The OWNER permission cannot be modified.

    Only the WRITER role can be granted via the API.

    Args:
        _redact_email(user_google_email) (str): The user's Google email address. Required.
        note_id (str): The ID of the note (e.g., "notes/abc123" or just "abc123").
        emails (List[str]): List of email addresses to grant WRITER access to.
            Pass an empty list to remove all non-owner permissions.
        member_type (str): Type of member for all emails: "user" (default) or "group".

    Returns:
        str: Confirmation message with the updated permissions.
    """
    logger.info(
        f"[set_permissions] Invoked. Email: '{_redact_email(user_google_email)}', Note ID: {note_id}, "
        f"Emails: {emails}"
    )
    if member_type not in ["user", "group"]:
        raise ValueError("member_type must be 'user' or 'group'")

    name = note_id if note_id.startswith("notes/") else f"notes/{note_id}"

    # Step 1: Read existing permissions
    result = await asyncio.to_thread(service.notes().get(name=name).execute)
    note = Note.from_api(result)

    # Step 2: Delete all existing non-OWNER permissions
    non_owner_perm_names = [
        perm.name
        for perm in note.permissions
        if perm.role != "OWNER" and perm.name
    ]

    if non_owner_perm_names:
        await asyncio.to_thread(
            service.notes()
            .permissions()
            .batchDelete(
                parent=name,
                body={"names": non_owner_perm_names},
            )
            .execute
        )
        logger.info(
            f"Deleted {len(non_owner_perm_names)} existing permissions for '{name}'"
        )

    # Step 3: Create new permissions if any emails provided
    if emails:
        permission_requests = []
        for email in emails:
            member_field = {member_type: {"email": email}}
            permission_requests.append(
                {
                    "parent": name,
                    "permission": {
                        "role": "WRITER",
                        "email": email,
                        **member_field,
                    },
                }
            )

        result = await asyncio.to_thread(
            service.notes()
            .permissions()
            .batchCreate(
                parent=name,
                body={"requests": permission_requests},
            )
            .execute
        )

        created_permissions = result.get("permissions", [])
        response = f"Permissions updated for note '{name}' ({_redact_email(user_google_email)}):\n"
        response += f"- Removed {len(non_owner_perm_names)} existing permission(s)\n"
        response += f"- Added {len(created_permissions)} new permission(s):\n"
        for perm in created_permissions:
            response += f"  - {perm.get('email', 'N/A')} (role: {perm.get('role', 'N/A')})\n"
    else:
        response = f"Permissions updated for note '{name}' ({_redact_email(user_google_email)}):\n"
        response += f"- Removed {len(non_owner_perm_names)} existing permission(s)\n"
        response += "- No new permissions added (empty email list)\n"

    logger.info(
        f"Set permissions for note '{name}': removed {len(non_owner_perm_names)}, "
        f"added {len(emails)} for {_redact_email(user_google_email)}"
    )
    return response
