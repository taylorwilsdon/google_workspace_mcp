"""
Google Gmail MCP Tools

This module provides MCP tools for interacting with the Gmail API.
"""

import logging
import asyncio
import base64
import ssl
from typing import Optional, List, Dict, Literal , Any , Tuple
from pathlib import Path
import json
import io

from email.mime.text import MIMEText

from fastapi import Body

from auth.service_decorator import require_google_service
from core.utils import handle_http_errors
from core.server import (
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_LABELS_SCOPE,
    server,
)

logger = logging.getLogger(__name__)

try:
    import pdfplumber
    PDF_AVAILABLE = True
    PDF_LIBRARY = "pdfplumber"
except ImportError:
    PDF_AVAILABLE = False
    PDF_LIBRARY = None

try:
    import mammoth
    DOCX_AVAILABLE = True
    DOCX_LIBRARY = "mammoth"
except ImportError:
    DOCX_AVAILABLE = False
    DOCX_LIBRARY = None

try:
    import pyxlsb
    import pandas as pd
    EXCEL_AVAILABLE = True
    EXCEL_LIBRARY = "pyxlsb"
except ImportError:
    EXCEL_AVAILABLE = False
    EXCEL_LIBRARY = None

try:
    from bs4 import BeautifulSoup
    HTML_AVAILABLE = True
except ImportError:
    HTML_AVAILABLE = False

try:
    import csv
    CSV_AVAILABLE = True
except ImportError:
    CSV_AVAILABLE = False



GMAIL_BATCH_SIZE = 25
GMAIL_REQUEST_DELAY = 0.1



def _extract_message_body(payload):
    """
    Helper function to extract plain text body from a Gmail message payload.

    Args:
        payload (dict): The message payload from Gmail API

    Returns:
        str: The plain text body content, or empty string if not found
    """
    body_data = ""
    parts = [payload] if "parts" not in payload else payload.get("parts", [])

    part_queue = list(parts)  # Use a queue for BFS traversal of parts
    while part_queue:
        part = part_queue.pop(0)
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            data = base64.urlsafe_b64decode(part["body"]["data"])
            body_data = data.decode("utf-8", errors="ignore")
            break  # Found plain text body
        elif part.get("mimeType", "").startswith("multipart/") and "parts" in part:
            part_queue.extend(part.get("parts", []))  # Add sub-parts to the queue

    # If no plain text found, check the main payload body if it exists
    if (
        not body_data
        and payload.get("mimeType") == "text/plain"
        and payload.get("body", {}).get("data")
    ):
        data = base64.urlsafe_b64decode(payload["body"]["data"])
        body_data = data.decode("utf-8", errors="ignore")

    return body_data


def _extract_message_body(payload):
    """
    Helper function to extract plain text body from a Gmail message payload.

    Args:
        payload (dict): The message payload from Gmail API

    Returns:
        str: The plain text body content, or empty string if not found
    """
    body_data = ""
    parts = [payload] if "parts" not in payload else payload.get("parts", [])

    part_queue = list(parts)  # Use a queue for BFS traversal of parts
    while part_queue:
        part = part_queue.pop(0)
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            data = base64.urlsafe_b64decode(part["body"]["data"])
            body_data = data.decode("utf-8", errors="ignore")
            break  # Found plain text body
        elif part.get("mimeType", "").startswith("multipart/") and "parts" in part:
            part_queue.extend(part.get("parts", []))  # Add sub-parts to the queue

    # If no plain text found, check the main payload body if it exists
    if (
        not body_data
        and payload.get("mimeType") == "text/plain"
        and payload.get("body", {}).get("data")
    ):
        data = base64.urlsafe_b64decode(payload["body"]["data"])
        body_data = data.decode("utf-8", errors="ignore")

    return body_data


def _extract_attachments(payload: Dict, message_id: str) -> List[Dict[str, Any]]:
    """Extract attachment information from message payload"""
    attachments = []
   
    def extract_from_part(part):
        filename = part.get('filename', '')
        body = part.get('body', {})
        
        if filename and body.get('attachmentId'):  # This part has an attachment
            attachment_id = body.get('attachmentId')
            attachments.append({
                'attachment_id': attachment_id,
                'filename': filename,
                'mime_type': part.get('mimeType', ''),
                'size': body.get('size', 0),
                'message_id': message_id
            })
               
        if 'parts' in part:
            for subpart in part['parts']:
                extract_from_part(subpart)
               
    extract_from_part(payload)
    return attachments



def _extract_body_content(payload: Dict) -> Tuple[str, str]:
    """Extract text and HTML body content from message payload"""
    text_content = ""
    html_content = ""
   
    def extract_from_part(part):
        nonlocal text_content, html_content
        mime_type = part.get('mimeType', '')
        body = part.get('body', {})
       
        if mime_type == 'text/plain' and body.get('data'):
            text_content = base64.urlsafe_b64decode(body['data']).decode('utf-8', errors='ignore')
        elif mime_type == 'text/html' and body.get('data'):
            html_content = base64.urlsafe_b64decode(body['data']).decode('utf-8', errors='ignore')
        elif 'parts' in part:
            for subpart in part['parts']:
                extract_from_part(subpart)
               
    extract_from_part(payload)
    return text_content, html_content


@server.tool()
@handle_http_errors("get_gmail_message_content_with_attachments", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_message_content_with_attachments(
    service, message_id: str, user_google_email: str
) -> Dict[str, Any]:
    """
    Retrieves the full content of a Gmail message including attachments info.

    Args:
        service: The Gmail API service object
        message_id (str): The unique ID of the Gmail message to retrieve.
        user_google_email (str): The user's Google email address. Required.

    Returns:
        dict: Message details including subject, sender, body content, and attachments list.
    """
    logger.info(
        f"[get_gmail_message_content_with_attachments] Message ID: '{message_id}', Email: '{user_google_email}'"
    )

    # Fetch the full message to get headers, body, and attachments
    message_full = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",  # Request full payload
        )
        .execute
    )

    # Extract headers
    headers = {
        h["name"]: h["value"]
        for h in message_full.get("payload", {}).get("headers", [])
    }
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")
    recipient = headers.get("To", "(unknown recipient)")

    # Extract body content (both text and HTML)
    payload = message_full.get("payload", {})
    text_body, html_body = _extract_body_content(payload)
   
    # Fallback to old method if new method doesn't find text
    if not text_body:
        text_body = _extract_message_body(payload)

    # Extract attachments
    attachments = _extract_attachments(payload, message_id)

    return {
        "message_id": message_id,
        "subject": subject,
        "sender": sender,
        "recipient": recipient,
        "text_body": text_body or '[No text body found]',
        "html_body": html_body,
        "attachments": attachments,
        "attachment_count": len(attachments),
        "snippet": message_full.get("snippet", ""),
        "thread_id": message_full.get("threadId", "")
    }


@server.tool()
@handle_http_errors("get_gmail_message_content", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_message_content(
    service, message_id: str, user_google_email: str
) -> str:
    """
    Retrieves the full content (subject, sender, plain text body) of a specific Gmail message.
    This is your original function, kept for backward compatibility.

    Args:
        message_id (str): The unique ID of the Gmail message to retrieve.
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: The message details including subject, sender, and body content.
    """
    logger.info(
        f"[get_gmail_message_content] Invoked. Message ID: '{message_id}', Email: '{user_google_email}'"
    )

    logger.info(f"[get_gmail_message_content] Using service for: {user_google_email}")

    # Fetch message metadata first to get headers
    message_metadata = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject", "From"],
        )
        .execute
    )

    headers = {
        h["name"]: h["value"]
        for h in message_metadata.get("payload", {}).get("headers", [])
    }
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")

    # Now fetch the full message to get the body parts
    message_full = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",  # Request full payload for body
        )
        .execute
    )

    # Extract the plain text body using helper function
    payload = message_full.get("payload", {})
    body_data = _extract_message_body(payload)

    content_text = "\n".join(
        [
            f"Subject: {subject}",
            f"From:    {sender}",
            f"\n--- BODY ---\n{body_data or '[No text/plain body found]'}",
        ]
    )
    return content_text


@server.tool()
@handle_http_errors("download_gmail_attachment", is_read_only=False, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def download_gmail_attachment(
    service,
    message_id: str,
    attachment_id: str,
    user_google_email: str,
    save_path: Optional[str] = None,
    max_size_mb: int = 100
) -> Dict[str, Any]:
    """
    Download an email attachment from Gmail.

    Args:
        service: The Gmail API service object
        message_id (str): The message ID containing the attachment
        attachment_id (str): The attachment ID to download
        user_google_email (str): The user's Google email address
        save_path (str, optional): Path to save the attachment file
        max_size_mb (int): Maximum attachment size in MB (default: 100)

    Returns:
        dict: Dictionary containing attachment info and data
    """
    logger.info(
        f"[download_gmail_attachment] Message ID: '{message_id}', Attachment ID: '{attachment_id}'"
    )

    try:
        # Get the attachment
        attachment = await asyncio.to_thread(
            service.users()
            .messages()
            .attachments()
            .get(
                userId="me",
                messageId=message_id,
                id=attachment_id
            )
            .execute
        )

        # Validate file size before downloading
        attachment_size = int(attachment.get('size', 0))
        max_size_bytes = max_size_mb * 1024 * 1024

        if attachment_size > max_size_bytes:
            raise Exception(f"Attachment too large: {attachment_size / (1024*1024):.1f}MB exceeds limit of {max_size_mb}MB")

        # Decode the attachment data
        file_data = base64.urlsafe_b64decode(attachment['data'])

        result = {
            'attachment_id': attachment_id,
            'message_id': message_id,
            'size': attachment['size'],
            'data_base64': base64.b64encode(file_data).decode('utf-8')  # Encode as base64 string for JSON serialization
        }

        if save_path:
            # Save to file with path traversal protection
            save_path = Path(save_path).resolve()
           
            # Ensure the path doesn't escape the intended directory
            base_dir = Path.cwd().resolve()
            try:
                save_path.relative_to(base_dir)
            except ValueError:
                raise Exception("Invalid file path - path traversal not allowed")
           
            save_path.parent.mkdir(parents=True, exist_ok=True)
           
            with open(save_path, 'wb') as f:
                f.write(file_data)
               
            result['saved_path'] = str(save_path)
            logger.info(f"Attachment saved to {save_path}")

        logger.info(f"Successfully downloaded attachment: {attachment_size} bytes")
        return result

    except Exception as e:
        logger.error(f"Failed to download attachment: {str(e)}")
        raise Exception(f"Unable to download attachment: {str(e)}")


@server.tool()
@handle_http_errors("list_gmail_message_attachments", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def list_gmail_message_attachments(
    service,
    message_id: str,
    user_google_email: str
) -> List[Dict[str, Any]]:
    """
    List all attachments in a Gmail message.

    Args:
        service: The Gmail API service object
        message_id (str): The message ID to check for attachments
        user_google_email (str): The user's Google email address

    Returns:
        list: List of attachment dictionaries with metadata
    """
    logger.info(f"[list_gmail_message_attachments] Message ID: '{message_id}'")

    # Get the full message
    message_full = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",
        )
        .execute
    )

    # Extract attachments
    payload = message_full.get("payload", {})
    attachments = _extract_attachments(payload, message_id)

    logger.info(f"Found {len(attachments)} attachments in message {message_id}")
    return attachments


def _read_pdf_content(file_data: bytes) -> str:
    """Extract text content from PDF bytes using pdfplumber"""
    if not PDF_AVAILABLE:
        return "PDF reading not available. Please install: pip install pdfplumber"
   
    try:
        pdf_file = io.BytesIO(file_data)
        text_content = []
       
        with pdfplumber.open(pdf_file) as pdf:
            for page_num, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text()
                    if page_text and page_text.strip():
                        text_content.append(f"--- Page {page_num + 1} ---\n{page_text}")
                    
                    # Also extract tables if present
                    tables = page.extract_tables()
                    if tables:
                        for table_num, table in enumerate(tables):
                            if table:
                                text_content.append(f"--- Page {page_num + 1} Table {table_num + 1} ---")
                                for row in table:
                                    if row:
                                        text_content.append(" | ".join(str(cell) if cell else "" for cell in row))
                except Exception as e:
                    text_content.append(f"--- Page {page_num + 1} (Error reading page) ---\nError: {str(e)}")
       
        return "\n\n".join(text_content) if text_content else "No text content found in PDF"
       
    except Exception as e:
        return f"Error reading PDF: {str(e)}"


def _read_docx_content(file_data: bytes) -> str:
    """Extract text content from DOCX bytes using mammoth"""
    if not DOCX_AVAILABLE:
        return "DOCX reading not available. Please install: pip install mammoth"
   
    try:
        docx_file = io.BytesIO(file_data)
        
        # Extract raw text
        result = mammoth.extract_raw_text(docx_file)
        text_content = result.value.strip() if result.value else ""
        
        # Check for conversion messages/warnings
        if result.messages:
            warnings = [msg.message for msg in result.messages]
            if warnings:
                text_content += "\n\n--- Conversion Notes ---\n" + "\n".join(warnings)
        
        return text_content if text_content else "No text content found in DOCX"
       
    except Exception as e:
        return f"Error reading DOCX: {str(e)}"


def _read_xlsx_content(file_data: bytes) -> str:
    """Extract text content from Excel files using pyxlsb and pandas"""
    if not EXCEL_AVAILABLE:
        return "Excel reading not available. Please install: pip install pyxlsb pandas"
   
    try:
        xlsx_file = io.BytesIO(file_data)
        
        # Try to detect file format
        xlsx_file.seek(0)
        header = xlsx_file.read(8)
        xlsx_file.seek(0)
        
        content = []
        
        # Handle .xlsb files with pyxlsb
        if b'Microsoft' in header or file_data.startswith(b'\x09\x08\x04\x00'):
            try:
                # Read XLSB file using pyxlsb
                with pyxlsb.open_workbook(xlsx_file) as wb:
                    for sheet_name in wb.sheets:
                        content.append(f"--- Sheet: {sheet_name} ---")
                        
                        rows = []
                        with wb.get_sheet(sheet_name) as sheet:
                            for row in sheet.rows():
                                if row:
                                    row_text = "\t".join(str(cell.v) if cell and cell.v is not None else "" for cell in row)
                                    if row_text.strip():
                                        rows.append(row_text)
                        
                        if rows:
                            content.append("\n".join(rows))
                        else:
                            content.append("No data in this sheet")
                            
            except Exception as e:
                # Fallback to pandas for regular Excel files
                content = []
                excel_data = pd.read_excel(xlsx_file, sheet_name=None, engine='openpyxl')
                
                for sheet_name, df in excel_data.items():
                    content.append(f"--- Sheet: {sheet_name} ---")
                    
                    if not df.empty:
                        sheet_content = df.to_string(index=False, na_rep='')
                        content.append(sheet_content)
                    else:
                        content.append("No data in this sheet")
        else:
            # Handle regular Excel files with pandas
            excel_data = pd.read_excel(xlsx_file, sheet_name=None, engine='openpyxl')
            
            for sheet_name, df in excel_data.items():
                content.append(f"--- Sheet: {sheet_name} ---")
                
                if not df.empty:
                    sheet_content = df.to_string(index=False, na_rep='')
                    content.append(sheet_content)
                else:
                    content.append("No data in this sheet")
           
        return "\n\n".join(content) if content else "No content found in Excel file"
       
    except Exception as e:
        return f"Error reading Excel file: {str(e)}"


def _read_csv_content(file_data: bytes) -> str:
    """Extract text content from CSV bytes"""
    if not CSV_AVAILABLE:
        return "CSV reading not available"
   
    try:
        csv_text = file_data.decode('utf-8', errors='ignore')
        csv_file = io.StringIO(csv_text)
       
        # Try to detect CSV dialect
        sample = csv_text[:1024]
        sniffer = csv.Sniffer()
        delimiter = ','
       
        try:
            dialect = sniffer.sniff(sample)
            delimiter = dialect.delimiter
        except:
            pass  # Use default comma delimiter
       
        csv_file.seek(0)
        reader = csv.reader(csv_file, delimiter=delimiter)
       
        rows = []
        for row_num, row in enumerate(reader, 1):
            if row:  # Skip empty rows
                rows.append(f"Row {row_num}: {' | '.join(row)}")
            if row_num > 1000:  # Limit to first 1000 rows
                rows.append("... (truncated, showing first 1000 rows)")
                break
       
        return "\n".join(rows) if rows else "No content found in CSV"
       
    except Exception as e:
        return f"Error reading CSV: {str(e)}"


def _read_html_content(file_data: bytes) -> str:
    """Extract text content from HTML using beautifulsoup4"""
    if not HTML_AVAILABLE:
        return "HTML reading not available. Please install: pip install beautifulsoup4"
    
    try:
        html_text = file_data.decode('utf-8', errors='ignore')
        soup = BeautifulSoup(html_text, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()
        
        # Get text content
        text = soup.get_text()
        
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        return text if text else "No text content found in HTML"
        
    except Exception as e:
        return f"Error reading HTML: {str(e)}"


def _read_text_content(file_data: bytes, encoding: str = 'utf-8') -> str:
    """Extract text content from plain text files"""
    try:
        # Try UTF-8 first, then other common encodings
        encodings = [encoding, 'utf-8', 'latin-1', 'cp1252']
       
        for enc in encodings:
            try:
                return file_data.decode(enc)
            except UnicodeDecodeError:
                continue
       
        # If all encodings fail, decode with errors ignored
        return file_data.decode('utf-8', errors='ignore')
       
    except Exception as e:
        return f"Error reading text file: {str(e)}"


def _read_json_content(file_data: bytes) -> str:
    """Extract and format JSON content"""
    try:
        json_text = file_data.decode('utf-8', errors='ignore')
        json_data = json.loads(json_text)
        return json.dumps(json_data, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error reading JSON: {str(e)}"


@server.tool()
@handle_http_errors("read_gmail_attachment_content", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def read_gmail_attachment_content(
    service,
    message_id: str,
    attachment_name: str,
    user_google_email: str,
    max_size_mb: int = 50
) -> Dict[str, Any]:
    """
    Download and read the content of a Gmail attachment.
   
    Supports: PDF, DOCX, XLSX, XLSB, CSV, HTML, TXT, JSON, and other text-based files.

    Args:
        service: The Gmail API service object
        message_id (str): The message ID containing the attachment
        attachment_name (str): The attachment name to read
        user_google_email (str): The user's Google email address
        max_size_mb (int): Maximum attachment size in MB (default: 50)

    Returns:
        dict: Dictionary containing attachment metadata and extracted content
    """
    logger.info(
        f"[read_gmail_attachment_content] Message ID: '{message_id}', Attachment name: '{attachment_name}'"
    )

    try:
        # First, get attachment metadata from the message
        message_full = await asyncio.to_thread(
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
            .execute
        )
        logger.info(f"Retrieved full message for ID: {message_id} with attachment name '{attachment_name}'")
        # Find the attachment metadata
        payload = message_full.get("payload", {})
        attachments = _extract_attachments(payload, message_id)
       
        attachment_info = None
        for att in attachments:
            if att['filename'] == attachment_name:
                attachment_info = att
                break

        logger.info(f"Found {attachment_info} attachments in message {message_id}")
        
        attachment_id = attachment_info['attachment_id'] #if attachment_info else attachment_name

        if not attachment_info:
            logger.error(f"Attachment {attachment_id} not found. Available attachments: {[att['attachment_id'] for att in attachments]}")
            # Try to find by filename if attachment_id doesn't match
            if attachments:
                logger.info(f"Using first available attachment: {attachments[0]['filename']}")
                attachment_info = attachments[0]
                attachment_id = attachment_info['attachment_id']  # Update to correct ID
            else:
                raise Exception(f"Attachment {attachment_id} not found in message {message_id}")

        # Download the attachment
        attachment = await asyncio.to_thread(
            service.users()
            .messages()
            .attachments()
            .get(
                userId="me",
                messageId=message_id,
                id=attachment_id
            )
            .execute
        )

        # Validate file size
        attachment_size = int(attachment.get('size', 0))
        max_size_bytes = max_size_mb * 1024 * 1024

        if attachment_size > max_size_bytes:
            raise Exception(f"Attachment too large: {attachment_size / (1024*1024):.1f}MB exceeds limit of {max_size_mb}MB")

        # Decode the attachment data
        file_data = base64.urlsafe_b64decode(attachment['data'])

        # Extract content based on file type
        filename = attachment_info['filename'].lower()
        mime_type = attachment_info['mime_type'].lower()
        content = ""
        file_type = "unknown"

        # Determine file type and extract content
        if filename.endswith('.pdf') or 'pdf' in mime_type:
            file_type = "pdf"
            content = _read_pdf_content(file_data)
           
        elif filename.endswith('.docx') or 'wordprocessingml' in mime_type:
            file_type = "docx"
            content = _read_docx_content(file_data)
           
        elif filename.endswith(('.xlsx', '.xls', '.xlsb')) or 'spreadsheetml' in mime_type:
            file_type = "xlsx"
            content = _read_xlsx_content(file_data)
           
        elif filename.endswith('.csv') or mime_type == 'text/csv':
            file_type = "csv"
            content = _read_csv_content(file_data)
           
        elif filename.endswith(('.html', '.htm')) or mime_type == 'text/html':
            file_type = "html"
            content = _read_html_content(file_data)
           
        elif filename.endswith('.json') or mime_type == 'application/json':
            file_type = "json"
            content = _read_json_content(file_data)
           
        elif (filename.endswith(('.txt', '.log', '.md', '.py', '.js', '.css', '.xml')) or
              mime_type.startswith('text/')):
            file_type = "text"
            content = _read_text_content(file_data)
           
        else:
            # Try to read as text if it's not a known binary format
            if not any(ext in filename for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp',
                                                   '.exe', '.zip', '.rar', '.7z']):
                file_type = "text"
                content = _read_text_content(file_data)
            else:
                content = f"Cannot read content from {filename}. File type not supported for content extraction."
                file_type = "binary"

        result = {
            'message_id': message_id,
            'attachment_id': attachment_id,
            'filename': attachment_info['filename'],
            'mime_type': attachment_info['mime_type'],
            'size': attachment_size,
            'size_mb': round(attachment_size / (1024 * 1024), 2),
            'file_type': file_type,
            'content': content,
            'content_length': len(content),
            'success': True
        }

        logger.info(f"Successfully extracted content from {attachment_info['filename']} ({file_type}): {len(content)} characters")
        return result

    except Exception as e:
        logger.error(f"Failed to read attachment content: {str(e)}")
        return {
            'message_id': message_id,
            'attachment_id': attachment_id,
            'error': str(e),
            'success': False
        }


@server.tool()
@handle_http_errors("read_all_gmail_message_attachments", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def read_all_gmail_message_attachments(
    service,
    message_id: str,
    user_google_email: str,
    max_size_mb: int = 50
) -> List[Dict[str, Any]]:
    """
    Read the content of all attachments in a Gmail message.

    Args:
        service: The Gmail API service object
        message_id (str): The message ID to read attachments from
        user_google_email (str): The user's Google email address
        max_size_mb (int): Maximum attachment size in MB per file (default: 50)

    Returns:
        list: List of dictionaries containing attachment content and metadata
    """
    logger.info(f"[read_all_gmail_message_attachments] Message ID: '{message_id}'")

    try:
        # Get list of attachments
        attachments = await list_gmail_message_attachments(service, message_id, user_google_email)
       
        if not attachments:
            logger.info(f"No attachments found in message {message_id}")
            return []

        results = []
        for attachment in attachments:
            try:
                result = await read_gmail_attachment_content(
                    service=service,
                    message_id=message_id,
                    attachment_id=attachment['attachment_id'],
                    user_google_email=user_google_email,
                    max_size_mb=max_size_mb
                )
                results.append(result)
               
            except Exception as e:
                logger.error(f"Failed to read attachment {attachment['filename']}: {str(e)}")
                results.append({
                    'message_id': message_id,
                    'attachment_id': attachment['attachment_id'],
                    'filename': attachment['filename'],
                    'error': str(e),
                    'success': False
                })

        return results

    except Exception as e:
        logger.error(f"Failed to read message attachments: {str(e)}")
        raise Exception(f"Unable to read message attachments: {str(e)}")


# Usage example functions
@server.tool()
@handle_http_errors("download_all_attachments_from_message", is_read_only=False, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def download_all_attachments_from_message(
    service,
    message_id: str,
    user_google_email: str,
    download_dir: str = "downloads",
    max_size_mb: int = 100
) -> List[Dict[str, Any]]:
    """
    Download all attachments from a Gmail message.

    Args:
        service: The Gmail API service object
        message_id (str): The message ID
        user_google_email (str): The user's email
        download_dir (str): Directory to save attachments
        max_size_mb (int): Max size per attachment in MB

    Returns:
        list: List of download results
    """
    # Get list of attachments
    attachments = await list_gmail_message_attachments(service, message_id, user_google_email)
   
    if not attachments:
        logger.info(f"No attachments found in message {message_id}")
        return []

    # Create download directory
    download_path = Path(download_dir)
    download_path.mkdir(exist_ok=True)

    results = []
    for attachment in attachments:
        try:
            filename = attachment['filename']
            safe_filename = "".join(c for c in filename if c.isalnum() or c in (' ', '.', '_', '-')).strip()
            file_path = download_path / safe_filename
           
            result = await download_gmail_attachment(
                service=service,
                message_id=message_id,
                attachment_id=attachment['attachment_id'],
                user_google_email=user_google_email,
                save_path=str(file_path),
                max_size_mb=max_size_mb
            )
           
            result['original_filename'] = filename
            results.append(result)
           
        except Exception as e:
            logger.error(f"Failed to download attachment {attachment['filename']}: {str(e)}")
            results.append({
                'attachment_id': attachment['attachment_id'],
                'original_filename': attachment['filename'],
                'error': str(e)
            })

    return results
        
def _extract_headers(payload: dict, header_names: List[str]) -> Dict[str, str]:
    """
    Extract specified headers from a Gmail message payload.

    Args:
        payload: The message payload from Gmail API
        header_names: List of header names to extract

    Returns:
        Dict mapping header names to their values
    """
    headers = {}
    for header in payload.get("headers", []):
        if header["name"] in header_names:
            headers[header["name"]] = header["value"]
    return headers


def _prepare_gmail_message(
    subject: str,
    body: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    thread_id: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Prepare a Gmail message with threading support.
    
    Args:
        subject: Email subject
        body: Email body (plain text)
        to: Optional recipient email address
        cc: Optional CC email address
        bcc: Optional BCC email address
        thread_id: Optional Gmail thread ID to reply within
        in_reply_to: Optional Message-ID of the message being replied to
        references: Optional chain of Message-IDs for proper threading
        
    Returns:
        Tuple of (raw_message, thread_id) where raw_message is base64 encoded
    """
    # Handle reply subject formatting
    reply_subject = subject
    if in_reply_to and not subject.lower().startswith('re:'):
        reply_subject = f"Re: {subject}"

    # Prepare the email
    message = MIMEText(body)
    message["subject"] = reply_subject

    # Add recipients if provided
    if to:
        message["to"] = to
    if cc:
        message["cc"] = cc
    if bcc:
        message["bcc"] = bcc

    # Add reply headers for threading
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    
    if references:
        message["References"] = references

    # Encode message
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    
    return raw_message, thread_id


def _generate_gmail_web_url(item_id: str, account_index: int = 0) -> str:
    """
    Generate Gmail web interface URL for a message or thread ID.
    Uses #all to access messages from any Gmail folder/label (not just inbox).

    Args:
        item_id: Gmail message ID or thread ID
        account_index: Google account index (default 0 for primary account)

    Returns:
        Gmail web interface URL that opens the message/thread in Gmail web interface
    """
    return f"https://mail.google.com/mail/u/{account_index}/#all/{item_id}"


def _format_gmail_results_plain(messages: list, query: str) -> str:
    """Format Gmail search results in clean, LLM-friendly plain text."""
    if not messages:
        return f"No messages found for query: '{query}'"

    lines = [
        f"Found {len(messages)} messages matching '{query}':",
        "",
        "📧 MESSAGES:",
    ]

    for i, msg in enumerate(messages, 1):
        # Handle potential null/undefined message objects
        if not msg or not isinstance(msg, dict):
            lines.extend([
                f"  {i}. Message: Invalid message data",
                "     Error: Message object is null or malformed",
                "",
            ])
            continue

        # Handle potential null/undefined values from Gmail API
        message_id = msg.get("id")
        thread_id = msg.get("threadId")

        # Convert None, empty string, or missing values to "unknown"
        if not message_id:
            message_id = "unknown"
        if not thread_id:
            thread_id = "unknown"

        if message_id != "unknown":
            message_url = _generate_gmail_web_url(message_id)
        else:
            message_url = "N/A"

        if thread_id != "unknown":
            thread_url = _generate_gmail_web_url(thread_id)
        else:
            thread_url = "N/A"

        lines.extend(
            [
                f"  {i}. Message ID: {message_id}",
                f"     Web Link: {message_url}",
                f"     Thread ID: {thread_id}",
                f"     Thread Link: {thread_url}",
                "",
            ]
        )

    lines.extend(
        [
            "💡 USAGE:",
            "  • Pass the Message IDs **as a list** to get_gmail_messages_content_batch()",
            "    e.g. get_gmail_messages_content_batch(message_ids=[...])",
            "  • Pass the Thread IDs to get_gmail_thread_content() (single) or get_gmail_threads_content_batch() (batch)",
        ]
    )

    return "\n".join(lines)


@server.tool()
@handle_http_errors("search_gmail_messages", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def search_gmail_messages(
    service, query: str, user_google_email: str, page_size: int = 10
) -> str:
    """
    Searches messages in a user's Gmail account based on a query.
    Returns both Message IDs and Thread IDs for each found message, along with Gmail web interface links for manual verification.

    Args:
        query (str): The search query. Supports standard Gmail search operators.
        user_google_email (str): The user's Google email address. Required.
        page_size (int): The maximum number of messages to return. Defaults to 10.

    Returns:
        str: LLM-friendly structured results with Message IDs, Thread IDs, and clickable Gmail web interface URLs for each found message.
    """
    logger.info(
        f"[search_gmail_messages] Email: '{user_google_email}', Query: '{query}'"
    )

    response = await asyncio.to_thread(
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=page_size)
        .execute
    )

    # Handle potential null response (but empty dict {} is valid)
    if response is None:
        logger.warning("[search_gmail_messages] Null response from Gmail API")
        return f"No response received from Gmail API for query: '{query}'"

    messages = response.get("messages", [])
    # Additional safety check for null messages array
    if messages is None:
        messages = []

    formatted_output = _format_gmail_results_plain(messages, query)

    logger.info(f"[search_gmail_messages] Found {len(messages)} messages")
    return formatted_output


@server.tool()
@handle_http_errors("get_gmail_message_content", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_message_content(
    service, message_id: str, user_google_email: str
) -> str:
    """
    Retrieves the full content (subject, sender, plain text body) of a specific Gmail message.

    Args:
        message_id (str): The unique ID of the Gmail message to retrieve.
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: The message details including subject, sender, and body content.
    """
    logger.info(
        f"[get_gmail_message_content] Invoked. Message ID: '{message_id}', Email: '{user_google_email}'"
    )

    logger.info(f"[get_gmail_message_content] Using service for: {user_google_email}")

    # Fetch message metadata first to get headers
    message_metadata = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=["Subject", "From"],
        )
        .execute
    )

    headers = {
        h["name"]: h["value"]
        for h in message_metadata.get("payload", {}).get("headers", [])
    }
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")

    # Now fetch the full message to get the body parts
    message_full = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="full",  # Request full payload for body
        )
        .execute
    )

    # Extract the plain text body using helper function
    payload = message_full.get("payload", {})
    body_data = _extract_message_body(payload)

    content_text = "\n".join(
        [
            f"Subject: {subject}",
            f"From:    {sender}",
            f"\n--- BODY ---\n{body_data or '[No text/plain body found]'}",
        ]
    )
    return content_text


@server.tool()
@handle_http_errors("get_gmail_messages_content_batch", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_messages_content_batch(
    service,
    message_ids: List[str],
    user_google_email: str,
    format: Literal["full", "metadata"] = "full",
) -> str:
    """
    Retrieves the content of multiple Gmail messages in a single batch request.
    Supports up to 25 messages per batch to prevent SSL connection exhaustion.

    Args:
        message_ids (List[str]): List of Gmail message IDs to retrieve (max 25 per batch).
        user_google_email (str): The user's Google email address. Required.
        format (Literal["full", "metadata"]): Message format. "full" includes body, "metadata" only headers.

    Returns:
        str: A formatted list of message contents with separators.
    """
    logger.info(
        f"[get_gmail_messages_content_batch] Invoked. Message count: {len(message_ids)}, Email: '{user_google_email}'"
    )

    if not message_ids:
        raise Exception("No message IDs provided")

    output_messages = []

    # Process in smaller chunks to prevent SSL connection exhaustion
    for chunk_start in range(0, len(message_ids), GMAIL_BATCH_SIZE):
        chunk_ids = message_ids[chunk_start : chunk_start + GMAIL_BATCH_SIZE]
        results: Dict[str, Dict] = {}

        def _batch_callback(request_id, response, exception):
            """Callback for batch requests"""
            results[request_id] = {"data": response, "error": exception}

        # Try to use batch API
        try:
            batch = service.new_batch_http_request(callback=_batch_callback)

            for mid in chunk_ids:
                if format == "metadata":
                    req = (
                        service.users()
                        .messages()
                        .get(
                            userId="me",
                            id=mid,
                            format="metadata",
                            metadataHeaders=["Subject", "From"],
                        )
                    )
                else:
                    req = (
                        service.users()
                        .messages()
                        .get(userId="me", id=mid, format="full")
                    )
                batch.add(req, request_id=mid)

            # Execute batch request
            await asyncio.to_thread(batch.execute)

        except Exception as batch_error:
            # Fallback to sequential processing instead of parallel to prevent SSL exhaustion
            logger.warning(
                f"[get_gmail_messages_content_batch] Batch API failed, falling back to sequential processing: {batch_error}"
            )

            async def fetch_message_with_retry(mid: str, max_retries: int = 3):
                """Fetch a single message with exponential backoff retry for SSL errors"""
                for attempt in range(max_retries):
                    try:
                        if format == "metadata":
                            msg = await asyncio.to_thread(
                                service.users()
                                .messages()
                                .get(
                                    userId="me",
                                    id=mid,
                                    format="metadata",
                                    metadataHeaders=["Subject", "From"],
                                )
                                .execute
                            )
                        else:
                            msg = await asyncio.to_thread(
                                service.users()
                                .messages()
                                .get(userId="me", id=mid, format="full")
                                .execute
                            )
                        return mid, msg, None
                    except ssl.SSLError as ssl_error:
                        if attempt < max_retries - 1:
                            # Exponential backoff: 1s, 2s, 4s
                            delay = 2 ** attempt
                            logger.warning(
                                f"[get_gmail_messages_content_batch] SSL error for message {mid} on attempt {attempt + 1}: {ssl_error}. Retrying in {delay}s..."
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                f"[get_gmail_messages_content_batch] SSL error for message {mid} on final attempt: {ssl_error}"
                            )
                            return mid, None, ssl_error
                    except Exception as e:
                        return mid, None, e

            # Process messages sequentially with small delays to prevent connection exhaustion
            for mid in chunk_ids:
                mid_result, msg_data, error = await fetch_message_with_retry(mid)
                results[mid_result] = {"data": msg_data, "error": error}
                # Brief delay between requests to allow connection cleanup
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        # Process results for this chunk
        for mid in chunk_ids:
            entry = results.get(mid, {"data": None, "error": "No result"})

            if entry["error"]:
                output_messages.append(f"⚠️ Message {mid}: {entry['error']}\n")
            else:
                message = entry["data"]
                if not message:
                    output_messages.append(f"⚠️ Message {mid}: No data returned\n")
                    continue

                # Extract content based on format
                payload = message.get("payload", {})

                if format == "metadata":
                    headers = _extract_headers(payload, ["Subject", "From"])
                    subject = headers.get("Subject", "(no subject)")
                    sender = headers.get("From", "(unknown sender)")

                    output_messages.append(
                        f"Message ID: {mid}\n"
                        f"Subject: {subject}\n"
                        f"From: {sender}\n"
                        f"Web Link: {_generate_gmail_web_url(mid)}\n"
                    )
                else:
                    # Full format - extract body too
                    headers = _extract_headers(payload, ["Subject", "From"])
                    subject = headers.get("Subject", "(no subject)")
                    sender = headers.get("From", "(unknown sender)")
                    body = _extract_message_body(payload)

                    output_messages.append(
                        f"Message ID: {mid}\n"
                        f"Subject: {subject}\n"
                        f"From: {sender}\n"
                        f"Web Link: {_generate_gmail_web_url(mid)}\n"
                        f"\n{body or '[No text/plain body found]'}\n"
                    )

    # Combine all messages with separators
    final_output = f"Retrieved {len(message_ids)} messages:\n\n"
    final_output += "\n---\n\n".join(output_messages)

    return final_output


@server.tool()
@handle_http_errors("send_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_SEND_SCOPE)
async def send_gmail_message(
    service,
    user_google_email: str,
    to: str = Body(..., description="Recipient email address."),
    subject: str = Body(..., description="Email subject."),
    body: str = Body(..., description="Email body (plain text)."),
    cc: Optional[str] = Body(None, description="Optional CC email address."),
    bcc: Optional[str] = Body(None, description="Optional BCC email address."),
    thread_id: Optional[str] = Body(None, description="Optional Gmail thread ID to reply within."),
    in_reply_to: Optional[str] = Body(None, description="Optional Message-ID of the message being replied to."),
    references: Optional[str] = Body(None, description="Optional chain of Message-IDs for proper threading."),
) -> str:
    """
    Sends an email using the user's Gmail account. Supports both new emails and replies.

    Args:
        to (str): Recipient email address.
        subject (str): Email subject.
        body (str): Email body (plain text).
        cc (Optional[str]): Optional CC email address.
        bcc (Optional[str]): Optional BCC email address.
        user_google_email (str): The user's Google email address. Required.
        thread_id (Optional[str]): Optional Gmail thread ID to reply within. When provided, sends a reply.
        in_reply_to (Optional[str]): Optional Message-ID of the message being replied to. Used for proper threading.
        references (Optional[str]): Optional chain of Message-IDs for proper threading. Should include all previous Message-IDs.

    Returns:
        str: Confirmation message with the sent email's message ID.
        
    Examples:
        # Send a new email
        send_gmail_message(to="user@example.com", subject="Hello", body="Hi there!")
        
        # Send an email with CC and BCC
        send_gmail_message(
            to="user@example.com", 
            cc="manager@example.com",
            bcc="archive@example.com",
            subject="Project Update", 
            body="Here's the latest update..."
        )
        
        # Send a reply
        send_gmail_message(
            to="user@example.com", 
            subject="Re: Meeting tomorrow", 
            body="Thanks for the update!",
            thread_id="thread_123",
            in_reply_to="<message123@gmail.com>",
            references="<original@gmail.com> <message123@gmail.com>"
        )
    """
    logger.info(
        f"[send_gmail_message] Invoked. Email: '{user_google_email}', Subject: '{subject}'"
    )

    # Prepare the email message
    raw_message, thread_id_final = _prepare_gmail_message(
        subject=subject,
        body=body,
        to=to,
        cc=cc,
        bcc=bcc,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
        references=references,
    )
    
    send_body = {"raw": raw_message}
    
    # Associate with thread if provided
    if thread_id_final:
        send_body["threadId"] = thread_id_final

    # Send the message
    sent_message = await asyncio.to_thread(
        service.users().messages().send(userId="me", body=send_body).execute
    )
    message_id = sent_message.get("id")
    return f"Email sent! Message ID: {message_id}"


@server.tool()
@handle_http_errors("draft_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_COMPOSE_SCOPE)
async def draft_gmail_message(
    service,
    user_google_email: str,
    subject: str = Body(..., description="Email subject."),
    body: str = Body(..., description="Email body (plain text)."),
    to: Optional[str] = Body(None, description="Optional recipient email address."),
    cc: Optional[str] = Body(None, description="Optional CC email address."),
    bcc: Optional[str] = Body(None, description="Optional BCC email address."),
    thread_id: Optional[str] = Body(None, description="Optional Gmail thread ID to reply within."),
    in_reply_to: Optional[str] = Body(None, description="Optional Message-ID of the message being replied to."),
    references: Optional[str] = Body(None, description="Optional chain of Message-IDs for proper threading."),
) -> str:
    """
    Creates a draft email in the user's Gmail account. Supports both new drafts and reply drafts.

    Args:
        user_google_email (str): The user's Google email address. Required.
        subject (str): Email subject.
        body (str): Email body (plain text).
        to (Optional[str]): Optional recipient email address. Can be left empty for drafts.
        cc (Optional[str]): Optional CC email address.
        bcc (Optional[str]): Optional BCC email address.
        thread_id (Optional[str]): Optional Gmail thread ID to reply within. When provided, creates a reply draft.
        in_reply_to (Optional[str]): Optional Message-ID of the message being replied to. Used for proper threading.
        references (Optional[str]): Optional chain of Message-IDs for proper threading. Should include all previous Message-IDs.

    Returns:
        str: Confirmation message with the created draft's ID.
        
    Examples:
        # Create a new draft
        draft_gmail_message(subject="Hello", body="Hi there!", to="user@example.com")
        
        # Create a draft with CC and BCC
        draft_gmail_message(
            subject="Project Update", 
            body="Here's the latest update...",
            to="user@example.com",
            cc="manager@example.com",
            bcc="archive@example.com"
        )
        
        # Create a reply draft
        draft_gmail_message(
            subject="Re: Meeting tomorrow", 
            body="Thanks for the update!",
            to="user@example.com",
            thread_id="thread_123",
            in_reply_to="<message123@gmail.com>",
            references="<original@gmail.com> <message123@gmail.com>"
        )
    """
    logger.info(
        f"[draft_gmail_message] Invoked. Email: '{user_google_email}', Subject: '{subject}'"
    )

    # Prepare the email message
    raw_message, thread_id_final = _prepare_gmail_message(
        subject=subject,
        body=body,
        to=to,
        cc=cc,
        bcc=bcc,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
        references=references,
    )

    # Create a draft instead of sending
    draft_body = {"message": {"raw": raw_message}}
    
    # Associate with thread if provided
    if thread_id_final:
        draft_body["message"]["threadId"] = thread_id_final

    # Create the draft
    created_draft = await asyncio.to_thread(
        service.users().drafts().create(userId="me", body=draft_body).execute
    )
    draft_id = created_draft.get("id")
    return f"Draft created! Draft ID: {draft_id}"


def _format_thread_content(thread_data: dict, thread_id: str) -> str:
    """
    Helper function to format thread content from Gmail API response.

    Args:
        thread_data (dict): Thread data from Gmail API
        thread_id (str): Thread ID for display

    Returns:
        str: Formatted thread content
    """
    messages = thread_data.get("messages", [])
    if not messages:
        return f"No messages found in thread '{thread_id}'."

    # Extract thread subject from the first message
    first_message = messages[0]
    first_headers = {
        h["name"]: h["value"]
        for h in first_message.get("payload", {}).get("headers", [])
    }
    thread_subject = first_headers.get("Subject", "(no subject)")

    # Build the thread content
    content_lines = [
        f"Thread ID: {thread_id}",
        f"Subject: {thread_subject}",
        f"Messages: {len(messages)}",
        "",
    ]

    # Process each message in the thread
    for i, message in enumerate(messages, 1):
        # Extract headers
        headers = {
            h["name"]: h["value"] for h in message.get("payload", {}).get("headers", [])
        }

        sender = headers.get("From", "(unknown sender)")
        date = headers.get("Date", "(unknown date)")
        subject = headers.get("Subject", "(no subject)")

        # Extract message body
        payload = message.get("payload", {})
        body_data = _extract_message_body(payload)

        # Add message to content
        content_lines.extend(
            [
                f"=== Message {i} ===",
                f"From: {sender}",
                f"Date: {date}",
            ]
        )

        # Only show subject if it's different from thread subject
        if subject != thread_subject:
            content_lines.append(f"Subject: {subject}")

        content_lines.extend(
            [
                "",
                body_data or "[No text/plain body found]",
                "",
            ]
        )

    return "\n".join(content_lines)


@server.tool()
@require_google_service("gmail", "gmail_read")
@handle_http_errors("get_gmail_thread_content", is_read_only=True, service_type="gmail")
async def get_gmail_thread_content(
    service, thread_id: str, user_google_email: str
) -> str:
    """
    Retrieves the complete content of a Gmail conversation thread, including all messages.

    Args:
        thread_id (str): The unique ID of the Gmail thread to retrieve.
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: The complete thread content with all messages formatted for reading.
    """
    logger.info(
        f"[get_gmail_thread_content] Invoked. Thread ID: '{thread_id}', Email: '{user_google_email}'"
    )

    # Fetch the complete thread with all messages
    thread_response = await asyncio.to_thread(
        service.users().threads().get(userId="me", id=thread_id, format="full").execute
    )

    return _format_thread_content(thread_response, thread_id)


@server.tool()
@require_google_service("gmail", "gmail_read")
@handle_http_errors("get_gmail_threads_content_batch", is_read_only=True, service_type="gmail")
async def get_gmail_threads_content_batch(
    service,
    thread_ids: List[str],
    user_google_email: str,
) -> str:
    """
    Retrieves the content of multiple Gmail threads in a single batch request.
    Supports up to 25 threads per batch to prevent SSL connection exhaustion.

    Args:
        thread_ids (List[str]): A list of Gmail thread IDs to retrieve. The function will automatically batch requests in chunks of 25.
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: A formatted list of thread contents with separators.
    """
    logger.info(
        f"[get_gmail_threads_content_batch] Invoked. Thread count: {len(thread_ids)}, Email: '{user_google_email}'"
    )

    if not thread_ids:
        raise ValueError("No thread IDs provided")

    output_threads = []

    def _batch_callback(request_id, response, exception):
        """Callback for batch requests"""
        results[request_id] = {"data": response, "error": exception}

    # Process in smaller chunks to prevent SSL connection exhaustion
    for chunk_start in range(0, len(thread_ids), GMAIL_BATCH_SIZE):
        chunk_ids = thread_ids[chunk_start : chunk_start + GMAIL_BATCH_SIZE]
        results: Dict[str, Dict] = {}

        # Try to use batch API
        try:
            batch = service.new_batch_http_request(callback=_batch_callback)

            for tid in chunk_ids:
                req = service.users().threads().get(userId="me", id=tid, format="full")
                batch.add(req, request_id=tid)

            # Execute batch request
            await asyncio.to_thread(batch.execute)

        except Exception as batch_error:
            # Fallback to sequential processing instead of parallel to prevent SSL exhaustion
            logger.warning(
                f"[get_gmail_threads_content_batch] Batch API failed, falling back to sequential processing: {batch_error}"
            )

            async def fetch_thread_with_retry(tid: str, max_retries: int = 3):
                """Fetch a single thread with exponential backoff retry for SSL errors"""
                for attempt in range(max_retries):
                    try:
                        thread = await asyncio.to_thread(
                            service.users()
                            .threads()
                            .get(userId="me", id=tid, format="full")
                            .execute
                        )
                        return tid, thread, None
                    except ssl.SSLError as ssl_error:
                        if attempt < max_retries - 1:
                            # Exponential backoff: 1s, 2s, 4s
                            delay = 2 ** attempt
                            logger.warning(
                                f"[get_gmail_threads_content_batch] SSL error for thread {tid} on attempt {attempt + 1}: {ssl_error}. Retrying in {delay}s..."
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error(
                                f"[get_gmail_threads_content_batch] SSL error for thread {tid} on final attempt: {ssl_error}"
                            )
                            return tid, None, ssl_error
                    except Exception as e:
                        return tid, None, e

            # Process threads sequentially with small delays to prevent connection exhaustion
            for tid in chunk_ids:
                tid_result, thread_data, error = await fetch_thread_with_retry(tid)
                results[tid_result] = {"data": thread_data, "error": error}
                # Brief delay between requests to allow connection cleanup
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        # Process results for this chunk
        for tid in chunk_ids:
            entry = results.get(tid, {"data": None, "error": "No result"})

            if entry["error"]:
                output_threads.append(f"⚠️ Thread {tid}: {entry['error']}\n")
            else:
                thread = entry["data"]
                if not thread:
                    output_threads.append(f"⚠️ Thread {tid}: No data returned\n")
                    continue

                output_threads.append(_format_thread_content(thread, tid))

    # Combine all threads with separators
    header = f"Retrieved {len(thread_ids)} threads:"
    return header + "\n\n" + "\n---\n\n".join(output_threads)


@server.tool()
@handle_http_errors("list_gmail_labels", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def list_gmail_labels(service, user_google_email: str) -> str:
    """
    Lists all labels in the user's Gmail account.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: A formatted list of all labels with their IDs, names, and types.
    """
    logger.info(f"[list_gmail_labels] Invoked. Email: '{user_google_email}'")

    response = await asyncio.to_thread(
        service.users().labels().list(userId="me").execute
    )
    labels = response.get("labels", [])

    if not labels:
        return "No labels found."

    lines = [f"Found {len(labels)} labels:", ""]

    system_labels = []
    user_labels = []

    for label in labels:
        if label.get("type") == "system":
            system_labels.append(label)
        else:
            user_labels.append(label)

    if system_labels:
        lines.append("📂 SYSTEM LABELS:")
        for label in system_labels:
            lines.append(f"  • {label['name']} (ID: {label['id']})")
        lines.append("")

    if user_labels:
        lines.append("🏷️  USER LABELS:")
        for label in user_labels:
            lines.append(f"  • {label['name']} (ID: {label['id']})")

    return "\n".join(lines)


@server.tool()
@handle_http_errors("manage_gmail_label", service_type="gmail")
@require_google_service("gmail", GMAIL_LABELS_SCOPE)
async def manage_gmail_label(
    service,
    user_google_email: str,
    action: Literal["create", "update", "delete"],
    name: Optional[str] = None,
    label_id: Optional[str] = None,
    label_list_visibility: Literal["labelShow", "labelHide"] = "labelShow",
    message_list_visibility: Literal["show", "hide"] = "show",
) -> str:
    """
    Manages Gmail labels: create, update, or delete labels.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["create", "update", "delete"]): Action to perform on the label.
        name (Optional[str]): Label name. Required for create, optional for update.
        label_id (Optional[str]): Label ID. Required for update and delete operations.
        label_list_visibility (Literal["labelShow", "labelHide"]): Whether the label is shown in the label list.
        message_list_visibility (Literal["show", "hide"]): Whether the label is shown in the message list.

    Returns:
        str: Confirmation message of the label operation.
    """
    logger.info(
        f"[manage_gmail_label] Invoked. Email: '{user_google_email}', Action: '{action}'"
    )

    if action == "create" and not name:
        raise Exception("Label name is required for create action.")

    if action in ["update", "delete"] and not label_id:
        raise Exception("Label ID is required for update and delete actions.")

    if action == "create":
        label_object = {
            "name": name,
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }
        created_label = await asyncio.to_thread(
            service.users().labels().create(userId="me", body=label_object).execute
        )
        return f"Label created successfully!\nName: {created_label['name']}\nID: {created_label['id']}"

    elif action == "update":
        current_label = await asyncio.to_thread(
            service.users().labels().get(userId="me", id=label_id).execute
        )

        label_object = {
            "id": label_id,
            "name": name if name is not None else current_label["name"],
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }

        updated_label = await asyncio.to_thread(
            service.users()
            .labels()
            .update(userId="me", id=label_id, body=label_object)
            .execute
        )
        return f"Label updated successfully!\nName: {updated_label['name']}\nID: {updated_label['id']}"

    elif action == "delete":
        label = await asyncio.to_thread(
            service.users().labels().get(userId="me", id=label_id).execute
        )
        label_name = label["name"]

        await asyncio.to_thread(
            service.users().labels().delete(userId="me", id=label_id).execute
        )
        return f"Label '{label_name}' (ID: {label_id}) deleted successfully!"


@server.tool()
@handle_http_errors("modify_gmail_message_labels", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def modify_gmail_message_labels(
    service,
    user_google_email: str,
    message_id: str,
    add_label_ids: Optional[List[str]] = None,
    remove_label_ids: Optional[List[str]] = None,
) -> str:
    """
    Adds or removes labels from a Gmail message.
    To archive an email, remove the INBOX label.
    To delete an email, add the TRASH label.

    Args:
        user_google_email (str): The user's Google email address. Required.
        message_id (str): The ID of the message to modify.
        add_label_ids (Optional[List[str]]): List of label IDs to add to the message.
        remove_label_ids (Optional[List[str]]): List of label IDs to remove from the message.

    Returns:
        str: Confirmation message of the label changes applied to the message.
    """
    logger.info(
        f"[modify_gmail_message_labels] Invoked. Email: '{user_google_email}', Message ID: '{message_id}'"
    )

    if not add_label_ids and not remove_label_ids:
        raise Exception(
            "At least one of add_label_ids or remove_label_ids must be provided."
        )

    body = {}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids

    await asyncio.to_thread(
        service.users().messages().modify(userId="me", id=message_id, body=body).execute
    )

    actions = []
    if add_label_ids:
        actions.append(f"Added labels: {', '.join(add_label_ids)}")
    if remove_label_ids:
        actions.append(f"Removed labels: {', '.join(remove_label_ids)}")

    return f"Message labels updated successfully!\nMessage ID: {message_id}\n{'; '.join(actions)}"


@server.tool()
@handle_http_errors("batch_modify_gmail_message_labels", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def batch_modify_gmail_message_labels(
    service,
    user_google_email: str,
    message_ids: List[str],
    add_label_ids: Optional[List[str]] = None,
    remove_label_ids: Optional[List[str]] = None,
) -> str:
    """
    Adds or removes labels from multiple Gmail messages in a single batch request.

    Args:
        user_google_email (str): The user's Google email address. Required.
        message_ids (List[str]): A list of message IDs to modify.
        add_label_ids (Optional[List[str]]): List of label IDs to add to the messages.
        remove_label_ids (Optional[List[str]]): List of label IDs to remove from the messages.

    Returns:
        str: Confirmation message of the label changes applied to the messages.
    """
    logger.info(
        f"[batch_modify_gmail_message_labels] Invoked. Email: '{user_google_email}', Message IDs: '{message_ids}'"
    )

    if not add_label_ids and not remove_label_ids:
        raise Exception(
            "At least one of add_label_ids or remove_label_ids must be provided."
        )

    body = {"ids": message_ids}
    if add_label_ids:
        body["addLabelIds"] = add_label_ids
    if remove_label_ids:
        body["removeLabelIds"] = remove_label_ids

    await asyncio.to_thread(
        service.users().messages().batchModify(userId="me", body=body).execute
    )

    actions = []
    if add_label_ids:
        actions.append(f"Added labels: {', '.join(add_label_ids)}")
    if remove_label_ids:
        actions.append(f"Removed labels: {', '.join(remove_label_ids)}")

    return f"Labels updated for {len(message_ids)} messages: {'; '.join(actions)}"
