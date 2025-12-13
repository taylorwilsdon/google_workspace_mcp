"""
Google Drive MCP Tools

This module provides MCP tools for interacting with Google Drive API.
"""

import logging
import asyncio
from typing import Optional
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
from urllib.request import url2pathname
from pathlib import Path

from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io
import httpx
import base64

from auth.service_decorator import require_google_service
from auth.oauth_config import is_stateless_mode
from core.utils import extract_office_xml_text, handle_http_errors
from core.server import server
from core.config import get_transport_mode
from gdrive.drive_helpers import (
    DRIVE_QUERY_PATTERNS,
    build_drive_list_params,
    resolve_drive_item,
    resolve_folder_id,
)

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
    logger.info(
        f"[search_drive_files] Invoked. Email: '{user_google_email}', Query: '{query}'"
    )

    # Check if the query looks like a structured Drive query or free text
    # Look for Drive API operators and structured query patterns
    is_structured_query = any(pattern.search(query) for pattern in DRIVE_QUERY_PATTERNS)

    if is_structured_query:
        final_query = query
        logger.info(
            f"[search_drive_files] Using structured query as-is: '{final_query}'"
        )
    else:
        # For free text queries, wrap in fullText contains
        escaped_query = query.replace("'", "\\'")
        final_query = f"fullText contains '{escaped_query}'"
        logger.info(
            f"[search_drive_files] Reformatting free text query '{query}' to '{final_query}'"
        )

    list_params = build_drive_list_params(
        query=final_query,
        page_size=page_size,
        drive_id=drive_id,
        include_items_from_all_drives=include_items_from_all_drives,
        corpora=corpora,
    )

    results = await asyncio.to_thread(service.files().list(**list_params).execute)
    files = results.get("files", [])
    if not files:
        return f"No files found for '{query}'."

    formatted_files_text_parts = [
        f"Found {len(files)} files for {user_google_email} matching '{query}':"
    ]
    for item in files:
        size_str = f", Size: {item.get('size', 'N/A')}" if "size" in item else ""
        formatted_files_text_parts.append(
            f'- Name: "{item["name"]}" (ID: {item["id"]}, Type: {item["mimeType"]}{size_str}, Modified: {item.get("modifiedTime", "N/A")}) Link: {item.get("webViewLink", "#")}'
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

    resolved_file_id, file_metadata = await resolve_drive_item(
        service,
        file_id,
        extra_fields="name, webViewLink",
    )
    file_id = resolved_file_id
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")
    export_mime_type = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }.get(mime_type)

    request_obj = (
        service.files().export_media(fileId=file_id, mimeType=export_mime_type)
        if export_mime_type
        else service.files().get_media(fileId=file_id)
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
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
        f"Link: {file_metadata.get('webViewLink', '#')}\n\n--- CONTENT ---\n"
    )
    return header + body_text


@server.tool()
@handle_http_errors(
    "get_drive_file_download_url", is_read_only=True, service_type="drive"
)
@require_google_service("drive", "drive_read")
async def get_drive_file_download_url(
    service,
    user_google_email: str,
    file_id: str,
    export_format: Optional[str] = None,
) -> str:
    """
    Gets a download URL for a Google Drive file. The file is prepared and made available via HTTP URL.

    For Google native files (Docs, Sheets, Slides), exports to a useful format:
    • Google Docs → PDF (default) or DOCX if export_format='docx'
    • Google Sheets → XLSX (default) or CSV if export_format='csv'
    • Google Slides → PDF (default) or PPTX if export_format='pptx'

    For other files, downloads the original file format.

    Args:
        user_google_email: The user's Google email address. Required.
        file_id: The Google Drive file ID to get a download URL for.
        export_format: Optional export format for Google native files.
                      Options: 'pdf', 'docx', 'xlsx', 'csv', 'pptx'.
                      If not specified, uses sensible defaults (PDF for Docs/Slides, XLSX for Sheets).

    Returns:
        str: Download URL and file metadata. The file is available at the URL for 1 hour.
    """
    logger.info(
        f"[get_drive_file_download_url] Invoked. File ID: '{file_id}', Export format: {export_format}"
    )

    # Resolve shortcuts and get file metadata
    resolved_file_id, file_metadata = await resolve_drive_item(
        service,
        file_id,
        extra_fields="name, webViewLink, mimeType",
    )
    file_id = resolved_file_id
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")

    # Determine export format for Google native files
    export_mime_type = None
    output_filename = file_name
    output_mime_type = mime_type

    if mime_type == "application/vnd.google-apps.document":
        # Google Docs
        if export_format == "docx":
            export_mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            output_mime_type = export_mime_type
            if not output_filename.endswith(".docx"):
                output_filename = f"{Path(output_filename).stem}.docx"
        else:
            # Default to PDF
            export_mime_type = "application/pdf"
            output_mime_type = export_mime_type
            if not output_filename.endswith(".pdf"):
                output_filename = f"{Path(output_filename).stem}.pdf"

    elif mime_type == "application/vnd.google-apps.spreadsheet":
        # Google Sheets
        if export_format == "csv":
            export_mime_type = "text/csv"
            output_mime_type = export_mime_type
            if not output_filename.endswith(".csv"):
                output_filename = f"{Path(output_filename).stem}.csv"
        else:
            # Default to XLSX
            export_mime_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            output_mime_type = export_mime_type
            if not output_filename.endswith(".xlsx"):
                output_filename = f"{Path(output_filename).stem}.xlsx"

    elif mime_type == "application/vnd.google-apps.presentation":
        # Google Slides
        if export_format == "pptx":
            export_mime_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            output_mime_type = export_mime_type
            if not output_filename.endswith(".pptx"):
                output_filename = f"{Path(output_filename).stem}.pptx"
        else:
            # Default to PDF
            export_mime_type = "application/pdf"
            output_mime_type = export_mime_type
            if not output_filename.endswith(".pdf"):
                output_filename = f"{Path(output_filename).stem}.pdf"

    # Download the file
    request_obj = (
        service.files().export_media(fileId=file_id, mimeType=export_mime_type)
        if export_mime_type
        else service.files().get_media(fileId=file_id)
    )

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request_obj)
    loop = asyncio.get_event_loop()
    done = False
    while not done:
        status, done = await loop.run_in_executor(None, downloader.next_chunk)

    file_content_bytes = fh.getvalue()
    size_bytes = len(file_content_bytes)
    size_kb = size_bytes / 1024 if size_bytes else 0

    # Check if we're in stateless mode (can't save files)
    if is_stateless_mode():
        result_lines = [
            "File downloaded successfully!",
            f"File: {file_name}",
            f"File ID: {file_id}",
            f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
            f"MIME Type: {output_mime_type}",
            "\n⚠️ Stateless mode: File storage disabled.",
            "\nBase64-encoded content (first 100 characters shown):",
            f"{base64.b64encode(file_content_bytes[:100]).decode('utf-8')}...",
        ]
        logger.info(
            f"[get_drive_file_download_url] Successfully downloaded {size_kb:.1f} KB file (stateless mode)"
        )
        return "\n".join(result_lines)

    # Save file and generate URL
    try:
        from core.attachment_storage import get_attachment_storage, get_attachment_url

        storage = get_attachment_storage()

        # Encode bytes to base64 (as expected by AttachmentStorage)
        base64_data = base64.urlsafe_b64encode(file_content_bytes).decode("utf-8")

        # Save attachment
        saved_file_id = storage.save_attachment(
            base64_data=base64_data,
            filename=output_filename,
            mime_type=output_mime_type,
        )

        # Generate URL
        download_url = get_attachment_url(saved_file_id)

        result_lines = [
            "File downloaded successfully!",
            f"File: {file_name}",
            f"File ID: {file_id}",
            f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
            f"MIME Type: {output_mime_type}",
            f"\n📎 Download URL: {download_url}",
            "\nThe file has been saved and is available at the URL above.",
            "The file will expire after 1 hour.",
        ]

        if export_mime_type:
            result_lines.append(
                f"\nNote: Google native file exported to {output_mime_type} format."
            )

        logger.info(
            f"[get_drive_file_download_url] Successfully saved {size_kb:.1f} KB file as {saved_file_id}"
        )
        return "\n".join(result_lines)

    except Exception as e:
        logger.error(f"[get_drive_file_download_url] Failed to save file: {e}")
        return (
            f"Error: Failed to save file for download.\n"
            f"File was downloaded successfully ({size_kb:.1f} KB) but could not be saved.\n\n"
            f"Error details: {str(e)}"
        )


@server.tool()
@handle_http_errors("list_drive_items", is_read_only=True, service_type="drive")
@require_google_service("drive", "drive_read")
async def list_drive_items(
    service,
    user_google_email: str,
    folder_id: str = "root",
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
    logger.info(
        f"[list_drive_items] Invoked. Email: '{user_google_email}', Folder ID: '{folder_id}'"
    )

    resolved_folder_id = await resolve_folder_id(service, folder_id)
    final_query = f"'{resolved_folder_id}' in parents and trashed=false"

    list_params = build_drive_list_params(
        query=final_query,
        page_size=page_size,
        drive_id=drive_id,
        include_items_from_all_drives=include_items_from_all_drives,
        corpora=corpora,
    )

    results = await asyncio.to_thread(service.files().list(**list_params).execute)
    files = results.get("files", [])
    if not files:
        return f"No items found in folder '{folder_id}'."

    formatted_items_text_parts = [
        f"Found {len(files)} items in folder '{folder_id}' for {user_google_email}:"
    ]
    for item in files:
        size_str = f", Size: {item.get('size', 'N/A')}" if "size" in item else ""
        formatted_items_text_parts.append(
            f'- Name: "{item["name"]}" (ID: {item["id"]}, Type: {item["mimeType"]}{size_str}, Modified: {item.get("modifiedTime", "N/A")}) Link: {item.get("webViewLink", "#")}'
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
    folder_id: str = "root",
    mime_type: str = "text/plain",
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
        fileUrl (Optional[str]): If provided, fetches the file content from this URL. Supports file://, http://, and https:// protocols.

    Returns:
        str: Confirmation message of the successful file creation with file link.
    """
    logger.info(
        f"[create_drive_file] Invoked. Email: '{user_google_email}', File Name: {file_name}, Folder ID: {folder_id}, fileUrl: {fileUrl}"
    )

    if not content and not fileUrl:
        raise Exception("You must provide either 'content' or 'fileUrl'.")

    file_data = None
    resolved_folder_id = await resolve_folder_id(service, folder_id)

    file_metadata = {
        "name": file_name,
        "parents": [resolved_folder_id],
        "mimeType": mime_type,
    }

    # Prefer fileUrl if both are provided
    if fileUrl:
        logger.info(f"[create_drive_file] Fetching file from URL: {fileUrl}")

        # Check if this is a file:// URL
        parsed_url = urlparse(fileUrl)
        if parsed_url.scheme == "file":
            # Handle file:// URL - read from local filesystem
            logger.info(
                "[create_drive_file] Detected file:// URL, reading from local filesystem"
            )
            transport_mode = get_transport_mode()
            running_streamable = transport_mode == "streamable-http"
            if running_streamable:
                logger.warning(
                    "[create_drive_file] file:// URL requested while server runs in streamable-http mode. Ensure the file path is accessible to the server (e.g., Docker volume) or use an HTTP(S) URL."
                )

            # Convert file:// URL to a cross-platform local path
            raw_path = parsed_url.path or ""
            netloc = parsed_url.netloc
            if netloc and netloc.lower() != "localhost":
                raw_path = f"//{netloc}{raw_path}"
            file_path = url2pathname(raw_path)

            # Verify file exists
            path_obj = Path(file_path)
            if not path_obj.exists():
                extra = (
                    " The server is running via streamable-http, so file:// URLs must point to files inside the container or remote host."
                    if running_streamable
                    else ""
                )
                raise Exception(f"Local file does not exist: {file_path}.{extra}")
            if not path_obj.is_file():
                extra = (
                    " In streamable-http/Docker deployments, mount the file into the container or provide an HTTP(S) URL."
                    if running_streamable
                    else ""
                )
                raise Exception(f"Path is not a file: {file_path}.{extra}")

            logger.info(f"[create_drive_file] Reading local file: {file_path}")

            # Read file and upload
            file_data = await asyncio.to_thread(path_obj.read_bytes)
            total_bytes = len(file_data)
            logger.info(f"[create_drive_file] Read {total_bytes} bytes from local file")

            media = MediaIoBaseUpload(
                io.BytesIO(file_data),
                mimetype=mime_type,
                resumable=True,
                chunksize=UPLOAD_CHUNK_SIZE_BYTES,
            )

            logger.info("[create_drive_file] Starting upload to Google Drive...")
            created_file = await asyncio.to_thread(
                service.files()
                .create(
                    body=file_metadata,
                    media_body=media,
                    fields="id, name, webViewLink",
                    supportsAllDrives=True,
                )
                .execute
            )
        # Handle HTTP/HTTPS URLs
        elif parsed_url.scheme in ("http", "https"):
            # when running in stateless mode, deployment may not have access to local file system
            if is_stateless_mode():
                async with httpx.AsyncClient(follow_redirects=True) as client:
                    resp = await client.get(fileUrl)
                    if resp.status_code != 200:
                        raise Exception(
                            f"Failed to fetch file from URL: {fileUrl} (status {resp.status_code})"
                        )
                    file_data = await resp.aread()
                    # Try to get MIME type from Content-Type header
                    content_type = resp.headers.get("Content-Type")
                    if content_type and content_type != "application/octet-stream":
                        mime_type = content_type
                        file_metadata["mimeType"] = content_type
                        logger.info(
                            f"[create_drive_file] Using MIME type from Content-Type header: {content_type}"
                        )

                media = MediaIoBaseUpload(
                    io.BytesIO(file_data),
                    mimetype=mime_type,
                    resumable=True,
                    chunksize=UPLOAD_CHUNK_SIZE_BYTES,
                )

                created_file = await asyncio.to_thread(
                    service.files()
                    .create(
                        body=file_metadata,
                        media_body=media,
                        fields="id, name, webViewLink",
                        supportsAllDrives=True,
                    )
                    .execute
                )
            else:
                # Use NamedTemporaryFile to stream download and upload
                with NamedTemporaryFile() as temp_file:
                    total_bytes = 0
                    # follow redirects
                    async with httpx.AsyncClient(follow_redirects=True) as client:
                        async with client.stream("GET", fileUrl) as resp:
                            if resp.status_code != 200:
                                raise Exception(
                                    f"Failed to fetch file from URL: {fileUrl} (status {resp.status_code})"
                                )

                            # Stream download in chunks
                            async for chunk in resp.aiter_bytes(
                                chunk_size=DOWNLOAD_CHUNK_SIZE_BYTES
                            ):
                                await asyncio.to_thread(temp_file.write, chunk)
                                total_bytes += len(chunk)

                            logger.info(
                                f"[create_drive_file] Downloaded {total_bytes} bytes from URL before upload."
                            )

                            # Try to get MIME type from Content-Type header
                            content_type = resp.headers.get("Content-Type")
                            if (
                                content_type
                                and content_type != "application/octet-stream"
                            ):
                                mime_type = content_type
                                file_metadata["mimeType"] = mime_type
                                logger.info(
                                    f"[create_drive_file] Using MIME type from Content-Type header: {mime_type}"
                                )

                    # Reset file pointer to beginning for upload
                    temp_file.seek(0)

                    # Upload with chunking
                    media = MediaIoBaseUpload(
                        temp_file,
                        mimetype=mime_type,
                        resumable=True,
                        chunksize=UPLOAD_CHUNK_SIZE_BYTES,
                    )

                    logger.info(
                        "[create_drive_file] Starting upload to Google Drive..."
                    )
                    created_file = await asyncio.to_thread(
                        service.files()
                        .create(
                            body=file_metadata,
                            media_body=media,
                            fields="id, name, webViewLink",
                            supportsAllDrives=True,
                        )
                        .execute
                    )
        else:
            if not parsed_url.scheme:
                raise Exception(
                    "fileUrl is missing a URL scheme. Use file://, http://, or https://."
                )
            raise Exception(
                f"Unsupported URL scheme '{parsed_url.scheme}'. Only file://, http://, and https:// are supported."
            )
    elif content:
        file_data = content.encode("utf-8")
        media = io.BytesIO(file_data)

        created_file = await asyncio.to_thread(
            service.files()
            .create(
                body=file_metadata,
                media_body=MediaIoBaseUpload(media, mimetype=mime_type, resumable=True),
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute
        )

    link = created_file.get("webViewLink", "No link available")
    confirmation_message = f"Successfully created file '{created_file.get('name', file_name)}' (ID: {created_file.get('id', 'N/A')}) in folder '{folder_id}' for {user_google_email}. Link: {link}"
    logger.info(f"Successfully created file. Link: {link}")
    return confirmation_message


@server.tool()
@handle_http_errors(
    "get_drive_file_permissions", is_read_only=True, service_type="drive"
)
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
    logger.info(
        f"[get_drive_file_permissions] Checking file {file_id} for {user_google_email}"
    )

    resolved_file_id, _ = await resolve_drive_item(service, file_id)
    file_id = resolved_file_id

    try:
        # Get comprehensive file metadata including permissions
        file_metadata = await asyncio.to_thread(
            service.files()
            .get(
                fileId=file_id,
                fields="id, name, mimeType, size, modifiedTime, owners, permissions, "
                "webViewLink, webContentLink, shared, sharingUser, viewersCanCopyContent",
                supportsAllDrives=True,
            )
            .execute
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
        sharing_user = file_metadata.get("sharingUser")
        if sharing_user:
            output_parts.append(
                f"  Shared by: {sharing_user.get('displayName', 'Unknown')} ({sharing_user.get('emailAddress', 'Unknown')})"
            )

        # Process permissions
        permissions = file_metadata.get("permissions", [])
        if permissions:
            output_parts.append(f"  Number of permissions: {len(permissions)}")
            output_parts.append("  Permissions:")
            for perm in permissions:
                perm_type = perm.get("type", "unknown")
                role = perm.get("role", "unknown")

                if perm_type == "anyone":
                    output_parts.append(f"    - Anyone with the link ({role})")
                elif perm_type == "user":
                    email = perm.get("emailAddress", "unknown")
                    output_parts.append(f"    - User: {email} ({role})")
                elif perm_type == "domain":
                    domain = perm.get("domain", "unknown")
                    output_parts.append(f"    - Domain: {domain} ({role})")
                elif perm_type == "group":
                    email = perm.get("emailAddress", "unknown")
                    output_parts.append(f"    - Group: {email} ({role})")
                else:
                    output_parts.append(f"    - {perm_type} ({role})")
        else:
            output_parts.append("  No additional permissions (private file)")

        # Add URLs
        output_parts.extend(
            [
                "",
                "URLs:",
                f"  View Link: {file_metadata.get('webViewLink', 'N/A')}",
            ]
        )

        # webContentLink is only available for files that can be downloaded
        web_content_link = file_metadata.get("webContentLink")
        if web_content_link:
            output_parts.append(f"  Direct Download Link: {web_content_link}")

        # Check if file has "anyone with link" permission
        from gdrive.drive_helpers import check_public_link_permission

        has_public_link = check_public_link_permission(permissions)

        if has_public_link:
            output_parts.extend(
                [
                    "",
                    "✅ This file is shared with 'Anyone with the link' - it can be inserted into Google Docs",
                ]
            )
        else:
            output_parts.extend(
                [
                    "",
                    "❌ This file is NOT shared with 'Anyone with the link' - it cannot be inserted into Google Docs",
                    "   To fix: Right-click the file in Google Drive → Share → Anyone with the link → Viewer",
                ]
            )

        return "\n".join(output_parts)

    except Exception as e:
        logger.error(f"Error getting file permissions: {e}")
        return f"Error getting file permissions: {e}"


@server.tool()
@handle_http_errors(
    "check_drive_file_public_access", is_read_only=True, service_type="drive"
)
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

    results = await asyncio.to_thread(service.files().list(**list_params).execute)

    files = results.get("files", [])
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
    file_id = files[0]["id"]
    resolved_file_id, _ = await resolve_drive_item(service, file_id)
    file_id = resolved_file_id

    # Get detailed permissions
    file_metadata = await asyncio.to_thread(
        service.files()
        .get(
            fileId=file_id,
            fields="id, name, mimeType, permissions, webViewLink, webContentLink, shared",
            supportsAllDrives=True,
        )
        .execute
    )

    permissions = file_metadata.get("permissions", [])
    from gdrive.drive_helpers import check_public_link_permission, get_drive_image_url

    has_public_link = check_public_link_permission(permissions)

    output_parts.extend(
        [
            f"File: {file_metadata['name']}",
            f"ID: {file_id}",
            f"Type: {file_metadata['mimeType']}",
            f"Shared: {file_metadata.get('shared', False)}",
            "",
        ]
    )

    if has_public_link:
        output_parts.extend(
            [
                "✅ PUBLIC ACCESS ENABLED - This file can be inserted into Google Docs",
                f"Use with insert_doc_image_url: {get_drive_image_url(file_id)}",
            ]
        )
    else:
        output_parts.extend(
            [
                "❌ NO PUBLIC ACCESS - Cannot insert into Google Docs",
                "Fix: Drive → Share → 'Anyone with the link' → 'Viewer'",
            ]
        )

    return "\n".join(output_parts)


@server.tool()
@handle_http_errors("update_drive_file", is_read_only=False, service_type="drive")
@require_google_service("drive", "drive_file")
async def update_drive_file(
    service,
    user_google_email: str,
    file_id: str,
    # File metadata updates
    name: Optional[str] = None,
    description: Optional[str] = None,
    mime_type: Optional[str] = None,
    # Folder organization
    add_parents: Optional[str] = None,  # Comma-separated folder IDs to add
    remove_parents: Optional[str] = None,  # Comma-separated folder IDs to remove
    # File status
    starred: Optional[bool] = None,
    trashed: Optional[bool] = None,
    # Sharing and permissions
    writers_can_share: Optional[bool] = None,
    copy_requires_writer_permission: Optional[bool] = None,
    # Custom properties
    properties: Optional[dict] = None,  # User-visible custom properties
) -> str:
    """
    Updates metadata and properties of a Google Drive file.

    Args:
        user_google_email (str): The user's Google email address. Required.
        file_id (str): The ID of the file to update. Required.
        name (Optional[str]): New name for the file.
        description (Optional[str]): New description for the file.
        mime_type (Optional[str]): New MIME type (note: changing type may require content upload).
        add_parents (Optional[str]): Comma-separated folder IDs to add as parents.
        remove_parents (Optional[str]): Comma-separated folder IDs to remove from parents.
        starred (Optional[bool]): Whether to star/unstar the file.
        trashed (Optional[bool]): Whether to move file to/from trash.
        writers_can_share (Optional[bool]): Whether editors can share the file.
        copy_requires_writer_permission (Optional[bool]): Whether copying requires writer permission.
        properties (Optional[dict]): Custom key-value properties for the file.

    Returns:
        str: Confirmation message with details of the updates applied.
    """
    logger.info(f"[update_drive_file] Updating file {file_id} for {user_google_email}")

    current_file_fields = (
        "name, description, mimeType, parents, starred, trashed, webViewLink, "
        "writersCanShare, copyRequiresWriterPermission, properties"
    )
    resolved_file_id, current_file = await resolve_drive_item(
        service,
        file_id,
        extra_fields=current_file_fields,
    )
    file_id = resolved_file_id

    # Build the update body with only specified fields
    update_body = {}
    if name is not None:
        update_body["name"] = name
    if description is not None:
        update_body["description"] = description
    if mime_type is not None:
        update_body["mimeType"] = mime_type
    if starred is not None:
        update_body["starred"] = starred
    if trashed is not None:
        update_body["trashed"] = trashed
    if writers_can_share is not None:
        update_body["writersCanShare"] = writers_can_share
    if copy_requires_writer_permission is not None:
        update_body["copyRequiresWriterPermission"] = copy_requires_writer_permission
    if properties is not None:
        update_body["properties"] = properties

    async def _resolve_parent_arguments(parent_arg: Optional[str]) -> Optional[str]:
        if not parent_arg:
            return None
        parent_ids = [part.strip() for part in parent_arg.split(",") if part.strip()]
        if not parent_ids:
            return None

        resolved_ids = []
        for parent in parent_ids:
            resolved_parent = await resolve_folder_id(service, parent)
            resolved_ids.append(resolved_parent)
        return ",".join(resolved_ids)

    resolved_add_parents = await _resolve_parent_arguments(add_parents)
    resolved_remove_parents = await _resolve_parent_arguments(remove_parents)

    # Build query parameters for parent changes
    query_params = {
        "fileId": file_id,
        "supportsAllDrives": True,
        "fields": "id, name, description, mimeType, parents, starred, trashed, webViewLink, writersCanShare, copyRequiresWriterPermission, properties",
    }

    if resolved_add_parents:
        query_params["addParents"] = resolved_add_parents
    if resolved_remove_parents:
        query_params["removeParents"] = resolved_remove_parents

    # Only include body if there are updates
    if update_body:
        query_params["body"] = update_body

    # Perform the update
    updated_file = await asyncio.to_thread(
        service.files().update(**query_params).execute
    )

    # Build response message
    output_parts = [
        f"✅ Successfully updated file: {updated_file.get('name', current_file['name'])}"
    ]
    output_parts.append(f"   File ID: {file_id}")

    # Report what changed
    changes = []
    if name is not None and name != current_file.get("name"):
        changes.append(f"   • Name: '{current_file.get('name')}' → '{name}'")
    if description is not None:
        old_desc_value = current_file.get("description")
        new_desc_value = description
        should_report_change = (old_desc_value or "") != (new_desc_value or "")
        if should_report_change:
            old_desc_display = (
                old_desc_value if old_desc_value not in (None, "") else "(empty)"
            )
            new_desc_display = (
                new_desc_value if new_desc_value not in (None, "") else "(empty)"
            )
            changes.append(f"   • Description: {old_desc_display} → {new_desc_display}")
    if add_parents:
        changes.append(f"   • Added to folder(s): {add_parents}")
    if remove_parents:
        changes.append(f"   • Removed from folder(s): {remove_parents}")
    current_starred = current_file.get("starred")
    if starred is not None and starred != current_starred:
        star_status = "starred" if starred else "unstarred"
        changes.append(f"   • File {star_status}")
    current_trashed = current_file.get("trashed")
    if trashed is not None and trashed != current_trashed:
        trash_status = "moved to trash" if trashed else "restored from trash"
        changes.append(f"   • File {trash_status}")
    current_writers_can_share = current_file.get("writersCanShare")
    if writers_can_share is not None and writers_can_share != current_writers_can_share:
        share_status = "can" if writers_can_share else "cannot"
        changes.append(f"   • Writers {share_status} share the file")
    current_copy_requires_writer_permission = current_file.get(
        "copyRequiresWriterPermission"
    )
    if (
        copy_requires_writer_permission is not None
        and copy_requires_writer_permission != current_copy_requires_writer_permission
    ):
        copy_status = (
            "requires" if copy_requires_writer_permission else "doesn't require"
        )
        changes.append(f"   • Copying {copy_status} writer permission")
    if properties:
        changes.append(f"   • Updated custom properties: {properties}")

    if changes:
        output_parts.append("")
        output_parts.append("Changes applied:")
        output_parts.extend(changes)
    else:
        output_parts.append("   (No changes were made)")

    output_parts.append("")
    output_parts.append(f"View file: {updated_file.get('webViewLink', '#')}")

    return "\n".join(output_parts)
