"""
Google Drive MCP Tools

This module provides MCP tools for interacting with Google Drive API.
"""

import logging
import asyncio
import re
import os
from typing import List, Optional, Dict, Any

from mcp import types
from fastapi import Header
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload  # For file content
import io  # For file content

# Use functions directly from google_auth
from auth.google_auth import (
    get_credentials,
    start_auth_flow,
    CONFIG_CLIENT_SECRETS_PATH,
)
from core.server import server, OAUTH_REDIRECT_URI, OAUTH_STATE_TO_SESSION_ID_MAP
from core.server import DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE, SCOPES

logger = logging.getLogger(__name__)


@server.tool()
async def search_drive_files(
    query: str,
    user_google_email: Optional[str] = None,
    page_size: int = 10,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Searches for files and folders within a user's Google Drive based on a query string.
    Prioritizes authentication via the active MCP session (`mcp_session_id`).
    If the session isn't authenticated for Drive, it falls back to using `user_google_email`.
    If neither provides valid credentials, it returns a message guiding the LLM to request the user's email
    or initiate the authentication flow via the centralized start_auth_flow.

    Args:
        query (str): The search query string. Supports Google Drive search operators (e.g., 'name contains "report"', 'mimeType="application/vnd.google-apps.document"', 'parents in "folderId"').
        user_google_email (Optional[str]): The user's Google email address. Required if the MCP session is not already authenticated for Drive access.
        page_size (int): The maximum number of files to return. Defaults to 10.
        mcp_session_id (Optional[str]): The active MCP session ID (automatically injected by FastMCP from the Mcp-Session-Id header). Used for session-based authentication.

    Returns:
        types.CallToolResult: Contains a list of found files/folders with their details (ID, name, type, size, modified time, link),
                               an error message if the API call fails,
                               or an authentication guidance message if credentials are required.
    """
    logger.info(
        f"[search_drive_files] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}', Query: '{query}'"
    )
    tool_specific_scopes = [DRIVE_READONLY_SCOPE]
    credentials = await asyncio.to_thread(
        get_credentials,
        user_google_email=user_google_email,
        required_scopes=tool_specific_scopes,
        client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,  # Use imported constant
        session_id=mcp_session_id,
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[search_drive_files] No valid credentials for Drive. Session: '{mcp_session_id}', Email: '{user_google_email}'."
        )
        if user_google_email and "@" in user_google_email:
            # Use the centralized start_auth_flow
            return await start_auth_flow(
                mcp_session_id=mcp_session_id,
                user_google_email=user_google_email,
                service_name="Google Drive",
                redirect_uri=OAUTH_REDIRECT_URI,
            )
        else:
            error_msg = (
                "Drive Authentication required. LLM: Please ask for Google email."
            )
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=error_msg)]
            )

    try:
        service = build("drive", "v3", credentials=credentials)
        user_email_from_creds = (
            credentials.id_token.get("email")
            if credentials.id_token
            else "Unknown (Drive)"
        )

        # Check if the query looks like a structured Drive query or free text
        # Basic check for operators or common keywords used in structured queries
        drive_query_pattern = r"(\w+\s*(=|!=|>|<|contains|in|has)\s*['\"]?.+?['\"]?|\w+\s*(=|!=|>|<)\s*\d+|trashed\s*=\s*(true|false)|starred\s*=\s*(true|false)|properties\s+has\s*\{.*?\}|appProperties\s+has\s*\{.*?\}|'[^']+'\s+in\s+parents)"
        is_structured_query = re.search(drive_query_pattern, query, re.IGNORECASE)

        if is_structured_query:
            final_query = query  # Use as is
        else:
            # Assume free text search, escape single quotes and wrap
            escaped_query = query.replace("'", "\\'")
            final_query = f"fullText contains '{escaped_query}'"
            logger.info(
                f"[search_drive_files] Reformatting free text query '{query}' to '{final_query}'"
            )

        results = await asyncio.to_thread(
            service.files()
            .list(
                q=final_query,  # Use the potentially modified query
                pageSize=page_size,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, iconLink, modifiedTime, size)",
            )
            .execute
        )
        files = results.get("files", [])
        if not files:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=f"No files found for '{query}'."
                    )
                ]
            )

        formatted_files_text_parts = [
            f"Found {len(files)} files for {user_email_from_creds} matching '{query}':"
        ]
        for item in files:
            size_str = f", Size: {item.get('size', 'N/A')}" if "size" in item else ""
            formatted_files_text_parts.append(
                f"- Name: \"{item['name']}\" (ID: {item['id']}, Type: {item['mimeType']}{size_str}, Modified: {item.get('modifiedTime', 'N/A')}) Link: {item.get('webViewLink', '#')}"
            )
        text_output = "\n".join(formatted_files_text_parts)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text_output)]
        )
    except HttpError as error:
        logger.error(f"API error searching Drive files: {error}", exc_info=True)
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"API error: {error}")],
        )
    except Exception as e:
        logger.exception(f"Unexpected error searching Drive files: {e}")
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"Unexpected error: {e}")],
        )


import pandas as pd  # For Excel handling
from docx import Document  # For Word document handling


@server.tool()
async def get_drive_file_content(
    file_id: str,
    user_google_email: Optional[str] = None,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Retrieves the content of a specific file from Google Drive by its ID.
    Handles both native Google Docs/Sheets/Slides (exporting them to plain text or CSV) and other file types (downloading directly).
    Now supports extracting text from Office files (.xlsx, .docx, .doc) directly.
    Prioritizes authentication via the active MCP session (`mcp_session_id`).
    If the session isn't authenticated for Drive, it falls back to using `user_google_email`.
    If neither provides valid credentials, it returns a message guiding the LLM to request the user's email
    or initiate the authentication flow via the centralized start_auth_flow.

    Args:
        file_id (str): The unique ID of the Google Drive file to retrieve content from. This ID is typically obtained from `search_drive_files` or `list_drive_items`.
        user_google_email (Optional[str]): The user's Google email address. Required if the MCP session is not already authenticated for Drive access.
        mcp_session_id (Optional[str]): The active MCP session ID (automatically injected by FastMCP from the Mcp-Session-Id header). Used for session-based authentication.

    Returns:
        types.CallToolResult: Contains the file metadata (name, ID, type, link) and its content (decoded as UTF-8 if possible, otherwise indicates binary content),
                               an error message if the API call fails or the file is not accessible/found,
                               or an authentication guidance message if credentials are required.
    """
    logger.info(f"[get_drive_file_content] Invoked. File ID: '{file_id}'")
    tool_specific_scopes = [DRIVE_READONLY_SCOPE]
    credentials = await asyncio.to_thread(
        get_credentials,
        user_google_email=user_google_email,
        required_scopes=tool_specific_scopes,
        client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,  # Use imported constant
        session_id=mcp_session_id,
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[get_drive_file_content] No valid credentials for Drive. Session: '{mcp_session_id}', Email: '{user_google_email}'."
        )
        if user_google_email and "@" in user_google_email:
            # Use the centralized start_auth_flow
            return await start_auth_flow(
                mcp_session_id=mcp_session_id,
                user_google_email=user_google_email,
                service_name="Google Drive",
                redirect_uri=OAUTH_REDIRECT_URI,
            )
        else:
            error_msg = (
                "Drive Authentication required. LLM: Please ask for Google email."
            )
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=error_msg)]
            )

    try:
        service = build("drive", "v3", credentials=credentials)
        file_metadata = await asyncio.to_thread(
            service.files()
            .get(fileId=file_id, fields="id, name, mimeType, webViewLink")
            .execute
        )
        mime_type = file_metadata.get("mimeType", "")
        file_name = file_metadata.get("name", "Unknown File")
        content_text = f"File: \"{file_name}\" (ID: {file_id}, Type: {mime_type})\nLink: {file_metadata.get('webViewLink', '#')}\n\n--- CONTENT ---\n"

        export_mime_type = None
        logger.info(f"Detected mime type: {mime_type}")
        if mime_type == "application/vnd.google-apps.document":
            export_mime_type = "text/plain"
        elif mime_type == "application/vnd.google-apps.spreadsheet":
            export_mime_type = "text/csv"
        elif mime_type == "application/vnd.google-apps.presentation":
            export_mime_type = "text/plain"

        request_obj = (
            service.files().export_media(fileId=file_id, mimeType=export_mime_type)
            if export_mime_type
            else service.files().get_media(fileId=file_id)
        )

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_obj)
        done = False
        loop = asyncio.get_event_loop()
        while not done:
            status, done = await loop.run_in_executor(None, downloader.next_chunk)

        # Handle Office formats directly
        if (
            mime_type
            == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ):  # .xlsx
            try:
                # Reset file pointer
                fh.seek(0)

                # Read all sheets
                xlsx_content = ""
                excel_file = pd.ExcelFile(fh)
                for sheet_name in excel_file.sheet_names:
                    df = pd.read_excel(excel_file, sheet_name=sheet_name)
                    xlsx_content += f"\n--- SHEET: {sheet_name} ---\n"
                    xlsx_content += df.to_csv(index=False)
                    xlsx_content += "\n"

                file_content_str = xlsx_content
            except Exception as e:
                logger.error(f"Error extracting Excel content: {e}", exc_info=True)
                file_content_str = f"[Error extracting Excel content: {str(e)}]"

        elif (
            mime_type
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):  # .docx
            try:
                # Reset file pointer
                fh.seek(0)

                # Extract text from docx
                doc = Document(fh)
                docx_content = "\n".join(
                    [paragraph.text for paragraph in doc.paragraphs]
                )
                file_content_str = docx_content
            except Exception as e:
                logger.error(f"Error extracting Word content: {e}", exc_info=True)
                file_content_str = f"[Error extracting Word content: {str(e)}]"
        else:
            file_content_bytes = fh.getvalue()
            try:
                file_content_str = file_content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                file_content_str = f"[Content is binary or uses an unsupported text encoding. Length: {len(file_content_bytes)} bytes]"

        content_text += file_content_str
        logger.info(f"Successfully retrieved content for Drive file ID: {file_id}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=content_text)]
        )
    except HttpError as error:
        logger.error(
            f"API error getting Drive file content for {file_id}: {error}",
            exc_info=True,
        )
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"API error: {error}")],
        )
    except Exception as e:
        logger.exception(
            f"Unexpected error getting Drive file content for {file_id}: {e}"
        )
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"Unexpected error: {e}")],
        )


@server.tool()
async def list_drive_items(
    folder_id: str = "root",  # Default to root folder
    user_google_email: Optional[str] = None,
    page_size: int = 100,  # Default page size for listing
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Lists files and folders directly within a specified Google Drive folder.
    Defaults to the root folder if `folder_id` is not provided. Does not recurse into subfolders.
    Prioritizes authentication via the active MCP session (`mcp_session_id`).
    If the session isn't authenticated for Drive, it falls back to using `user_google_email`.
    If neither provides valid credentials, it returns a message guiding the LLM to request the user's email
    or initiate the authentication flow via the centralized start_auth_flow.

    Args:
        folder_id (str): The ID of the Google Drive folder to list items from. Defaults to 'root'.
        user_google_email (Optional[str]): The user's Google email address. Required if the MCP session is not already authenticated for Drive access.
        page_size (int): The maximum number of items to return per page. Defaults to 100.
        mcp_session_id (Optional[str]): The active MCP session ID (automatically injected by FastMCP from the Mcp-Session-Id header). Used for session-based authentication.

    Returns:
        types.CallToolResult: Contains a list of files/folders within the specified folder, including their details (ID, name, type, size, modified time, link),
                               an error message if the API call fails or the folder is not accessible/found,
                               or an authentication guidance message if credentials are required.
    """
    logger.info(
        f"[list_drive_items] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}', Folder ID: '{folder_id}'"
    )
    tool_specific_scopes = [DRIVE_READONLY_SCOPE]
    credentials = await asyncio.to_thread(
        get_credentials,
        user_google_email=user_google_email,
        required_scopes=tool_specific_scopes,
        client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,  # Use imported constant
        session_id=mcp_session_id,
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[list_drive_items] No valid credentials for Drive. Session: '{mcp_session_id}', Email: '{user_google_email}'."
        )
        if user_google_email and "@" in user_google_email:
            # Use the centralized start_auth_flow
            return await start_auth_flow(
                mcp_session_id=mcp_session_id,
                user_google_email=user_google_email,
                service_name="Google Drive",
                redirect_uri=OAUTH_REDIRECT_URI,
            )
        else:
            error_msg = (
                "Drive Authentication required. LLM: Please ask for Google email."
            )
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=error_msg)]
            )

    try:
        service = build("drive", "v3", credentials=credentials)
        user_email_from_creds = (
            credentials.id_token.get("email")
            if credentials.id_token
            else "Unknown (Drive)"
        )

        results = await asyncio.to_thread(
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false",  # List items directly in the folder
                pageSize=page_size,
                fields="nextPageToken, files(id, name, mimeType, webViewLink, iconLink, modifiedTime, size)",
            )
            .execute
        )
        files = results.get("files", [])
        if not files:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=f"No items found in folder '{folder_id}'."
                    )
                ]
            )

        formatted_items_text_parts = [
            f"Found {len(files)} items in folder '{folder_id}' for {user_email_from_creds}:"
        ]
        for item in files:
            size_str = f", Size: {item.get('size', 'N/A')}" if "size" in item else ""
            formatted_items_text_parts.append(
                f"- Name: \"{item['name']}\" (ID: {item['id']}, Type: {item['mimeType']}{size_str}, Modified: {item.get('modifiedTime', 'N/A')}) Link: {item.get('webViewLink', '#')}"
            )
        text_output = "\n".join(formatted_items_text_parts)
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text_output)]
        )
    except HttpError as error:
        logger.error(
            f"API error listing Drive items in folder {folder_id}: {error}",
            exc_info=True,
        )
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"API error: {error}")],
        )
    except Exception as e:
        logger.exception(
            f"Unexpected error listing Drive items in folder {folder_id}: {e}"
        )
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"Unexpected error: {e}")],
        )


@server.tool()
async def create_drive_file(
    file_name: str,
    content: str,
    folder_id: str = "root",  # Default to root folder
    user_google_email: Optional[str] = None,
    mime_type: str = "text/plain",  # Default to plain text
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Creates a new file in Google Drive with the specified name, content, and optional parent folder.
    Prioritizes authenticated MCP session, then `user_google_email`.
    If no valid authentication is found, guides the LLM to obtain user's email or use `start_auth`.

    Args:
        file_name (str): The name for the new file.
        content (str): The content to write to the file.
        folder_id (str): The ID of the parent folder. Defaults to 'root'.
        user_google_email (Optional[str]): User's Google email. Used if session isn't authenticated.
        mime_type (str): The MIME type of the file. Defaults to 'text/plain'.
        mcp_session_id (Optional[str]): Active MCP session ID (injected by FastMCP from Mcp-Session-Id header).

    Returns:
        A CallToolResult confirming creation or an error/auth guidance message.
    """
    logger.info(
        f"[create_drive_file] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}', File Name: {file_name}, Folder ID: {folder_id}"
    )
    tool_specific_scopes = [DRIVE_FILE_SCOPE]  # Use DRIVE_FILE_SCOPE for creating files
    credentials = await asyncio.to_thread(
        get_credentials,
        user_google_email=user_google_email,
        required_scopes=tool_specific_scopes,
        client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,  # Use imported constant
        session_id=mcp_session_id,
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[create_drive_file] No valid credentials. Session: '{mcp_session_id}', Email: '{user_google_email}'."
        )
        if user_google_email and "@" in user_google_email:
            # Use the centralized start_auth_flow
            return await start_auth_flow(
                mcp_session_id=mcp_session_id,
                user_google_email=user_google_email,
                service_name="Google Drive",
                redirect_uri=OAUTH_REDIRECT_URI,
            )
        else:
            error_msg = "Authentication required to create file. LLM: Please ask for Google email."
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=error_msg)]
            )

    try:
        service = build("drive", "v3", credentials=credentials)
        user_email_from_creds = (
            credentials.id_token.get("email")
            if credentials.id_token
            else "Unknown (Drive)"
        )

        file_metadata = {
            "name": file_name,
            "parents": [folder_id],
            "mimeType": mime_type,
        }
        media = io.BytesIO(content.encode("utf-8"))  # Encode content to bytes

        created_file = await asyncio.to_thread(
            service.files()
            .create(
                body=file_metadata,
                media_body=MediaIoBaseDownload(
                    media, service.files().get_media(fileId="placeholder")
                ),  # Placeholder request for MediaIoBaseDownload
                fields="id, name, webViewLink",
            )
            .execute
        )

        link = created_file.get("webViewLink", "No link available")
        confirmation_message = f"Successfully created file '{created_file.get('name', file_name)}' (ID: {created_file.get('id', 'N/A')}) in folder '{folder_id}' for {user_email_from_creds}. Link: {link}"
        logger.info(f"Successfully created file. Link: {link}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=confirmation_message)]
        )
    except HttpError as error:
        logger.error(
            f"API error creating Drive file '{file_name}': {error}", exc_info=True
        )
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"API error: {error}")],
        )
    except Exception as e:
        logger.exception(f"Unexpected error creating Drive file '{file_name}': {e}")
        return types.CallToolResult(
            isError=True,
            content=[types.TextContent(type="text", text=f"Unexpected error: {e}")],
        )
