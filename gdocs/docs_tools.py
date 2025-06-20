"""
Google Docs MCP Tools

This module provides MCP tools for interacting with Google Docs API and managing Google Docs via Drive.
"""
import logging
import asyncio
import io
from typing import List, Annotated, Optional, Dict

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

# Auth & server utilities
from auth.service_decorator import require_google_service, require_multiple_services
from core.utils import extract_office_xml_text, handle_http_errors
from core.server import server

from pydantic import Field

logger = logging.getLogger(__name__)

@server.tool()
@require_google_service("drive", "drive_read")
@handle_http_errors("search_docs")
async def search_docs(
    service,
    user_google_email: str,
    query: str,
    page_size: int = 10,
) -> str:
    """
    Searches for Google Docs by name using Drive API (mimeType filter).

    Returns:
        str: A formatted list of Google Docs matching the search query.
    """
    logger.info(f"[search_docs] Email={user_google_email}, Query='{query}'")

    escaped_query = query.replace("'", "\\'")

    response = await asyncio.to_thread(
        service.files().list(
            q=f"name contains '{escaped_query}' and mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=page_size,
            fields="files(id, name, createdTime, modifiedTime, webViewLink)"
        ).execute
    )
    files = response.get('files', [])
    if not files:
        return f"No Google Docs found matching '{query}'."

    output = [f"Found {len(files)} Google Docs matching '{query}':"]
    for f in files:
        output.append(
            f"- {f['name']} (ID: {f['id']}) Modified: {f.get('modifiedTime')} Link: {f.get('webViewLink')}"
        )
    return "\n".join(output)

@server.tool()
@require_multiple_services([
    {"service_type": "drive", "scopes": "drive_read", "param_name": "drive_service"},
    {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"}
])
@handle_http_errors("get_doc_content")
async def get_doc_content(
    drive_service,
    docs_service,
    user_google_email: str,
    document_id: str,
) -> str:
    """
    Retrieves content of a Google Doc or a Drive file (like .docx) identified by document_id.
    - Native Google Docs: Fetches content via Docs API.
    - Office files (.docx, etc.) stored in Drive: Downloads via Drive API and extracts text.

    Returns:
        str: The document content with metadata header.
    """
    logger.info(f"[get_doc_content] Invoked. Document/File ID: '{document_id}' for user '{user_google_email}'")

    # Step 2: Get file metadata from Drive
    file_metadata = await asyncio.to_thread(
        drive_service.files().get(
            fileId=document_id, fields="id, name, mimeType, webViewLink"
        ).execute
    )
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")
    web_view_link = file_metadata.get("webViewLink", "#")

    logger.info(f"[get_doc_content] File '{file_name}' (ID: {document_id}) has mimeType: '{mime_type}'")

    body_text = "" # Initialize body_text

    # Step 3: Process based on mimeType
    if mime_type == "application/vnd.google-apps.document":
        logger.info(f"[get_doc_content] Processing as native Google Doc.")
        doc_data = await asyncio.to_thread(
            docs_service.documents().get(documentId=document_id).execute
        )
        body_elements = doc_data.get('body', {}).get('content', [])

        processed_text_lines: List[str] = []
        for element in body_elements:
            if 'paragraph' in element:
                paragraph = element.get('paragraph', {})
                para_elements = paragraph.get('elements', [])
                current_line_text = ""
                for pe in para_elements:
                    text_run = pe.get('textRun', {})
                    if text_run and 'content' in text_run:
                        current_line_text += text_run['content']
                if current_line_text.strip():
                        processed_text_lines.append(current_line_text)
        body_text = "".join(processed_text_lines)
    else:
        logger.info(f"[get_doc_content] Processing as Drive file (e.g., .docx, other). MimeType: {mime_type}")

        export_mime_type_map = {
                # Example: "application/vnd.google-apps.spreadsheet"z: "text/csv",
                # Native GSuite types that are not Docs would go here if this function
                # was intended to export them. For .docx, direct download is used.
        }
        effective_export_mime = export_mime_type_map.get(mime_type)

        request_obj = (
            drive_service.files().export_media(fileId=document_id, mimeType=effective_export_mime)
            if effective_export_mime
            else drive_service.files().get_media(fileId=document_id)
        )

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_obj)
        loop = asyncio.get_event_loop()
        done = False
        while not done:
            status, done = await loop.run_in_executor(None, downloader.next_chunk)

        file_content_bytes = fh.getvalue()

        office_text = extract_office_xml_text(file_content_bytes, mime_type)
        if office_text:
            body_text = office_text
        else:
            try:
                body_text = file_content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                body_text = (
                    f"[Binary or unsupported text encoding for mimeType '{mime_type}' - "
                    f"{len(file_content_bytes)} bytes]"
                )

    header = (
        f'File: "{file_name}" (ID: {document_id}, Type: {mime_type})\n'
        f'Link: {web_view_link}\n\n--- CONTENT ---\n'
    )
    return header + body_text

@server.tool()
@require_google_service("drive", "drive_read")
@handle_http_errors("list_docs_in_folder")
async def list_docs_in_folder(
    service,
    user_google_email: str,
    folder_id: str = 'root',
    page_size: int = 100
) -> str:
    """
    Lists Google Docs within a specific Drive folder.

    Returns:
        str: A formatted list of Google Docs in the specified folder.
    """
    logger.info(f"[list_docs_in_folder] Invoked. Email: '{user_google_email}', Folder ID: '{folder_id}'")

    rsp = await asyncio.to_thread(
        service.files().list(
            q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=page_size,
            fields="files(id, name, modifiedTime, webViewLink)"
        ).execute
    )
    items = rsp.get('files', [])
    if not items:
        return f"No Google Docs found in folder '{folder_id}'."
    out = [f"Found {len(items)} Docs in folder '{folder_id}':"]
    for f in items:
        out.append(f"- {f['name']} (ID: {f['id']}) Modified: {f.get('modifiedTime')} Link: {f.get('webViewLink')}")
    return "\n".join(out)

@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("create_doc")
async def create_doc(
    service,
    user_google_email: str,
    title: str,
    content: str = '',
) -> str:
    """
    Creates a new Google Doc and optionally inserts initial content.

    Returns:
        str: Confirmation message with document ID and link.
    """
    logger.info(f"[create_doc] Invoked. Email: '{user_google_email}', Title='{title}'")

    doc = await asyncio.to_thread(service.documents().create(body={'title': title}).execute)
    doc_id = doc.get('documentId')
    if content:
        requests = [{'insertText': {'location': {'index': 1}, 'text': content}}]
        await asyncio.to_thread(service.documents().batchUpdate(documentId=doc_id, body={'requests': requests}).execute)
    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    msg = f"Created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. Link: {link}"
    logger.info(f"Successfully created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. Link: {link}")
    return msg


@server.tool()
@require_google_service("drive", "drive_read")
async def copy_google_doc(
    service,
    user_google_email: str,
    template_id: str,
    new_title: str,
    target_folder_id: Optional[str] = None,
) -> str:
    """
    Creates a new Google Doc by making a copy of an existing document. This is useful for creating documents from templates
    or duplicating existing documents while preserving their formatting and content.

    The tool will:
    1. Create an exact copy of the source document
    2. Give it the specified new title
    3. Place it in the specified folder (or root if no folder specified)
    4. Return the ID and view link of the new document

    Args:
        service: Authenticated Google Drive service instance.
        user_google_email: Email of the user making the request.
        template_id: The Google Drive ID of the source document that will be used as a template. This is the document you want to copy from.
        new_title: The title/name that will be given to the new copy of the document. This is what the document will be called in Google Drive.
        target_folder_id: Optional Google Drive folder ID where the new document should be created. If not provided, the document will be created in the root of the user's Google Drive.
    Returns:
        str: A message containing the new document's ID and view link.
    """
    logger.info(f"[copy_google_doc] Copying document {template_id} with new title {new_title}. Email: '{user_google_email}'")

    try:
        # Prepare copy metadata
        copy_metadata = {
            'name': new_title,
        }

        if target_folder_id:
            copy_metadata['parents'] = [target_folder_id]

        # Execute the copy
        response = service.files().copy(
            fileId=template_id,
            body=copy_metadata,
            fields='id,name,webViewLink'
        ).execute()

        document_id = response['id']
        document_name = response['name']
        view_link = response.get('webViewLink')

        return f'Successfully created document "{document_name}" with ID: {document_id}\nView Link: {view_link}'

    except HttpError as e:
        status = e.resp.status
        logger.error(f"Error copying document: {str(e)}")
        if status == 404:
            raise Exception("Template document or parent folder not found. Check the IDs. HTTP Status: 404")
        elif status == 403:
            raise Exception("Permission denied. Make sure you have read access to the template and write access to the destination folder. HTTP Status: 403")
        else:
            raise Exception(f"Failed to copy document: {e._get_reason() or 'Unknown error'} HTTP Status: {status}")

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise e


@server.tool()
@require_google_service("docs", "docs_write")
async def replace_text_in_google_doc(
    service,
    user_google_email: Annotated[str, Field(description="Email of the user making the request")],
    document_id: Annotated[str, Field(description="The Google Drive ID of the document where text replacements should be performed")],
    replacements: Annotated[Dict[str, str], Field(
        description="Dictionary mapping text patterns to their replacements. Each key is the text to find (case-insensitive), and each value is the text to replace it with",
        json_schema_extra={"additionalProperties": {"type": "string"}}
    )],
) -> str:
    """
    Performs multiple text replacements within a Google Doc in a single operation. This is useful for:
    - Replacing template placeholders with actual content
    - Updating multiple instances of the same text
    - Making bulk text changes across the document

    The tool will:
    1. Find all instances of each specified text pattern (case-insensitive)
    2. Replace them with their corresponding replacement text
    3. Perform all replacements in a single batch operation
    4. Return a summary of how many replacements were made

    Args:
        service: Authenticated Google Docs service instance.
        user_google_email: Email of the user making the request.
        document_id: The Google Drive ID of the document where text replacements should be performed. This is the document you want to modify.
        replacements: A dictionary mapping text patterns to their replacements. Each key is the text to find (case-insensitive),
                     and each value is the text to replace it with. Example: {'{{NAME}}': 'John Doe', '(% DATE %)': '2025-01-01'}
                     will replace all instances of '{{NAME}}' with 'John Doe' and '(% DATE %)' with '2025-01-01'.
    Returns:
        str: A message confirming the number of replacements that were successfully applied.
    """
    logger.info(f'Replacing text in document {document_id}. Amount of replacements: {len(replacements)}')

    try:
        requests = []
        for search_text, replace_text in replacements.items():
            requests.append({
                "replaceAllText": {
                    "containsText": {
                        "text": search_text,
                        "matchCase": False
                    },
                    "replaceText": replace_text
                }
            })

        if not requests:
            raise Exception("Error: The replacements dictionary is empty. Please provide at least one replacement.")

        service.documents().batchUpdate(
            documentId=document_id,
            body={"requests": requests}
        ).execute()

        count = len(requests)
        return f"Successfully applied {count} text replacement{'s' if count != 1 else ''} to the document."

    except HttpError as e:
        status = e.resp.status
        logger.error(f"Error replacing text in document: {str(e)}")
        if status == 404:
            raise Exception("Document not found. Check the document ID. HTTP Status: 404") from e
        elif status == 403:
            raise Exception("Permission denied. Make sure you have write access to the document. HTTP Status: 403") from e
        else:
            raise Exception(f"Failed to replace text: {e._get_reason() or 'Unknown error'} HTTP Status: {status}") from e

    except Exception as e:
        logger.error(f"Unhandled error: {str(e)}")
        raise e