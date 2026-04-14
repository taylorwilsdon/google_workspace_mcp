"""
Google Docs MCP Tools

This module provides MCP tools for interacting with Google Docs API and managing Google Docs via Drive.
"""

import logging
import asyncio
import io
import inspect
import re
from typing import List, Any, Optional

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# Auth & server utilities
from auth.service_decorator import require_google_service, require_multiple_services
from core.utils import extract_office_xml_text, handle_http_errors, UserInputError
from core.server import server
from core.comments import create_comment_tools

# Import helper functions for document operations
from gdocs.docs_helpers import (
    create_insert_text_request,
    create_delete_range_request,
    create_format_text_request,
    create_find_replace_request,
    create_insert_table_request,
    create_insert_page_break_request,
    create_insert_image_request,
    create_bullet_list_request,
    create_insert_doc_tab_request,
    create_update_doc_tab_request,
    create_delete_doc_tab_request,
    validate_suggestions_view_mode,
    create_update_paragraph_style_request,
)

# Import document structure and table utilities
from gdocs.docs_structure import (
    parse_document_structure,
    find_tables,
    analyze_document_complexity,
)
from gdocs.docs_tables import extract_table_as_data
from gdocs.docs_markdown import (
    convert_doc_to_markdown,
    format_comments_inline,
    format_comments_appendix,
    parse_drive_comments,
)
from gdocs.operation_schemas import BatchDocOperations

# Import operation managers for complex business logic
from gdocs.managers import (
    TableOperationManager,
    HeaderFooterManager,
    ValidationManager,
    BatchOperationManager,
)
import json

logger = logging.getLogger(__name__)
HEADER_FOOTER_RUNTIME_CANARY = "docs-hf-canary-20260328b"


@server.tool()
@handle_http_errors("search_docs", is_read_only=True, service_type="docs")
@require_google_service("drive", "drive_read")
async def search_docs(
    service: Any,
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
        service.files()
        .list(
            q=f"name contains '{escaped_query}' and mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=page_size,
            fields="files(id, name, createdTime, modifiedTime, webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute
    )
    files = response.get("files", [])
    if not files:
        return f"No Google Docs found matching '{query}'."

    output = [f"Found {len(files)} Google Docs matching '{query}':"]
    for f in files:
        output.append(
            f"- {f['name']} (ID: {f['id']}) Modified: {f.get('modifiedTime')} Link: {f.get('webViewLink')}"
        )
    return "\n".join(output)


@server.tool()
@handle_http_errors("get_doc_content", is_read_only=True, service_type="docs")
@require_multiple_services(
    [
        {
            "service_type": "drive",
            "scopes": "drive_read",
            "param_name": "drive_service",
        },
        {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"},
    ]
)
async def get_doc_content(
    drive_service: Any,
    docs_service: Any,
    user_google_email: str,
    document_id: str,
    suggestions_view_mode: str = "DEFAULT_FOR_CURRENT_ACCESS",
) -> str:
    """
    Retrieves content of a Google Doc or a Drive file (like .docx) identified by document_id.
    - Native Google Docs: Fetches content via Docs API.
    - Office files (.docx, etc.) stored in Drive: Downloads via Drive API and extracts text.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        suggestions_view_mode: How to render suggestions in the returned content:
            - "DEFAULT_FOR_CURRENT_ACCESS": Default based on user's access level
            - "SUGGESTIONS_INLINE": Suggested changes appear inline in the document
            - "PREVIEW_SUGGESTIONS_ACCEPTED": Preview as if all suggestions were accepted
            - "PREVIEW_WITHOUT_SUGGESTIONS": Preview as if all suggestions were rejected

    Returns:
        str: The document content with metadata header.
    """
    validation_error = validate_suggestions_view_mode(suggestions_view_mode)
    if validation_error:
        return validation_error
    logger.info(
        f"[get_doc_content] Invoked. Document/File ID: '{document_id}' for user '{user_google_email}'"
    )

    file_metadata = await asyncio.to_thread(
        drive_service.files()
        .get(
            fileId=document_id,
            fields="id, name, mimeType, webViewLink",
            supportsAllDrives=True,
        )
        .execute
    )
    mime_type = file_metadata.get("mimeType", "")
    file_name = file_metadata.get("name", "Unknown File")
    web_view_link = file_metadata.get("webViewLink", "#")

    logger.info(
        f"[get_doc_content] File '{file_name}' (ID: {document_id}) has mimeType: '{mime_type}'"
    )

    body_text = ""

    if mime_type == "application/vnd.google-apps.document":
        logger.info("[get_doc_content] Processing as native Google Doc.")
        doc_data = await asyncio.to_thread(
            docs_service.documents()
            .get(
                documentId=document_id,
                includeTabsContent=True,
                suggestionsViewMode=suggestions_view_mode,
            )
            .execute
        )
        TAB_HEADER_FORMAT = "\n--- TAB: {tab_name} (ID: {tab_id}) ---\n"

        def extract_text_from_elements(elements, tab_name=None, tab_id=None, depth=0):
            """Extract text from document elements (paragraphs, tables, etc.)"""
            if depth > 5:
                return ""
            text_lines = []
            if tab_name:
                text_lines.append(
                    TAB_HEADER_FORMAT.format(tab_name=tab_name, tab_id=tab_id)
                )

            for element in elements:
                if "paragraph" in element:
                    paragraph = element.get("paragraph", {})
                    para_elements = paragraph.get("elements", [])
                    current_line_text = ""
                    for pe in para_elements:
                        text_run = pe.get("textRun", {})
                        if text_run and "content" in text_run:
                            current_line_text += text_run["content"]
                    if current_line_text.strip():
                        text_lines.append(current_line_text)
                elif "table" in element:
                    # Handle table content
                    table = element.get("table", {})
                    table_rows = table.get("tableRows", [])
                    for row in table_rows:
                        row_cells = row.get("tableCells", [])
                        for cell in row_cells:
                            cell_content = cell.get("content", [])
                            cell_text = extract_text_from_elements(
                                cell_content, depth=depth + 1
                            )
                            if cell_text.strip():
                                text_lines.append(cell_text)
            return "".join(text_lines)

        def process_tab_hierarchy(tab, level=0):
            """Process a tab and its nested child tabs recursively"""
            tab_text = ""

            if "documentTab" in tab:
                props = tab.get("tabProperties", {})
                tab_title = props.get("title", "Untitled Tab")
                tab_id = props.get("tabId", "Unknown ID")
                if level > 0:
                    tab_title = "    " * level + f"{tab_title}"
                tab_body = tab.get("documentTab", {}).get("body", {}).get("content", [])
                tab_text += extract_text_from_elements(tab_body, tab_title, tab_id)

            child_tabs = tab.get("childTabs", [])
            for child_tab in child_tabs:
                tab_text += process_tab_hierarchy(child_tab, level + 1)

            return tab_text

        processed_text_lines = []

        body_elements = doc_data.get("body", {}).get("content", [])
        main_content = extract_text_from_elements(body_elements)
        if main_content.strip():
            processed_text_lines.append(main_content)

        tabs = doc_data.get("tabs", [])
        for tab in tabs:
            tab_content = process_tab_hierarchy(tab)
            if tab_content.strip():
                processed_text_lines.append(tab_content)

        body_text = "".join(processed_text_lines)
    else:
        logger.info(
            f"[get_doc_content] Processing as Drive file (e.g., .docx, other). MimeType: {mime_type}"
        )

        export_mime_type_map = {
            # Example: "application/vnd.google-apps.spreadsheet"z: "text/csv",
            # Native GSuite types that are not Docs would go here if this function
            # was intended to export them. For .docx, direct download is used.
        }
        effective_export_mime = export_mime_type_map.get(mime_type)

        request_obj = (
            drive_service.files().export_media(
                fileId=document_id,
                mimeType=effective_export_mime,
                supportsAllDrives=True,
            )
            if effective_export_mime
            else drive_service.files().get_media(
                fileId=document_id, supportsAllDrives=True
            )
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
        f"Link: {web_view_link}\n\n--- CONTENT ---\n"
    )
    return header + body_text


@server.tool()
@handle_http_errors("list_docs_in_folder", is_read_only=True, service_type="docs")
@require_google_service("drive", "drive_read")
async def list_docs_in_folder(
    service: Any, user_google_email: str, folder_id: str = "root", page_size: int = 100
) -> str:
    """
    Lists Google Docs within a specific Drive folder.

    Returns:
        str: A formatted list of Google Docs in the specified folder.
    """
    logger.info(
        f"[list_docs_in_folder] Invoked. Email: '{user_google_email}', Folder ID: '{folder_id}'"
    )

    rsp = await asyncio.to_thread(
        service.files()
        .list(
            q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.document' and trashed=false",
            pageSize=page_size,
            fields="files(id, name, modifiedTime, webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute
    )
    items = rsp.get("files", [])
    if not items:
        return f"No Google Docs found in folder '{folder_id}'."
    out = [f"Found {len(items)} Docs in folder '{folder_id}':"]
    for f in items:
        out.append(
            f"- {f['name']} (ID: {f['id']}) Modified: {f.get('modifiedTime')} Link: {f.get('webViewLink')}"
        )
    return "\n".join(out)


@server.tool()
@handle_http_errors("create_doc", service_type="docs")
@require_google_service("docs", "docs_write")
async def create_doc(
    service: Any,
    user_google_email: str,
    title: str,
    content: str = "",
) -> str:
    """
    Creates a new Google Doc and optionally inserts initial content.

    After creation, the document body starts at index 1. A new empty doc
    has total length 2 (one section break at index 0, one newline at index 1).

    To build a rich document after creation, use batch_update_doc with
    insert_text operations using end_of_segment=true to append content
    sequentially without calculating indices. Then call inspect_doc_structure
    to get exact positions before applying formatting in a separate batch call.

    Args:
        user_google_email: User's Google email address
        title: Title of the new document
        content: Optional initial plain text content to insert

    Returns:
        str: Confirmation message with document ID, link, and initial document state.
    """
    logger.info(f"[create_doc] Invoked. Email: '{user_google_email}', Title='{title}'")

    doc = await asyncio.to_thread(
        service.documents().create(body={"title": title}).execute
    )
    doc_id = doc.get("documentId")
    if content:
        requests = [{"insertText": {"location": {"index": 1}, "text": content}}]
        await asyncio.to_thread(
            service.documents()
            .batchUpdate(documentId=doc_id, body={"requests": requests})
            .execute
        )
    link = f"https://docs.google.com/document/d/{doc_id}/edit"
    if content:
        content_note = f"Initial content: {len(content)} characters inserted."
    else:
        content_note = "Document is empty (body starts at index 1, total length 2)."
    msg = (
        f"Created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. "
        f"{content_note} "
        f"Use batch_update_doc with end_of_segment=true to append content. "
        f"Link: {link}"
    )
    logger.info(
        f"Successfully created Google Doc '{title}' (ID: {doc_id}) for {user_google_email}. Link: {link}"
    )
    return msg


@server.tool()
@handle_http_errors("modify_doc_text", service_type="docs")
@require_google_service("docs", "docs_write")
async def modify_doc_text(
    service: Any,
    user_google_email: str,
    document_id: str,
    start_index: int,
    end_index: int = None,
    text: str = None,
    tab_id: str = None,
    segment_id: str = None,
    end_of_segment: bool = False,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    strikethrough: bool = None,
    font_size: int = None,
    font_family: str = None,
    font_weight: int = None,
    text_color: str = None,
    background_color: str = None,
    link_url: str = None,
    clear_link: bool = None,
    baseline_offset: str = None,
    small_caps: bool = None,
) -> str:
    """
    Modifies text in a Google Doc - can insert/replace text and/or apply formatting in a single operation.

    TIP: To append text to the end of the document without calculating indices,
    set end_of_segment=true. This avoids index calculation errors.

    For ordinary header/footer text, prefer update_doc_headers_footers.
    Only pass segment_id when you already have a real header/footer/footnote
    segment ID from inspect_doc_structure output. Do not guess IDs such as
    "kix.header" or "kix.footer".

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        start_index: Start position for operation using Docs API indices from
            inspect_doc_structure. For the main body, 0 is also accepted as an
            alias for the first writable position.
        end_index: End position for text replacement/formatting (if not provided with text, text is inserted)
        text: New text to insert or replace with (optional - can format existing text without changing it)
        tab_id: Optional document tab ID to target
        segment_id: Optional header/footer/footnote segment ID to target
        end_of_segment: Insert text at the end of the targeted segment instead of start_index
        bold: Whether to make text bold (True/False/None to leave unchanged)
        italic: Whether to make text italic (True/False/None to leave unchanged)
        underline: Whether to underline text (True/False/None to leave unchanged)
        strikethrough: Whether to strike through text (True/False/None to leave unchanged)
        font_size: Font size in points
        font_family: Font family name (e.g., "Arial", "Times New Roman")
        font_weight: Font weight (100-900 in steps of 100; requires font_family)
        text_color: Foreground text color (#RRGGBB)
        background_color: Background/highlight color (#RRGGBB)
        link_url: Hyperlink URL (http/https)
        clear_link: Remove hyperlink from the target range
        baseline_offset: One of NONE, SUPERSCRIPT, SUBSCRIPT
        small_caps: Whether to apply small caps

    Returns:
        str: Confirmation message with operation details
    """
    logger.info(
        f"[modify_doc_text] Doc={document_id}, start={start_index}, end={end_index}, text={text is not None}, "
        f"formatting={any(p is not None for p in [bold, italic, underline, strikethrough, font_size, font_family, font_weight, text_color, background_color, link_url, clear_link, baseline_offset, small_caps])}"
    )

    # Input validation
    validator = ValidationManager()

    is_valid, error_msg = validator.validate_document_id(document_id)
    if not is_valid:
        return f"Error: {error_msg}"

    # Validate that we have something to do
    formatting_params = [
        bold,
        italic,
        underline,
        strikethrough,
        font_size,
        font_family,
        font_weight,
        text_color,
        background_color,
        link_url,
        clear_link,
        baseline_offset,
        small_caps,
    ]
    if text is None and not any(p is not None for p in formatting_params):
        return "Error: Must provide either 'text' to insert/replace, or formatting parameters (bold, italic, underline, strikethrough, font_size, font_family, text_color, background_color, link_url)."

    # Validate text formatting params if provided
    if any(p is not None for p in formatting_params):
        is_valid, error_msg = validator.validate_text_formatting_params(
            bold,
            italic,
            underline,
            strikethrough,
            font_size,
            font_family,
            font_weight,
            text_color,
            background_color,
            link_url,
            clear_link,
            baseline_offset,
            small_caps,
        )
        if not is_valid:
            return f"Error: {error_msg}"

        # For formatting, we need end_index
        if end_index is None:
            return "Error: 'end_index' is required when applying formatting."
        if end_of_segment:
            return "Error: end_of_segment cannot be used when applying formatting."

        is_valid, error_msg = validator.validate_index_range(start_index, end_index)
        if not is_valid:
            return f"Error: {error_msg}"

    requests = []
    operations = []

    # Handle text insertion/replacement
    if text is not None:
        if end_index is not None and end_index > start_index:
            # Text replacement
            if end_of_segment:
                return "Error: end_of_segment cannot be combined with text replacement."
            if start_index == 0 and segment_id is None and tab_id is None:
                # Special case: Cannot delete at index 0 (first section break)
                # Instead, we insert new text at index 1 and then delete the old text
                requests.append(
                    create_insert_text_request(1, text, tab_id, segment_id=segment_id)
                )
                adjusted_end = end_index + len(text)
                requests.append(
                    create_delete_range_request(
                        1 + len(text), adjusted_end, tab_id, segment_id=segment_id
                    )
                )
                operations.append(
                    f"Replaced text from index {start_index} to {end_index}"
                )
            else:
                # Normal replacement: delete old text, then insert new text
                requests.extend(
                    [
                        create_delete_range_request(
                            start_index, end_index, tab_id, segment_id=segment_id
                        ),
                        create_insert_text_request(
                            start_index, text, tab_id, segment_id=segment_id
                        ),
                    ]
                )
                operations.append(
                    f"Replaced text from index {start_index} to {end_index}"
                )
        else:
            # Text insertion
            actual_index = (
                1
                if start_index == 0
                and not end_of_segment
                and segment_id is None
                and tab_id is None
                else start_index
            )
            requests.append(
                create_insert_text_request(
                    None if end_of_segment else actual_index,
                    text,
                    tab_id,
                    segment_id=segment_id,
                    end_of_segment=end_of_segment,
                )
            )
            if end_of_segment:
                operations.append(
                    f"Inserted text at end of segment '{segment_id or 'body'}'"
                )
            else:
                operations.append(f"Inserted text at index {start_index}")

    # Handle formatting
    if any(p is not None for p in formatting_params):
        # Adjust range for formatting based on text operations
        format_start = start_index
        format_end = end_index

        if text is not None:
            if end_index is not None and end_index > start_index:
                # Text was replaced - format the new text
                format_end = start_index + len(text)
            else:
                # Text was inserted - format the inserted text
                actual_index = 1 if start_index == 0 else start_index
                format_start = actual_index
                format_end = actual_index + len(text)

        # Handle special case for formatting at index 0
        if format_start == 0 and segment_id is None and tab_id is None:
            format_start = 1
        if format_end is not None and format_end <= format_start:
            format_end = format_start + 1

        requests.append(
            create_format_text_request(
                format_start,
                format_end,
                bold,
                italic,
                underline,
                strikethrough,
                font_size,
                font_family,
                font_weight,
                text_color,
                background_color,
                link_url,
                clear_link,
                baseline_offset,
                small_caps,
                tab_id,
                segment_id,
            )
        )

        format_details = [
            f"{name}={value}"
            for name, value in [
                ("bold", bold),
                ("italic", italic),
                ("underline", underline),
                ("strikethrough", strikethrough),
                ("font_size", font_size),
                ("font_family", font_family),
                ("font_weight", font_weight),
                ("text_color", text_color),
                ("background_color", background_color),
                ("link_url", link_url),
                ("clear_link", clear_link),
                ("baseline_offset", baseline_offset),
                ("small_caps", small_caps),
            ]
            if value is not None
        ]

        operations.append(
            f"Applied formatting ({', '.join(format_details)}) to range {format_start}-{format_end}"
        )

    try:
        await asyncio.to_thread(
            service.documents()
            .batchUpdate(documentId=document_id, body={"requests": requests})
            .execute
        )
    except HttpError as error:
        raise _rewrite_modify_doc_text_http_error(error, segment_id) from error

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    operation_summary = "; ".join(operations)
    text_info = f" Text length: {len(text)} characters." if text else ""
    return f"{operation_summary} in document {document_id}.{text_info} Link: {link}"


@server.tool()
@handle_http_errors("find_and_replace_doc", service_type="docs")
@require_google_service("docs", "docs_write")
async def find_and_replace_doc(
    service: Any,
    user_google_email: str,
    document_id: str,
    find_text: str,
    replace_text: str,
    match_case: bool = False,
    tab_id: Optional[str] = None,
) -> str:
    """
    Finds and replaces text throughout a Google Doc. No index calculation required.

    This is the safest way to update specific text in a document because it
    does not require knowing any indices. Use this tool when you need to:
    - Replace placeholder text (e.g., {{TITLE}}) with real content
    - Update specific words or phrases throughout the document
    - Make targeted text changes without risk of index errors

    For building documents from scratch, consider inserting text with unique
    placeholders via batch_update_doc, then using this tool to replace them.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        find_text: Text to search for
        replace_text: Text to replace with
        match_case: Whether to match case exactly
        tab_id: Optional ID of the tab to target

    Returns:
        str: Confirmation message with replacement count
    """
    logger.info(
        f"[find_and_replace_doc] Doc={document_id}, find='{find_text}', replace='{replace_text}', tab='{tab_id}'"
    )

    requests = [
        create_find_replace_request(find_text, replace_text, match_case, tab_id)
    ]

    result = await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute
    )

    # Extract number of replacements from response
    replacements = 0
    if "replies" in result and result["replies"]:
        reply = result["replies"][0]
        if "replaceAllText" in reply:
            replacements = reply["replaceAllText"].get("occurrencesChanged", 0)

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Replaced {replacements} occurrence(s) of '{find_text}' with '{replace_text}' in document {document_id}. Link: {link}"


@server.tool()
@handle_http_errors("insert_doc_elements", service_type="docs")
@require_google_service("docs", "docs_write")
async def insert_doc_elements(
    service: Any,
    user_google_email: str,
    document_id: str,
    element_type: str,
    index: int,
    rows: int = None,
    columns: int = None,
    list_type: str = None,
    text: str = None,
) -> str:
    """
    Inserts structural elements like tables, lists, or page breaks into a Google Doc.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        element_type: Type of element to insert ("table", "list", "page_break")
        index: Position to insert element (0-based)
        rows: Number of rows for table (required for table)
        columns: Number of columns for table (required for table)
        list_type: Type of list ("UNORDERED", "ORDERED") (required for list)
        text: Initial text content for list items

    Returns:
        str: Confirmation message with insertion details
    """
    logger.info(
        f"[insert_doc_elements] Doc={document_id}, type={element_type}, index={index}"
    )

    # Handle the special case where we can't insert at the first section break
    # If index is 0, bump it to 1 to avoid the section break
    if index == 0:
        logger.debug("Adjusting index from 0 to 1 to avoid first section break")
        index = 1

    requests = []

    if element_type == "table":
        if not rows or not columns:
            return "Error: 'rows' and 'columns' parameters are required for table insertion."

        requests.append(create_insert_table_request(index, rows, columns))
        description = f"table ({rows}x{columns})"

    elif element_type == "list":
        if not list_type:
            return "Error: 'list_type' parameter is required for list insertion ('UNORDERED' or 'ORDERED')."

        if not text:
            text = "List item"

        # Insert text first, then create list
        requests.extend(
            [
                create_insert_text_request(index, text + "\n"),
                create_bullet_list_request(index, index + len(text), list_type),
            ]
        )
        description = f"{list_type.lower()} list"

    elif element_type == "page_break":
        requests.append(create_insert_page_break_request(index))
        description = "page break"

    else:
        return f"Error: Unsupported element type '{element_type}'. Supported types: 'table', 'list', 'page_break'."

    await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute
    )

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Inserted {description} at index {index} in document {document_id}. Link: {link}"


@server.tool()
@handle_http_errors("insert_doc_image", service_type="docs")
@require_multiple_services(
    [
        {"service_type": "docs", "scopes": "docs_write", "param_name": "docs_service"},
        {
            "service_type": "drive",
            "scopes": "drive_read",
            "param_name": "drive_service",
        },
    ]
)
async def insert_doc_image(
    docs_service: Any,
    drive_service: Any,
    user_google_email: str,
    document_id: str,
    image_source: str,
    index: int,
    width: int = 0,
    height: int = 0,
) -> str:
    """
    Inserts an image into a Google Doc from Drive or a URL.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        image_source: Drive file ID or public image URL
        index: Position to insert image (0-based)
        width: Image width in points (optional)
        height: Image height in points (optional)

    Returns:
        str: Confirmation message with insertion details
    """
    logger.info(
        f"[insert_doc_image] Doc={document_id}, source={image_source}, index={index}"
    )

    # Handle the special case where we can't insert at the first section break
    # If index is 0, bump it to 1 to avoid the section break
    if index == 0:
        logger.debug("Adjusting index from 0 to 1 to avoid first section break")
        index = 1

    # Determine if source is a Drive file ID or URL
    is_drive_file = not (
        image_source.startswith("http://") or image_source.startswith("https://")
    )

    if is_drive_file:
        # Verify Drive file exists and get metadata
        try:
            file_metadata = await asyncio.to_thread(
                drive_service.files()
                .get(
                    fileId=image_source,
                    fields="id, name, mimeType",
                    supportsAllDrives=True,
                )
                .execute
            )
            mime_type = file_metadata.get("mimeType", "")
            if not mime_type.startswith("image/"):
                return f"Error: File {image_source} is not an image (MIME type: {mime_type})."

            image_uri = f"https://drive.google.com/uc?id={image_source}"
            source_description = f"Drive file {file_metadata.get('name', image_source)}"
        except Exception as e:
            return f"Error: Could not access Drive file {image_source}: {str(e)}"
    else:
        image_uri = image_source
        source_description = "URL image"

    # Use helper to create image request
    requests = [create_insert_image_request(index, image_uri, width, height)]

    await asyncio.to_thread(
        docs_service.documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute
    )

    size_info = ""
    if width or height:
        size_info = f" (size: {width or 'auto'}x{height or 'auto'} points)"

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Inserted {source_description}{size_info} at index {index} in document {document_id}. Link: {link}"


@server.tool()
@handle_http_errors("update_doc_headers_footers", service_type="docs")
@require_google_service("docs", "docs_write")
async def update_doc_headers_footers(
    service: Any,
    user_google_email: str,
    document_id: str,
    section_type: str,
    content: str,
    header_footer_type: str = "DEFAULT",
) -> str:
    """
    Safely creates or updates header/footer text in a Google Doc.

    This is the default tool for header/footer content. Do NOT use
    batch_update_doc with create_header_footer just to set header/footer text;
    that low-level operation is only for advanced section-break workflows and
    can fail when the default header/footer already exists.

    This tool handles both creation and update in one call:
    - If the header/footer does not exist, it is automatically created first.
    - If the header/footer already exists, its content is replaced.

    You do NOT need to create a header/footer separately before calling this tool.
    Simply call it with the desired content and it will work whether the
    header/footer exists or not.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        section_type: Type of section to create or update ("header" or "footer")
        content: Text content for the header/footer
        header_footer_type: Type of header/footer ("DEFAULT", "FIRST_PAGE_ONLY", "EVEN_PAGE")

    Returns:
        str: Confirmation message with update details
    """
    logger.info(f"[update_doc_headers_footers] Doc={document_id}, type={section_type}")

    # Input validation
    validator = ValidationManager()

    is_valid, error_msg = validator.validate_document_id(document_id)
    if not is_valid:
        return f"Error: {error_msg}"

    is_valid, error_msg = validator.validate_header_footer_params(
        section_type, header_footer_type
    )
    if not is_valid:
        return f"Error: {error_msg}"

    is_valid, error_msg = validator.validate_text_content(content)
    if not is_valid:
        return f"Error: {error_msg}"

    # Use HeaderFooterManager to handle the complex logic
    header_footer_manager = HeaderFooterManager(service)

    success, message = await header_footer_manager.update_header_footer_content(
        document_id, section_type, content, header_footer_type
    )

    if success:
        link = f"https://docs.google.com/document/d/{document_id}/edit"
        return f"{message}. Runtime: {HEADER_FOOTER_RUNTIME_CANARY}. Link: {link}"
    else:
        return f"Error: {message}. Runtime: {HEADER_FOOTER_RUNTIME_CANARY}"


@server.tool()
@handle_http_errors("batch_update_doc", service_type="docs")
@require_google_service("docs", "docs_write")
async def batch_update_doc(
    service: Any,
    user_google_email: str,
    document_id: str,
    operations: BatchDocOperations,
) -> str:
    """
    Executes multiple low-level document operations in a single atomic batch update.

    For normal header/footer text, prefer update_doc_headers_footers.
    Only use create_header_footer here for advanced section-break layouts.

    RECOMMENDED WORKFLOW FOR BUILDING DOCUMENTS:
    =============================================
    To avoid index calculation errors, build documents in phases:

    PHASE 1 - INSERT ALL CONTENT (use end_of_segment=true, no index math):
      Append text, section breaks, and page breaks sequentially.
      Each operation appends to the end of the body. No index needed.
      Example batch: [
        {"type": "insert_text", "end_of_segment": true, "text": "Report Title\\n"},
        {"type": "insert_text", "end_of_segment": true, "text": "\\nExecutive Summary\\n"},
        {"type": "insert_text", "end_of_segment": true, "text": "Revenue grew 15%.\\n"},
        {"type": "insert_section_break", "end_of_segment": true, "section_type": "NEXT_PAGE"},
        {"type": "insert_text", "end_of_segment": true, "text": "Detailed Analysis\\n"}
      ]

    PHASE 2 - CREATE HEADERS/FOOTERS (if needed):
      For normal header/footer text, use update_doc_headers_footers
      (it auto-creates if missing and writes the content for you).
      Only include create_header_footer operations in a batch when you are
      intentionally managing advanced section-break-specific layouts.

    PHASE 3 - INSPECT STRUCTURE:
      Call inspect_doc_structure with detailed=true to get exact start_index
      and end_index for every paragraph and element.

    PHASE 4 - APPLY FORMATTING (separate batch using indices from Phase 3):
      Use the real indices from inspect_doc_structure output: [
        {"type": "update_paragraph_style", "start_index": 1, "end_index": 15,
         "heading_level": 1, "alignment": "CENTER"},
        {"type": "format_text", "start_index": 1, "end_index": 15,
         "bold": true, "font_size": 24},
        {"type": "create_bullet_list", "start_index": 50, "end_index": 120,
         "list_type": "ORDERED"}
      ]

    ALTERNATIVE - FIND_REPLACE PATTERN (no indices needed at all):
      Insert text with unique placeholders, then use find_replace:
      [
        {"type": "insert_text", "end_of_segment": true, "text": "{{TITLE}}\\n{{BODY}}\\n"},
        {"type": "find_replace", "find_text": "{{TITLE}}", "replace_text": "Quarterly Report"},
        {"type": "find_replace", "find_text": "{{BODY}}", "replace_text": "The results show..."}
      ]

    WARNING - AVOID THIS ANTI-PATTERN:
      Do NOT pre-compute sequential indices for multiple insert_text operations.
      Each insertion shifts all subsequent indices and manual calculation is error-prone.
      Use end_of_segment=true instead, which always appends to the current end.
      BAD:  [{"type":"insert_text","index":1,"text":"Hello\\n"},
             {"type":"insert_text","index":8,"text":"World\\n"}]  <- index 8 may be wrong
      GOOD: [{"type":"insert_text","end_of_segment":true,"text":"Hello\\n"},
             {"type":"insert_text","end_of_segment":true,"text":"World\\n"}]

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        operations: List of operation dicts. Each operation MUST have a 'type' field.
                    All operations accept an optional 'tab_id' to target a specific tab.

    Supported operation types and their parameters:

      insert_text      - required: text (str)
                         PREFERRED: end_of_segment (bool) - set true to append at end
                           of body without calculating an index. Safest way to build
                           documents sequentially.
                         optional: index (int), tab_id, segment_id
      delete_text      - required: start_index (int), end_index (int)
                         optional: tab_id, segment_id
      replace_text     - required: start_index (int), end_index (int), text (str)
                         optional: tab_id, segment_id
      format_text      - required: start_index (int), end_index (int)
                         optional: bold, italic, underline, strikethrough, font_size,
                                   font_family, font_weight, text_color, background_color,
                                   link_url, clear_link, baseline_offset, small_caps,
                                   tab_id, segment_id
      update_paragraph_style
                       - required: start_index (int), end_index (int)
                         optional: heading_level (0-6, 0=normal), alignment
                                   (START/CENTER/END/JUSTIFIED), line_spacing,
                                   indent_first_line, indent_start, indent_end,
                                   space_above, space_below, named_style_type,
                                   direction, keep_lines_together, keep_with_next,
                                   avoid_widow_and_orphan, page_break_before,
                                   spacing_mode, shading_color, tab_id, segment_id
      update_table_cell_style
                       - required: table_start_index (int)
                         optional: background_color, border_color, border_width,
                                   padding_top, padding_bottom, padding_left,
                                   padding_right (float, points),
                                   content_alignment ("TOP"|"MIDDLE"|"BOTTOM"),
                                   row_index, column_index, row_span, column_span
                         Use inspect_doc_structure to find table_start_index from
                         table_details[].start_index. If row/column values are
                         omitted, the style is applied to the entire table.
      insert_table     - required: rows (int), columns (int)
                         optional: index (int), tab_id, segment_id, end_of_segment
      insert_table_row - required: table_start_index (int), row_index (int)
                         optional: insert_below (bool, default true), tab_id
      delete_table_row - required: table_start_index (int), row_index (int)
                         optional: tab_id
      insert_table_column
                       - required: table_start_index (int), column_index (int)
                         optional: insert_right (bool, default true), tab_id
      delete_table_column
                       - required: table_start_index (int), column_index (int)
                         optional: tab_id
      merge_table_cells
                       - required: table_start_index (int), row_index (int),
                                   column_index (int), row_span (int), column_span (int)
                         optional: tab_id
      unmerge_table_cells
                       - required: table_start_index (int), row_index (int),
                                   column_index (int), row_span (int), column_span (int)
                         optional: tab_id
      update_table_column_properties
                       - required: table_start_index (int), column_indices (list[int])
                         optional: width (float, points), width_type
                                   (FIXED_WIDTH|EVENLY_DISTRIBUTED), tab_id
      insert_page_break- optional: index (int), end_of_segment, tab_id
      insert_section_break
                       - optional: index (int), end_of_segment, section_type
                                   ('CONTINUOUS'|'NEXT_PAGE')
      find_replace     - required: find_text (str), replace_text (str)
                         optional: match_case (bool, default false)
      create_bullet_list - required: start_index (int), end_index (int)
                         optional: list_type ('UNORDERED'|'ORDERED'|'CHECKBOX'|'NONE',
                                   default UNORDERED), nesting_level (0-8),
                                   paragraph_start_indices (list[int]), bullet_preset
                         Use list_type='NONE' to remove existing bullet/list formatting
      create_named_range
                       - required: name (str), start_index (int), end_index (int)
                         optional: tab_id, segment_id
      replace_named_range_content
                       - required: text (str)
                         optional: named_range_id, named_range_name, tab_id
      delete_named_range
                       - optional: named_range_id, named_range_name, tab_id
      update_document_style
                       - optional: background_color, margin_top, margin_bottom,
                                   margin_left, margin_right, margin_header,
                                   margin_footer, page_width, page_height,
                                   page_number_start, use_even_page_header_footer,
                                   use_first_page_header_footer,
                                   flip_page_orientation, document_mode, tab_id
      update_section_style
                       - required: start_index (int), end_index (int)
                         optional: margin_top, margin_bottom, margin_left,
                                   margin_right, margin_header, margin_footer,
                                   page_number_start, use_first_page_header_footer,
                                   flip_page_orientation, content_direction,
                                   column_count, column_spacing,
                                   column_separator_style
      create_header_footer
                       - required: section_type ('header'|'footer')
                         optional: header_footer_type, section_break_index
                         Advanced only. For ordinary header/footer text, use
                         update_doc_headers_footers instead.
      insert_image     - required: image_uri (str)
                         optional: index, width, height, tab_id, segment_id,
                                   end_of_segment
      insert_doc_tab   - required: title (str), index (int)
                         optional: parent_tab_id (str)
      delete_doc_tab   - required: tab_id (str)
      update_doc_tab   - required: tab_id (str), title (str)

    Example - Safe document building (no index calculation needed):
        [
            {"type": "insert_text", "end_of_segment": true, "text": "Quarterly Report\\n\\n"},
            {"type": "insert_text", "end_of_segment": true, "text": "Executive Summary\\n"},
            {"type": "insert_text", "end_of_segment": true, "text": "Revenue grew 15% YoY.\\n"}
        ]

    Example - Formatting (use exact indices from inspect_doc_structure output):
        [
            {"type": "format_text", "start_index": 1, "end_index": 19, "bold": true, "font_size": 24},
            {"type": "update_paragraph_style", "start_index": 1, "end_index": 19,
             "heading_level": 1, "alignment": "CENTER"},
            {"type": "update_paragraph_style", "start_index": 20, "end_index": 39, "heading_level": 2}
        ]

    Example - Using find_replace (zero index risk):
        [
            {"type": "find_replace", "find_text": "{{DATE}}", "replace_text": "March 2026"},
            {"type": "find_replace", "find_text": "{{AUTHOR}}", "replace_text": "Jane Smith"}
        ]

    Example - Insert table at end of document:
        [{"type": "insert_table", "end_of_segment": true, "rows": 3, "columns": 4}]

    Example - Headers, footers, and document style:
        [
            {"type": "create_header_footer", "section_type": "header"},
            {"type": "create_header_footer", "section_type": "footer"},
            {"type": "update_document_style", "margin_top": 72, "margin_bottom": 72}
        ]

    Returns:
        str: Confirmation message with batch results and document length for chaining
    """
    normalized_operations = [
        operation.model_dump(exclude_none=True)
        if hasattr(operation, "model_dump")
        else operation
        for operation in operations
    ]

    logger.debug(
        f"[batch_update_doc] Doc={document_id}, operations={len(normalized_operations)}"
    )

    # Input validation
    validator = ValidationManager()

    is_valid, error_msg = validator.validate_document_id(document_id)
    if not is_valid:
        return f"Error: {error_msg}"

    is_valid, error_msg = validator.validate_batch_operations(normalized_operations)
    if not is_valid:
        return f"Error: {error_msg}"

    # Use BatchOperationManager to handle the complex logic
    batch_manager = BatchOperationManager(service)

    success, message, metadata = await batch_manager.execute_batch_operations(
        document_id, normalized_operations
    )

    if success:
        link = f"https://docs.google.com/document/d/{document_id}/edit"
        replies_count = metadata.get("replies_count", 0)
        doc_length = metadata.get("document_length")
        length_info = f" Document length: {doc_length}." if doc_length else ""
        return (
            f"{message} on document {document_id}. "
            f"API replies: {replies_count}.{length_info} "
            f"To apply formatting, call inspect_doc_structure to get exact text positions. "
            f"Link: {link}"
        )
    else:
        return f"Error: {message}"


@server.tool()
@handle_http_errors("inspect_doc_structure", is_read_only=True, service_type="docs")
@require_google_service("docs", "docs_read")
async def inspect_doc_structure(
    service: Any,
    user_google_email: str,
    document_id: str,
    detailed: bool = False,
    tab_id: str = None,
) -> str:
    """
    Essential tool for finding safe insertion points and understanding document structure.

    USE THIS FOR:
    - Finding the correct index for table insertion
    - Understanding document layout before making changes
    - Locating existing tables and their positions
    - Getting document statistics and complexity info
    - Inspecting structure of specific tabs

    CRITICAL FOR TABLE OPERATIONS:
    ALWAYS call this BEFORE creating tables to get a safe insertion index.

    WHAT THE OUTPUT SHOWS:
    - total_elements: Number of document elements
    - total_length: Maximum safe index for insertion
    - tables: Number of existing tables
    - table_details: Position and dimensions of each table
    - headers / footers: Real segment IDs and previews for header/footer editing
    - tabs: List of available tabs in the document (if no tab_id specified)

    WORKFLOW FOR TABLE INSERTION:
    Step 1: Call this function
    Step 2: Note the "total_length" value
    Step 3: Use an index < total_length for table insertion
    Step 4: Create your table

    FORMATTING WORKFLOW:
    After inserting all text via batch_update_doc with end_of_segment=true,
    call this tool with detailed=true to get exact start_index and end_index
    for every paragraph. Use those indices directly in format_text and
    update_paragraph_style operations in a second batch_update_doc call.

    HEADER/FOOTER WORKFLOW:
    For ordinary header/footer text, use update_doc_headers_footers.
    If you need low-level segment editing, call this tool first and use the
    real segment_id values returned under headers/footers. Do not invent IDs.

    The detailed output includes elements[].start_index and elements[].end_index
    with text_preview for each paragraph, making it easy to identify which
    ranges to format.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to inspect
        detailed: Whether to return detailed structure information
        tab_id: Optional ID of the tab to inspect. If not provided, inspects main document.

    Returns:
        str: JSON string containing document structure and safe insertion indices
    """
    logger.debug(
        f"[inspect_doc_structure] Doc={document_id}, detailed={detailed}, tab_id={tab_id}"
    )

    # Get the document
    doc = await asyncio.to_thread(
        service.documents().get(documentId=document_id, includeTabsContent=True).execute
    )

    # If tab_id is specified, find the tab and use its content
    target_content = doc.get("body", {})

    def find_tab(tabs, target_id):
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == target_id:
                return tab
            if "childTabs" in tab:
                found = find_tab(tab["childTabs"], target_id)
                if found:
                    return found
        return None

    if tab_id:
        tab = find_tab(doc.get("tabs", []), tab_id)
        if tab and "documentTab" in tab:
            document_tab = tab["documentTab"]
            target_content = document_tab.get("body", {})
            analysis_named_ranges = document_tab.get("namedRanges", {})
        elif tab:
            return f"Error: Tab {tab_id} is not a document tab and has no body content."
        else:
            return f"Error: Tab {tab_id} not found in document."
    else:
        analysis_named_ranges = doc.get("namedRanges", {})
        document_tab = None

    # Create a dummy doc object for analysis tools that expect a full doc
    analysis_doc = doc.copy()
    analysis_doc["body"] = target_content
    analysis_doc["namedRanges"] = analysis_named_ranges
    if tab_id and document_tab:
        analysis_doc["headers"] = document_tab.get("headers", {})
        analysis_doc["footers"] = document_tab.get("footers", {})
        analysis_doc["documentStyle"] = document_tab.get("documentStyle", {})
    elif not tab_id and doc.get("tabs"):
        # Default to the first document tab for tab-aware header/footer inspection.
        def first_document_tab(tabs):
            for candidate in tabs:
                if "documentTab" in candidate:
                    return candidate["documentTab"]
                child = first_document_tab(candidate.get("childTabs", []))
                if child:
                    return child
            return None

        first_tab_doc = first_document_tab(doc.get("tabs", []))
        if first_tab_doc:
            analysis_doc["headers"] = first_tab_doc.get("headers", {})
            analysis_doc["footers"] = first_tab_doc.get("footers", {})
            analysis_doc["documentStyle"] = first_tab_doc.get("documentStyle", {})

    structure = parse_document_structure(analysis_doc)

    if detailed:
        # Return full parsed structure
        # Simplify for JSON serialization
        result = {
            "title": structure["title"],
            "total_length": structure["total_length"],
            "statistics": {
                "elements": len(structure["body"]),
                "tables": len(structure["tables"]),
                "paragraphs": sum(
                    1 for e in structure["body"] if e.get("type") == "paragraph"
                ),
                "section_breaks": len(structure["section_breaks"]),
                "named_ranges": len(structure["named_ranges"]),
                "has_headers": bool(structure["headers"]),
                "has_footers": bool(structure["footers"]),
            },
            "elements": [],
        }

        # Add element summaries
        for element in structure["body"]:
            elem_summary = {
                "type": element["type"],
                "start_index": element["start_index"],
                "end_index": element["end_index"],
            }

            if element["type"] == "table":
                elem_summary["rows"] = element["rows"]
                elem_summary["columns"] = element["columns"]
                elem_summary["cell_count"] = len(element.get("cells", []))
            elif element["type"] == "paragraph":
                elem_summary["text_preview"] = element.get("text", "")[:100]

            result["elements"].append(elem_summary)

        # Add table details
        if structure["tables"]:
            result["tables"] = []
            for i, table in enumerate(structure["tables"]):
                table_data = extract_table_as_data(table)
                result["tables"].append(
                    {
                        "index": i,
                        "position": {
                            "start": table["start_index"],
                            "end": table["end_index"],
                        },
                        "dimensions": {
                            "rows": table["rows"],
                            "columns": table["columns"],
                        },
                        "preview": table_data[:3] if table_data else [],  # First 3 rows
                    }
                )

        if structure["section_breaks"]:
            result["section_breaks"] = [
                {
                    "start_index": section_break["start_index"],
                    "end_index": section_break["end_index"],
                    "section_style": section_break.get("section_style", {}),
                }
                for section_break in structure["section_breaks"]
            ]

        if structure["named_ranges"]:
            result["named_ranges"] = structure["named_ranges"]

    else:
        # Return basic analysis
        result = analyze_document_complexity(analysis_doc)

        # Add table information
        tables = find_tables(analysis_doc)
        if tables:
            result["table_details"] = []
            for i, table in enumerate(tables):
                result["table_details"].append(
                    {
                        "index": i,
                        "rows": table["rows"],
                        "columns": table["columns"],
                        "start_index": table["start_index"],
                        "end_index": table["end_index"],
                    }
                )

    if structure["headers"]:
        result["headers"] = _build_segment_inspection_entries(doc, structure, "header")

    else:
        header_entries = _build_segment_inspection_entries(doc, structure, "header")
        if header_entries:
            result["headers"] = header_entries

    if structure["footers"]:
        result["footers"] = _build_segment_inspection_entries(doc, structure, "footer")
    else:
        footer_entries = _build_segment_inspection_entries(doc, structure, "footer")
        if footer_entries:
            result["footers"] = footer_entries

    # Always include available tabs if no tab_id was specified
    if not tab_id:

        def get_tabs_summary(tabs):
            summary = []
            for tab in tabs:
                props = tab.get("tabProperties", {})
                tab_info = {
                    "title": props.get("title"),
                    "tab_id": props.get("tabId"),
                }
                if "childTabs" in tab:
                    tab_info["child_tabs"] = get_tabs_summary(tab["childTabs"])
                summary.append(tab_info)
            return summary

        result["tabs"] = get_tabs_summary(doc.get("tabs", []))

    if tab_id:
        result["inspected_tab_id"] = tab_id

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Document structure analysis for {document_id}:\n\n{json.dumps(result, indent=2)}\n\nLink: {link}"


def _rewrite_modify_doc_text_http_error(
    error: HttpError, segment_id: Optional[str]
) -> Exception:
    """
    Convert common low-level Docs API failures into actionable caller guidance.
    """
    details = str(error)
    lowered = details.lower()

    if segment_id and "segment with id" in lowered and "was not found" in lowered:
        return UserInputError(
            f"segment_id '{segment_id}' was not found. segment_id must be a real "
            "header/footer/footnote ID returned by inspect_doc_structure; do not "
            "guess IDs such as 'kix.header'. For ordinary header/footer text, use "
            "update_doc_headers_footers instead."
        )

    return error


def _build_segment_inspection_entries(
    doc: dict[str, Any], structure: dict[str, Any], section_type: str
) -> list[dict[str, Any]]:
    """
    Build header/footer inspection entries from both populated segments and style IDs.
    """
    parsed_segments = (
        structure["headers"] if section_type == "header" else structure["footers"]
    )
    entries: dict[str, dict[str, Any]] = {}

    for segment_id, segment_info in parsed_segments.items():
        entries[segment_id] = {
            "segment_id": segment_id,
            "start_index": segment_info["start_index"],
            "end_index": segment_info["end_index"],
            "content_preview": segment_info.get("text_preview", ""),
            "element_count": segment_info.get("element_count", 0),
            "source": "segment_content",
        }

    style_field_map = {
        "header": {
            "DEFAULT": "defaultHeaderId",
            "FIRST_PAGE_ONLY": "firstPageHeaderId",
            "EVEN_PAGE": "evenPageHeaderId",
        },
        "footer": {
            "DEFAULT": "defaultFooterId",
            "FIRST_PAGE_ONLY": "firstPageFooterId",
            "EVEN_PAGE": "evenPageFooterId",
        },
    }

    for variant, style_field in style_field_map[section_type].items():
        doc_style_id = doc.get("documentStyle", {}).get(style_field)
        if doc_style_id and doc_style_id not in entries:
            entries[doc_style_id] = {
                "segment_id": doc_style_id,
                "start_index": 0,
                "end_index": 0,
                "content_preview": "",
                "element_count": 0,
                "source": f"documentStyle.{style_field}",
                "variant": variant,
            }

        for element in doc.get("body", {}).get("content", []):
            section_style = element.get("sectionBreak", {}).get("sectionStyle", {})
            if not section_style:
                continue
            section_id = section_style.get(style_field)
            if section_id and section_id not in entries:
                entries[section_id] = {
                    "segment_id": section_id,
                    "start_index": 0,
                    "end_index": 0,
                    "content_preview": "",
                    "element_count": 0,
                    "source": f"sectionStyle.{style_field}",
                    "variant": variant,
                }
            break

    return list(entries.values())


@server.tool()
@handle_http_errors("debug_docs_runtime_info", is_read_only=True, service_type="docs")
@require_google_service("docs", "docs_read")
async def debug_docs_runtime_info(
    service: Any,
    user_google_email: str,
) -> str:
    """
    Return runtime/source information for diagnosing stale MCP server instances.

    This is a temporary diagnostic tool intended to verify which code checkout
    the running MCP server has loaded.
    """
    import gdocs.managers.header_footer_manager as header_footer_manager_module

    return json.dumps(
        {
            "runtime_canary": HEADER_FOOTER_RUNTIME_CANARY,
            "docs_tools_file": inspect.getsourcefile(
                inspect.getmodule(debug_docs_runtime_info)
            ),
            "header_footer_manager_file": inspect.getsourcefile(
                header_footer_manager_module
            ),
        },
        indent=2,
    )


@server.tool()
@handle_http_errors("create_table_with_data", service_type="docs")
@require_google_service("docs", "docs_write")
async def create_table_with_data(
    service: Any,
    user_google_email: str,
    document_id: str,
    table_data: List[List[str]],
    index: int,
    bold_headers: bool = True,
    tab_id: Optional[str] = None,
) -> str:
    """
    Creates a table and populates it with data in one reliable operation.

    CRITICAL: YOU MUST CALL inspect_doc_structure FIRST TO GET THE INDEX!

    MANDATORY WORKFLOW - DO THESE STEPS IN ORDER:

    Step 1: ALWAYS call inspect_doc_structure first
    Step 2: Use the 'total_length' value from inspect_doc_structure as your index
    Step 3: Format data as 2D list: [["col1", "col2"], ["row1col1", "row1col2"]]
    Step 4: Call this function with the correct index and data

    EXAMPLE DATA FORMAT:
    table_data = [
        ["Header1", "Header2", "Header3"],    # Row 0 - headers
        ["Data1", "Data2", "Data3"],          # Row 1 - first data row
        ["Data4", "Data5", "Data6"]           # Row 2 - second data row
    ]

    CRITICAL INDEX REQUIREMENTS:
    - NEVER use index values like 1, 2, 10 without calling inspect_doc_structure first
    - ALWAYS get index from inspect_doc_structure 'total_length' field
    - Index must be a valid insertion point in the document

    DATA FORMAT REQUIREMENTS:
    - Must be 2D list of strings only
    - Each inner list = one table row
    - All rows MUST have same number of columns
    - Use empty strings "" for empty cells, never None
    - Use debug_table_structure after creation to verify results

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        table_data: 2D list of strings - EXACT format: [["col1", "col2"], ["row1col1", "row1col2"]]
        index: Document position (MANDATORY: get from inspect_doc_structure 'total_length')
        bold_headers: Whether to make first row bold (default: true)
        tab_id: Optional tab ID to create the table in a specific tab

    Returns:
        str: Confirmation with table details and link
    """
    logger.debug(f"[create_table_with_data] Doc={document_id}, index={index}")

    # Input validation
    validator = ValidationManager()

    is_valid, error_msg = validator.validate_document_id(document_id)
    if not is_valid:
        return f"ERROR: {error_msg}"

    is_valid, error_msg = validator.validate_table_data(table_data)
    if not is_valid:
        return f"ERROR: {error_msg}"

    is_valid, error_msg = validator.validate_index(index, "Index")
    if not is_valid:
        return f"ERROR: {error_msg}"

    # Use TableOperationManager to handle the complex logic
    table_manager = TableOperationManager(service)

    # Try to create the table, and if it fails due to index being at document end, retry with index-1
    success, message, metadata = await table_manager.create_and_populate_table(
        document_id, table_data, index, bold_headers, tab_id
    )

    # If it failed due to index being at or beyond document end, retry with adjusted index
    if not success and "must be less than the end index" in message:
        logger.debug(
            f"Index {index} is at document boundary, retrying with index {index - 1}"
        )
        success, message, metadata = await table_manager.create_and_populate_table(
            document_id, table_data, index - 1, bold_headers, tab_id
        )

    if success:
        link = f"https://docs.google.com/document/d/{document_id}/edit"
        rows = metadata.get("rows", 0)
        columns = metadata.get("columns", 0)

        return (
            f"SUCCESS: {message}. Table: {rows}x{columns}, Index: {index}. Link: {link}"
        )
    else:
        return f"ERROR: {message}"


@server.tool()
@handle_http_errors("debug_table_structure", is_read_only=True, service_type="docs")
@require_google_service("docs", "docs_read")
async def debug_table_structure(
    service: Any,
    user_google_email: str,
    document_id: str,
    table_index: int = 0,
) -> str:
    """
    ESSENTIAL DEBUGGING TOOL - Use this whenever tables don't work as expected.

    USE THIS IMMEDIATELY WHEN:
    - Table population put data in wrong cells
    - You get "table not found" errors
    - Data appears concatenated in first cell
    - Need to understand existing table structure
    - Planning to use populate_existing_table

    WHAT THIS SHOWS YOU:
    - Exact table dimensions (rows × columns)
    - Each cell's position coordinates (row,col)
    - Current content in each cell
    - Insertion indices for each cell
    - Table boundaries and ranges

    HOW TO READ THE OUTPUT:
    - "dimensions": "2x3" = 2 rows, 3 columns
    - "position": "(0,0)" = first row, first column
    - "current_content": What's actually in each cell right now
    - "insertion_index": Where new text would be inserted in that cell

    WORKFLOW INTEGRATION:
    1. After creating table → Use this to verify structure
    2. Before populating → Use this to plan your data format
    3. After population fails → Use this to see what went wrong
    4. When debugging → Compare your data array to actual table structure

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to inspect
        table_index: Which table to debug (0 = first table, 1 = second table, etc.)

    Returns:
        str: Detailed JSON structure showing table layout, cell positions, and current content
    """
    logger.debug(
        f"[debug_table_structure] Doc={document_id}, table_index={table_index}"
    )

    # Get the document
    doc = await asyncio.to_thread(
        service.documents().get(documentId=document_id).execute
    )

    # Find tables
    tables = find_tables(doc)
    if table_index >= len(tables):
        return f"Error: Table index {table_index} not found. Document has {len(tables)} table(s)."

    table_info = tables[table_index]

    # Extract detailed cell information
    debug_info = {
        "table_index": table_index,
        "dimensions": f"{table_info['rows']}x{table_info['columns']}",
        "table_range": f"[{table_info['start_index']}-{table_info['end_index']}]",
        "cells": [],
    }

    for row_idx, row in enumerate(table_info["cells"]):
        row_info = []
        for col_idx, cell in enumerate(row):
            cell_debug = {
                "position": f"({row_idx},{col_idx})",
                "range": f"[{cell['start_index']}-{cell['end_index']}]",
                "insertion_index": cell.get("insertion_index", "N/A"),
                "current_content": repr(cell.get("content", "")),
                "content_elements_count": len(cell.get("content_elements", [])),
            }
            row_info.append(cell_debug)
        debug_info["cells"].append(row_info)

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Table structure debug for table {table_index}:\n\n{json.dumps(debug_info, indent=2)}\n\nLink: {link}"


@server.tool()
@handle_http_errors("export_doc_to_pdf", service_type="drive")
@require_google_service("drive", "drive_file")
async def export_doc_to_pdf(
    service: Any,
    user_google_email: str,
    document_id: str,
    pdf_filename: str = None,
    folder_id: str = None,
) -> str:
    """
    Exports a Google Doc to PDF format and saves it to Google Drive.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc to export
        pdf_filename: Name for the PDF file (optional - if not provided, uses original name + "_PDF")
        folder_id: Drive folder ID to save PDF in (optional - if not provided, saves in root)

    Returns:
        str: Confirmation message with PDF file details and links
    """
    logger.info(
        f"[export_doc_to_pdf] Email={user_google_email}, Doc={document_id}, pdf_filename={pdf_filename}, folder_id={folder_id}"
    )

    # Get file metadata first to validate it's a Google Doc
    try:
        file_metadata = await asyncio.to_thread(
            service.files()
            .get(
                fileId=document_id,
                fields="id, name, mimeType, webViewLink",
                supportsAllDrives=True,
            )
            .execute
        )
    except Exception as e:
        return f"Error: Could not access document {document_id}: {str(e)}"

    mime_type = file_metadata.get("mimeType", "")
    original_name = file_metadata.get("name", "Unknown Document")
    web_view_link = file_metadata.get("webViewLink", "#")

    # Verify it's a Google Doc
    if mime_type != "application/vnd.google-apps.document":
        return f"Error: File '{original_name}' is not a Google Doc (MIME type: {mime_type}). Only native Google Docs can be exported to PDF."

    logger.info(f"[export_doc_to_pdf] Exporting '{original_name}' to PDF")

    # Export the document as PDF
    try:
        request_obj = service.files().export_media(
            fileId=document_id, mimeType="application/pdf"
        )

        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request_obj)

        done = False
        while not done:
            _, done = await asyncio.to_thread(downloader.next_chunk)

        pdf_content = fh.getvalue()
        pdf_size = len(pdf_content)

    except Exception as e:
        return f"Error: Failed to export document to PDF: {str(e)}"

    # Determine PDF filename
    if not pdf_filename:
        pdf_filename = f"{original_name}_PDF.pdf"
    elif not pdf_filename.endswith(".pdf"):
        pdf_filename += ".pdf"

    # Upload PDF to Drive
    try:
        # Reuse the existing BytesIO object by resetting to the beginning
        fh.seek(0)
        # Create media upload object
        media = MediaIoBaseUpload(fh, mimetype="application/pdf", resumable=True)

        # Prepare file metadata for upload
        file_metadata = {"name": pdf_filename, "mimeType": "application/pdf"}

        # Add parent folder if specified
        if folder_id:
            file_metadata["parents"] = [folder_id]

        # Upload the file
        uploaded_file = await asyncio.to_thread(
            service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, name, webViewLink, parents",
                supportsAllDrives=True,
            )
            .execute
        )

        pdf_file_id = uploaded_file.get("id")
        pdf_web_link = uploaded_file.get("webViewLink", "#")
        pdf_parents = uploaded_file.get("parents", [])

        logger.info(
            f"[export_doc_to_pdf] Successfully uploaded PDF to Drive: {pdf_file_id}"
        )

        folder_info = ""
        if folder_id:
            folder_info = f" in folder {folder_id}"
        elif pdf_parents:
            folder_info = f" in folder {pdf_parents[0]}"

        return f"Successfully exported '{original_name}' to PDF and saved to Drive as '{pdf_filename}' (ID: {pdf_file_id}, {pdf_size:,} bytes){folder_info}. PDF: {pdf_web_link} | Original: {web_view_link}"

    except Exception as e:
        return f"Error: Failed to upload PDF to Drive: {str(e)}. PDF was generated successfully ({pdf_size:,} bytes) but could not be saved to Drive."


# ==============================================================================
# STYLING TOOLS - Paragraph Formatting
# ==============================================================================


async def _get_paragraph_start_indices_in_range(
    service: Any, document_id: str, start_index: int, end_index: int
) -> list[int]:
    """
    Fetch paragraph start indices that overlap a target range.
    """
    doc_data = await asyncio.to_thread(
        service.documents()
        .get(
            documentId=document_id,
            fields="body/content(startIndex,endIndex,paragraph)",
        )
        .execute
    )

    paragraph_starts = []
    for element in doc_data.get("body", {}).get("content", []):
        if "paragraph" not in element:
            continue
        paragraph_start = element.get("startIndex")
        paragraph_end = element.get("endIndex")
        if not isinstance(paragraph_start, int) or not isinstance(paragraph_end, int):
            continue
        if paragraph_end > start_index and paragraph_start < end_index:
            paragraph_starts.append(paragraph_start)

    return paragraph_starts or [start_index]


@server.tool()
@handle_http_errors("update_paragraph_style", service_type="docs")
@require_google_service("docs", "docs_write")
async def update_paragraph_style(
    service: Any,
    user_google_email: str,
    document_id: str,
    start_index: int,
    end_index: int,
    heading_level: int = None,
    alignment: str = None,
    line_spacing: float = None,
    indent_first_line: float = None,
    indent_start: float = None,
    indent_end: float = None,
    space_above: float = None,
    space_below: float = None,
    named_style_type: str = None,
    tab_id: str = None,
    segment_id: str = None,
    direction: str = None,
    keep_lines_together: bool = None,
    keep_with_next: bool = None,
    avoid_widow_and_orphan: bool = None,
    page_break_before: bool = None,
    spacing_mode: str = None,
    shading_color: str = None,
    list_type: str = None,
    list_nesting_level: int = None,
    bullet_preset: str = None,
) -> str:
    """
    Apply paragraph-level formatting, heading styles, and/or list formatting to a range in a Google Doc.

    This tool can apply named heading styles (H1-H6) for semantic document structure,
    create bulleted or numbered lists with nested indentation, and customize paragraph
    properties like alignment, spacing, and indentation. All operations can be applied
    in a single call.

    Args:
        user_google_email: User's Google email address
        document_id: Document ID to modify
        start_index: Start position using Docs API indices from
            inspect_doc_structure. For the main body, 0 is also accepted as an
            alias for the first writable position.
        end_index: End position (exclusive) - should cover the entire paragraph
        heading_level: Heading level 0-6 (0 = NORMAL_TEXT, 1 = H1, 2 = H2, etc.)
                       Use for semantic document structure
        alignment: Text alignment - 'START' (left), 'CENTER', 'END' (right), or 'JUSTIFIED'
        line_spacing: Line spacing multiplier (1.0 = single, 1.5 = 1.5x, 2.0 = double)
        indent_first_line: First line indent in points (e.g., 36 for 0.5 inch)
        indent_start: Left/start indent in points
        indent_end: Right/end indent in points
        space_above: Space above paragraph in points (e.g., 12 for one line)
        space_below: Space below paragraph in points
        named_style_type: Direct named style type - 'NORMAL_TEXT', 'TITLE', 'SUBTITLE',
                         'HEADING_1' through 'HEADING_6'. Mutually exclusive with heading_level.
        tab_id: Optional document tab ID to target
        segment_id: Optional header/footer/footnote segment ID to target
        direction: Paragraph direction - 'LEFT_TO_RIGHT' or 'RIGHT_TO_LEFT'
        keep_lines_together: Keep all lines of the paragraph together
        keep_with_next: Keep the paragraph with the next paragraph
        avoid_widow_and_orphan: Avoid widows/orphans for the paragraph
        page_break_before: Start the paragraph on a new page
        spacing_mode: 'NEVER_COLLAPSE' or 'COLLAPSE_LISTS'
        shading_color: Paragraph shading/background color (#RRGGBB)
        list_type: Create a list from existing paragraphs ('UNORDERED' for bullets, 'ORDERED' for numbers, 'CHECKBOX' for checklists)
        list_nesting_level: Nesting level for lists (0-8, where 0 is top level, default is 0)
                           Use higher levels for nested/indented list items
        bullet_preset: Optional explicit Google Docs bullet preset

    Returns:
        str: Confirmation message with formatting details

    Examples:
        # Apply H1 heading style
        update_paragraph_style(document_id="...", start_index=1, end_index=20, heading_level=1)

        # Create a bulleted list
        update_paragraph_style(document_id="...", start_index=1, end_index=50,
                               list_type="UNORDERED")

        # Create a nested numbered list item
        update_paragraph_style(document_id="...", start_index=1, end_index=30,
                               list_type="ORDERED", list_nesting_level=1)

        # Apply H2 heading with custom spacing
        update_paragraph_style(document_id="...", start_index=1, end_index=30,
                               heading_level=2, space_above=18, space_below=12)

        # Center-align a paragraph with double spacing
        update_paragraph_style(document_id="...", start_index=1, end_index=50,
                               alignment="CENTER", line_spacing=2.0)
    """
    logger.info(
        f"[update_paragraph_style] Doc={document_id}, Range: {start_index}-{end_index}"
    )

    # Validate range
    if start_index < 0:
        return "Error: start_index must be >= 0"
    if end_index <= start_index:
        return "Error: end_index must be greater than start_index"

    # Validate list parameters
    list_type_value = list_type
    if list_type_value is not None:
        # Coerce non-string inputs to string before normalization to avoid AttributeError
        if not isinstance(list_type_value, str):
            list_type_value = str(list_type_value)
        valid_list_types = ["UNORDERED", "ORDERED", "CHECKBOX"]
        normalized_list_type = list_type_value.upper()
        if normalized_list_type not in valid_list_types:
            return f"Error: list_type must be one of: {', '.join(valid_list_types)}"

        list_type_value = normalized_list_type

    if list_nesting_level is not None:
        if list_type_value is None:
            return "Error: list_nesting_level requires list_type parameter"
        if not isinstance(list_nesting_level, int):
            return "Error: list_nesting_level must be an integer"
        if list_nesting_level < 0 or list_nesting_level > 8:
            return "Error: list_nesting_level must be between 0 and 8"

    # Validate named_style_type
    if named_style_type is not None and heading_level is not None:
        return "Error: heading_level and named_style_type are mutually exclusive; provide only one"

    validator = ValidationManager()
    is_valid, error_msg = validator.validate_paragraph_style_params(
        heading_level=heading_level,
        alignment=alignment,
        line_spacing=line_spacing,
        indent_first_line=indent_first_line,
        indent_start=indent_start,
        indent_end=indent_end,
        space_above=space_above,
        space_below=space_below,
        named_style_type=named_style_type,
        direction=direction,
        keep_lines_together=keep_lines_together,
        keep_with_next=keep_with_next,
        avoid_widow_and_orphan=avoid_widow_and_orphan,
        page_break_before=page_break_before,
        spacing_mode=spacing_mode,
        shading_color=shading_color,
    )
    if not is_valid and list_type_value is None:
        return f"Error: {error_msg}"

    # Create batch update requests
    requests = []

    # Add paragraph style update if we have any style changes
    paragraph_style_request = create_update_paragraph_style_request(
        start_index,
        end_index,
        heading_level,
        alignment,
        line_spacing,
        indent_first_line,
        indent_start,
        indent_end,
        space_above,
        space_below,
        tab_id,
        named_style_type,
        segment_id,
        direction,
        keep_lines_together,
        keep_with_next,
        avoid_widow_and_orphan,
        page_break_before,
        spacing_mode,
        shading_color,
    )
    if paragraph_style_request:
        requests.append(paragraph_style_request)

    # Add list creation if requested
    if list_type_value is not None:
        # Default to level 0 if not specified
        nesting_level = list_nesting_level if list_nesting_level is not None else 0
        try:
            paragraph_start_indices = None
            if nesting_level > 0:
                paragraph_start_indices = await _get_paragraph_start_indices_in_range(
                    service, document_id, start_index, end_index
                )
            list_requests = create_bullet_list_request(
                start_index,
                end_index,
                list_type_value,
                nesting_level,
                paragraph_start_indices=paragraph_start_indices,
                doc_tab_id=tab_id,
                bullet_preset=bullet_preset,
                segment_id=segment_id,
            )
            requests.extend(list_requests)
        except ValueError as e:
            return f"Error: {e}"

    # Validate we have at least one operation
    if not requests:
        return f"No paragraph style changes or list creation specified for document {document_id}"

    await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body={"requests": requests})
        .execute
    )

    # Build summary
    summary_parts = []
    if named_style_type is not None:
        summary_parts.append(named_style_type)
    elif heading_level is not None:
        summary_parts.append(
            "NORMAL_TEXT" if heading_level == 0 else f"HEADING_{heading_level}"
        )
    detail_labels = [
        name
        for name, value in [
            ("alignment", alignment),
            ("line_spacing", line_spacing),
            ("indent_first_line", indent_first_line),
            ("indent_start", indent_start),
            ("indent_end", indent_end),
            ("space_above", space_above),
            ("space_below", space_below),
            ("direction", direction),
            ("keep_lines_together", keep_lines_together),
            ("keep_with_next", keep_with_next),
            ("avoid_widow_and_orphan", avoid_widow_and_orphan),
            ("page_break_before", page_break_before),
            ("spacing_mode", spacing_mode),
            ("shading_color", shading_color),
        ]
        if value is not None
    ]
    if detail_labels:
        summary_parts.append(", ".join(detail_labels))
    if list_type_value is not None:
        list_desc = f"{list_type_value.lower()} list"
        if list_nesting_level is not None and list_nesting_level > 0:
            list_desc += f" (level {list_nesting_level})"
        if bullet_preset is not None:
            list_desc += f" using {bullet_preset}"
        summary_parts.append(list_desc)

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Applied paragraph formatting ({', '.join(summary_parts)}) to range {start_index}-{end_index} in document {document_id}. Link: {link}"


@server.tool()
@handle_http_errors("get_doc_as_markdown", is_read_only=True, service_type="docs")
@require_multiple_services(
    [
        {
            "service_type": "drive",
            "scopes": "drive_read",
            "param_name": "drive_service",
        },
        {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"},
    ]
)
async def get_doc_as_markdown(
    drive_service: Any,
    docs_service: Any,
    user_google_email: str,
    document_id: str,
    include_comments: bool = True,
    comment_mode: str = "inline",
    include_resolved: bool = False,
    suggestions_view_mode: str = "DEFAULT_FOR_CURRENT_ACCESS",
) -> str:
    """
    Reads a Google Doc and returns it as clean Markdown with optional comment context.

    Unlike get_doc_content which returns plain text, this tool preserves document
    formatting as Markdown: headings, bold/italic/strikethrough, links, code spans,
    ordered/unordered lists with nesting, and tables.

    When comments are included (the default), each comment's anchor text — the specific
    text the comment was attached to — is preserved, giving full context for the discussion.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the Google Doc (or full URL)
        include_comments: Whether to include comments (default: True)
        comment_mode: How to display comments:
            - "inline": Footnote-style references placed at the anchor text location (default)
            - "appendix": All comments grouped at the bottom with blockquoted anchor text
            - "none": No comments included
        include_resolved: Whether to include resolved comments (default: False)
        suggestions_view_mode: How to render suggestions in the returned content:
            - "DEFAULT_FOR_CURRENT_ACCESS": Default based on user's access level
            - "SUGGESTIONS_INLINE": Suggested changes appear inline in the document
            - "PREVIEW_SUGGESTIONS_ACCEPTED": Preview as if all suggestions were accepted
            - "PREVIEW_WITHOUT_SUGGESTIONS": Preview as if all suggestions were rejected

    Returns:
        str: The document content as Markdown, optionally with comments
    """
    # Extract doc ID from URL if a full URL was provided
    url_match = re.search(r"/d/([\w-]+)", document_id)
    if url_match:
        document_id = url_match.group(1)

    valid_modes = ("inline", "appendix", "none")
    if comment_mode not in valid_modes:
        return f"Error: comment_mode must be one of {valid_modes}, got '{comment_mode}'"

    validation_error = validate_suggestions_view_mode(suggestions_view_mode)
    if validation_error:
        return validation_error

    logger.info(
        f"[get_doc_as_markdown] Doc={document_id}, comments={include_comments}, mode={comment_mode}"
    )

    # Fetch document content via Docs API (includeTabsContent for multi-tab docs)
    try:
        doc = await asyncio.wait_for(
            asyncio.to_thread(
                docs_service.documents()
                .get(
                    documentId=document_id,
                    includeTabsContent=True,
                    suggestionsViewMode=suggestions_view_mode,
                )
                .execute
            ),
            timeout=30,
        )
    except (TimeoutError, asyncio.TimeoutError):
        return (
            f"Error: Timed out fetching document {document_id} from Google Docs API. "
            "The document may be too large or there may be a network issue. Please try again."
        )

    markdown = convert_doc_to_markdown(doc)

    if not include_comments or comment_mode == "none":
        return markdown

    # Fetch comments via Drive API
    all_comments = []
    page_token = None

    while True:
        response = await asyncio.to_thread(
            drive_service.comments()
            .list(
                fileId=document_id,
                fields="comments(id,content,author,createdTime,modifiedTime,"
                "resolved,quotedFileContent,"
                "replies(id,content,author,createdTime,modifiedTime)),"
                "nextPageToken",
                includeDeleted=False,
                pageToken=page_token,
            )
            .execute
        )
        all_comments.extend(response.get("comments", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    comments = parse_drive_comments(
        {"comments": all_comments}, include_resolved=include_resolved
    )

    if not comments:
        return markdown

    if comment_mode == "inline":
        return format_comments_inline(markdown, comments)
    else:
        appendix = format_comments_appendix(comments)
        return markdown.rstrip("\n") + "\n\n" + appendix


@server.tool()
@handle_http_errors("insert_doc_tab", service_type="docs")
@require_google_service("docs", "docs_write")
async def insert_doc_tab(
    service: Any,
    user_google_email: str,
    document_id: str,
    title: str,
    index: int,
    parent_tab_id: Optional[str] = None,
) -> str:
    """
    Inserts a new tab into a Google Doc.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        title: Title of the new tab
        index: Position index for the new tab (0-based among sibling tabs)
        parent_tab_id: Optional ID of a parent tab to nest the new tab under

    Returns:
        str: Confirmation message with document link
    """
    logger.info(f"[insert_doc_tab] Doc={document_id}, title='{title}', index={index}")

    request = create_insert_doc_tab_request(title, index, parent_tab_id)
    result = await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body={"requests": [request]})
        .execute
    )

    # Extract the new tab ID from the batchUpdate response
    tab_id = None
    if "replies" in result and result["replies"]:
        reply = result["replies"][0]
        if "createDocumentTab" in reply:
            tab_id = reply["createDocumentTab"].get("tabProperties", {}).get("tabId")

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    msg = f"Inserted tab '{title}' at index {index} in document {document_id}."
    if tab_id:
        msg += f" Tab ID: {tab_id}."
    if parent_tab_id:
        msg += f" Nested under parent tab {parent_tab_id}."
    return f"{msg} Link: {link}"


@server.tool()
@handle_http_errors("delete_doc_tab", service_type="docs")
@require_google_service("docs", "docs_write")
async def delete_doc_tab(
    service: Any,
    user_google_email: str,
    document_id: str,
    tab_id: str,
) -> str:
    """
    Deletes a tab from a Google Doc by its tab ID.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        tab_id: ID of the tab to delete (use inspect_doc_structure to find tab IDs)

    Returns:
        str: Confirmation message with document link
    """
    logger.info(f"[delete_doc_tab] Doc={document_id}, tab_id='{tab_id}'")

    request = create_delete_doc_tab_request(tab_id)
    await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body={"requests": [request]})
        .execute
    )

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return f"Deleted tab '{tab_id}' from document {document_id}. Link: {link}"


@server.tool()
@handle_http_errors("update_doc_tab", service_type="docs")
@require_google_service("docs", "docs_write")
async def update_doc_tab(
    service: Any,
    user_google_email: str,
    document_id: str,
    tab_id: str,
    title: str,
) -> str:
    """
    Renames a tab in a Google Doc.

    Args:
        user_google_email: User's Google email address
        document_id: ID of the document to update
        tab_id: ID of the tab to rename (use inspect_doc_structure to find tab IDs)
        title: New title for the tab

    Returns:
        str: Confirmation message with document link
    """
    logger.info(
        f"[update_doc_tab] Doc={document_id}, tab_id='{tab_id}', title='{title}'"
    )

    request = create_update_doc_tab_request(tab_id, title)
    await asyncio.to_thread(
        service.documents()
        .batchUpdate(documentId=document_id, body={"requests": [request]})
        .execute
    )

    link = f"https://docs.google.com/document/d/{document_id}/edit"
    return (
        f"Renamed tab '{tab_id}' to '{title}' in document {document_id}. Link: {link}"
    )


# Create comment management tools for documents
_comment_tools = create_comment_tools("document", "document_id")

# Extract and register the functions
list_document_comments = _comment_tools["list_comments"]
manage_document_comment = _comment_tools["manage_comment"]
