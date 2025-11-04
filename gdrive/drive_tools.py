"""
Google Drive MCP Tools

This module provides MCP tools for interacting with Google Drive API.
"""
import logging
import asyncio
from typing import Optional
from tempfile import NamedTemporaryFile

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io
import httpx

from auth.service_decorator import require_google_service
from auth.oauth_config import is_stateless_mode
from core.utils import extract_office_xml_text, handle_http_errors
from core.server import server
from gdrive.drive_helpers import DRIVE_QUERY_PATTERNS, build_drive_list_params

logger = logging.getLogger(__name__)

DOWNLOAD_CHUNK_SIZE_BYTES = 256 * 1024  # 256 KB
UPLOAD_CHUNK_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB (Google recommended minimum)

@server.tool()
@handle_http_errors("search_drive_files", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def search_drive_files(
    service,
    user_google_email: str,
    query: str,
    page_size: int = 10,
    drive_id: Optional[str] = None,
    include_items_from_all_drives: bool = True,
    corpora: Optional[str] = None,
) -> str:
    """
    Searches for files and folders within a user's Google Drive, including shared drives.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): The search query string. Supports Google Drive search operators.
        page_size (int): The maximum number of files to return. Defaults to 10.
        drive_id (Optional[str]): ID of the shared drive to search. If None, behavior depends on `corpora` and `include_items_from_all_drives`.
        include_items_from_all_drives (bool): Whether shared drive items should be included in results. Defaults to True. This is effective when not specifying a `drive_id`.
        corpora (Optional[str]): Bodies of items to query (e.g., 'user', 'domain', 'drive', 'allDrives').
                                 If 'drive_id' is specified and 'corpora' is None, it defaults to 'drive'.
                                 Otherwise, Drive API default behavior applies. Prefer 'user' or 'drive' over 'allDrives' for efficiency.

    Returns:
        str: A formatted list of found files/folders with their details (ID, name, type, size, modified time, link).
    """
    logger.info(f"[search_drive_files] Invoked. Email: '{user_google_email}', Query: '{query}'")

    # Check if the query looks like a structured Drive query or free text
    # Look for Drive API operators and structured query patterns
    is_structured_query = any(pattern.search(query) for pattern in DRIVE_QUERY_PATTERNS)

    if is_structured_query:
        final_query = query
        logger.info(f"[search_drive_files] Using structured query as-is: '{final_query}'")
    else:
        # For free text queries, wrap in fullText contains
        escaped_query = query.replace("'", "\\'")
        final_query = f"fullText contains '{escaped_query}'"
        logger.info(f"[search_drive_files] Reformatting free text query '{query}' to '{final_query}'")

    list_params = build_drive_list_params(
        query=final_query,
        page_size=page_size,
        drive_id=drive_id,
        include_items_from_all_drives=include_items_from_all_drives,
        corpora=corpora,
    )

    results = await asyncio.to_thread(
        service.files().list(**list_params).execute
    )
    files = results.get('files', [])
    if not files:
        return f"No files found for '{query}'."

    formatted_files_text_parts = [f"Found {len(files)} files for {user_google_email} matching '{query}':"]
    for item in files:
        size_str = f", Size: {item.get('size', 'N/A')}" if 'size' in item else ""
        formatted_files_text_parts.append(
            f"- Name: \"{item['name']}\" (ID: {item['id']}, Type: {item['mimeType']}{size_str}, Modified: {item.get('modifiedTime', 'N/A')}) Link: {item.get('webViewLink', '#')}"
        )
    text_output = "\n".join(formatted_files_text_parts)
    return text_output

@server.tool()
@handle_http_errors("get_drive_file_content", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def get_drive_file_content(
    service,
    user_google_email: str,
    file_id: str,
) -> str:
    """
    Retrieves the content of a specific Google Drive file by ID, supporting files in shared drives.

    • Native Google Docs, Sheets, Slides → exported as text / CSV.
    • Office files (.docx, .xlsx, .pptx) → unzipped & parsed with std-lib to
      extract readable text.
    • Any other file → downloaded; tries UTF-8 decode, else notes binary.

    Args:
        user_google_email: The user’s Google email address.
        file_id: Drive file ID.

    Returns:
        str: The file content as plain text with metadata header.
    """
    logger.info(f"[get_drive_file_content] Invoked. File ID: '{file_id}'")

    file_metadata = await asyncio.to_thread(
        service.files().get(
            fileId=file_id, fields="id, name, mimeType, webViewLink", supportsAllDrives=True
        ).execute
    )
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")
    export_mime_type = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }.get(mime_type)

    request_obj = (
        service.files().export_media(fileId=file_id, mimeType=export_mime_type, supportsAllDrives=True)
        if export_mime_type
        else service.files().get_media(fileId=file_id, supportsAllDrives=True)
    )
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request_obj)
    loop = asyncio.get_event_loop()
    done = False
    while not done:
        status, done = await loop.run_in_executor(None, downloader.next_chunk)

    file_content_bytes = fh.getvalue()

    # Attempt Office XML extraction only for actual Office XML files
    office_mime_types = {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    }

    if mime_type in office_mime_types:
        office_text = extract_office_xml_text(file_content_bytes, mime_type)
        if office_text:
            body_text = office_text
        else:
            # Fallback: try UTF-8; otherwise flag binary
            try:
                body_text = file_content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = (
                    f"[Binary or unsupported text encoding for mimeType '{mime_type}' - "
                    f"{len(file_content_bytes)} bytes]"
                )
    else:
        # For non-Office files (including Google native files), try UTF-8 decode directly
        try:
            body_text = file_content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            body_text = (
                f"[Binary or unsupported text encoding for mimeType '{mime_type}' - "
                f"{len(file_content_bytes)} bytes]"
            )

    # Assemble response
    header = (
        f'File: "{file_name}" (ID: {file_id}, Type: {mime_type})\n'
        f'Link: {file_metadata.get("webViewLink", "#")}\n\n--- CONTENT ---\n'
    )
    return header + body_text


@server.tool()
@handle_http_errors("list_drive_items", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def list_drive_items(
    service,
    user_google_email: str,
    folder_id: str = 'root',
    page_size: int = 100,
    drive_id: Optional[str] = None,
    include_items_from_all_drives: bool = True,
    corpora: Optional[str] = None,
) -> str:
    """
    Lists files and folders, supporting shared drives.
    If `drive_id` is specified, lists items within that shared drive. `folder_id` is then relative to that drive (or use drive_id as folder_id for root).
    If `drive_id` is not specified, lists items from user's "My Drive" and accessible shared drives (if `include_items_from_all_drives` is True).

    Args:
        user_google_email (str): The user's Google email address. Required.
        folder_id (str): The ID of the Google Drive folder. Defaults to 'root'. For a shared drive, this can be the shared drive's ID to list its root, or a folder ID within that shared drive.
        page_size (int): The maximum number of items to return. Defaults to 100.
        drive_id (Optional[str]): ID of the shared drive. If provided, the listing is scoped to this drive.
        include_items_from_all_drives (bool): Whether items from all accessible shared drives should be included if `drive_id` is not set. Defaults to True.
        corpora (Optional[str]): Corpus to query ('user', 'drive', 'allDrives'). If `drive_id` is set and `corpora` is None, 'drive' is used. If None and no `drive_id`, API defaults apply.

    Returns:
        str: A formatted list of files/folders in the specified folder.
    """
    logger.info(f"[list_drive_items] Invoked. Email: '{user_google_email}', Folder ID: '{folder_id}'")

    final_query = f"'{folder_id}' in parents and trashed=false"

    list_params = build_drive_list_params(
        query=final_query,
        page_size=page_size,
        drive_id=drive_id,
        include_items_from_all_drives=include_items_from_all_drives,
        corpora=corpora,
    )

    results = await asyncio.to_thread(
        service.files().list(**list_params).execute
    )
    files = results.get('files', [])
    if not files:
        return f"No items found in folder '{folder_id}'."

    formatted_items_text_parts = [f"Found {len(files)} items in folder '{folder_id}' for {user_google_email}:"]
    for item in files:
        size_str = f", Size: {item.get('size', 'N/A')}" if 'size' in item else ""
        formatted_items_text_parts.append(
            f"- Name: \"{item['name']}\" (ID: {item['id']}, Type: {item['mimeType']}{size_str}, Modified: {item.get('modifiedTime', 'N/A')}) Link: {item.get('webViewLink', '#')}"
        )
    text_output = "\n".join(formatted_items_text_parts)
    return text_output

@server.tool()
@handle_http_errors("create_drive_file", service_type="drive")
@require_google_service("drive", "drive_file")
async def create_drive_file(
    service,
    user_google_email: str,
    file_name: str,
    content: Optional[str] = None,  # Now explicitly Optional
    folder_id: str = 'root',
    mime_type: str = 'text/plain',
    fileUrl: Optional[str] = None,  # Now explicitly Optional
) -> str:
    """
    Creates a new file in Google Drive, supporting creation within shared drives.
    Accepts either direct content or a fileUrl to fetch the content from.

    Args:
        user_google_email (str): The user's Google email address. Required.
        file_name (str): The name for the new file.
        content (Optional[str]): If provided, the content to write to the file.
        folder_id (str): The ID of the parent folder. Defaults to 'root'. For shared drives, this must be a folder ID within the shared drive.
        mime_type (str): The MIME type of the file. Defaults to 'text/plain'.
        fileUrl (Optional[str]): If provided, fetches the file content from this URL.

    Returns:
        str: Confirmation message of the successful file creation with file link.
    """
    logger.info(f"[create_drive_file] Invoked. Email: '{user_google_email}', File Name: {file_name}, Folder ID: {folder_id}, fileUrl: {fileUrl}")

    if not content and not fileUrl:
        raise Exception("You must provide either 'content' or 'fileUrl'.")

    file_data = None

    file_metadata = {
        'name': file_name,
        'parents': [folder_id],
        'mimeType': mime_type
    }

    # Prefer fileUrl if both are provided
    if fileUrl:
        logger.info(f"[create_drive_file] Fetching file from URL: {fileUrl}")
        # when running in stateless mode, deployment may not have access to local file system
        if is_stateless_mode():
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(fileUrl)
                if resp.status_code != 200:
                    raise Exception(f"Failed to fetch file from URL: {fileUrl} (status {resp.status_code})")
                file_data = await resp.aread()
                # Try to get MIME type from Content-Type header
                content_type = resp.headers.get("Content-Type")
                if content_type and content_type != "application/octet-stream":
                    mime_type = content_type
                    file_metadata['mimeType'] = content_type
                    logger.info(f"[create_drive_file] Using MIME type from Content-Type header: {content_type}")

            media = MediaIoBaseUpload(
                io.BytesIO(file_data),
                mimetype=mime_type,
                resumable=True,
                chunksize=UPLOAD_CHUNK_SIZE_BYTES
            )

            created_file = await asyncio.to_thread(
                service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id, name, webViewLink',
                    supportsAllDrives=True
                ).execute
            )
        else:
            # Use NamedTemporaryFile to stream download and upload
            with NamedTemporaryFile() as temp_file:
                total_bytes = 0
                # follow redirects
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    async with client.stream('GET', fileUrl) as resp:
                        if resp.status_code != 200:
                            raise Exception(f"Failed to fetch file from URL: {fileUrl} (status {resp.status_code})")

                        # Stream download in chunks
                        async for chunk in resp.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_SIZE_BYTES):
                            await asyncio.to_thread(temp_file.write, chunk)
                            total_bytes += len(chunk)

                        logger.info(f"[create_drive_file] Downloaded {total_bytes} bytes from URL before upload.")

                        # Try to get MIME type from Content-Type header
                        content_type = resp.headers.get("Content-Type")
                        if content_type and content_type != "application/octet-stream":
                            mime_type = content_type
                            file_metadata['mimeType'] = mime_type
                            logger.info(f"[create_drive_file] Using MIME type from Content-Type header: {mime_type}")

                # Reset file pointer to beginning for upload
                temp_file.seek(0)

                # Upload with chunking
                media = MediaIoBaseUpload(
                    temp_file,
                    mimetype=mime_type,
                    resumable=True,
                    chunksize=UPLOAD_CHUNK_SIZE_BYTES
                )

                logger.info("[create_drive_file] Starting upload to Google Drive...")
                created_file = await asyncio.to_thread(
                    service.files().create(
                        body=file_metadata,
                        media_body=media,
                        fields='id, name, webViewLink',
                        supportsAllDrives=True
                    ).execute
                )
    elif content:
        file_data = content.encode('utf-8')
        media = io.BytesIO(file_data)

        created_file = await asyncio.to_thread(
            service.files().create(
                body=file_metadata,
                media_body=MediaIoBaseUpload(media, mimetype=mime_type, resumable=True),
                fields='id, name, webViewLink',
                supportsAllDrives=True
            ).execute
        )

    link = created_file.get('webViewLink', 'No link available')
    confirmation_message = f"Successfully created file '{created_file.get('name', file_name)}' (ID: {created_file.get('id', 'N/A')}) in folder '{folder_id}' for {user_google_email}. Link: {link}"
    logger.info(f"Successfully created file. Link: {link}")
    return confirmation_message

@server.tool()
@handle_http_errors("get_drive_file_permissions", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def get_drive_file_permissions(
    service,
    user_google_email: str,
    file_id: str,
) -> str:
    """
    Gets detailed metadata about a Google Drive file including sharing permissions.
    
    Args:
        user_google_email (str): The user's Google email address. Required.
        file_id (str): The ID of the file to check permissions for.
    
    Returns:
        str: Detailed file metadata including sharing status and URLs.
    """
    logger.info(f"[get_drive_file_permissions] Checking file {file_id} for {user_google_email}")
    
    try:
        # Get comprehensive file metadata including permissions
        file_metadata = await asyncio.to_thread(
            service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, size, modifiedTime, owners, permissions, "
                       "webViewLink, webContentLink, shared, sharingUser, viewersCanCopyContent",
                supportsAllDrives=True
            ).execute
        )
        
        # Format the response
        output_parts = [
            f"File: {file_metadata.get('name', 'Unknown')}",
            f"ID: {file_id}",
            f"Type: {file_metadata.get('mimeType', 'Unknown')}",
            f"Size: {file_metadata.get('size', 'N/A')} bytes",
            f"Modified: {file_metadata.get('modifiedTime', 'N/A')}",
            "",
            "Sharing Status:",
            f"  Shared: {file_metadata.get('shared', False)}",
        ]
        
        # Add sharing user if available
        sharing_user = file_metadata.get('sharingUser')
        if sharing_user:
            output_parts.append(f"  Shared by: {sharing_user.get('displayName', 'Unknown')} ({sharing_user.get('emailAddress', 'Unknown')})")
        
        # Process permissions
        permissions = file_metadata.get('permissions', [])
        if permissions:
            output_parts.append(f"  Number of permissions: {len(permissions)}")
            output_parts.append("  Permissions:")
            for perm in permissions:
                perm_type = perm.get('type', 'unknown')
                role = perm.get('role', 'unknown')
                
                if perm_type == 'anyone':
                    output_parts.append(f"    - Anyone with the link ({role})")
                elif perm_type == 'user':
                    email = perm.get('emailAddress', 'unknown')
                    output_parts.append(f"    - User: {email} ({role})")
                elif perm_type == 'domain':
                    domain = perm.get('domain', 'unknown')
                    output_parts.append(f"    - Domain: {domain} ({role})")
                elif perm_type == 'group':
                    email = perm.get('emailAddress', 'unknown')
                    output_parts.append(f"    - Group: {email} ({role})")
                else:
                    output_parts.append(f"    - {perm_type} ({role})")
        else:
            output_parts.append("  No additional permissions (private file)")
        
        # Add URLs
        output_parts.extend([
            "",
            "URLs:",
            f"  View Link: {file_metadata.get('webViewLink', 'N/A')}",
        ])
        
        # webContentLink is only available for files that can be downloaded
        web_content_link = file_metadata.get('webContentLink')
        if web_content_link:
            output_parts.append(f"  Direct Download Link: {web_content_link}")
        
        # Check if file has "anyone with link" permission
        from gdrive.drive_helpers import check_public_link_permission
        has_public_link = check_public_link_permission(permissions)
        
        if has_public_link:
            output_parts.extend([
                "",
                "✅ This file is shared with 'Anyone with the link' - it can be inserted into Google Docs"
            ])
        else:
            output_parts.extend([
                "",
                "❌ This file is NOT shared with 'Anyone with the link' - it cannot be inserted into Google Docs",
                "   To fix: Right-click the file in Google Drive → Share → Anyone with the link → Viewer"
            ])
        
        return "\n".join(output_parts)
        
    except Exception as e:
        logger.error(f"Error getting file permissions: {e}")
        return f"Error getting file permissions: {e}"


@server.tool()
@handle_http_errors("save_drive_file_to_disk", service_type="drive")
@require_google_service("drive", "drive_read")
async def save_drive_file_to_disk(
    service,
    user_google_email: str,
    file_id: str,
    local_file_path: str,
) -> str:
    """
    Downloads a Google Drive file and saves it directly to disk without streaming content to AI.
    Perfect for large files, binary files, or any file you want to save locally.

    Args:
        user_google_email (str): The user's Google email address. Required.
        file_id (str): The ID of the Google Drive file to download.
        local_file_path (str): The local file path where the file should be saved.

    Returns:
        str: Success message with file details and saved location.
    """
    import os
    logger.info(f"[save_drive_file_to_disk] Downloading file {file_id} to {local_file_path} for user {user_google_email}")

    # Convert relative paths to be relative from workspace root instead of server directory
    if not os.path.isabs(local_file_path):
        # Server runs from google-workspace-mcp-dev/google_workspace_mcp
        # This file is in gdrive/drive_tools.py, so workspace root is ../../../ from here
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        local_file_path = os.path.join(workspace_root, local_file_path)
        logger.info(f"[save_drive_file_to_disk] Converted relative path to: {local_file_path}")

    # Get file metadata first
    file_metadata = await asyncio.to_thread(
        service.files().get(
            fileId=file_id, 
            fields="id, name, mimeType, size, webViewLink", 
            supportsAllDrives=True
        ).execute
    )
    
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")
    file_size = file_metadata.get("size", "Unknown")
    
    # Check if it's a Google native file that needs export
    export_mime_type = {
        "application/vnd.google-apps.document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 
        "application/vnd.google-apps.presentation": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(mime_type)

    # Create directory if it doesn't exist
    os.makedirs(os.path.dirname(local_file_path) if os.path.dirname(local_file_path) else ".", exist_ok=True)

    # Determine request method
    if export_mime_type:
        request_obj = service.files().export_media(fileId=file_id, mimeType=export_mime_type)
        download_type = f"exported as {export_mime_type}"
    else:
        request_obj = service.files().get_media(fileId=file_id)
        download_type = "downloaded as original file"

    # Download directly to file
    with open(local_file_path, 'wb') as fh:
        downloader = MediaIoBaseDownload(fh, request_obj)
        loop = asyncio.get_event_loop()
        done = False
        while not done:
            status, done = await loop.run_in_executor(None, downloader.next_chunk)
            if status:
                logger.info(f"Download progress: {int(status.progress() * 100)}%")

    # Get actual file size after download
    actual_size = os.path.getsize(local_file_path)
    
    success_message = (
        f"✅ Successfully saved Google Drive file to disk!\n\n"
        f"File Details:\n"
        f"  Name: '{file_name}'\n"
        f"  ID: {file_id}\n"
        f"  Original Type: {mime_type}\n"
        f"  Google Drive Size: {file_size} bytes\n"
        f"  Downloaded Size: {actual_size:,} bytes\n"
        f"  Method: {download_type}\n\n"
        f"Local File:\n"
        f"  Saved to: {local_file_path}\n"
        f"  Full path: {os.path.abspath(local_file_path)}\n\n"
        f"Link: {file_metadata.get('webViewLink', 'N/A')}"
    )
    
    logger.info(f"Successfully saved file to {local_file_path} ({actual_size:,} bytes)")
    return success_message


@server.tool()
@handle_http_errors("check_drive_file_public_access", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def check_drive_file_public_access(
    service,
    user_google_email: str,
    file_name: str,
) -> str:
    """
    Searches for a file by name and checks if it has public link sharing enabled.
    
    Args:
        user_google_email (str): The user's Google email address. Required.
        file_name (str): The name of the file to check.
    
    Returns:
        str: Information about the file's sharing status and whether it can be used in Google Docs.
    """
    logger.info(f"[check_drive_file_public_access] Searching for {file_name}")
    
    # Search for the file
    escaped_name = file_name.replace("'", "\\'")
    query = f"name = '{escaped_name}'"
    
    list_params = {
        "q": query,
        "pageSize": 10,
        "fields": "files(id, name, mimeType, webViewLink)",
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True,
    }
    
    results = await asyncio.to_thread(
        service.files().list(**list_params).execute
    )
    
    files = results.get('files', [])
    if not files:
        return f"No file found with name '{file_name}'"
    
    if len(files) > 1:
        output_parts = [f"Found {len(files)} files with name '{file_name}':"]
        for f in files:
            output_parts.append(f"  - {f['name']} (ID: {f['id']})")
        output_parts.append("\nChecking the first file...")
        output_parts.append("")
    else:
        output_parts = []
    
    # Check permissions for the first file
    file_id = files[0]['id']
    
    # Get detailed permissions
    file_metadata = await asyncio.to_thread(
        service.files().get(
            fileId=file_id,
            fields="id, name, mimeType, permissions, webViewLink, webContentLink, shared",
            supportsAllDrives=True
        ).execute
    )
    
    permissions = file_metadata.get('permissions', [])
    from gdrive.drive_helpers import check_public_link_permission, get_drive_image_url
    has_public_link = check_public_link_permission(permissions)
    
    output_parts.extend([
        f"File: {file_metadata['name']}",
        f"ID: {file_id}",
        f"Type: {file_metadata['mimeType']}",
        f"Shared: {file_metadata.get('shared', False)}",
        ""
    ])
    
    if has_public_link:
        output_parts.extend([
            "✅ PUBLIC ACCESS ENABLED - This file can be inserted into Google Docs",
            f"Use with insert_doc_image_url: {get_drive_image_url(file_id)}"
        ])
    else:
        output_parts.extend([
            "❌ NO PUBLIC ACCESS - Cannot insert into Google Docs",
            "Fix: Drive → Share → 'Anyone with the link' → 'Viewer'"
        ])
    
    return "\n".join(output_parts)
