"""
Google Gmail MCP Tools

This module provides MCP tools for interacting with the Gmail API.
"""

import logging
import asyncio
import base64
import ssl
from typing import Optional, List, Dict, Literal, Any

from email.mime.text import MIMEText

from fastapi import Body
from pydantic import Field

from auth.service_decorator import require_google_service
from core.utils import handle_http_errors
from core.server import server
from auth.scopes import (
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_LABELS_SCOPE,
)

logger = logging.getLogger(__name__)

GMAIL_BATCH_SIZE = 25
GMAIL_REQUEST_DELAY = 0.1
HTML_BODY_TRUNCATE_LIMIT = 20000


def _extract_message_body(payload):
    """
    Helper function to extract plain text body from a Gmail message payload.
    (Maintained for backward compatibility)

    Args:
        payload (dict): The message payload from Gmail API

    Returns:
        str: The plain text body content, or empty string if not found
    """
    bodies = _extract_message_bodies(payload)
    return bodies.get("text", "")


def _extract_message_bodies(payload):
    """
    Helper function to extract both plain text and HTML bodies from a Gmail message payload.

    Args:
        payload (dict): The message payload from Gmail API

    Returns:
        dict: Dictionary with 'text' and 'html' keys containing body content
    """
    text_body = ""
    html_body = ""
    parts = [payload] if "parts" not in payload else payload.get("parts", [])

    part_queue = list(parts)  # Use a queue for BFS traversal of parts
    while part_queue:
        part = part_queue.pop(0)
        mime_type = part.get("mimeType", "")
        body_data = part.get("body", {}).get("data")

        if body_data:
            try:
                decoded_data = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="ignore")
                if mime_type == "text/plain" and not text_body:
                    text_body = decoded_data
                elif mime_type == "text/html" and not html_body:
                    html_body = decoded_data
            except Exception as e:
                logger.warning(f"Failed to decode body part: {e}")

        # Add sub-parts to queue for multipart messages
        if mime_type.startswith("multipart/") and "parts" in part:
            part_queue.extend(part.get("parts", []))

    # Check the main payload if it has body data directly
    if payload.get("body", {}).get("data"):
        try:
            decoded_data = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="ignore")
            mime_type = payload.get("mimeType", "")
            if mime_type == "text/plain" and not text_body:
                text_body = decoded_data
            elif mime_type == "text/html" and not html_body:
                html_body = decoded_data
        except Exception as e:
            logger.warning(f"Failed to decode main payload body: {e}")

    return {
        "text": text_body,
        "html": html_body
    }


def _format_body_content(text_body: str, html_body: str) -> str:
    """
    Helper function to format message body content with HTML fallback and truncation.

    Args:
        text_body: Plain text body content
        html_body: HTML body content

    Returns:
        Formatted body content string
    """
    if text_body.strip():
        return text_body
    elif html_body.strip():
        # Truncate very large HTML to keep responses manageable
        if len(html_body) > HTML_BODY_TRUNCATE_LIMIT:
            html_body = html_body[:HTML_BODY_TRUNCATE_LIMIT] + "\n\n[HTML content truncated...]"
        return f"[HTML Content Converted]\n{html_body}"
    else:
        return "[No readable content found]"


def _extract_attachments(payload: dict) -> List[Dict[str, Any]]:
    """
    Extract attachment metadata from a Gmail message payload.

    Args:
        payload: The message payload from Gmail API

    Returns:
        List of attachment dictionaries with filename, mimeType, size, and attachmentId
    """
    attachments = []

    def search_parts(part):
        """Recursively search for attachments in message parts"""
        # Check if this part is an attachment
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            attachments.append({
                "filename": part["filename"],
                "mimeType": part.get("mimeType", "application/octet-stream"),
                "size": part.get("body", {}).get("size", 0),
                "attachmentId": part["body"]["attachmentId"]
            })

        # Recursively search sub-parts
        if "parts" in part:
            for subpart in part["parts"]:
                search_parts(subpart)

    # Start searching from the root payload
    search_parts(payload)
    return attachments


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
    body_format: Literal["plain", "html"] = "plain",
    from_email: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    Prepare a Gmail message with threading support.

    Args:
        subject: Email subject
        body: Email body content
        to: Optional recipient email address
        cc: Optional CC email address
        bcc: Optional BCC email address
        thread_id: Optional Gmail thread ID to reply within
        in_reply_to: Optional Message-ID of the message being replied to
        references: Optional chain of Message-IDs for proper threading
        body_format: Content type for the email body ('plain' or 'html')
        from_email: Optional sender email address

    Returns:
        Tuple of (raw_message, thread_id) where raw_message is base64 encoded
    """
    # Handle reply subject formatting
    reply_subject = subject
    if in_reply_to and not subject.lower().startswith('re:'):
        reply_subject = f"Re: {subject}"

    # Prepare the email
    normalized_format = body_format.lower()
    if normalized_format not in {"plain", "html"}:
        raise ValueError("body_format must be either 'plain' or 'html'.")

    message = MIMEText(body, normalized_format)
    message["Subject"] = reply_subject

    # Add sender if provided
    if from_email:
        message["From"] = from_email

    # Add recipients if provided
    if to:
        message["To"] = to
    if cc:
        message["Cc"] = cc
    if bcc:
        message["Bcc"] = bcc

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
            "  • Pass the Message IDs **as a list** to get_gmail_content()",
            "    e.g. get_gmail_content(operation='messages_batch', message_ids=[...])",
            "  • Pass the Thread IDs to get_gmail_content()",
            "    e.g. get_gmail_content(operation='thread', thread_id='...') or",
            "         get_gmail_content(operation='threads_batch', thread_ids=[...])",
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
@handle_http_errors("get_gmail_content", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def get_gmail_content(
    service,
    user_google_email: str,
    operation: Literal["message", "messages_batch", "attachment", "thread", "threads_batch"],
    message_id: Optional[str] = None,
    message_ids: Optional[List[str]] = None,
    attachment_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    thread_ids: Optional[List[str]] = None,
    format: Literal["full", "metadata"] = "full",
) -> str:
    """
    Retrieves Gmail content: messages, threads, or attachments.

    Args:
        user_google_email (str): The user's Google email address. Required.
        operation (str): Operation type: "message", "messages_batch", "attachment", "thread", "threads_batch".
        message_id (Optional[str]): Message ID (required for "message" and "attachment" operations).
        message_ids (Optional[List[str]]): List of message IDs (required for "messages_batch" operation).
        attachment_id (Optional[str]): Attachment ID (required for "attachment" operation).
        thread_id (Optional[str]): Thread ID (required for "thread" operation).
        thread_ids (Optional[List[str]]): List of thread IDs (required for "threads_batch" operation).
        format (Literal["full", "metadata"]): Message format for batch operations. "full" includes body, "metadata" only headers.

    Returns:
        str: Content based on operation type.

    Examples:
        # Get single message
        get_gmail_content(operation="message", message_id="abc123")

        # Get multiple messages
        get_gmail_content(operation="messages_batch", message_ids=["abc", "def"])

        # Get attachment
        get_gmail_content(operation="attachment", message_id="abc123", attachment_id="xyz")

        # Get single thread
        get_gmail_content(operation="thread", thread_id="thread123")

        # Get multiple threads
        get_gmail_content(operation="threads_batch", thread_ids=["t1", "t2"])
    """
    logger.info(f"[get_gmail_content] Operation: {operation}, Email: '{user_google_email}'")

    try:
        if operation == "message":
            if not message_id:
                raise ValueError("'message_id' is required for message operation")

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
                .get(userId="me", id=message_id, format="full")
                .execute
            )

            # Extract both text and HTML bodies
            payload = message_full.get("payload", {})
            bodies = _extract_message_bodies(payload)
            text_body = bodies.get("text", "")
            html_body = bodies.get("html", "")

            # Format body content with HTML fallback
            body_data = _format_body_content(text_body, html_body)

            # Extract attachment metadata
            attachments = _extract_attachments(payload)

            content_lines = [
                f"Subject: {subject}",
                f"From:    {sender}",
                f"\n--- BODY ---\n{body_data or '[No text/plain body found]'}",
            ]

            # Add attachment information if present
            if attachments:
                content_lines.append("\n--- ATTACHMENTS ---")
                for i, att in enumerate(attachments, 1):
                    size_kb = att['size'] / 1024
                    content_lines.append(
                        f"{i}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                        f"   Attachment ID: {att['attachmentId']}\n"
                        f"   Use get_gmail_content(operation='attachment', message_id='{message_id}', attachment_id='{att['attachmentId']}') to download"
                    )

            return "\n".join(content_lines)

        elif operation == "messages_batch":
            if not message_ids:
                raise ValueError("'message_ids' is required for messages_batch operation")

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
                    # Fallback to sequential processing
                    logger.warning(
                        f"[get_gmail_content] Batch API failed, falling back to sequential processing: {batch_error}"
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
                                    delay = 2 ** attempt
                                    logger.warning(
                                        f"[get_gmail_content] SSL error for message {mid} on attempt {attempt + 1}: {ssl_error}. Retrying in {delay}s..."
                                    )
                                    await asyncio.sleep(delay)
                                else:
                                    logger.error(
                                        f"[get_gmail_content] SSL error for message {mid} on final attempt: {ssl_error}"
                                    )
                                    return mid, None, ssl_error
                            except Exception as e:
                                return mid, None, e

                    # Process messages sequentially with small delays
                    for mid in chunk_ids:
                        mid_result, msg_data, error = await fetch_message_with_retry(mid)
                        results[mid_result] = {"data": msg_data, "error": error}
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

                            bodies = _extract_message_bodies(payload)
                            text_body = bodies.get("text", "")
                            html_body = bodies.get("html", "")
                            body_data = _format_body_content(text_body, html_body)

                            output_messages.append(
                                f"Message ID: {mid}\n"
                                f"Subject: {subject}\n"
                                f"From: {sender}\n"
                                f"Web Link: {_generate_gmail_web_url(mid)}\n"
                                f"\n{body_data}\n"
                            )

            # Combine all messages with separators
            final_output = f"Retrieved {len(message_ids)} messages:\n\n"
            final_output += "\n---\n\n".join(output_messages)
            return final_output

        elif operation == "attachment":
            if not message_id or not attachment_id:
                raise ValueError("'message_id' and 'attachment_id' are required for attachment operation")

            # Download attachment
            try:
                attachment = await asyncio.to_thread(
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=attachment_id)
                    .execute
                )
            except Exception as e:
                logger.error(f"[get_gmail_content] Failed to download attachment: {e}")
                return (
                    f"Error: Failed to download attachment. The attachment ID may have changed.\n"
                    f"Please fetch the message content again to get an updated attachment ID.\n\n"
                    f"Error details: {str(e)}"
                )

            # Format response with attachment data
            size_bytes = attachment.get('size', 0)
            size_kb = size_bytes / 1024 if size_bytes else 0

            result_lines = [
                "Attachment downloaded successfully!",
                f"Message ID: {message_id}",
                f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
                "\nBase64-encoded content (first 100 characters shown):",
                f"{attachment['data'][:100]}...",
                "\n\nThe full base64-encoded attachment data is available.",
                "To save: decode the base64 data and write to a file with the appropriate extension.",
                "\nNote: Attachment IDs are ephemeral. Always use IDs from the most recent message fetch."
            ]

            logger.info(f"[get_gmail_content] Successfully downloaded {size_kb:.1f} KB attachment")
            return "\n".join(result_lines)

        elif operation == "thread":
            if not thread_id:
                raise ValueError("'thread_id' is required for thread operation")

            # Fetch the complete thread with all messages
            thread_response = await asyncio.to_thread(
                service.users().threads().get(userId="me", id=thread_id, format="full").execute
            )

            return _format_thread_content(thread_response, thread_id)

        elif operation == "threads_batch":
            if not thread_ids:
                raise ValueError("'thread_ids' is required for threads_batch operation")

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
                    # Fallback to sequential processing
                    logger.warning(
                        f"[get_gmail_content] Batch API failed, falling back to sequential processing: {batch_error}"
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
                                    delay = 2 ** attempt
                                    logger.warning(
                                        f"[get_gmail_content] SSL error for thread {tid} on attempt {attempt + 1}: {ssl_error}. Retrying in {delay}s..."
                                    )
                                    await asyncio.sleep(delay)
                                else:
                                    logger.error(
                                        f"[get_gmail_content] SSL error for thread {tid} on final attempt: {ssl_error}"
                                    )
                                    return tid, None, ssl_error
                            except Exception as e:
                                return tid, None, e

                    # Process threads sequentially with small delays
                    for tid in chunk_ids:
                        tid_result, thread_data, error = await fetch_thread_with_retry(tid)
                        results[tid_result] = {"data": thread_data, "error": error}
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

        else:
            raise ValueError(f"Invalid operation: {operation}")

    except Exception as e:
        logger.error(f"[get_gmail_content] Error in {operation} operation: {e}")
        raise


@server.tool()
@handle_http_errors("send_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_SEND_SCOPE)
async def send_gmail_message(
    service,
    user_google_email: str,
    to: str = Body(..., description="Recipient email address."),
    subject: str = Body(..., description="Email subject."),
    body: str = Body(..., description="Email body content (plain text or HTML)."),
    body_format: Literal["plain", "html"] = Body(
        "plain", description="Email body format. Use 'plain' for plaintext or 'html' for HTML content."
    ),
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
        body (str): Email body content.
        body_format (Literal['plain', 'html']): Email body format. Defaults to 'plain'.
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

        # Send an HTML email
        send_gmail_message(
            to="user@example.com",
            subject="Hello",
            body="<strong>Hi there!</strong>",
            body_format="html"
        )

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
        body_format=body_format,
        from_email=user_google_email,
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
    body_format: Literal["plain", "html"] = Body(
        "plain", description="Email body format. Use 'plain' for plaintext or 'html' for HTML content."
    ),
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
        body_format (Literal['plain', 'html']): Email body format. Defaults to 'plain'.
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

        # Create a plaintext draft with CC and BCC
        draft_gmail_message(
            subject="Project Update",
            body="Here's the latest update...",
            to="user@example.com",
            cc="manager@example.com",
            bcc="archive@example.com"
        )

        # Create a HTML draft with CC and BCC
        draft_gmail_message(
            subject="Project Update",
            body="<strong>Hi there!</strong>",
            body_format="html",
            to="user@example.com",
            cc="manager@example.com",
            bcc="archive@example.com"
        )

        # Create a reply draft in plaintext
        draft_gmail_message(
            subject="Re: Meeting tomorrow",
            body="Thanks for the update!",
            to="user@example.com",
            thread_id="thread_123",
            in_reply_to="<message123@gmail.com>",
            references="<original@gmail.com> <message123@gmail.com>"
        )

        # Create a reply draft in HTML
        draft_gmail_message(
            subject="Re: Meeting tomorrow",
            body="<strong>Thanks for the update!</strong>",
            body_format="html,
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
        body_format=body_format,
        to=to,
        cc=cc,
        bcc=bcc,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
        references=references,
        from_email=user_google_email,
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

        # Extract both text and HTML bodies
        payload = message.get("payload", {})
        bodies = _extract_message_bodies(payload)
        text_body = bodies.get("text", "")
        html_body = bodies.get("html", "")

        # Format body content with HTML fallback
        body_data = _format_body_content(text_body, html_body)

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
                body_data,
                "",
            ]
        )

    return "\n".join(content_lines)


@server.tool()
@handle_http_errors("manage_gmail_label", service_type="gmail")
@require_google_service("gmail", GMAIL_LABELS_SCOPE)
async def manage_gmail_label(
    service,
    user_google_email: str,
    action: Literal["list", "create", "update", "delete"],
    name: Optional[str] = None,
    label_id: Optional[str] = None,
    label_list_visibility: Literal["labelShow", "labelHide"] = "labelShow",
    message_list_visibility: Literal["show", "hide"] = "show",
) -> str:
    """
    Manages Gmail labels: list, create, update, or delete labels.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["list", "create", "update", "delete"]): Action to perform.
        name (Optional[str]): Label name. Required for create, optional for update.
        label_id (Optional[str]): Label ID. Required for update and delete operations.
        label_list_visibility (Literal["labelShow", "labelHide"]): Whether the label is shown in the label list.
        message_list_visibility (Literal["show", "hide"]): Whether the label is shown in the message list.

    Returns:
        str: Result of the label operation.

    Examples:
        # List all labels
        manage_gmail_label(action="list")

        # Create a label
        manage_gmail_label(action="create", name="My Label")

        # Update a label
        manage_gmail_label(action="update", label_id="Label_123", name="Updated Name")

        # Delete a label
        manage_gmail_label(action="delete", label_id="Label_123")
    """
    logger.info(
        f"[manage_gmail_label] Invoked. Email: '{user_google_email}', Action: '{action}'"
    )

    if action == "list":
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

    elif action == "create":
        if not name:
            raise ValueError("Label name is required for create action.")

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
        if not label_id:
            raise ValueError("Label ID is required for update action.")

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
        if not label_id:
            raise ValueError("Label ID is required for delete action.")

        label = await asyncio.to_thread(
            service.users().labels().get(userId="me", id=label_id).execute
        )
        label_name = label["name"]

        await asyncio.to_thread(
            service.users().labels().delete(userId="me", id=label_id).execute
        )
        return f"Label '{label_name}' (ID: {label_id}) deleted successfully!"

    else:
        raise ValueError(f"Invalid action: {action}")


@server.tool()
@handle_http_errors("manage_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def manage_gmail_message(
    service,
    user_google_email: str,
    action: Literal["trash", "untrash", "delete"],
    target_type: Literal["message", "thread"] = "message",
    message_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> str:
    """
    Manages Gmail message/thread lifecycle: trash, untrash, and permanent delete operations.

    Note: For label operations (archive, spam, important, star, read/unread), use modify_gmail_labels instead.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (Literal["trash", "untrash", "delete"]): Action to perform:
            - "trash": Move to trash (recoverable within 30 days)
            - "untrash": Restore from trash
            - "delete": Permanently delete (WARNING: cannot be undone!)
        target_type (Literal["message", "thread"]): Whether to operate on a message or thread. Defaults to "message".
        message_id (Optional[str]): Message ID (required when target_type="message").
        thread_id (Optional[str]): Thread ID (required when target_type="thread").

    Returns:
        str: Confirmation message of the action performed.

    Examples:
        # Move message to trash
        manage_gmail_message(action="trash", target_type="message", message_id="msg123")

        # Restore message from trash
        manage_gmail_message(action="untrash", target_type="message", message_id="msg123")

        # Permanently delete a message (use with caution!)
        manage_gmail_message(action="delete", target_type="message", message_id="msg123")

        # Trash entire thread
        manage_gmail_message(action="trash", target_type="thread", thread_id="thread123")

    Note:
        For other operations, use modify_gmail_labels:
        - Archive: modify_gmail_labels(operation="single", message_id="...", remove_label_ids=["INBOX"])
        - Mark spam: modify_gmail_labels(operation="single", message_id="...", add_label_ids=["SPAM"])
        - Star: modify_gmail_labels(operation="single", message_id="...", add_label_ids=["STARRED"])
        - Mark important: modify_gmail_labels(operation="single", message_id="...", add_label_ids=["IMPORTANT"])
        - Mark read: modify_gmail_labels(operation="single", message_id="...", remove_label_ids=["UNREAD"])
    """
    logger.info(
        f"[manage_gmail_message] Action: {action}, Target: {target_type}, Email: '{user_google_email}'"
    )

    # Validate inputs
    if target_type == "message" and not message_id:
        raise ValueError("'message_id' is required when target_type='message'")
    if target_type == "thread" and not thread_id:
        raise ValueError("'thread_id' is required when target_type='thread'")

    # Trash/untrash/delete actions use dedicated API endpoints
    if action == "trash":
        if target_type == "message":
            await asyncio.to_thread(
                service.users().messages().trash(userId="me", id=message_id).execute
            )
            return f"Message moved to trash successfully!\nMessage ID: {message_id}\nNote: Messages in trash are permanently deleted after 30 days."
        else:  # thread
            await asyncio.to_thread(
                service.users().threads().trash(userId="me", id=thread_id).execute
            )
            return f"Thread moved to trash successfully!\nThread ID: {thread_id}\nNote: Threads in trash are permanently deleted after 30 days."

    elif action == "untrash":
        if target_type == "message":
            await asyncio.to_thread(
                service.users().messages().untrash(userId="me", id=message_id).execute
            )
            return f"Message restored from trash successfully!\nMessage ID: {message_id}"
        else:  # thread
            await asyncio.to_thread(
                service.users().threads().untrash(userId="me", id=thread_id).execute
            )
            return f"Thread restored from trash successfully!\nThread ID: {thread_id}"

    elif action == "delete":
        if target_type == "message":
            await asyncio.to_thread(
                service.users().messages().delete(userId="me", id=message_id).execute
            )
            return f"⚠️ Message PERMANENTLY deleted!\nMessage ID: {message_id}\nWARNING: This action cannot be undone."
        else:  # thread
            await asyncio.to_thread(
                service.users().threads().delete(userId="me", id=thread_id).execute
            )
            return f"⚠️ Thread PERMANENTLY deleted!\nThread ID: {thread_id}\nWARNING: This action cannot be undone."

    else:
        raise ValueError(f"Invalid action: {action}")


@server.tool()
@handle_http_errors("modify_gmail_labels", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def modify_gmail_labels(
    service,
    user_google_email: str,
    operation: Literal["single", "batch"],
    message_id: Optional[str] = None,
    message_ids: Optional[List[str]] = None,
    add_label_ids: List[str] = Field(default=[], description="Label IDs to add."),
    remove_label_ids: List[str] = Field(default=[], description="Label IDs to remove."),
) -> str:
    """
    Adds or removes labels from Gmail messages (single or batch).

    Common operations using system labels:
    - Archive: Remove INBOX label (message stays in All Mail)
    - Mark as spam: Add SPAM label
    - Mark as important: Add IMPORTANT label
    - Star: Add STARRED label
    - Mark as read: Remove UNREAD label
    - Mark as unread: Add UNREAD label

    Args:
        user_google_email (str): The user's Google email address. Required.
        operation (Literal["single", "batch"]): Operation type: "single" for one message, "batch" for multiple.
        message_id (Optional[str]): Message ID (required for "single" operation).
        message_ids (Optional[List[str]]): List of message IDs (required for "batch" operation).
        add_label_ids (List[str]): List of label IDs to add to the message(s).
        remove_label_ids (List[str]): List of label IDs to remove from the message(s).

    Returns:
        str: Confirmation message of the label changes applied.

    Examples:
        # Archive a message (remove from inbox)
        modify_gmail_labels(
            operation="single",
            message_id="msg123",
            remove_label_ids=["INBOX"]
        )

        # Mark as spam
        modify_gmail_labels(
            operation="single",
            message_id="msg123",
            add_label_ids=["SPAM"]
        )

        # Star and mark as important
        modify_gmail_labels(
            operation="single",
            message_id="msg123",
            add_label_ids=["STARRED", "IMPORTANT"]
        )

        # Mark as read
        modify_gmail_labels(
            operation="single",
            message_id="msg123",
            remove_label_ids=["UNREAD"]
        )

        # Archive multiple messages (batch operation)
        modify_gmail_labels(
            operation="batch",
            message_ids=["msg1", "msg2", "msg3"],
            remove_label_ids=["INBOX"]
        )

        # Add custom label and remove from inbox
        modify_gmail_labels(
            operation="single",
            message_id="msg123",
            add_label_ids=["Label_12345"],
            remove_label_ids=["INBOX"]
        )
    """
    logger.info(
        f"[modify_gmail_labels] Operation: {operation}, Email: '{user_google_email}'"
    )

    if not add_label_ids and not remove_label_ids:
        raise ValueError(
            "At least one of add_label_ids or remove_label_ids must be provided."
        )

    if operation == "single":
        if not message_id:
            raise ValueError("'message_id' is required for single operation")

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

    elif operation == "batch":
        if not message_ids:
            raise ValueError("'message_ids' is required for batch operation")

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

    else:
        raise ValueError(f"Invalid operation: {operation}")
