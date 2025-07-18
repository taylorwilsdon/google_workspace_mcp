"""
Google Docs MCP Tools

This module provides MCP tools for interacting with Google Docs API and managing Google Docs via Drive.
"""
import logging
import asyncio
import io
from typing import List

from mcp import types
from googleapiclient.http import MediaIoBaseDownload

# Auth & server utilities
from auth.service_decorator import require_google_service, require_multiple_services
from core.utils import extract_office_xml_text, handle_http_errors
from core.server import server
from core.comments import create_comment_tools

logger = logging.getLogger(__name__)

@server.tool()
@handle_http_errors("search_docs", is_read_only=True)
@require_google_service("drive", "drive_read")
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
@handle_http_errors("get_doc_content", is_read_only=True)
@require_multiple_services([
    {"service_type": "drive", "scopes": "drive_read", "param_name": "drive_service"},
    {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"}
])
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
@handle_http_errors("list_docs_in_folder", is_read_only=True)
@require_google_service("drive", "drive_read")
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
@handle_http_errors("create_doc")
@require_google_service("docs", "docs_write")
async def create_doc(
    service,
    user_google_email: str,
    title: str,
    content: str = '',
) -> str:
    """
    Creates a new Google Doc and optionally inserts initial content.

    Args:
        service: Google Docs service
        user_google_email: User's email
        title: Document title
        content: Initial content (optional)

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
@require_google_service("docs", "docs_write")
@handle_http_errors("insert_text_at_line")
async def insert_text_at_line(
    service,
    user_google_email: str,
    document_id: str,
    line_number: int,
    text: str,
) -> str:
    """
    Insert text at a specific line number in a Google Doc.

    Args:
        service: Google Docs service
        user_google_email: User's email
        document_id: Document ID to modify
        line_number: Line number to insert at (1-based)
        text: Text to insert

    Returns:
        str: Confirmation message
    """
    logger.info(f"[insert_text_at_line] Invoked. Document ID: '{document_id}', Line: {line_number}, User: '{user_google_email}'")
    
    # Validate line number
    if line_number < 1:
        raise ValueError("Line number must be 1 or greater")

    # Get document to calculate insertion index
    doc = await asyncio.to_thread(service.documents().get(documentId=document_id).execute)
    body_elements = doc.get('body', {}).get('content', [])

    # Find the insertion index based on line number
    current_line = 1
    insertion_index = 1  # Default to beginning if line not found

    for element in body_elements:
        if 'paragraph' in element:
            if current_line == line_number:
                insertion_index = element.get('startIndex', 1)
                break
            current_line += 1

    # If line number is beyond document length, append at end
    if line_number > current_line:
        # Find the last element's end index
        last_index = 1
        for element in body_elements:
            if 'endIndex' in element:
                last_index = max(last_index, element.get('endIndex', 1))
        insertion_index = last_index

    # Insert text at the calculated index
    requests = [{
        'insertText': {
            'location': {'index': insertion_index},
            'text': text
        }
    }]

    await asyncio.to_thread(
        service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[insert_text_at_line] Successfully inserted text at line {line_number} in document {document_id} at index {insertion_index}")
    return f"Inserted text at line {line_number} in document {document_id} for {user_google_email}"


@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("find_and_replace_text")
async def find_and_replace_text(
    service,
    user_google_email: str,
    document_id: str,
    find_text: str,
    replace_text: str,
    match_case: bool = False,
) -> str:
    """
    Find and replace text in a Google Doc.

    Args:
        service: Google Docs service
        user_google_email: User's email
        document_id: Document ID to modify
        find_text: Text to find
        replace_text: Text to replace with
        match_case: Whether to match case

    Returns:
        str: Confirmation message with replacement count
    """
    logger.info(f"[find_and_replace_text] Invoked. Document ID: '{document_id}', Find: '{find_text}', Replace: '{replace_text}', User: '{user_google_email}'")

    # Validate input parameters
    if not find_text:
        raise ValueError("Find text cannot be empty")

    requests = [{
        'replaceAllText': {
            'containsText': {
                'text': find_text,
                'matchCase': match_case
            },
            'replaceText': replace_text
        }
    }]

    response = await asyncio.to_thread(
        service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    # Count replacements from response
    replacements = 0
    for reply in response.get('replies', []):
        if 'replaceAllText' in reply:
            replacements += reply['replaceAllText'].get('occurrencesChanged', 0)

    logger.info(f"[find_and_replace_text] Successfully replaced {replacements} occurrences in document {document_id}")
    return f"Replaced {replacements} occurrences of '{find_text}' with '{replace_text}' in document {document_id} for {user_google_email}"


@server.tool()
@require_multiple_services([
    {"service_type": "docs", "scopes": "docs_write", "param_name": "docs_service"},
    {"service_type": "drive", "scopes": "drive_write", "param_name": "drive_service"}
])
@handle_http_errors("create_versioned_document")
async def create_versioned_document(
    docs_service,
    drive_service,
    user_google_email: str,
    document_id: str,
    new_content: str,
    version_comment: str = "Document updated via MCP",
) -> str:
    """
    Create a new version of a document by overwriting content while preserving version history.
    Creates a backup copy before modification for rollback capability.

    Args:
        docs_service: Google Docs service
        drive_service: Google Drive service
        user_google_email: User's email
        document_id: Document ID to update
        new_content: New content to replace existing content
        version_comment: Comment for the version

    Returns:
        str: Confirmation message with backup document ID
    """
    logger.info(f"[create_versioned_document] Invoked. Document ID: '{document_id}', User: '{user_google_email}', Version: '{version_comment}'")

    # Validate input parameters
    if not new_content:
        raise ValueError("New content cannot be empty")
    if not version_comment.strip():
        raise ValueError("Version comment cannot be empty")

    # Get original document metadata
    doc_metadata = await asyncio.to_thread(
        drive_service.files().get(fileId=document_id, fields="name").execute
    )
    original_name = doc_metadata.get('name', 'Untitled Document')

    # Create backup copy
    backup_name = f"{original_name} - Backup {version_comment}"
    backup_response = await asyncio.to_thread(
        drive_service.files().copy(
            fileId=document_id,
            body={'name': backup_name}
        ).execute
    )
    backup_id = backup_response.get('id')
    if not backup_id:
        raise Exception("Failed to create backup copy of document")

    # Get current document to calculate content range
    doc = await asyncio.to_thread(docs_service.documents().get(documentId=document_id).execute)
    body_content = doc.get('body', {}).get('content', [])

    # Calculate the range to delete (everything except the first empty paragraph)
    end_index = 1
    for element in body_content:
        if 'endIndex' in element:
            end_index = max(end_index, element['endIndex'])

    # Clear document and insert new content
    requests = []

    # Delete existing content (but leave the first character to maintain structure)
    if end_index > 1:
        requests.append({
            'deleteContentRange': {
                'range': {
                    'startIndex': 1,
                    'endIndex': end_index - 1
                }
            }
        })

    # Insert new content
    requests.append({
        'insertText': {
            'location': {'index': 1},
            'text': new_content
        }
    })

    # Execute batch update
    await asyncio.to_thread(
        docs_service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    backup_link = f"https://docs.google.com/document/d/{backup_id}/edit"
    logger.info(f"[create_versioned_document] Successfully updated document {document_id}. Backup created: {backup_name} (ID: {backup_id})")
    return f"Document {document_id} updated successfully for {user_google_email}. Backup created: {backup_name} (ID: {backup_id}, Link: {backup_link})"


@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("format_text_style")
async def format_text_style(
    service,
    user_google_email: str,
    document_id: str,
    start_index: int,
    end_index: int,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    font_size: int = None,
    font_family: str = None,
    text_color: str = None,
    background_color: str = None,
) -> str:
    """
    Apply text formatting to a range of text in a Google Doc.

    Args:
        service: Google Docs service
        user_google_email: User's email
        document_id: Document ID to modify
        start_index: Start position (0-based)
        end_index: End position (0-based, exclusive)
        bold: Set bold formatting (True/False)
        italic: Set italic formatting (True/False)
        underline: Set underline formatting (True/False)
        font_size: Font size in points
        font_family: Font family name (e.g., 'Arial', 'Times New Roman')
        text_color: Text color in hex format (e.g., '#FF0000' for red)
        background_color: Background color in hex format

    Returns:
        str: Confirmation message
    """
    logger.info(f"[format_text_style] Invoked. Document ID: '{document_id}', Range: {start_index}-{end_index}, User: '{user_google_email}'")

    if start_index < 0 or end_index <= start_index:
        raise ValueError("Invalid range: start_index must be >= 0 and end_index must be > start_index")

    # Build text style object
    text_style = {}
    if bold is not None:
        text_style['bold'] = bold
    if italic is not None:
        text_style['italic'] = italic
    if underline is not None:
        text_style['underline'] = underline
    if font_size is not None:
        text_style['fontSize'] = {'magnitude': font_size, 'unit': 'PT'}
    if font_family is not None:
        text_style['fontFamily'] = font_family
    if text_color is not None:
        text_style['foregroundColor'] = {'color': {'rgbColor': _hex_to_rgb(text_color)}}
    if background_color is not None:
        text_style['backgroundColor'] = {'color': {'rgbColor': _hex_to_rgb(background_color)}}

    if not text_style:
        return f"No formatting changes specified for document {document_id}"

    # Create batch update request
    requests = [{
        'updateTextStyle': {
            'range': {
                'startIndex': start_index,
                'endIndex': end_index
            },
            'textStyle': text_style,
            'fields': ','.join(text_style.keys())
        }
    }]

    await asyncio.to_thread(
        service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[format_text_style] Successfully applied text formatting to document {document_id} for {user_google_email}")
    return f"Text formatting applied to range {start_index}-{end_index} in document {document_id} for {user_google_email}"


@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("format_paragraph_style")
async def format_paragraph_style(
    service,
    user_google_email: str,
    document_id: str,
    start_index: int,
    end_index: int,
    alignment: str = None,
    line_spacing: float = None,
    indent_first_line: float = None,
    indent_start: float = None,
    indent_end: float = None,
    space_above: float = None,
    space_below: float = None,
) -> str:
    """
    Apply paragraph formatting to a range of text in a Google Doc.

    Args:
        service: Google Docs service
        user_google_email: User's email
        document_id: Document ID to modify
        start_index: Start position (0-based)
        end_index: End position (0-based, exclusive)
        alignment: Text alignment ('START', 'CENTER', 'END', 'JUSTIFY')
        line_spacing: Line spacing multiplier (e.g., 1.0 for single, 1.5 for 1.5x, 2.0 for double)
        indent_first_line: First line indent in points
        indent_start: Left indent in points
        indent_end: Right indent in points
        space_above: Space above paragraph in points
        space_below: Space below paragraph in points

    Returns:
        str: Confirmation message
    """
    logger.info(f"[format_paragraph_style] Invoked. Document ID: '{document_id}', Range: {start_index}-{end_index}, User: '{user_google_email}'")

    if start_index < 0 or end_index <= start_index:
        raise ValueError("Invalid range: start_index must be >= 0 and end_index must be > start_index")

    # Build paragraph style object
    paragraph_style = {}
    if alignment is not None:
        valid_alignments = ['START', 'CENTER', 'END', 'JUSTIFY']
        if alignment.upper() not in valid_alignments:
            raise ValueError(f"Invalid alignment. Must be one of: {valid_alignments}")
        paragraph_style['alignment'] = alignment.upper()
    if line_spacing is not None:
        paragraph_style['lineSpacing'] = line_spacing
    if indent_first_line is not None:
        paragraph_style['indentFirstLine'] = {'magnitude': indent_first_line, 'unit': 'PT'}
    if indent_start is not None:
        paragraph_style['indentStart'] = {'magnitude': indent_start, 'unit': 'PT'}
    if indent_end is not None:
        paragraph_style['indentEnd'] = {'magnitude': indent_end, 'unit': 'PT'}
    if space_above is not None:
        paragraph_style['spaceAbove'] = {'magnitude': space_above, 'unit': 'PT'}
    if space_below is not None:
        paragraph_style['spaceBelow'] = {'magnitude': space_below, 'unit': 'PT'}

    if not paragraph_style:
        return f"No paragraph formatting changes specified for document {document_id}"

    # Create batch update request
    requests = [{
        'updateParagraphStyle': {
            'range': {
                'startIndex': start_index,
                'endIndex': end_index
            },
            'paragraphStyle': paragraph_style,
            'fields': ','.join(paragraph_style.keys())
        }
    }]

    await asyncio.to_thread(
        service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[format_paragraph_style] Successfully applied paragraph formatting to document {document_id} for {user_google_email}")
    return f"Paragraph formatting applied to range {start_index}-{end_index} in document {document_id} for {user_google_email}"


@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("apply_heading_style")
async def apply_heading_style(
    service,
    user_google_email: str,
    document_id: str,
    start_index: int,
    end_index: int,
    heading_level: int,
) -> str:
    """
    Apply heading style to a range of text in a Google Doc.

    Args:
        service: Google Docs service
        user_google_email: User's email
        document_id: Document ID to modify
        start_index: Start position (0-based)
        end_index: End position (0-based, exclusive)
        heading_level: Heading level (1-6, where 1 is H1, 2 is H2, etc.)

    Returns:
        str: Confirmation message
    """
    logger.info(f"[apply_heading_style] Invoked. Document ID: '{document_id}', Range: {start_index}-{end_index}, Heading Level: {heading_level}, User: '{user_google_email}'")

    if start_index < 0 or end_index <= start_index:
        raise ValueError("Invalid range: start_index must be >= 0 and end_index must be > start_index")

    if heading_level < 1 or heading_level > 6:
        raise ValueError("Heading level must be between 1 and 6")

    # Map heading level to Google Docs named style
    heading_style = f"HEADING_{heading_level}"

    # Create batch update request
    requests = [{
        'updateParagraphStyle': {
            'range': {
                'startIndex': start_index,
                'endIndex': end_index
            },
            'paragraphStyle': {
                'namedStyleType': heading_style
            },
            'fields': 'namedStyleType'
        }
    }]

    await asyncio.to_thread(
        service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[apply_heading_style] Successfully applied {heading_style} to document {document_id} for {user_google_email}")
    return f"Applied {heading_style} to range {start_index}-{end_index} in document {document_id} for {user_google_email}"


@server.tool()
@require_google_service("docs", "docs_write")
@handle_http_errors("create_list")
async def create_list(
    service,
    user_google_email: str,
    document_id: str,
    start_index: int,
    end_index: int,
    list_type: str = "BULLET",
    nesting_level: int = 0,
) -> str:
    """
    Convert paragraphs to a bulleted or numbered list in a Google Doc.

    Args:
        service: Google Docs service
        user_google_email: User's email
        document_id: Document ID to modify
        start_index: Start position (0-based)
        end_index: End position (0-based, exclusive)
        list_type: Type of list ('BULLET' or 'NUMBERED')
        nesting_level: Nesting level (0-8, where 0 is top level)

    Returns:
        str: Confirmation message
    """
    logger.info(f"[create_list] Invoked. Document ID: '{document_id}', Range: {start_index}-{end_index}, List Type: {list_type}, User: '{user_google_email}'")

    if start_index < 0 or end_index <= start_index:
        raise ValueError("Invalid range: start_index must be >= 0 and end_index must be > start_index")

    if list_type not in ['BULLET', 'NUMBERED']:
        raise ValueError("List type must be 'BULLET' or 'NUMBERED'")

    if nesting_level < 0 or nesting_level > 8:
        raise ValueError("Nesting level must be between 0 and 8")

    # Create batch update request
    requests = [{
        'createParagraphBullets': {
            'range': {
                'startIndex': start_index,
                'endIndex': end_index
            },
            'bulletPreset': f"{list_type}_DISC_CIRCLE_SQUARE" if list_type == "BULLET" else "NUMBERED_DECIMAL_ALPHA_ROMAN"
        }
    }]

    # Add nesting if specified
    if nesting_level > 0:
        requests.append({
            'updateParagraphStyle': {
                'range': {
                    'startIndex': start_index,
                    'endIndex': end_index
                },
                'paragraphStyle': {
                    'indentStart': {'magnitude': nesting_level * 18, 'unit': 'PT'}
                },
                'fields': 'indentStart'
            }
        })

    await asyncio.to_thread(
        service.documents().batchUpdate(
            documentId=document_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[create_list] Successfully created {list_type} list in document {document_id} for {user_google_email}")
    return f"Created {list_type} list at range {start_index}-{end_index} in document {document_id} for {user_google_email}"


def _hex_to_rgb(hex_color: str) -> dict:
    """Convert hex color to RGB dict for Google Docs API."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        raise ValueError("Invalid hex color format. Use #RRGGBB format")
    
    return {
        'red': int(hex_color[0:2], 16) / 255.0,
        'green': int(hex_color[2:4], 16) / 255.0,
        'blue': int(hex_color[4:6], 16) / 255.0
    }


# Create comment management tools for documents
_comment_tools = create_comment_tools("document", "document_id")

# Extract and register the functions
read_doc_comments = _comment_tools['read_comments']
create_doc_comment = _comment_tools['create_comment']
reply_to_comment = _comment_tools['reply_to_comment']
resolve_comment = _comment_tools['resolve_comment']
