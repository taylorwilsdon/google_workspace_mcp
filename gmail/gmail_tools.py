"""
Google Gmail MCP Tools

This module provides MCP tools for interacting with the Gmail API.
"""

import logging
import asyncio
import base64
import binascii
import json
import re
import mimetypes
import html
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Optional, List, Dict, Literal, Any, Union
from urllib.parse import unquote, urlparse, urlunsplit

from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr

import httpx
from fastmcp.exceptions import ToolError as ToolExecutionError
from mcp.types import ToolAnnotations

from pydantic import Field
from pydantic.json_schema import SkipJsonSchema

from auth.oauth_config import is_stateless_mode
from auth.service_decorator import require_google_service
from core.attachment_storage import (
    get_attachment_storage,
    get_attachment_url,
    STORAGE_DIR,
)
from core.file_limits import (
    FileTooLargeError,
    ensure_within_file_size_limit,
    get_max_file_bytes,
)
from core.config import (
    get_transport_mode,
    WORKSPACE_EXTERNAL_URL,
    WORKSPACE_MCP_BASE_URI,
    WORKSPACE_MCP_PORT,
)
from core.http_utils import ssrf_safe_stream
from core.utils import (
    GOOGLE_API_WRITE_RETRIES,
    handle_http_errors,
    validate_file_path,
    UserInputError,
    StringList,
    JsonDict,
    DictList,
)
from core.server import server
from auth.scopes import (
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_LABELS_SCOPE,
)
from gmail.gmail_helpers import (
    GMAIL_METADATA_HEADERS,
    RAW_BODY_TRUNCATE_LIMIT,
    _analyze_thread_ownership_impl,
    _build_forward_content,
    _derive_reply_all_recipients,
    _derive_reply_headers,
    _fetch_with_retry,
    _get_send_as_identity_and_signature,
    _get_send_as_signature_html_for_tool,
    _http_error_status,
    _retryable_result_ids,
    _signature_html_to_text,
    build_label_color,
    html_to_text_preserving_breaks,
)

logger = logging.getLogger(__name__)

GMAIL_BATCH_SIZE = 25
# Smaller chunks for search-result header fetches: the batch endpoint executes
# every get in a chunk concurrently server-side, and chunks of 25 metadata gets
# trip Gmail's per-user concurrency limit ("Too many concurrent requests").
GMAIL_SEARCH_HEADER_BATCH_SIZE = 10
GMAIL_REQUEST_DELAY = 0.1
GMAIL_RATE_LIMIT_BACKOFF = 2.0
HTML_BODY_TRUNCATE_LIMIT = 20000

# Keep ``None`` valid at runtime without publishing it as an ``anyOf`` branch.
# Cowork needs a top-level array schema, while Moonshot rejects the previous
# parent-level array override alongside a nullable ``anyOf``.
_OptionalLabelIdList = Union[StringList, SkipJsonSchema[None]]

LOW_VALUE_TEXT_PLACEHOLDERS = (
    "your client does not support html",
    "view this email in your browser",
    "open this email in your browser",
)
LOW_VALUE_TEXT_FOOTER_MARKERS = (
    "mailing list",
    "mailman/listinfo",
    "unsubscribe",
    "list-unsubscribe",
    "manage preferences",
)
LOW_VALUE_TEXT_HTML_DIFF_MIN = 80
CONTENT_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-@]*$")


class _HTMLTextExtractor(HTMLParser):
    """Extract readable text from HTML using stdlib."""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip = True
            return
        if tag == "br" and not self._skip:
            self._text.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self) -> str:
        return " ".join("".join(self._text).split())


def _html_to_text(html: str) -> str:
    """Convert HTML to readable plain text."""
    try:
        parser = _HTMLTextExtractor()
        parser.feed(html)
        return parser.get_text()
    except Exception:
        return html


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
                decoded_data = base64.urlsafe_b64decode(body_data).decode(
                    "utf-8", errors="ignore"
                )
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
            decoded_data = base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                "utf-8", errors="ignore"
            )
            mime_type = payload.get("mimeType", "")
            if mime_type == "text/plain" and not text_body:
                text_body = decoded_data
            elif mime_type == "text/html" and not html_body:
                html_body = decoded_data
        except Exception as e:
            logger.warning(f"Failed to decode main payload body: {e}")

    return {"text": text_body, "html": html_body}


def _format_body_content(
    text_body: str,
    html_body: str,
    body_format: Literal["text", "html"] = "text",
) -> str:
    """
    Helper function to format message body content with HTML fallback and truncation.
    Detects useless text/plain fallbacks (e.g., "Your client does not support HTML").

    Args:
        text_body: Plain text body content
        html_body: HTML body content
        body_format: Output format - "text" converts HTML to plaintext (default),
                     "html" returns raw HTML body as-is

    Returns:
        Formatted body content string
    """
    if body_format == "html":
        html_stripped = html_body.strip()
        if html_stripped:
            if len(html_stripped) > HTML_BODY_TRUNCATE_LIMIT:
                return (
                    html_stripped[:HTML_BODY_TRUNCATE_LIMIT]
                    + "\n\n[Content truncated...]"
                )
            return html_stripped
        # Fall back to text body when no HTML is available
        text_stripped = text_body.strip()
        return text_stripped if text_stripped else "[No readable content found]"

    text_stripped = text_body.strip()
    html_stripped = html_body.strip()
    html_text = _html_to_text(html_stripped).strip() if html_stripped else ""

    plain_lower = " ".join(text_stripped.split()).lower()
    html_lower = " ".join(html_text.split()).lower()
    plain_is_low_value = plain_lower and (
        any(marker in plain_lower for marker in LOW_VALUE_TEXT_PLACEHOLDERS)
        or (
            any(marker in plain_lower for marker in LOW_VALUE_TEXT_FOOTER_MARKERS)
            and len(html_lower) >= len(plain_lower) + LOW_VALUE_TEXT_HTML_DIFF_MIN
        )
        or (
            len(html_lower) >= len(plain_lower) + LOW_VALUE_TEXT_HTML_DIFF_MIN
            and html_lower.endswith(plain_lower)
        )
    )

    # Prefer plain text, but fall back to HTML when plain text is empty or clearly low-value.
    use_html = html_text and (
        not text_stripped or "<!--" in text_stripped or plain_is_low_value
    )

    if use_html:
        content = html_text
        if len(content) > HTML_BODY_TRUNCATE_LIMIT:
            content = content[:HTML_BODY_TRUNCATE_LIMIT] + "\n\n[Content truncated...]"
        return content
    elif text_stripped:
        return text_body
    else:
        return "[No readable content found]"


def _truncate_content(content: str, limit: int) -> str:
    """Truncate content to a readable length for tool responses."""
    if len(content) <= limit:
        return content
    return content[:limit] + "\n\n[Content truncated...]"


def _decode_raw_mime_content(raw_data: str) -> str:
    """Decode Gmail raw MIME content into readable text."""
    if not raw_data:
        return "[No raw content found]"

    padded_raw = raw_data + "=" * (-len(raw_data) % 4)
    try:
        decoded_raw = base64.urlsafe_b64decode(padded_raw).decode(
            "utf-8", errors="replace"
        )
    except (binascii.Error, ValueError) as exc:
        return f"[Failed to decode raw MIME: {exc}]"

    return _truncate_content(decoded_raw, RAW_BODY_TRUNCATE_LIMIT)


def _format_message_header_lines(
    headers: Dict[str, str], message_id: Optional[str] = None
) -> List[str]:
    """Format standard Gmail message headers for response output."""
    subject = headers.get("Subject", "(no subject)")
    sender = headers.get("From", "(unknown sender)")
    reply_to = headers.get("Reply-To", "")
    to = headers.get("To", "")
    cc = headers.get("Cc", "")
    rfc822_msg_id = headers.get("Message-ID", "")
    in_reply_to = headers.get("In-Reply-To", "")
    references = headers.get("References", "")
    list_unsub = headers.get("List-Unsubscribe", "")
    precedence = headers.get("Precedence", "")
    list_id = headers.get("List-Id", "")

    content_lines = []
    if message_id:
        content_lines.append(f"Message ID: {message_id}")

    content_lines.extend(
        [
            f"Subject: {subject}",
            f"From: {sender}",
            f"Date: {headers.get('Date', '(unknown date)')}",
        ]
    )
    if reply_to:
        content_lines.append(f"Reply-To: {reply_to}")

    if rfc822_msg_id:
        content_lines.append(f"Message-ID: {rfc822_msg_id}")
    if in_reply_to:
        content_lines.append(f"In-Reply-To: {in_reply_to}")
    if references:
        content_lines.append(f"References: {references}")
    content_lines.append(
        f"To: {to}" if "To" in headers else "To: [not present in Gmail response]"
    )
    content_lines.append(
        f"Cc: {cc}" if "Cc" in headers else "Cc: [not present in Gmail response]"
    )
    if list_unsub:
        content_lines.append(f"List-Unsubscribe: {list_unsub}")
    if precedence:
        content_lines.append(f"Precedence: {precedence}")
    if list_id:
        content_lines.append(f"List-Id: {list_id}")

    return content_lines


async def _export_full_message(
    service,
    message_id: str,
    headers: Dict[str, str],
    body_format: Literal["text", "html", "raw"],
    declared_size: Optional[int] = None,
) -> str:
    """
    Return a message's complete, untruncated content: saved to local storage and
    referenced by download URL (HTTP transport) or file path (stdio transport), or —
    in stateless mode, where there is no storage — inlined in the response.

    Whenever a file is written the body is kept out of the returned string, which is
    the point of the export: large messages are handed off out-of-band instead of
    through the model context. Either way no truncation limit applies.

    Args:
        service: Authenticated Gmail API service.
        message_id: The message to export.
        headers: Already-fetched message headers, used for the summary and filename.
        body_format: "raw" saves the byte-exact RFC 5322 message as .eml,
            "html" saves the raw HTML body, "text" saves the plaintext body.

    Returns:
        str: Header summary plus the saved file's URL or path (or the inline body in
            stateless mode), or an "Error:" string.
    """
    subject = headers.get("Subject", "message") or "message"
    notes: List[str] = []

    # Gmail's full/raw endpoints return their payload as one JSON response, so
    # use the metadata response's sizeEstimate to fail closed before asking the
    # client library to buffer and decode an oversized message.
    try:
        ensure_within_file_size_limit(
            declared_size,
            file_name=subject,
            file_id=message_id,
            kind="message export",
        )
    except FileTooLargeError as exc:
        return str(exc)

    if body_format == "raw":
        message_raw = await asyncio.to_thread(
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute
        )
        raw_data = message_raw.get("raw", "")
        if not raw_data:
            return "Error: message has no raw content to export."
        padded_raw = raw_data + "=" * (-len(raw_data) % 4)
        try:
            content_bytes = base64.urlsafe_b64decode(padded_raw)
        except (binascii.Error, ValueError) as exc:
            return f"Error: failed to decode raw MIME content: {exc}"
        mime_type = "message/rfc822"
        extension = ".eml"
    else:
        message_full = await asyncio.to_thread(
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute
        )
        bodies = _extract_message_bodies(message_full.get("payload", {}))
        # Preserve the body exactly — the export must be complete — so use .strip()
        # only to test for emptiness, never to trim the content that gets saved.
        text_body = bodies.get("text", "")
        html_body = bodies.get("html", "")

        if body_format == "html":
            if html_body.strip():
                content_str = html_body
                mime_type = "text/html"
                extension = ".html"
            elif text_body.strip():
                # No HTML part; fall back to plaintext and label it honestly.
                content_str = text_body
                mime_type = "text/plain"
                extension = ".txt"
                notes.append(
                    "No HTML body present; exported the plaintext body instead."
                )
            else:
                content_str = ""
                mime_type = "text/html"
                extension = ".html"
        else:  # text
            if text_body.strip():
                content_str = text_body
            elif html_body.strip():
                content_str = _html_to_text(html_body)
            else:
                content_str = ""
            mime_type = "text/plain"
            extension = ".txt"

        if not content_str.strip():
            return "Error: message has no readable body content to export."
        content_bytes = content_str.encode("utf-8")

    # sizeEstimate is intentionally approximate. Enforce the exact decoded
    # size too before producing another representation or saving the export.
    try:
        ensure_within_file_size_limit(
            len(content_bytes),
            file_name=subject,
            file_id=message_id,
            kind="message export",
        )
    except FileTooLargeError as exc:
        return str(exc)

    # Stateless deployments have no persistent storage to hand a file reference off
    # from, but the guarantee callers actually want is "complete and untruncated".
    # Inline delivery satisfies that; it only costs model context.
    stateless = is_stateless_mode()

    size_bytes = len(content_bytes)
    size_kb = size_bytes / 1024
    result_lines = _format_message_header_lines(headers)
    result_lines.append(
        "\n--- FULL MESSAGE ---" if stateless else "\n--- FULL MESSAGE EXPORT ---"
    )
    result_lines.append(f"Format: {extension.lstrip('.')}")
    result_lines.append(f"Size: {size_kb:.1f} KB ({size_bytes} bytes)")

    if stateless:
        for note in notes:
            result_lines.append(f"Note: {note}")
        result_lines.append(
            "\nStateless mode: no file storage available, so the complete message is "
            "included inline below instead of as a download URL. It is NOT truncated."
        )
        result_lines.append(
            "\n--- BODY (COMPLETE, NOT TRUNCATED) ---\n"
            f"{content_bytes.decode('utf-8', errors='replace')}"
        )
        logger.info(
            f"[get_gmail_message_content] Returned {size_kb:.1f} KB "
            f"({extension.lstrip('.')}) inline (stateless mode)"
        )
        return "\n".join(result_lines)

    # Write on a worker thread so a large export doesn't block the event loop.
    # Cap the sender-controlled subject so a pathologically long Subject can't overflow
    # the filesystem's filename limit, and surface a clean error if the write fails.
    storage = get_attachment_storage()

    def _save_export():
        return storage.save_attachment_bytes(
            file_bytes=content_bytes,
            filename=f"{subject[:80]}{extension}",
            mime_type=mime_type,
        )

    try:
        saved = await asyncio.to_thread(_save_export)
    except OSError as exc:
        logger.error(f"[get_gmail_message_content] Failed to save message: {exc}")
        return f"Error: failed to save message to storage: {exc}"

    result_lines.append(f"Saved filename: {Path(saved.path).name}")
    for note in notes:
        result_lines.append(f"Note: {note}")

    if get_transport_mode() == "stdio":
        result_lines.append(f"\n📎 Saved to: {saved.path}")
        result_lines.append(
            "\nThe full message has been written to disk and can be read directly "
            "from the file path (its content is NOT included above)."
        )
    else:
        result_lines.append(f"\n📎 Download URL: {get_attachment_url(saved.file_id)}")
        result_lines.append(
            "\nFetch the full message from the URL above (content is NOT included "
            "in this response). The file will expire after 1 hour."
        )

    logger.info(
        f"[get_gmail_message_content] Exported {size_kb:.1f} KB "
        f"({extension.lstrip('.')}) to {saved.path}"
    )
    return "\n".join(result_lines)


def _build_message_get_request(
    service,
    message_id: str,
    message_format: Literal["metadata", "full", "raw"],
):
    """Build a Gmail messages.get request for the requested format."""
    request_kwargs = {"userId": "me", "id": message_id, "format": message_format}
    if message_format == "metadata":
        request_kwargs["metadataHeaders"] = GMAIL_METADATA_HEADERS
    return service.users().messages().get(**request_kwargs)


def _validate_message_batch_options(
    response_format: Literal["full", "metadata"],
    body_format: Literal["text", "html", "raw"],
) -> None:
    """Reject incompatible output combinations for batch message reads."""
    if response_format == "metadata" and body_format != "text":
        raise UserInputError(
            "body_format='html' and body_format='raw' require format='full'."
        )


async def _fetch_message_with_retry(
    service,
    message_id: str,
    message_format: Literal["metadata", "full", "raw"],
    log_prefix: str,
    max_retries: int = 3,
):
    """Fetch a single Gmail message, retrying transient failures."""
    return await _fetch_with_retry(
        lambda: _build_message_get_request(
            service, message_id=message_id, message_format=message_format
        ),
        item_id=message_id,
        item_label="message",
        log_prefix=log_prefix,
        max_retries=max_retries,
    )


async def _fetch_thread_with_retry(
    service,
    thread_id: str,
    log_prefix: str,
    max_retries: int = 3,
):
    """Fetch a single Gmail thread, retrying transient failures."""
    return await _fetch_with_retry(
        lambda: service.users().threads().get(userId="me", id=thread_id, format="full"),
        item_id=thread_id,
        item_label="thread",
        log_prefix=log_prefix,
        max_retries=max_retries,
    )


async def _fetch_raw_message_contents(
    service, message_ids: List[str], log_prefix: str
) -> Dict[str, str]:
    """Fetch decoded raw MIME content for a set of Gmail message IDs."""
    raw_contents: Dict[str, str] = {}
    for message_id in message_ids:
        _, raw_message, raw_error = await _fetch_message_with_retry(
            service,
            message_id=message_id,
            message_format="raw",
            log_prefix=log_prefix,
        )
        raw_contents[message_id] = (
            _decode_raw_mime_content(raw_message.get("raw", ""))
            if raw_message
            else f"[Failed to fetch raw MIME: {raw_error}]"
        )
        await asyncio.sleep(GMAIL_REQUEST_DELAY)

    return raw_contents


def _append_signature_to_body(
    body: str, body_format: Literal["plain", "html"], signature_html: str
) -> str:
    """Append a Gmail signature to the outgoing body, preserving body format."""
    if not signature_html or not signature_html.strip():
        return body

    if body_format == "html":
        separator = "<br><br>" if body.strip() else ""
        return f"{body}{separator}{signature_html}"

    signature_text = _signature_html_to_text(signature_html).strip()
    if not signature_text:
        return body
    separator = "\n\n" if body.strip() else ""
    return f"{body}{separator}{signature_text}"


async def _fetch_original_for_quote(
    service, thread_id: str, in_reply_to: Optional[str] = None
) -> Optional[dict]:
    """Fetch the original message from a thread for quoting in a reply.

    When *in_reply_to* is provided the function looks for that specific
    Message-ID inside the thread.  Otherwise it falls back to the last
    message in the thread.

    Returns a dict with keys: sender, date, text_body, html_body -- or
    *None* when the message cannot be retrieved.
    """
    context = await _fetch_thread_reply_context(
        service, thread_id, in_reply_to=in_reply_to, include_bodies=True
    )
    if not context or not context.get("target"):
        return None

    target = context["target"]
    return {
        "sender": target.get("from") or "unknown",
        "date": target.get("date", ""),
        "text_body": target.get("text_body", ""),
        "html_body": target.get("html_body", ""),
    }


def _build_quoted_reply_body(
    reply_body: str,
    body_format: Literal["plain", "html"],
    signature_html: str,
    original: dict,
) -> str:
    """Assemble reply body + signature + quoted original message.

    Layout:
        reply_body
        -- signature --
        On {date}, {sender} wrote:
        > quoted original
    """
    if original.get("date"):
        attribution = f"On {original['date']}, {original['sender']} wrote:"
    else:
        attribution = f"{original['sender']} wrote:"

    if body_format == "html":
        # Signature
        sig_block = ""
        if signature_html and signature_html.strip():
            sig_block = f"<br><br>{signature_html}"

        # Quoted original
        orig_html = original.get("html_body") or ""
        if not orig_html:
            orig_text = original.get("text_body", "")
            orig_html = f"<pre>{html.escape(orig_text)}</pre>"

        quote_block = (
            '<br><br><div class="gmail_quote">'
            f"<span>{html.escape(attribution)}</span><br>"
            '<blockquote style="margin:0 0 0 .8ex;border-left:1px solid #ccc;padding-left:1ex">'
            f"{orig_html}"
            "</blockquote></div>"
        )
        return f"{reply_body}{sig_block}{quote_block}"

    # Plain text path
    sig_block = ""
    if signature_html and signature_html.strip():
        sig_text = _signature_html_to_text(signature_html).strip()
        if sig_text:
            sig_block = f"\n\n{sig_text}"

    orig_text = original.get("text_body") or ""
    if not orig_text and original.get("html_body"):
        orig_text = _html_to_text(original["html_body"])
    quoted_lines = "\n".join(f"> {line}" for line in orig_text.splitlines())

    return f"{reply_body}{sig_block}\n\n{attribution}\n{quoted_lines}"


def _format_attachment_result(attached_count: int, requested_count: int) -> str:
    """Format attachment result message for user-facing responses."""
    if requested_count <= 0:
        return ""
    if attached_count == requested_count:
        return f" with {attached_count} attachment(s)"
    return f" with {attached_count}/{requested_count} attachment(s) attached"


def _format_attachment_error(
    file_path: Optional[str], filename: Optional[str], error: Exception
) -> str:
    """Convert attachment processing failures into user-facing guidance."""
    label = filename or file_path or "attachment"
    detail = str(error)

    if file_path and isinstance(error, ValueError):
        if "outside permitted directories" in detail:
            detail = (
                "local file access is limited to the server's permitted directories, "
                f"so '{file_path}' could not be read. Files on external mounts such as "
                "/run/media may be blocked; move the file into the managed attachment "
                "directory or another allowed directory, or set ALLOWED_FILE_DIRS."
            )

    return f"{label}: {detail}"


def _normalize_attachment_content_id(content_id: Any) -> str:
    """Return a header-safe Content-ID value without surrounding brackets."""
    raw = str(content_id)
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise ValueError("content_id contains invalid control characters")

    cid_value = raw.strip().strip("<>").strip()
    if not cid_value:
        raise ValueError("content_id must not be empty")
    if not CONTENT_ID_SAFE_RE.fullmatch(cid_value):
        raise ValueError(
            "content_id contains invalid characters; use letters, digits, "
            "'.', '_', '+', '-', or '@'"
        )
    return cid_value


def _format_base64_content_block(urlsafe_b64_data: str) -> List[str]:
    """
    Convert Gmail's URL-safe base64 attachment data to standard base64 and
    format it as a labeled block of result lines.

    The Gmail API returns attachment bodies in URL-safe base64 (per RFC 4648).
    ``draft_gmail_message`` and most stdlib consumers expect standard base64
    (``base64.b64decode``). Converting here keeps the response self-contained
    so a caller can pass the bytes straight back into the draft flow without
    knowing about the alphabet difference.

    Args:
        urlsafe_b64_data: URL-safe base64 string as returned by Gmail.

    Returns:
        A list of strings to extend onto ``result_lines``. On failure to
        decode, returns a single warning line instead of raising.
    """
    try:
        raw_bytes = base64.urlsafe_b64decode(urlsafe_b64_data)
        standard_b64 = base64.b64encode(raw_bytes).decode("ascii")
        return [
            f"\n📦 Base64 content ({len(standard_b64)} chars, standard base64):",
            standard_b64,
        ]
    except (binascii.Error, ValueError) as e:
        logger.warning(
            f"[get_gmail_attachment_content] Failed to convert attachment "
            f"to standard base64: {e}"
        )
        return [f"\n⚠️ Could not include base64 content: {e}"]


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
            attachments.append(
                {
                    "filename": part["filename"],
                    "mimeType": part.get("mimeType", "application/octet-stream"),
                    "size": part.get("body", {}).get("size", 0),
                    "attachmentId": part["body"]["attachmentId"],
                }
            )

        # Recursively search sub-parts
        if "parts" in part:
            for subpart in part["parts"]:
                search_parts(subpart)

    # Start searching from the root payload
    search_parts(payload)
    return attachments


def _find_attachment_metadata(payload: dict, attachment_id: str) -> Optional[dict]:
    """Find one attachment by ID without requiring it to have a filename."""
    body = payload.get("body") or {}
    if body.get("attachmentId") == attachment_id:
        return {
            "filename": payload.get("filename") or None,
            "mimeType": payload.get("mimeType") or "application/octet-stream",
            "size": body.get("size"),
        }
    for part in payload.get("parts") or []:
        match = _find_attachment_metadata(part, attachment_id)
        if match is not None:
            return match
    return None


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
    target_headers = {name.lower(): name for name in header_names}
    for header in payload.get("headers", []):
        header_name_lower = header["name"].lower()
        if header_name_lower in target_headers:
            # Store using the original requested casing
            target_name = target_headers[header_name_lower]
            value = header["value"]
            if header_name_lower in {"to", "cc"} and target_name in headers:
                headers[target_name] = ", ".join(
                    part for part in (headers[target_name], value) if part
                )
            else:
                headers[target_name] = value
    return headers


async def _fetch_thread_reply_context(
    service,
    thread_id: str,
    in_reply_to: Optional[str] = None,
    include_bodies: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch reply metadata for a thread, optionally including message bodies."""
    header_names = [
        "Message-ID",
        "In-Reply-To",
        "References",
        "Subject",
        "From",
        "Reply-To",
        "To",
        "Cc",
        "Date",
    ]

    try:
        request_kwargs = {
            "userId": "me",
            "id": thread_id,
            "format": "full" if include_bodies else "metadata",
        }
        if not include_bodies:
            request_kwargs["metadataHeaders"] = header_names

        request = service.users().threads().get(**request_kwargs)
        thread = await asyncio.to_thread(request.execute)
    except Exception as e:
        logger.warning(f"Failed to fetch reply context for thread {thread_id}: {e}")
        return None

    messages = thread.get("messages", [])
    if not messages:
        return None

    message_contexts = []
    eligible_contexts = []
    for msg in messages:
        labels = msg.get("labelIds", [])
        payload = msg.get("payload", {})
        headers = _extract_headers(payload, header_names)
        context = {
            "message_id": headers.get("Message-ID"),
            "in_reply_to": headers.get("In-Reply-To"),
            "references": headers.get("References"),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "reply_to": headers.get("Reply-To", ""),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "date": headers.get("Date", ""),
        }
        if include_bodies:
            bodies = _extract_message_bodies(payload)
            context["text_body"] = bodies.get("text", "")
            context["html_body"] = bodies.get("html", "")
        message_contexts.append(context)
        # Automatic selection only considers actual sent or received messages.
        # Keep every context above so an explicit In-Reply-To can still resolve
        # to the exact message the caller selected.
        if context["message_id"] and "DRAFT" not in labels and "TRASH" not in labels:
            eligible_contexts.append(context)

    target = None
    if in_reply_to:
        for msg in message_contexts:
            if msg.get("message_id") == in_reply_to:
                target = msg
                break
    elif eligible_contexts:
        target = eligible_contexts[-1]

    return {
        "messages": eligible_contexts,
        "message_ids": [
            msg["message_id"] for msg in eligible_contexts if msg.get("message_id")
        ],
        "target": target,
    }


async def _fetch_thread_message_ids(service, thread_id: str) -> List[str]:
    """
    Fetch Message-ID headers from a Gmail thread for reply threading.

    Args:
        service: Gmail API service instance
        thread_id: Gmail thread ID

    Returns:
        Message-IDs in thread order. Returns an empty list on failure.
    """
    context = await _fetch_thread_reply_context(service, thread_id)
    if not context:
        return []
    return context.get("message_ids", [])


MAX_EMAIL_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB Gmail attachment limit


def _redact_url(url: str) -> str:
    """Remove query/fragment components before surfacing a URL in errors or logs."""
    parsed = urlparse(url)
    if not parsed.scheme and not parsed.netloc:
        return parsed.path or url
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _get_trusted_attachment_origins() -> set[tuple[str, str]]:
    """Return local origins allowed to resolve /attachments/{id} from disk."""
    origins: set[tuple[str, str]] = set()
    for origin in (
        WORKSPACE_EXTERNAL_URL,
        f"{WORKSPACE_MCP_BASE_URI}:{WORKSPACE_MCP_PORT}",
    ):
        if not origin:
            continue
        parsed = urlparse(origin)
        if parsed.scheme and parsed.netloc:
            origins.add((parsed.scheme.lower(), parsed.netloc.lower()))
    return origins


def _read_attachment_bytes(file_path: Path) -> bytes:
    """Read a local attachment after enforcing the Gmail size limit."""
    size_bytes = file_path.stat().st_size
    if size_bytes > MAX_EMAIL_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment exceeds {MAX_EMAIL_ATTACHMENT_BYTES} bytes: {file_path.name}"
        )
    return file_path.read_bytes()


_ATTACHMENT_TIMEOUT = httpx.Timeout(connect=10, read=30, write=10, pool=10)


async def _download_attachment_bytes(url: str) -> tuple[bytes, httpx.Response]:
    """Download an attachment with streaming size enforcement."""
    total_bytes = 0
    chunks: list[bytes] = []
    redacted_url = _redact_url(url)

    async with ssrf_safe_stream(url, timeout=_ATTACHMENT_TIMEOUT) as resp:
        if resp.status_code != 200:
            raise ValueError(
                f"Failed to fetch attachment URL {redacted_url} (status {resp.status_code})"
            )

        async for chunk in resp.aiter_bytes(chunk_size=256 * 1024):
            total_bytes += len(chunk)
            if total_bytes > MAX_EMAIL_ATTACHMENT_BYTES:
                raise ValueError(
                    f"Attachment from {redacted_url} exceeds 25 MB Gmail limit ({total_bytes} bytes)"
                )
            chunks.append(chunk)

        return b"".join(chunks), resp


def _build_attachment_error_entry(
    attachment: Dict[str, Any], exc: Exception
) -> Dict[str, Any]:
    """Preserve failed attachment context so message creation can continue."""
    failed_attachment = dict(attachment)
    if "url" in failed_attachment:
        failed_attachment["display_url"] = _redact_url(str(failed_attachment["url"]))
    failed_attachment["error"] = str(exc)
    failed_attachment["error_type"] = type(exc).__name__
    return failed_attachment


def _format_resolved_attachment_error(attachment: Dict[str, Any]) -> str:
    """Render a pre-resolved attachment failure for user-facing reporting."""
    label = (
        attachment.get("filename")
        or attachment.get("display_url")
        or (
            _redact_url(str(attachment["url"]))
            if attachment.get("url")
            else attachment.get("path")
        )
        or "attachment"
    )
    detail = attachment.get("error", "attachment could not be resolved")
    error_type = attachment.get("error_type")
    if error_type:
        detail = f"{error_type}: {detail}"
    return f"{label}: {detail}"


def _try_read_local_attachment(url: str) -> Optional[tuple[bytes, str, Optional[str]]]:
    """Try to resolve a URL as an MCP attachment stored on local disk.

    Returns (data, filename, mime_type) if the URL points to a local
    ``/attachments/{file_id}`` resource, otherwise ``None``.
    """
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "attachments":
        return None
    if parsed.netloc:
        origin = (parsed.scheme.lower(), parsed.netloc.lower())
        if origin not in _get_trusted_attachment_origins():
            return None

    file_id = parts[1]
    storage = get_attachment_storage()
    metadata = storage.get_attachment_metadata(file_id)
    if metadata is None:
        logger.debug(
            "Attachment metadata missing for %s; refusing local fallback under %s",
            file_id,
            STORAGE_DIR,
        )
        return None

    file_path = storage.get_attachment_path(file_id)
    if file_path is None:
        logger.debug(
            "Attachment file path missing for %s; refusing local fallback under %s",
            file_id,
            STORAGE_DIR,
        )
        return None

    file_path = Path(file_path)
    data = _read_attachment_bytes(file_path)
    filename = metadata["filename"]
    mime_type = metadata.get("mime_type")
    return data, filename, mime_type


async def _resolve_url_attachments(
    attachments: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    """Pre-resolve any URL-based attachments to raw bytes.

    For each attachment dict that carries a ``url`` key:
    * If the URL matches the MCP's own ``/attachments/{id}`` pattern the file
      is read directly from :data:`STORAGE_DIR` (avoids HTTP + SSRF blocks on
      localhost).
    * Otherwise the URL is fetched via :func:`ssrf_safe_fetch`.

    The resolved entry replaces ``url`` with ``_resolved_bytes`` (raw
    ``bytes``) so that :func:`_prepare_gmail_message` can attach it without a
    redundant base64 round-trip.
    """
    if not attachments:
        return attachments

    resolved: List[Dict[str, Any]] = []
    for att in attachments:
        if "url" not in att:
            resolved.append(att)
            continue

        url = att["url"]
        filename = att.get("filename")
        mime_type = att.get("mime_type")

        # Fast path: MCP-local attachment URL.
        try:
            local = _try_read_local_attachment(url)
        except Exception as exc:
            logger.exception("Failed to read local attachment URL %s", _redact_url(url))
            resolved.append(_build_attachment_error_entry(att, exc))
            continue
        if local is not None:
            data, local_filename, local_mime = local
            entry = {
                "_resolved_bytes": data,
                "filename": filename or local_filename,
                "mime_type": mime_type or local_mime,
            }
            if "content_id" in att:
                entry["content_id"] = att["content_id"]
            resolved.append(entry)
            continue

        # External URL — SSRF-safe fetch.
        try:
            data, resp = await _download_attachment_bytes(url)
        except Exception as exc:
            logger.exception("Failed to fetch attachment URL %s", _redact_url(url))
            resolved.append(_build_attachment_error_entry(att, exc))
            continue

        # Infer filename from URL path if not provided.
        if not filename:
            url_path = urlparse(url).path
            candidate = unquote(url_path.rsplit("/", 1)[-1]) if url_path else ""
            filename = candidate if candidate and "." in candidate else "attachment"

        # Infer MIME type from Content-Type header or filename.
        if not mime_type:
            ct = resp.headers.get("content-type", "")
            # Strip parameters (e.g. "text/plain; charset=utf-8")
            ct_base = ct.split(";", 1)[0].strip()
            if ct_base and ct_base != "application/octet-stream":
                mime_type = ct_base
            elif filename:
                mime_type, _ = mimetypes.guess_type(filename)

        entry = {
            "_resolved_bytes": data,
            "filename": filename,
            "mime_type": mime_type,
        }
        if "content_id" in att:
            entry["content_id"] = att["content_id"]
        resolved.append(entry)

    return resolved


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
    from_name: Optional[str] = None,
    attachments: Optional[List[Dict[str, str]]] = None,
) -> tuple[str, Optional[str], int, List[str]]:
    """
    Prepare a Gmail message with threading and attachment support.

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
        from_name: Optional sender display name (e.g., "Peter Hartree")
        attachments: Optional list of attachments. Each can have 'path' (file path) OR 'content' (base64) + 'filename'

    Returns:
        Tuple of (raw_message, thread_id, attached_count, attachment_errors)
        where raw_message is base64 encoded.
    """
    # Handle reply subject formatting
    reply_subject = subject
    if in_reply_to and not subject.lower().startswith("re:"):
        reply_subject = f"Re: {subject}"

    # Prepare the email
    normalized_format = body_format.lower()
    if normalized_format not in {"plain", "html"}:
        raise ValueError("body_format must be either 'plain' or 'html'.")

    attached_count = 0
    attachment_errors: List[str] = []
    message = EmailMessage(policy=SMTP)

    message["Subject"] = reply_subject

    # Add sender if provided
    if from_email:
        if from_name:
            # Sanitize from_name to prevent header injection
            safe_name = (
                from_name.replace("\r", "").replace("\n", "").replace("\x00", "")
            )
            message["From"] = formataddr((safe_name, from_email))
        else:
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

    if normalized_format == "html":
        # Include a text/plain fallback so reply drafts and recipients don't
        # depend on clients successfully parsing HTML-only bodies. This is what
        # a non-HTML client actually displays, so block boundaries have to
        # survive -- _html_to_text() flattens them and would run paragraphs
        # together ("First paragraph.Second paragraph.").
        plain_body = html_to_text_preserving_breaks(body).strip()
        message.set_content(plain_body)
        message.add_alternative(body, subtype="html")
    else:
        message.set_content(body)

    seen_content_ids: set[str] = set()

    for attachment in attachments or []:
        if attachment.get("error"):
            attachment_errors.append(_format_resolved_attachment_error(attachment))
            continue

        file_path = attachment.get("path")
        filename = attachment.get("filename")
        content_base64 = attachment.get("content")
        resolved_bytes = attachment.get("_resolved_bytes")
        mime_type = attachment.get("mime_type")
        content_id = attachment.get("content_id")

        try:
            if resolved_bytes is not None:
                # Pre-resolved from a URL by _resolve_url_attachments.
                file_data = resolved_bytes
                if not filename:
                    filename = "attachment"
                if not mime_type:
                    mime_type = "application/octet-stream"
            elif file_path:
                path_obj = validate_file_path(file_path)
                if not path_obj.exists():
                    logger.error(f"File not found: path_len={len(file_path)}")
                    continue

                with open(path_obj, "rb") as f:
                    file_data = f.read()

                if not filename:
                    filename = path_obj.name

                if not mime_type:
                    mime_type, _ = mimetypes.guess_type(str(path_obj))
                    if not mime_type:
                        mime_type = "application/octet-stream"
            elif content_base64:
                if not filename:
                    logger.warning("Skipping attachment: missing filename")
                    continue

                file_data = base64.b64decode(content_base64)
                if not mime_type:
                    mime_type = "application/octet-stream"
            else:
                logger.warning("Skipping attachment: missing path, content, and url")
                continue

            safe_filename = (
                (filename or "attachment")
                .replace("\r", "")
                .replace("\n", "")
                .replace("\x00", "")
            ) or "attachment"

            main_type, sub_type = (
                mime_type.split("/", 1)
                if mime_type and "/" in mime_type
                else ("application", "octet-stream")
            )
            if content_id:
                cid_value = _normalize_attachment_content_id(content_id)
                if cid_value in seen_content_ids:
                    logger.warning(
                        "Duplicate content_id on attachment: content_id_len=%d, "
                        "filename_len=%d, path_len=%d; email clients may only "
                        "render one instance",
                        len(cid_value),
                        len(filename) if filename else 0,
                        len(file_path) if file_path else 0,
                    )
                seen_content_ids.add(cid_value)
                # Find the right MIME part to attach the inline image to.
                # First inline image: target text/html (creates multipart/related).
                # Subsequent inline images: target the existing multipart/related
                # (appends as sibling). Use walk() for recursive search since
                # iter_parts() only iterates direct children and html may be nested
                # inside multipart/related after the first inline image is added.
                target = None
                for part in message.walk():
                    if part.get_content_type() == "multipart/related":
                        target = part
                        break
                if target is None:
                    for part in message.walk():
                        if part.get_content_type() == "text/html":
                            target = part
                            break
                if target is None:
                    target = message  # Plain-text body fallback
                target.add_related(
                    file_data,
                    maintype=main_type,
                    subtype=sub_type,
                    cid=f"<{cid_value}>",
                    filename=safe_filename,
                    disposition="inline",
                )
                logger.info(
                    f"Attached inline: content_id_len={len(cid_value)}, "
                    f"filename_len={len(safe_filename)} ({len(file_data)} bytes)"
                )
            else:
                message.add_attachment(
                    file_data,
                    maintype=main_type,
                    subtype=sub_type,
                    filename=safe_filename,
                )
                logger.info(
                    f"Attached file: filename_len={len(safe_filename)} "
                    f"({len(file_data)} bytes)"
                )
            attached_count += 1
        except (binascii.Error, ValueError) as e:
            logger.error(
                f"Failed to decode attachment: "
                f"filename_len={len(filename) if filename else 0}, "
                f"path_len={len(file_path) if file_path else 0}, "
                f"error_type={type(e).__name__}"
            )
            attachment_errors.append(_format_attachment_error(file_path, filename, e))
            continue
        except Exception as e:
            logger.error(
                f"Failed to attach: filename_len={len(filename) if filename else 0}, "
                f"path_len={len(file_path) if file_path else 0}, "
                f"error_type={type(e).__name__}"
            )
            attachment_errors.append(_format_attachment_error(file_path, filename, e))
            continue

    # Encode message
    raw_message = base64.urlsafe_b64encode(message.as_bytes(policy=SMTP)).decode()

    return raw_message, thread_id, attached_count, attachment_errors


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


async def _fetch_search_result_headers(
    service, message_ids: List[str]
) -> Dict[str, Optional[Dict[str, str]]]:
    """Fetch metadata headers for search result message IDs.

    Uses the Gmail batch HTTP endpoint (one request per
    GMAIL_SEARCH_HEADER_BATCH_SIZE chunk, with a delay between chunks),
    falling back to sequential fetches with a delay if the batch API fails.
    Messages that fail inside a batch (typically transient per-user
    concurrency 429s) are retried sequentially afterwards. A message that
    still cannot be fetched maps to None so the caller can degrade that row
    instead of failing the whole search.
    """
    headers_by_id: Dict[str, Optional[Dict[str, str]]] = {}

    for chunk_start in range(0, len(message_ids), GMAIL_SEARCH_HEADER_BATCH_SIZE):
        if chunk_start:
            await asyncio.sleep(GMAIL_REQUEST_DELAY)
        chunk_ids = message_ids[
            chunk_start : chunk_start + GMAIL_SEARCH_HEADER_BATCH_SIZE
        ]
        results: Dict[str, Dict] = {}

        def _batch_callback(request_id, response, exception):
            results[request_id] = {"data": response, "error": exception}

        try:
            batch = service.new_batch_http_request(callback=_batch_callback)
            for mid in chunk_ids:
                batch.add(
                    _build_message_get_request(
                        service, message_id=mid, message_format="metadata"
                    ),
                    request_id=mid,
                )
            await asyncio.to_thread(batch.execute)
        except Exception as batch_error:
            logger.warning(
                f"[search_gmail_messages] Batch metadata fetch failed, falling back to sequential processing: {batch_error}"
            )
            for mid in chunk_ids:
                mid_result, msg_data, error = await _fetch_message_with_retry(
                    service,
                    message_id=mid,
                    message_format="metadata",
                    log_prefix="search_gmail_messages",
                )
                results[mid_result] = {"data": msg_data, "error": error}
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        for mid in chunk_ids:
            entry = results.get(mid, {"data": None, "error": "No result"})
            if entry["error"] or not entry["data"]:
                if entry["error"]:
                    logger.debug(
                        f"[search_gmail_messages] Metadata fetch failed for message {mid}, will retry: {entry['error']}"
                    )
                headers_by_id[mid] = None
            else:
                try:
                    headers_by_id[mid] = _extract_headers(
                        entry["data"].get("payload") or {}, GMAIL_METADATA_HEADERS
                    )
                except Exception as exc:
                    logger.warning(
                        f"[search_gmail_messages] Invalid metadata for message {mid}, will retry: {exc}"
                    )
                    headers_by_id[mid] = None

    # Retry failures one at a time. Batch failures are usually Gmail's
    # per-user concurrency limit (429), which sequential requests don't hit.
    failed_ids = [mid for mid in message_ids if headers_by_id.get(mid) is None]
    if failed_ids:
        logger.info(
            f"[search_gmail_messages] Retrying {len(failed_ids)} failed metadata fetches sequentially"
        )
        for mid in failed_ids:
            await asyncio.sleep(GMAIL_REQUEST_DELAY)
            _, msg_data, error = await _fetch_message_with_retry(
                service,
                message_id=mid,
                message_format="metadata",
                log_prefix="search_gmail_messages",
            )
            if msg_data and not error:
                try:
                    headers_by_id[mid] = _extract_headers(
                        msg_data.get("payload") or {}, GMAIL_METADATA_HEADERS
                    )
                except Exception as exc:
                    logger.warning(
                        f"[search_gmail_messages] Invalid metadata for message {mid} after retry: {exc}"
                    )
            else:
                logger.warning(
                    f"[search_gmail_messages] Metadata fetch failed for message {mid} after retry: {error}"
                )

    return headers_by_id


def _format_gmail_results_plain(
    messages: list,
    query: str,
    next_page_token: Optional[str] = None,
    headers_by_id: Optional[Dict[str, Optional[Dict[str, str]]]] = None,
) -> str:
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
            lines.extend(
                [
                    f"  {i}. Message: Invalid message data",
                    "     Error: Message object is null or malformed",
                    "",
                ]
            )
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

        lines.append(f"  {i}. Message ID: {message_id}")

        if headers_by_id is not None:
            headers = headers_by_id.get(message_id)
            if headers is None:
                lines.append("     Headers: unavailable (metadata fetch failed)")
            else:
                lines.extend(
                    [
                        f"     Subject: {headers.get('Subject', '(no subject)')}",
                        f"     From: {headers.get('From', '(unknown sender)')}",
                        f"     Date: {headers.get('Date', '(unknown date)')}",
                    ]
                )

        lines.extend(
            [
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

    # Add pagination info if there's a next page
    if next_page_token:
        lines.append("")
        lines.append(
            f"📄 PAGINATION: To get the next page, call search_gmail_messages again with page_token='{next_page_token}'"
        )

    return "\n".join(lines)


@server.tool(
    title="Search Gmail Messages",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("search_gmail_messages", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def search_gmail_messages(
    service,
    query: str,
    user_google_email: str,
    page_size: int = 10,
    page_token: Optional[str] = None,
    include_headers: bool = False,
) -> str:
    """
    Searches messages in a user's Gmail account based on a query.
    Returns both Message IDs and Thread IDs for each found message, along with Gmail web interface links for manual verification.
    Supports pagination via page_token parameter.

    Args:
        query (str): The search query. Supports standard Gmail search operators.
        user_google_email (str): The user's Google email address. Required.
        page_size (int): The maximum number of messages to return. Defaults to 10.
        page_token (Optional[str]): Token for retrieving the next page of results. Use the next_page_token from a previous response.
        include_headers (bool): If True, also fetch each message's metadata and include
            Subject, From, and Date per result. Costs one metadata get per result,
            grouped into HTTP batches of up to 10, plus retries for transient failures.
            Defaults to False (output unchanged from prior versions).

    Returns:
        str: LLM-friendly structured results with Message IDs, Thread IDs, and clickable Gmail web interface URLs for each found message.
        With include_headers=True, each result also includes Subject, From, and Date.
        Includes pagination token if more results are available.
    """
    logger.info(
        f"[search_gmail_messages] Email: '{user_google_email}', query_len={len(query)}, Page size: {page_size}"
    )
    logger.debug(f"[search_gmail_messages] Query: '{query}'")

    # Build the API request parameters
    request_params = {"userId": "me", "q": query, "maxResults": page_size}

    # Add page token if provided
    if page_token:
        request_params["pageToken"] = page_token
        logger.info("[search_gmail_messages] Using page_token for pagination")

    response = await asyncio.to_thread(
        service.users().messages().list(**request_params).execute
    )

    # Handle potential null response (but empty dict {} is valid)
    if response is None:
        logger.warning("[search_gmail_messages] Null response from Gmail API")
        return f"No response received from Gmail API for query: '{query}'"

    messages = response.get("messages", [])
    # Additional safety check for null messages array
    if messages is None:
        messages = []

    # Extract next page token for pagination
    next_page_token = response.get("nextPageToken")

    headers_by_id: Optional[Dict[str, Optional[Dict[str, str]]]] = None
    if include_headers and messages:
        result_ids = [
            msg["id"]
            for msg in messages
            if msg and isinstance(msg, dict) and msg.get("id")
        ]
        try:
            headers_by_id = await _fetch_search_result_headers(service, result_ids)
        except Exception as exc:
            logger.exception(
                "[search_gmail_messages] Header enrichment failed; "
                "returning search results without headers: %s",
                exc,
            )
            headers_by_id = dict.fromkeys(result_ids)

    formatted_output = _format_gmail_results_plain(
        messages, query, next_page_token, headers_by_id
    )

    logger.info(f"[search_gmail_messages] Found {len(messages)} messages")
    if next_page_token:
        logger.info(
            "[search_gmail_messages] More results available (next_page_token present)"
        )
    return formatted_output


@server.tool(
    title="Get Gmail Message Content",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors(
    "get_gmail_message_content", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", "gmail_read")
async def get_gmail_message_content(
    service,
    message_id: str,
    user_google_email: str,
    body_format: Annotated[
        Literal["text", "html", "raw"],
        Field(
            description=(
                "Body output format. "
                "'text' (default) returns plaintext (HTML converted to text as fallback). "
                "'html' returns the raw HTML body as-is without conversion. "
                "'raw' fetches the full raw MIME message and returns the base64url-decoded content."
            ),
        ),
    ] = "text",
    full: Annotated[
        bool,
        Field(
            description=(
                "When True, return the COMPLETE untruncated message: saved to local "
                "storage and referenced by download URL/file path instead of the body "
                "text, or inlined in the response when the server has no file storage "
                "(stateless mode). Use for messages large enough to hit the truncation "
                "limit, or when byte-exact fidelity is needed (pair with "
                "body_format='raw' for a .eml export)."
            ),
        ),
    ] = False,
) -> str:
    """
    Retrieves the full content (subject, sender, recipients, body) of a specific Gmail message.

    Bodies are returned inline and truncated at 20,000 characters. Set full=True to
    get the complete, untruncated message instead: it is exported to disk and the
    response carries a short-lived download URL (HTTP transport) or file path (stdio
    transport) rather than the body, so large messages never stream through the model
    context. Stateless deployments have no file storage, so there full=True returns the
    untruncated body inline.

    Args:
        message_id (str): The unique ID of the Gmail message to retrieve.
        user_google_email (str): The user's Google email address. Required.
        body_format (Literal["text", "html", "raw"]): Body output format.
            "text" (default) returns plaintext (HTML converted to text as fallback).
            "html" returns the raw HTML body as-is without conversion.
            "raw" fetches the full raw MIME message and returns the base64url-decoded content.
        full (bool): When True, write the untruncated message to local storage and
            return its URL/path instead of the body. body_format selects the exported
            file type: "raw" saves the byte-exact RFC 5322 message as .eml, "html"
            saves the raw HTML body, "text" saves the plaintext body. The "html"/"text"
            exports decode as UTF-8 and drop undecodable bytes, so prefer "raw" when
            byte-exact fidelity matters. In stateless mode there is no storage to write
            to, so the untruncated content is returned inline instead.

    Returns:
        str: The message details including subject, sender, date, Message-ID, recipients
            (To, Cc), and body content — or, when full=True, the saved file's download
            URL or path in place of the body (the untruncated body itself in stateless
            mode).
    """
    logger.info(
        f"[get_gmail_message_content] Invoked. Message ID: '{message_id}', "
        f"Email: '{user_google_email}', body_format='{body_format}', full={full}"
    )

    # Fetch message metadata first to get headers
    message_metadata = await asyncio.to_thread(
        service.users()
        .messages()
        .get(
            userId="me",
            id=message_id,
            format="metadata",
            metadataHeaders=GMAIL_METADATA_HEADERS,
        )
        .execute
    )

    headers = _extract_headers(
        message_metadata.get("payload", {}), GMAIL_METADATA_HEADERS
    )

    # Full export: hand back a file reference instead of the (truncated) body.
    if full:
        return await _export_full_message(
            service,
            message_id,
            headers,
            body_format,
            declared_size=message_metadata.get("sizeEstimate"),
        )

    # Handle raw format separately - fetch with format="raw" and return decoded MIME
    if body_format == "raw":
        message_raw = await asyncio.to_thread(
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute
        )
        decoded_raw = _decode_raw_mime_content(message_raw.get("raw", ""))

        content_lines = _format_message_header_lines(headers)
        content_lines.append(f"\n--- RAW MIME ---\n{decoded_raw}")
        return "\n".join(content_lines)

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

    # Extract both text and HTML bodies using enhanced helper function
    payload = message_full.get("payload", {})
    bodies = _extract_message_bodies(payload)
    text_body = bodies.get("text", "")
    html_body = bodies.get("html", "")

    # Format body content with HTML fallback
    body_data = _format_body_content(text_body, html_body, body_format=body_format)

    # Extract attachment metadata
    attachments = _extract_attachments(payload)

    content_lines = _format_message_header_lines(headers)
    content_lines.append(f"\n--- BODY ---\n{body_data or '[No text/plain body found]'}")

    # Add attachment information if present
    if attachments:
        content_lines.append("\n--- ATTACHMENTS ---")
        for attachment_index, att in enumerate(attachments):
            size_kb = att["size"] / 1024
            content_lines.append(
                f"{attachment_index + 1}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                f"   Attachment ID: {att['attachmentId']}\n"
                f"   Use get_gmail_attachment_content(message_id='{message_id}', "
                f"attachment_id='{att['attachmentId']}', attachment_index={attachment_index}) to download"
            )

    return "\n".join(content_lines)


@server.tool(
    title="Get Gmail Messages Content Batch",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors(
    "get_gmail_messages_content_batch", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", "gmail_read")
async def get_gmail_messages_content_batch(
    service,
    message_ids: StringList,
    user_google_email: str,
    format: Literal["full", "metadata"] = "full",
    body_format: Annotated[
        Literal["text", "html", "raw"],
        Field(
            description=(
                "Body output format (only applies when format='full'). "
                "'text' (default) returns plaintext (HTML converted to text as fallback). "
                "'html' returns the raw HTML body as-is without conversion. "
                "'raw' fetches the full raw MIME message and returns the base64url-decoded content."
            ),
        ),
    ] = "text",
) -> str:
    """
    Retrieves the content of multiple Gmail messages in a single batch request.
    Supports up to 25 messages per batch to prevent SSL connection exhaustion.

    Args:
        message_ids (List[str]): List of Gmail message IDs to retrieve (max 25 per batch).
        user_google_email (str): The user's Google email address. Required.
        format (Literal["full", "metadata"]): Message format. "full" includes body, "metadata" only headers.
        body_format (Literal["text", "html", "raw"]): Body output format (only applies when format='full').
            "text" (default) returns plaintext (HTML converted to text as fallback).
            "html" returns the raw HTML body as-is without conversion.
            "raw" fetches the full raw MIME message and returns the base64url-decoded content.

    Returns:
        str: A formatted list of message contents including subject, sender, date, Message-ID, recipients (To, Cc), and body (if full format).
    """
    logger.info(
        f"[get_gmail_messages_content_batch] Invoked. Message count: {len(message_ids)}, Email: '{user_google_email}'"
    )

    if not message_ids:
        raise Exception("No message IDs provided")
    _validate_message_batch_options(format, body_format)

    output_messages = []
    message_format: Literal["metadata", "full"] = (
        "metadata" if format == "metadata" or body_format == "raw" else "full"
    )

    # Process in smaller chunks to prevent SSL connection exhaustion
    for chunk_start in range(0, len(message_ids), GMAIL_BATCH_SIZE):
        chunk_ids = message_ids[chunk_start : chunk_start + GMAIL_BATCH_SIZE]
        results: Dict[str, Dict] = {}

        def _batch_callback(request_id, response, exception):
            """Callback for batch requests"""
            results[request_id] = {"data": response, "error": exception}

        batch_completed = False

        # Try to use batch API
        try:
            batch = service.new_batch_http_request(callback=_batch_callback)

            for mid in chunk_ids:
                req = _build_message_get_request(
                    service, message_id=mid, message_format=message_format
                )
                batch.add(req, request_id=mid)

            # Execute batch request
            await asyncio.to_thread(batch.execute)
            batch_completed = True

        except Exception as batch_error:
            # Fallback to sequential processing instead of parallel to prevent SSL exhaustion
            logger.warning(
                f"[get_gmail_messages_content_batch] Batch API failed, falling back to sequential processing: {batch_error}"
            )

            # Process messages sequentially with small delays to prevent connection exhaustion
            for mid in chunk_ids:
                mid_result, msg_data, error = await _fetch_message_with_retry(
                    service,
                    message_id=mid,
                    message_format=message_format,
                    log_prefix="get_gmail_messages_content_batch",
                )
                results[mid_result] = {"data": msg_data, "error": error}
                # Brief delay between requests to allow connection cleanup
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        # Sub-requests that failed with a transient error (e.g. 429 rate limit)
        # inside an otherwise successful batch response: re-fetch only those IDs
        # and merge. The sequential fallback has already exhausted its retries.
        retryable_ids = (
            _retryable_result_ids(results, chunk_ids) if batch_completed else []
        )
        if retryable_ids:
            logger.warning(
                f"[get_gmail_messages_content_batch] {len(retryable_ids)}/{len(chunk_ids)} "
                f"messages failed with retryable errors; re-fetching: {retryable_ids}"
            )
            # Backoff briefly so the rate limit has time to reset.
            await asyncio.sleep(GMAIL_RATE_LIMIT_BACKOFF)
            for mid in retryable_ids:
                mid_result, msg_data, error = await _fetch_message_with_retry(
                    service,
                    message_id=mid,
                    message_format=message_format,
                    log_prefix="get_gmail_messages_content_batch",
                )
                results[mid_result] = {"data": msg_data, "error": error}
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        raw_contents: Optional[Dict[str, str]] = None
        if format != "metadata" and body_format == "raw":
            raw_message_ids = [
                mid for mid in chunk_ids if not results.get(mid, {}).get("error")
            ]
            raw_contents = await _fetch_raw_message_contents(
                service,
                raw_message_ids,
                log_prefix="get_gmail_messages_content_batch",
            )

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
                    headers = _extract_headers(payload, GMAIL_METADATA_HEADERS)
                    msg_output = "\n".join(
                        _format_message_header_lines(headers, message_id=mid)
                    )
                    msg_output += f"\nWeb Link: {_generate_gmail_web_url(mid)}\n"

                    output_messages.append(msg_output)
                else:
                    headers = _extract_headers(payload, GMAIL_METADATA_HEADERS)
                    if body_format == "raw":
                        body_data = (
                            raw_contents.get(
                                mid, "[Failed to fetch raw MIME: No result]"
                            )
                            if raw_contents
                            else "[Failed to fetch raw MIME: No result]"
                        )
                        body_label = "RAW MIME"
                    else:
                        # Full format - extract body too
                        bodies = _extract_message_bodies(payload)
                        text_body = bodies.get("text", "")
                        html_body = bodies.get("html", "")
                        body_data = _format_body_content(
                            text_body, html_body, body_format=body_format
                        )
                        body_label = "BODY"

                    attachments = _extract_attachments(payload)

                    msg_output = "\n".join(
                        _format_message_header_lines(headers, message_id=mid)
                    )
                    msg_output += f"\nWeb Link: {_generate_gmail_web_url(mid)}\n"
                    msg_output += f"\n--- {body_label} ---\n{body_data}\n"

                    if attachments:
                        msg_output += "\n--- ATTACHMENTS ---\n"
                        for attachment_index, att in enumerate(attachments):
                            size_kb = att["size"] / 1024
                            msg_output += (
                                f"{attachment_index + 1}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                                f"   Attachment ID: {att['attachmentId']}\n"
                                f"   Use get_gmail_attachment_content(message_id='{mid}', "
                                f"attachment_id='{att['attachmentId']}', attachment_index={attachment_index}) to download\n"
                            )

                    output_messages.append(msg_output)

    # Combine all messages with separators
    final_output = f"Retrieved {len(message_ids)} messages:\n\n"
    final_output += "\n---\n\n".join(output_messages)

    return final_output


def _attachment_metadata_fields(depth: int) -> str:
    """Build a metadata-only fields mask for a MIME tree of the given depth."""
    node = "filename,mimeType,body(attachmentId,size)"
    for _ in range(depth):
        node = f"filename,mimeType,body(attachmentId,size),parts({node})"
    return f"payload({node})"


_ATTACHMENT_METADATA_FIELDS = _attachment_metadata_fields(6)


@server.tool(
    title="Get Gmail Attachment Content",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors(
    "get_gmail_attachment_content", is_read_only=True, service_type="gmail"
)
@require_google_service("gmail", "gmail_read")
async def get_gmail_attachment_content(
    service,
    message_id: str,
    attachment_id: str,
    user_google_email: str,
    return_base64: bool = False,
    attachment_index: Optional[int] = None,
) -> str:
    """
    Downloads an email attachment and saves it to local disk.

    In stdio mode, returns the local file path for direct access.
    In HTTP mode, returns a temporary download URL (valid for 1 hour).
    May re-fetch message metadata to resolve filename and MIME type.

    Args:
        message_id (str): The ID of the Gmail message containing the attachment.
        attachment_id (str): The ID of the attachment to download.
        user_google_email (str): The user's Google email address. Required.
        return_base64 (bool): When True, includes the full attachment as a
            standard base64 string in the response (in addition to any file
            path or download URL). Useful for sandboxed clients that cannot
            reach localhost download URLs or the MCP server's local file
            paths (e.g. containerized agents with network allowlists). The
            returned base64 uses the standard alphabet, so it can be passed
            directly to tools like ``draft_gmail_message`` that expect
            standard (not URL-safe) base64. Default False preserves the
            existing behavior and response size.
        attachment_index (Optional[int]): Zero-based attachment position from
            the message-content response. When the cap is enabled, this lets
            the server safely resolve Gmail's refreshed attachment IDs against
            current metadata before downloading.

    Returns:
        str: Attachment metadata with either a local file path or download URL,
            optionally followed by a base64 content block when
            ``return_base64=True``.
    """
    logger.info(
        f"[get_gmail_attachment_content] Invoked. Message ID: '{message_id}', Email: '{user_google_email}'"
    )

    # Resolve attachment size/filename from message metadata before downloading
    # the binary payload so we can reject oversized attachments before the API
    # returns the full base64 body in one shot.
    filename = None
    mime_type = None
    declared_size = None
    download_attachment_id = attachment_id
    max_file_bytes = get_max_file_bytes()
    if max_file_bytes is not None:
        try:
            message_full = await asyncio.to_thread(
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="full",
                    fields=_ATTACHMENT_METADATA_FIELDS,
                )
                .execute
            )
            payload = message_full.get("payload", {})
            attachments = _extract_attachments(payload)
            matched = _find_attachment_metadata(payload, attachment_id)

            if matched is None and attachment_index is not None:
                if attachment_index < 0 or attachment_index >= len(attachments):
                    return (
                        f"Error: Invalid attachment_index {attachment_index}. Message "
                        f"has {len(attachments)} downloadable attachment(s)."
                    )
                # Gmail can refresh attachment IDs between messages.get calls.
                # The stable ordinal emitted with the original ID selects the
                # corresponding current attachment safely.
                matched = attachments[attachment_index]
            elif matched is None and len(attachments) == 1:
                # A single attachment is unambiguous even if Gmail refreshed
                # its ID since the caller fetched the message.
                matched = attachments[0]

            if matched is not None:
                filename = matched.get("filename")
                mime_type = matched.get("mimeType")
                declared_size = matched.get("size")
                download_attachment_id = matched.get("attachmentId", attachment_id)
        except Exception:
            logger.debug(
                f"Could not fetch attachment metadata for {attachment_id} before download"
            )

        # attachments().get() returns the complete base64 payload in one API
        # response, so there is no opportunity to stop mid-body. If the bounded
        # metadata projection could not resolve this attachment (for example,
        # because it is nested deeper than the fields mask), fail closed before
        # requesting data that may exceed the configured cap.
        if declared_size is None:
            return (
                "Error: Could not verify the attachment size before download while "
                "WORKSPACE_MCP_MAX_FILE_BYTES is configured. Fetch the message again "
                "and pass both its current attachment ID and attachment_index."
            )

    try:
        ensure_within_file_size_limit(
            declared_size,
            file_name=filename,
            file_id=attachment_id,
            kind="attachment",
            max_bytes=max_file_bytes,
        )
    except FileTooLargeError as e:
        return str(e)

    try:
        attachment = await asyncio.to_thread(
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=download_attachment_id)
            .execute
        )
    except Exception as e:
        logger.error(
            f"[get_gmail_attachment_content] Failed to download attachment: {e}"
        )
        return (
            f"Error: Failed to download attachment. The attachment ID may have changed.\n"
            f"Please fetch the message content again to get an updated attachment ID.\n\n"
            f"Error details: {str(e)}"
        )

    # Format response with attachment data
    size_bytes = attachment.get("size", 0) or declared_size or 0
    size_kb = size_bytes / 1024 if size_bytes else 0
    base64_data = attachment.get("data", "")

    # The Gmail API already buffered the full body above; this only prevents
    # returning oversized data when the pre-download declared_size was absent.
    try:
        ensure_within_file_size_limit(
            size_bytes,
            file_name=filename,
            file_id=attachment_id,
            kind="attachment",
            max_bytes=max_file_bytes,
        )
    except FileTooLargeError as e:
        if isinstance(attachment, dict):
            attachment.pop("data", None)
        base64_data = ""
        return str(e)

    # Check if we're in stateless mode (can't save files)
    from auth.oauth_config import is_stateless_mode

    if is_stateless_mode():
        result_lines = [
            "Attachment downloaded successfully!",
            f"Message ID: {message_id}",
            f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
            "\n⚠️ Stateless mode: File storage disabled.",
            "\nBase64-encoded content (first 100 characters shown):",
            f"{base64_data[:100]}...",
            "\nNote: Attachment IDs are ephemeral. Always use IDs from the most recent message fetch.",
        ]
        if return_base64 and base64_data:
            result_lines.extend(_format_base64_content_block(base64_data))
        logger.info(
            f"[get_gmail_attachment_content] Successfully downloaded {size_kb:.1f} KB attachment (stateless mode)"
        )
        return "\n".join(result_lines)

    # Save attachment to local disk and return file path
    try:
        from core.attachment_storage import get_attachment_storage, get_attachment_url
        from core.config import get_transport_mode

        storage = get_attachment_storage()

        # If the pre-download metadata fetch missed the filename, try again
        # with the full nested MIME tree and size-based fallback heuristics.
        if not filename:
            try:
                message_full = await asyncio.to_thread(
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=message_id,
                        format="full",
                        fields=_ATTACHMENT_METADATA_FIELDS,
                    )
                    .execute
                )
                payload = message_full.get("payload", {})
                attachments = _extract_attachments(payload)

                for att in attachments:
                    if att.get("attachmentId") == attachment_id:
                        filename = att.get("filename")
                        mime_type = att.get("mimeType")
                        break

                if not filename and attachments:
                    size_matches = [
                        att
                        for att in attachments
                        if att.get("size") and abs(att["size"] - size_bytes) < 100
                    ]
                    if len(size_matches) == 1:
                        filename = size_matches[0].get("filename")
                        mime_type = size_matches[0].get("mimeType")
                        logger.warning(
                            f"Attachment {attachment_id} matched by size fallback as '{filename}'"
                        )

                if not filename and len(attachments) == 1:
                    filename = attachments[0].get("filename")
                    mime_type = attachments[0].get("mimeType")
            except Exception:
                logger.debug(
                    f"Could not fetch attachment metadata for {attachment_id}, using defaults"
                )

        # Save attachment to local disk
        result = storage.save_attachment(
            base64_data=base64_data, filename=filename, mime_type=mime_type
        )
        saved_filename = Path(result.path).name

        result_lines = [
            "Attachment downloaded successfully!",
            f"Message ID: {message_id}",
            f"Filename: {filename or 'unknown'}",
            f"Saved filename: {saved_filename}",
            f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
        ]

        if get_transport_mode() == "stdio":
            result_lines.append(f"\n📎 Saved to: {result.path}")
            result_lines.append(
                "\nThe file has been saved to disk and can be accessed directly via the file path."
            )
        else:
            download_url = get_attachment_url(result.file_id)
            result_lines.append(f"\n📎 Download URL: {download_url}")
            result_lines.append("\nThe file will expire after 1 hour.")

        result_lines.append(
            "\nNote: Attachment IDs are ephemeral. Always use IDs from the most recent message fetch."
        )

        if return_base64 and base64_data:
            result_lines.extend(_format_base64_content_block(base64_data))

        logger.info(
            f"[get_gmail_attachment_content] Successfully saved {size_kb:.1f} KB attachment to {result.path}"
        )
        return "\n".join(result_lines)

    except Exception as e:
        logger.error(
            f"[get_gmail_attachment_content] Failed to save attachment: {e}",
            exc_info=True,
        )
        # Fallback to showing base64 preview
        result_lines = [
            "Attachment downloaded successfully!",
            f"Message ID: {message_id}",
            f"Size: {size_kb:.1f} KB ({size_bytes} bytes)",
            "\n⚠️ Failed to save attachment file. Showing preview instead.",
            "\nBase64-encoded content (first 100 characters shown):",
            f"{base64_data[:100]}...",
            f"\nError: {str(e)}",
            "\nNote: Attachment IDs are ephemeral. Always use IDs from the most recent message fetch.",
        ]
        if return_base64 and base64_data:
            result_lines.extend(_format_base64_content_block(base64_data))
        return "\n".join(result_lines)


@server.tool(
    title="Send Gmail Message",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("send_gmail_message", service_type="gmail")
@require_google_service("gmail", ["gmail_read", GMAIL_SEND_SCOPE])
async def send_gmail_message(
    service,
    user_google_email: str,
    to: Annotated[
        Optional[str],
        Field(
            description="Recipient email address. Optional when replying with reply_all=True, which derives it from the thread.",
        ),
    ] = None,
    subject: Annotated[
        Optional[str],
        Field(
            description="Email subject. Required when sending; optional when forwarding (defaults to 'Fwd: <original subject>').",
        ),
    ] = None,
    body: Annotated[
        Optional[str],
        Field(
            description="Email body content (plain text or HTML). Required when sending. When forwarding, this is an optional note prepended above the quoted original.",
        ),
    ] = None,
    body_format: Annotated[
        Literal["plain", "html"],
        Field(
            description="Format of the body content (and of the prepended note when forwarding). Use 'plain' for plaintext or 'html' for HTML content.",
        ),
    ] = "plain",
    forward_message_id: Annotated[
        Optional[str],
        Field(
            description="Set to a Gmail message ID to forward that message instead of composing a new one. The original subject, body, and (optionally) attachments are carried over; 'body' becomes an optional note prepended to the forward.",
        ),
    ] = None,
    include_forwarded_attachments: Annotated[
        bool,
        Field(
            description="When forwarding, whether to include the original message's attachments. Ignored unless forward_message_id is set.",
        ),
    ] = True,
    cc: Annotated[
        Optional[str], Field(description="Optional CC email address.")
    ] = None,
    bcc: Annotated[
        Optional[str], Field(description="Optional BCC email address.")
    ] = None,
    from_name: Annotated[
        Optional[str],
        Field(
            description="Optional sender display name (e.g., 'Peter Hartree'). If provided, the From header will be formatted as 'Name <email>'.",
        ),
    ] = None,
    from_email: Annotated[
        Optional[str],
        Field(
            description="Optional 'Send As' alias email address. Must be configured in Gmail settings (Settings > Accounts > Send mail as). If not provided, uses the authenticated user's email.",
        ),
    ] = None,
    thread_id: Annotated[
        Optional[str],
        Field(
            description="Optional Gmail thread ID to reply within. When in_reply_to is omitted, replies to the latest non-draft, non-trash message with an RFC Message-ID.",
        ),
    ] = None,
    in_reply_to: Annotated[
        Optional[str],
        Field(
            description="Optional RFC Message-ID to explicitly reply to a specific message (e.g., '<message123@gmail.com>'). Omit to reply to the latest eligible message in thread_id.",
        ),
    ] = None,
    references: Annotated[
        Optional[str],
        Field(
            description="Optional Message-ID ancestry chain. Normally omit when thread_id is provided; the server derives the chain through the selected reply target.",
        ),
    ] = None,
    attachments: Annotated[
        Optional[DictList],
        Field(
            description='Optional list of attachments. Each can have: "url" (fetch from URL — works with MCP attachment URLs from get_drive_file_download_url / get_gmail_attachment_content), OR "path" (file path, auto-encodes), OR "content" (standard base64, not urlsafe) + "filename". Optional "mime_type". Optional "content_id" (string) makes the attachment inline-rendered: it lands in a multipart/related part with `Content-ID: <content_id>` and `Content-Disposition: inline`, and the HTML body can reference it via `<img src="cid:<content_id>">` (RFC 2392). Without `content_id` the attachment is a regular multipart/mixed attachment. Example: [{"url": "https://host/attachments/abc-123", "filename": "report.pdf"}]',
        ),
    ] = None,
    include_signature: Annotated[
        bool,
        Field(
            description="Whether to append the Gmail signature from Settings > Signature when available. Defaults to true.",
        ),
    ] = True,
    quote_original: Annotated[
        bool,
        Field(
            description="Whether to include the message being replied to as a quoted original. Only has an effect when thread_id is provided. Defaults to false.",
        ),
    ] = False,
    reply_all: Annotated[
        bool,
        Field(
            description="Whether to derive reply-all recipients from the thread: To = the sender being replied to, Cc = the other participants, excluding the authenticated account and from_email. Requires thread_id. Explicit to/cc win; when cc is omitted the sender being replied to is added to the derived Cc if they are not already in To. Defaults to false.",
        ),
    ] = False,
) -> str:
    """
    Sends an email using the user's Gmail account. Supports new emails, replies, and
    forwards, with optional attachments. Supports Gmail's "Send As" feature to send
    from configured alias addresses.

    To forward an existing message, pass forward_message_id. The original subject,
    body (quoted with a "Forwarded message" header), and attachments are carried over.
    In forward mode, body (if any) is prepended as a note and subject is optional.
    Threading, reply, and signature options do not apply when forwarding.

    THIS TOOL SENDS IMMEDIATELY AND CANNOT SCHEDULE. Gmail's REST API exposes no
    send-time parameter; Schedule send is a web-UI feature with no API equivalent,
    so no argument to this tool can defer delivery. Never tell a user a message
    was scheduled. For "prepare now, deliver later", create a draft with
    draft_gmail_message. An external scheduler must retain the message data to
    create and send a new message via send_gmail_message at the chosen time, or
    call users.drafts.send with the draft ID returned by draft_gmail_message.
    Alternatively, let the user schedule that draft in the Gmail UI.

    Args:
        to (str): Recipient email address.
        subject (str): Email subject. Required unless forwarding (then defaults to 'Fwd: <original subject>').
        body (str): Email body content. Required unless forwarding (then an optional prepended note).
        body_format (Literal['plain', 'html']): Body format (and prepended note format when forwarding). Defaults to 'plain'.
        forward_message_id (Optional[str]): Gmail message ID to forward. When set, the tool forwards that message.
        include_forwarded_attachments (bool): Whether to carry over the original attachments when forwarding. Defaults to True.
        attachments (Optional[List[Dict[str, str]]]): Optional list of attachments. Each dict can contain:
            Option 1 - File path (auto-encodes):
              - 'path' (required): File path to attach
              - 'filename' (optional): Override filename
              - 'mime_type' (optional): Override MIME type (auto-detected if not provided)
            Option 2 - Base64 content:
              - 'content' (required): Standard base64-encoded file content (not urlsafe)
              - 'filename' (required): Name of the file
              - 'mime_type' (optional): MIME type (defaults to 'application/octet-stream')
        cc (Optional[str]): Optional CC email address.
        bcc (Optional[str]): Optional BCC email address.
        from_name (Optional[str]): Optional sender display name. If provided, the From header will be formatted as 'Name <email>'.
        from_email (Optional[str]): Optional 'Send As' alias email address. The alias must be
            configured in Gmail settings (Settings > Accounts > Send mail as). If not provided,
            the email will be sent from the authenticated user's primary email address.
        user_google_email (str): The user's Google email address. Required for authentication.
        thread_id (Optional[str]): Optional Gmail thread ID to reply within. When
            in_reply_to is omitted, replies to the latest non-draft, non-trash message
            with an RFC Message-ID.
        in_reply_to (Optional[str]): Optional RFC Message-ID to explicitly reply to
            a specific message. Omit to reply to the latest eligible message.
        references (Optional[str]): Optional RFC Message-ID ancestry chain. Normally
            omit when thread_id is provided; the chain is derived automatically.
        include_signature (bool): Whether to append Gmail signature HTML from send-as settings.
            When include_signature is true and Gmail signature retrieval fails for benign reasons
            (e.g., missing gmail.settings.basic scope), the send proceeds without a signature.
            Non-benign failures such as quota/rate-limit or API errors raise ToolError and abort
            the send.
        quote_original (bool): Whether to append the message being replied to as a quoted
            original. Only has an effect when thread_id is provided.
        reply_all (bool): Whether to derive reply-all recipients from the thread: To = the
            sender being replied to, Cc = the other participants, excluding the authenticated
            account and from_email. Requires thread_id. Explicit to/cc win over the derived
            values; when cc is omitted, the sender being replied to is added to the derived Cc
            unless they are already in To (so an explicit 'to' that redirects the reply still
            keeps them on it).

    Returns:
        str: Confirmation message with the sent email's message ID.

    Examples:
        # Send a new email
        send_gmail_message(to="user@example.com", subject="Hello", body="Hi there!")

        # Send with a custom display name
        send_gmail_message(to="user@example.com", subject="Hello", body="Hi there!", from_name="John Doe")

        # Send an HTML email
        send_gmail_message(
            to="user@example.com",
            subject="Hello",
            body="<strong>Hi there!</strong>",
            body_format="html"
        )

        # Send from a configured alias (Send As)
        send_gmail_message(
            to="user@example.com",
            subject="Business Inquiry",
            body="Hello from my business address...",
            from_email="business@mydomain.com"
        )

        # Send an email with CC and BCC
        send_gmail_message(
            to="user@example.com",
            cc="manager@example.com",
            bcc="archive@example.com",
            subject="Project Update",
            body="Here's the latest update..."
        )

        # Send an email with attachments (using file path)
        send_gmail_message(
            to="user@example.com",
            subject="Report",
            body="Please see attached report.",
            attachments=[{
                "path": "/path/to/report.pdf"
            }]
        )

        # Send an email with attachments (using base64 content)
        send_gmail_message(
            to="user@example.com",
            subject="Report",
            body="Please see attached report.",
            attachments=[{
                "filename": "report.pdf",
                "content": "JVBERi0xLjQK...",  # base64 encoded PDF
                "mime_type": "application/pdf"
            }]
        )

        # Send a reply
        send_gmail_message(
            to="user@example.com",
            subject="Re: Meeting tomorrow",
            body="Thanks for the update!",
            thread_id="thread_123"
        )

        # Send a reply-all with the original quoted, deriving headers and
        # recipients from the thread
        send_gmail_message(
            subject="Re: Meeting tomorrow",
            body="Thanks for the update!",
            thread_id="thread_123",
            reply_all=True,
            quote_original=True
        )

        # Forward a message with a note
        send_gmail_message(
            to="user@example.com",
            forward_message_id="abc123",
            body="FYI - see below."
        )

        # Forward without the original attachments
        send_gmail_message(
            to="user@example.com",
            forward_message_id="abc123",
            include_forwarded_attachments=False
        )
    """
    # Forwarding reuses the original message's content, so it follows a dedicated
    # path that fetches and quotes the source message.
    if forward_message_id:
        # 'to' is optional on the signature only so a reply_all send can derive it;
        # a forward has no thread to derive from, so it still requires one.
        if not to:
            raise UserInputError(
                "'to' is required when forwarding via 'forward_message_id'."
            )
        logger.info(
            f"[send_gmail_message] Forwarding message '{forward_message_id}' for '{user_google_email}'"
        )
        return await _forward_gmail_message_impl(
            service=service,
            message_id=forward_message_id,
            to=to,
            subject=subject,
            forward_message=body,
            forward_message_format=body_format,
            include_attachments=include_forwarded_attachments,
            cc=cc,
            bcc=bcc,
            from_name=from_name,
            from_email=from_email,
            user_google_email=user_google_email,
        )

    if subject is None or body is None:
        raise UserInputError(
            "Both 'subject' and 'body' are required when sending a message "
            "(they are optional only when forwarding via 'forward_message_id')."
        )

    if reply_all and not thread_id:
        raise UserInputError(
            "'reply_all' requires a thread_id: the recipients are derived from "
            "the message being replied to."
        )

    if not to and not (thread_id and reply_all):
        raise UserInputError(
            "'to' is required unless replying with reply_all=True and a thread_id, "
            "which derives the recipients from the thread."
        )

    logger.info(
        f"[send_gmail_message] Invoked. Email: '{user_google_email}', subject_len={len(subject) if subject else 0}, Attachments: {len(attachments) if attachments else 0}"
    )

    # Prepare the email message
    # Use from_email (Send As alias) if provided, otherwise default to authenticated user
    sender_email = from_email or user_google_email

    # A reply's headers, recipients and quoted original are all derivable from the
    # thread, so a caller should not have to assemble them by hand. Mirrors
    # draft_gmail_message; one thread fetch serves all three.
    reply_context = None
    if thread_id and (quote_original or reply_all or not in_reply_to or not references):
        reply_context = await _fetch_thread_reply_context(
            service,
            thread_id,
            in_reply_to=in_reply_to,
            include_bodies=quote_original,
        )

    target_reply = reply_context.get("target") if reply_context else None
    if thread_id and (not in_reply_to or not references):
        in_reply_to, references = _derive_reply_headers(
            reply_context.get("message_ids", []) if reply_context else [],
            in_reply_to,
            references,
            target_reply,
        )

    if reply_all and target_reply:
        to, cc = _derive_reply_all_recipients(
            target_reply, {user_google_email, sender_email}, to, cc
        )
    if not to:
        raise UserInputError(
            f"Could not derive a recipient from thread '{thread_id}'. Pass 'to' explicitly."
        )

    # Optionally append the Gmail signature from send-as settings, mirroring
    # draft_gmail_message so sent mail respects the user's Settings > Signature.
    signature_html = ""
    if include_signature:
        signature_html = await _get_send_as_signature_html_for_tool(
            service, from_email=sender_email
        )

    if quote_original and target_reply:
        send_body_content = _build_quoted_reply_body(
            body,
            body_format,
            signature_html,
            {
                "sender": target_reply.get("from") or "unknown",
                "date": target_reply.get("date", ""),
                "text_body": target_reply.get("text_body", ""),
                "html_body": target_reply.get("html_body", ""),
            },
        )
    else:
        send_body_content = _append_signature_to_body(body, body_format, signature_html)

    resolved_attachments = await _resolve_url_attachments(attachments)
    raw_message, thread_id_final, attached_count, attachment_errors = (
        _prepare_gmail_message(
            subject=subject,
            body=send_body_content,
            to=to,
            cc=cc,
            bcc=bcc,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references,
            body_format=body_format,
            from_email=sender_email,
            from_name=from_name,
            attachments=resolved_attachments if resolved_attachments else None,
        )
    )

    requested_attachment_count = len(attachments or [])
    if requested_attachment_count > 0 and attached_count == 0:
        details = (
            f" Details: {'; '.join(attachment_errors)}" if attachment_errors else ""
        )
        raise UserInputError(
            "No valid attachments were added. Verify each attachment path/content and retry."
            f"{details}"
        )

    send_body = {"raw": raw_message}

    # Associate with thread if provided
    if thread_id_final:
        send_body["threadId"] = thread_id_final

    # Send the message
    sent_message = await asyncio.to_thread(
        service.users().messages().send(userId="me", body=send_body).execute,
        num_retries=GOOGLE_API_WRITE_RETRIES,
    )
    message_id = sent_message.get("id")

    if requested_attachment_count > 0:
        attachment_info = _format_attachment_result(
            attached_count, requested_attachment_count
        )
        return f"Email sent{attachment_info}! Message ID: {message_id}"
    return f"Email sent! Message ID: {message_id}"


# Internal implementation function for testing
async def _forward_gmail_message_impl(
    service,
    message_id: str,
    to: str,
    subject: Optional[str] = None,
    forward_message: Optional[str] = None,
    forward_message_format: Literal["plain", "html"] = "plain",
    include_attachments: bool = True,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    from_name: Optional[str] = None,
    from_email: Optional[str] = None,
    user_google_email: str = "",
) -> str:
    """Build and send a forward of an existing Gmail message.

    Shared by send_gmail_message's forward path. An explicit ``subject`` overrides
    the auto-derived 'Fwd: <original subject>'.
    """
    # Fetch the original message with full payload
    original_message = await asyncio.to_thread(
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute
    )

    payload = original_message.get("payload", {})

    forward_subject, forward_body, body_format = _build_forward_content(
        headers=_extract_headers(payload, ["Subject", "From", "Date", "To"]),
        bodies=_extract_message_bodies(payload),
        forward_message=forward_message,
        forward_message_format=forward_message_format,
        subject_override=subject,
    )

    # Handle attachments
    attachments_to_send = []
    if include_attachments:
        attachment_metadata = _extract_attachments(payload)
        failed_attachments = []
        for att in attachment_metadata:
            try:
                ensure_within_file_size_limit(
                    att.get("size"),
                    file_name=att.get("filename"),
                    file_id=att.get("attachmentId"),
                    kind="attachment",
                )
            except FileTooLargeError as exc:
                # Do not let the broad per-attachment download handler turn a
                # configured safety rejection into a generic partial-forward
                # failure, and never send the message without requested files.
                raise UserInputError(str(exc)) from exc
            try:
                # Download attachment content
                attachment_data = await asyncio.to_thread(
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=att["attachmentId"])
                    .execute
                )
                # Gmail normally repeats the decoded byte size in this
                # response. Re-check it before making padded/decoded/re-encoded
                # copies in case it differs from the message metadata.
                ensure_within_file_size_limit(
                    attachment_data.get("size"),
                    file_name=att.get("filename"),
                    file_id=att.get("attachmentId"),
                    kind="attachment",
                )
                # Gmail returns URL-safe base64 (often unpadded). Decode it
                # tolerantly and re-encode as standard, padded base64 so the
                # downstream base64.b64decode() in _prepare_gmail_message succeeds.
                urlsafe_data = attachment_data.get("data", "")
                padded = urlsafe_data + "=" * (-len(urlsafe_data) % 4)
                standard_b64 = base64.b64encode(
                    base64.urlsafe_b64decode(padded)
                ).decode()
                attachments_to_send.append(
                    {
                        "content": standard_b64,
                        "filename": att["filename"],
                        "mime_type": att["mimeType"],
                    }
                )
                logger.info(
                    f"[forward_gmail_message] Downloaded attachment: {att['filename']}"
                )
            except FileTooLargeError as exc:
                raise UserInputError(str(exc)) from exc
            except Exception as e:
                logger.warning(
                    f"[forward_gmail_message] Failed to download attachment {att['filename']}: {e}"
                )
                failed_attachments.append(att["filename"])

        # Fail loudly rather than silently delivering an incomplete forward when
        # the caller asked for the original attachments to be preserved.
        if failed_attachments:
            raise Exception(
                "Failed to include requested attachment(s): "
                + ", ".join(failed_attachments)
            )

    # Prepare and send the message
    sender_email = from_email or user_google_email
    raw_message, _, attached_count, attachment_errors = _prepare_gmail_message(
        subject=forward_subject,
        body=forward_body,
        to=to,
        cc=cc,
        bcc=bcc,
        body_format=body_format,
        from_email=sender_email,
        from_name=from_name,
        attachments=attachments_to_send if attachments_to_send else None,
    )
    if attachments_to_send and attached_count != len(attachments_to_send):
        details = (
            f" Details: {'; '.join(attachment_errors)}" if attachment_errors else ""
        )
        raise UserInputError(
            "Failed to include requested attachment(s): "
            f"{attached_count}/{len(attachments_to_send)} attached.{details}"
        )

    send_body = {"raw": raw_message}

    # Send the message
    sent_message = await asyncio.to_thread(
        service.users().messages().send(userId="me", body=send_body).execute,
        num_retries=GOOGLE_API_WRITE_RETRIES,
    )
    sent_message_id = sent_message.get("id")

    attachment_info = (
        _format_attachment_result(attached_count, len(attachments_to_send))
        if attachments_to_send
        else ""
    )
    return f"Email forwarded{attachment_info}! Message ID: {sent_message_id}"


@server.tool(
    title="Draft Gmail Message",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("draft_gmail_message", service_type="gmail")
@require_google_service("gmail", GMAIL_COMPOSE_SCOPE)
async def draft_gmail_message(
    service,
    user_google_email: str,
    subject: Annotated[str, Field(description="Email subject.")],
    body: Annotated[str, Field(description="Email body (plain text).")],
    body_format: Annotated[
        Literal["plain", "html"],
        Field(
            description="Email body format. Use 'plain' for plaintext or 'html' for HTML content.",
        ),
    ] = "plain",
    to: Annotated[
        Optional[str],
        Field(
            description="Optional recipient email address.",
        ),
    ] = None,
    cc: Annotated[
        Optional[str], Field(description="Optional CC email address.")
    ] = None,
    bcc: Annotated[
        Optional[str], Field(description="Optional BCC email address.")
    ] = None,
    from_name: Annotated[
        Optional[str],
        Field(
            description="Optional sender display name (e.g., 'Peter Hartree'). If provided, the From header will be formatted as 'Name <email>'.",
        ),
    ] = None,
    from_email: Annotated[
        Optional[str],
        Field(
            description="Optional 'Send As' alias email address. Must be configured in Gmail settings (Settings > Accounts > Send mail as). If not provided, uses the account's default Send As address, falling back to the authenticated user's email when Gmail returns no usable Send-As entry or settings access is not authorized.",
        ),
    ] = None,
    thread_id: Annotated[
        Optional[str],
        Field(
            description="Optional Gmail thread ID to reply within. When in_reply_to is omitted, replies to the latest non-draft, non-trash message with an RFC Message-ID.",
        ),
    ] = None,
    in_reply_to: Annotated[
        Optional[str],
        Field(
            description="Optional RFC Message-ID to explicitly reply to a specific message (e.g., '<message123@gmail.com>'). Omit to reply to the latest eligible message in thread_id.",
        ),
    ] = None,
    references: Annotated[
        Optional[str],
        Field(
            description="Optional Message-ID ancestry chain. Normally omit when thread_id is provided; the server derives the chain through the selected reply target.",
        ),
    ] = None,
    attachments: Annotated[
        Optional[DictList],
        Field(
            description="Optional list of attachments. Each can have: 'url' (fetch from URL — works with MCP attachment URLs from get_drive_file_download_url / get_gmail_attachment_content), OR 'path' (file path, auto-encodes), OR 'content' (standard base64, not urlsafe) + 'filename'. Optional 'mime_type'. Optional 'content_id' (string) makes the attachment inline-rendered: it lands in a multipart/related part with `Content-ID: <content_id>` and `Content-Disposition: inline`, and the HTML body can reference it via `<img src=\"cid:<content_id>\">` (RFC 2392). Without `content_id` the attachment is a regular multipart/mixed attachment.",
        ),
    ] = None,
    include_signature: Annotated[
        bool,
        Field(
            description="Whether to append the Gmail signature from Settings > Signature when available. Defaults to true.",
        ),
    ] = True,
    quote_original: Annotated[
        bool,
        Field(
            description="Whether to include the original message as a quoted reply. Only has an effect when thread_id is provided. Defaults to false.",
        ),
    ] = False,
) -> str:
    """
    Creates a draft email in the user's Gmail account. Supports both new drafts and reply drafts with optional attachments.
    Supports Gmail's "Send As" feature to draft from configured alias addresses.

    SCHEDULED SEND IS NOT AVAILABLE. Gmail's REST API exposes no send-time
    parameter; the Schedule send feature is web-UI only, and a message cannot be
    placed in the Scheduled folder through the API. Do not claim a message was
    scheduled. To deliver at a chosen time, create a draft with
    draft_gmail_message. An external scheduler must retain the message data to
    create and send a new message via send_gmail_message then, or call
    users.drafts.send with the draft ID returned by draft_gmail_message.
    Alternatively, let the user schedule the draft in the Gmail UI.

    Args:
        user_google_email (str): The user's Google email address. Required for authentication.
        subject (str): Email subject.
        body (str): Email body (plain text).
        body_format (Literal['plain', 'html']): Email body format. Defaults to 'plain'.
        to (Optional[str]): Optional recipient email address. Can be left empty for drafts.
        cc (Optional[str]): Optional CC email address.
        bcc (Optional[str]): Optional BCC email address.
        from_name (Optional[str]): Optional sender display name. If provided, the From header will be formatted as 'Name <email>'.
        from_email (Optional[str]): Optional 'Send As' alias email address. The alias must be
            configured in Gmail settings (Settings > Accounts > Send mail as). If not provided,
            the draft uses the account's default Send As address, then the primary or first
            usable Send-As entry, falling back to the authenticated user's email when Gmail
            returns no usable entry or settings access is not authorized.
        thread_id (Optional[str]): Optional Gmail thread ID to reply within. When
            in_reply_to is omitted, replies to the latest non-draft, non-trash message
            with an RFC Message-ID.
        in_reply_to (Optional[str]): Optional RFC Message-ID to explicitly reply to
            a specific message. Omit to reply to the latest eligible message.
        references (Optional[str]): Optional RFC Message-ID ancestry chain. Normally
            omit when thread_id is provided; the chain is derived automatically.
        attachments (List[Dict[str, str]]): Optional list of attachments. Each dict can contain:
            Option 1 - File path (auto-encodes):
              - 'path' (required): File path to attach
              - 'filename' (optional): Override filename
              - 'mime_type' (optional): Override MIME type (auto-detected if not provided)
            Option 2 - Base64 content:
              - 'content' (required): Standard base64-encoded file content (not urlsafe)
              - 'filename' (required): Name of the file
              - 'mime_type' (optional): MIME type (defaults to 'application/octet-stream')
        include_signature (bool): Whether to append Gmail signature HTML from send-as settings.
            When include_signature is true and Gmail signature retrieval fails for benign reasons
            (e.g., missing settings authorization), the draft proceeds with the requested or
            authenticated sender and without a signature.
            Non-benign failures such as quota/rate-limit or API errors raise ToolError and abort
            the draft.
        quote_original (bool): Whether to include the original message as a quoted reply.
            Only has an effect when thread_id is provided. When enabled, fetches the
            original message and appends it below the signature. Defaults to False.

    Returns:
        str: Confirmation message with the created draft's ID.

    Examples:
        # Create a new draft
        draft_gmail_message(subject="Hello", body="Hi there!", to="user@example.com")

        # Create a draft from a configured alias (Send As)
        draft_gmail_message(
            subject="Business Inquiry",
            body="Hello from my business address...",
            to="user@example.com",
            from_email="business@mydomain.com"
        )

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
            thread_id="thread_123"
        )

        # Create a reply draft in HTML
        draft_gmail_message(
            subject="Re: Meeting tomorrow",
            body="<strong>Thanks for the update!</strong>",
            body_format="html",
            to="user@example.com",
            thread_id="thread_123"
        )
    """
    logger.info(
        f"[draft_gmail_message] Invoked. Email: '{user_google_email}', subject_len={len(subject) if subject else 0}"
    )

    # Prepare the email message. An explicit alias needs no settings lookup when
    # its signature is disabled. Otherwise resolve the identity and signature
    # together so Gmail's default sender and its signature stay aligned.
    if from_email and not include_signature:
        sender_email, resolved_signature_html = from_email, ""
    else:
        (
            sender_email,
            resolved_signature_html,
        ) = await _get_send_as_identity_and_signature(
            service,
            from_email=from_email,
            fallback_email=user_google_email,
        )
    draft_body = body
    signature_html = resolved_signature_html if include_signature else ""

    reply_context = None
    if thread_id and (quote_original or not in_reply_to or not references or not to):
        reply_context = await _fetch_thread_reply_context(
            service,
            thread_id,
            in_reply_to=in_reply_to,
            include_bodies=quote_original,
        )

    target_reply = reply_context.get("target") if reply_context else None
    if thread_id and (not in_reply_to or not references):
        thread_message_ids = (
            reply_context.get("message_ids", []) if reply_context else []
        )
        in_reply_to, references = _derive_reply_headers(
            thread_message_ids, in_reply_to, references, target_reply
        )

    if thread_id and not to and target_reply:
        to = target_reply.get("reply_to") or target_reply.get("from") or to
    if thread_id and not subject.strip() and target_reply:
        subject = target_reply.get("subject") or subject

    if quote_original and target_reply:
        draft_body = _build_quoted_reply_body(
            draft_body,
            body_format,
            signature_html,
            {
                "sender": target_reply.get("from") or "unknown",
                "date": target_reply.get("date", ""),
                "text_body": target_reply.get("text_body", ""),
                "html_body": target_reply.get("html_body", ""),
            },
        )
    else:
        draft_body = _append_signature_to_body(draft_body, body_format, signature_html)

    resolved_attachments = await _resolve_url_attachments(attachments)
    raw_message, _thread_id_final, attached_count, attachment_errors = (
        _prepare_gmail_message(
            subject=subject,
            body=draft_body,
            body_format=body_format,
            to=to,
            cc=cc,
            bcc=bcc,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references,
            from_email=sender_email,
            from_name=from_name,
            attachments=resolved_attachments,
        )
    )

    requested_attachment_count = len(attachments or [])
    if requested_attachment_count > 0 and attached_count == 0:
        details = (
            f" Details: {'; '.join(attachment_errors)}" if attachment_errors else ""
        )
        raise UserInputError(
            "No valid attachments were added. Verify each attachment path/content and retry."
            f"{details}"
        )

    # Create a draft instead of sending. Gmail requires message.threadId plus
    # RFC-compliant In-Reply-To/References headers to add a draft to a thread.
    # If we could not derive the headers, fall back to an unthreaded draft
    # instead of sending an invalid thread request.
    draft_body = {"message": {"raw": raw_message}}
    if thread_id and in_reply_to and references:
        draft_body["message"]["threadId"] = thread_id

    # Create the draft
    created_draft = await asyncio.to_thread(
        service.users().drafts().create(userId="me", body=draft_body).execute,
        num_retries=GOOGLE_API_WRITE_RETRIES,
    )
    draft_id = created_draft.get("id")
    attachment_info = _format_attachment_result(
        attached_count, requested_attachment_count
    )
    return f"Draft created{attachment_info}! Draft ID: {draft_id}"


def _format_thread_content(
    thread_data: dict,
    thread_id: str,
    body_format: Literal["text", "html", "raw"] = "text",
    raw_contents: Optional[Dict[str, str]] = None,
) -> str:
    """
    Helper function to format thread content from Gmail API response.

    Args:
        thread_data (dict): Thread data from Gmail API
        thread_id (str): Thread ID for display
        body_format: Output format - "text" (default), "html", or "raw"
        raw_contents: Optional mapping of message IDs to decoded raw MIME content

    Returns:
        str: Formatted thread content
    """
    messages = thread_data.get("messages", [])
    if not messages:
        return f"No messages found in thread '{thread_id}'."

    # Extract thread subject from the first message
    first_message = messages[0]
    first_headers = _extract_headers(first_message.get("payload", {}), ["Subject"])
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
        payload = message.get("payload", {})
        # Extract headers
        headers = _extract_headers(payload, GMAIL_METADATA_HEADERS)

        sender = headers.get("From", "(unknown sender)")
        date = headers.get("Date", "(unknown date)")
        subject = headers.get("Subject", "(no subject)")
        reply_to = headers.get("Reply-To", "")
        to = headers.get("To", "")
        cc = headers.get("Cc", "")
        rfc822_message_id = headers.get("Message-ID", "")
        in_reply_to = headers.get("In-Reply-To", "")
        references = headers.get("References", "")

        if body_format == "raw":
            body_data = (raw_contents or {}).get(
                message.get("id", ""), "[No raw content found]"
            )
            body_label = "RAW MIME"
        else:
            # Extract both text and HTML bodies
            bodies = _extract_message_bodies(payload)
            text_body = bodies.get("text", "")
            html_body = bodies.get("html", "")

            # Format body content with HTML fallback
            body_data = _format_body_content(
                text_body, html_body, body_format=body_format
            )
            body_label = "BODY"

        # Extract attachment metadata for this message
        attachments = _extract_attachments(payload)
        message_id = message.get("id", "")

        # Add message to content
        content_lines.extend(
            [
                f"=== Message {i} ===",
                f"From: {sender}",
                f"Date: {date}",
            ]
        )
        if reply_to:
            content_lines.append(f"Reply-To: {reply_to}")
        content_lines.append(
            f"To: {to}" if "To" in headers else "To: [not present in Gmail response]"
        )
        content_lines.append(
            f"Cc: {cc}" if "Cc" in headers else "Cc: [not present in Gmail response]"
        )

        if rfc822_message_id:
            content_lines.append(f"Message-ID: {rfc822_message_id}")
        if in_reply_to:
            content_lines.append(f"In-Reply-To: {in_reply_to}")
        if references:
            content_lines.append(f"References: {references}")

        # Only show subject if it's different from thread subject
        if subject != thread_subject:
            content_lines.append(f"Subject: {subject}")

        if body_format == "raw":
            content_lines.extend(
                [
                    "",
                    f"--- {body_label} ---",
                    body_data,
                    "",
                ]
            )
        else:
            content_lines.extend(["", body_data, ""])

        if attachments:
            content_lines.append("--- ATTACHMENTS ---")
            for attachment_index, att in enumerate(attachments):
                size_kb = att["size"] / 1024
                content_lines.append(
                    f"{attachment_index + 1}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                    f"   Attachment ID: {att['attachmentId']}\n"
                    f"   Use get_gmail_attachment_content(message_id='{message_id}', "
                    f"attachment_id='{att['attachmentId']}', attachment_index={attachment_index}) to download"
                )
            content_lines.append("")

    return "\n".join(content_lines)


@server.tool(
    title="Get Gmail Thread Content",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("gmail", "gmail_read")
@handle_http_errors("get_gmail_thread_content", is_read_only=True, service_type="gmail")
async def get_gmail_thread_content(
    service,
    thread_id: str,
    user_google_email: str,
    body_format: Annotated[
        Literal["text", "html", "raw"],
        Field(
            description=(
                "Body output format. "
                "'text' (default) returns plaintext (HTML converted to text as fallback). "
                "'html' returns the raw HTML body as-is without conversion. "
                "'raw' fetches each message's full raw MIME content and returns the base64url-decoded body."
            ),
        ),
    ] = "text",
    include_analysis: Annotated[
        bool,
        Field(
            description=(
                "When True, the return value is a dict with both the formatted "
                "thread content AND structured ownership analysis (last sender, "
                "ball-in-court verdict, per-sender message counts, participants). "
                "Defaults to False, in which case the existing string return shape "
                "is preserved."
            ),
        ),
    ] = False,
) -> "str | Dict[str, Any]":
    """
    Retrieves the complete content of a Gmail conversation thread, including all messages.

    Optionally also returns structured ownership analysis so a caller can
    determine who sent the last message and who owes whom a response without
    re-parsing the formatted string or making a second tool call.

    Args:
        thread_id (str): The unique ID of the Gmail thread to retrieve.
        user_google_email (str): The user's Google email address. Required.
        body_format (Literal["text", "html", "raw"]): Body output format.
            "text" (default) returns plaintext (HTML converted to text as fallback).
            "html" returns the raw HTML body as-is without conversion.
            "raw" fetches each message's full raw MIME content and returns the base64url-decoded body.
        include_analysis (bool): When True, returns a dict containing both the
            formatted thread content and structured ownership analysis. When
            False (default), returns the formatted content string (existing
            behavior, unchanged).

    Returns:
        str: When `include_analysis=False` (default). The complete thread
        content with all messages formatted for reading.

        Dict[str, Any]: When `include_analysis=True`. A dict with keys
            "content" (str) and "analysis" (dict). See
            `_analyze_thread_ownership_impl` for the analysis schema.
    """
    logger.info(
        f"[get_gmail_thread_content] Invoked. Thread ID: '{thread_id}', "
        f"Email: '{user_google_email}', include_analysis={include_analysis}"
    )

    # Fetch the complete thread with all messages
    thread_response = await asyncio.to_thread(
        service.users().threads().get(userId="me", id=thread_id, format="full").execute
    )

    raw_contents = None
    if body_format == "raw":
        message_ids = [
            message["id"]
            for message in thread_response.get("messages", [])
            if message.get("id")
        ]
        raw_contents = await _fetch_raw_message_contents(
            service, message_ids, log_prefix="get_gmail_thread_content"
        )

    content = _format_thread_content(
        thread_response,
        thread_id,
        body_format=body_format,
        raw_contents=raw_contents,
    )

    if not include_analysis:
        return content

    analysis = _analyze_thread_ownership_impl(thread_response, user_google_email)
    return {"content": content, "analysis": analysis}


@server.tool(
    title="Get Gmail Threads Content Batch",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("gmail", "gmail_read")
@handle_http_errors(
    "get_gmail_threads_content_batch", is_read_only=True, service_type="gmail"
)
async def get_gmail_threads_content_batch(
    service,
    thread_ids: StringList,
    user_google_email: str,
    body_format: Annotated[
        Literal["text", "html", "raw"],
        Field(
            description=(
                "Body output format. "
                "'text' (default) returns plaintext (HTML converted to text as fallback). "
                "'html' returns the raw HTML body as-is without conversion. "
                "'raw' fetches each message's full raw MIME content and returns the base64url-decoded body."
            ),
        ),
    ] = "text",
) -> str:
    """
    Retrieves the content of multiple Gmail threads in a single batch request.
    Supports up to 25 threads per batch to prevent SSL connection exhaustion.

    Args:
        thread_ids (List[str]): A list of Gmail thread IDs to retrieve. The function will automatically batch requests in chunks of 25.
        user_google_email (str): The user's Google email address. Required.
        body_format (Literal["text", "html", "raw"]): Body output format.
            "text" (default) returns plaintext (HTML converted to text as fallback).
            "html" returns the raw HTML body as-is without conversion.
            "raw" fetches each message's full raw MIME content and returns the base64url-decoded body.

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

        batch_completed = False

        # Try to use batch API
        try:
            batch = service.new_batch_http_request(callback=_batch_callback)

            for tid in chunk_ids:
                req = service.users().threads().get(userId="me", id=tid, format="full")
                batch.add(req, request_id=tid)

            # Execute batch request
            await asyncio.to_thread(batch.execute)
            batch_completed = True

        except Exception as batch_error:
            # Fallback to sequential processing instead of parallel to prevent SSL exhaustion
            logger.warning(
                f"[get_gmail_threads_content_batch] Batch API failed, falling back to sequential processing: {batch_error}"
            )

            # Process threads sequentially with small delays to prevent connection exhaustion
            for tid in chunk_ids:
                tid_result, thread_data, error = await _fetch_thread_with_retry(
                    service,
                    thread_id=tid,
                    log_prefix="get_gmail_threads_content_batch",
                )
                results[tid_result] = {"data": thread_data, "error": error}
                # Brief delay between requests to allow connection cleanup
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        # Sub-requests that failed with a transient error (e.g. 429 rate limit)
        # inside an otherwise successful batch response: re-fetch only those IDs
        # and merge. The sequential fallback has already exhausted its retries.
        retryable_ids = (
            _retryable_result_ids(results, chunk_ids) if batch_completed else []
        )
        if retryable_ids:
            logger.warning(
                f"[get_gmail_threads_content_batch] {len(retryable_ids)}/{len(chunk_ids)} "
                f"threads failed with retryable errors; re-fetching: {retryable_ids}"
            )
            await asyncio.sleep(GMAIL_RATE_LIMIT_BACKOFF)
            for tid in retryable_ids:
                tid_result, thread_data, error = await _fetch_thread_with_retry(
                    service,
                    thread_id=tid,
                    log_prefix="get_gmail_threads_content_batch",
                )
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

                raw_contents = None
                if body_format == "raw":
                    message_ids = [
                        message["id"]
                        for message in thread.get("messages", [])
                        if message.get("id")
                    ]
                    raw_contents = await _fetch_raw_message_contents(
                        service,
                        message_ids,
                        log_prefix="get_gmail_threads_content_batch",
                    )

                output_threads.append(
                    _format_thread_content(
                        thread,
                        tid,
                        body_format=body_format,
                        raw_contents=raw_contents,
                    )
                )

    # Combine all threads with separators
    header = f"Retrieved {len(thread_ids)} threads:"
    return header + "\n\n" + "\n---\n\n".join(output_threads)


@server.tool(
    title="List Gmail Labels",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("list_gmail_labels", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_read")
async def list_gmail_labels(
    service,
    user_google_email: str,
    prefix: Optional[str] = None,
    compact: bool = False,
    include_system: bool = True,
) -> str:
    """
    Lists labels in the user's Gmail account.

    Args:
        user_google_email (str): The user's Google email address. Required.
        prefix (Optional[str]): Return only labels whose name starts with this
            exact (case-sensitive) string. users.labels.list accepts no filter,
            so the full list is fetched and narrowed here: this shrinks what the
            caller receives, not the API call.
        compact (bool): Return minimal JSON {"count", "labels": [{"id", "name"}]}
            sorted by name, instead of the formatted text list. For callers
            that parse the result, e.g. a label cache refresh.
        include_system (bool): Include Gmail system labels (INBOX, SENT, ...).
            Set False to return user labels only.

    Returns:
        str: A formatted list of labels, or minimal JSON when compact=True.
    """
    logger.info(f"[list_gmail_labels] Invoked. Email: '{user_google_email}'")

    response = await asyncio.to_thread(
        service.users().labels().list(userId="me").execute
    )
    labels = response.get("labels", [])

    if not include_system:
        labels = [lab for lab in labels if lab.get("type") != "system"]
    if prefix is not None:
        labels = [lab for lab in labels if lab.get("name", "").startswith(prefix)]

    if compact:
        return json.dumps(
            {
                "count": len(labels),
                "labels": sorted(
                    ({"id": lab["id"], "name": lab["name"]} for lab in labels),
                    key=lambda lab: lab["name"],
                ),
            },
            ensure_ascii=False,
        )

    if not labels:
        parts = ["No"]
        parts.append("user" if not include_system else "")
        parts.append("labels found")
        if prefix is not None:
            parts.append(f"with prefix {prefix!r}")
        msg = " ".join(p for p in parts if p) + "."
        return msg

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


@server.tool(
    title="Manage Gmail Label",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
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
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    clear_color: bool = False,
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
        background_color (Optional[str]): Label background color as a hex string, e.g. "#fb4c2f". Set together with text_color; Gmail requires both. Gmail accepts only its own palette, and an unsupported value is rejected before the request. Colors apply to user labels, not system labels.
        text_color (Optional[str]): Label text color as a hex string, e.g. "#ffffff". Set together with background_color. Same palette. On update, omitting both keeps the label's current color.
        clear_color (bool): On update, remove the label's current color. Cannot be combined with background_color or text_color.

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

    color = None
    if action in ["create", "update"]:
        if clear_color:
            if action == "create":
                raise ToolExecutionError(
                    "clear_color is only valid for update actions."
                )
            if background_color is not None or text_color is not None:
                raise ToolExecutionError(
                    "clear_color cannot be combined with background_color or text_color."
                )
        else:
            # Validated before any request so a bad color costs no API call.
            color = build_label_color(background_color, text_color)

    if action == "create":
        label_object = {
            "name": name,
            "labelListVisibility": label_list_visibility,
            "messageListVisibility": message_list_visibility,
        }
        if color:
            label_object["color"] = color
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
        label_color = None if clear_color else color or current_label.get("color")
        if label_color:
            label_object["color"] = label_color

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


@server.tool(
    title="List Gmail Filters",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("list_gmail_filters", is_read_only=True, service_type="gmail")
@require_google_service("gmail", "gmail_settings_basic")
async def list_gmail_filters(service, user_google_email: str) -> str:
    """
    Lists all Gmail filters configured in the user's mailbox.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: A formatted list of filters with their criteria and actions.
    """
    logger.info(f"[list_gmail_filters] Invoked. Email: '{user_google_email}'")

    response = await asyncio.to_thread(
        service.users().settings().filters().list(userId="me").execute
    )

    filters = response.get("filter") or response.get("filters") or []

    if not filters:
        return "No filters found."

    lines = [f"Found {len(filters)} filters:", ""]

    for filter_obj in filters:
        filter_id = filter_obj.get("id", "(no id)")
        criteria = filter_obj.get("criteria", {})
        action = filter_obj.get("action", {})

        lines.append(f"🔹 Filter ID: {filter_id}")
        lines.append("  Criteria:")

        criteria_lines = []
        if criteria.get("from"):
            criteria_lines.append(f"From: {criteria['from']}")
        if criteria.get("to"):
            criteria_lines.append(f"To: {criteria['to']}")
        if criteria.get("subject"):
            criteria_lines.append(f"Subject: {criteria['subject']}")
        if criteria.get("query"):
            criteria_lines.append(f"Query: {criteria['query']}")
        if criteria.get("negatedQuery"):
            criteria_lines.append(f"Exclude Query: {criteria['negatedQuery']}")
        if criteria.get("hasAttachment"):
            criteria_lines.append("Has attachment")
        if criteria.get("excludeChats"):
            criteria_lines.append("Exclude chats")
        if criteria.get("size"):
            comparison = criteria.get("sizeComparison", "")
            criteria_lines.append(
                f"Size {comparison or ''} {criteria['size']} bytes".strip()
            )

        if not criteria_lines:
            criteria_lines.append("(none)")

        lines.extend([f"    • {line}" for line in criteria_lines])

        lines.append("  Actions:")
        action_lines = []
        if action.get("forward"):
            action_lines.append(f"Forward to: {action['forward']}")
        if action.get("removeLabelIds"):
            action_lines.append(f"Remove labels: {', '.join(action['removeLabelIds'])}")
        if action.get("addLabelIds"):
            action_lines.append(f"Add labels: {', '.join(action['addLabelIds'])}")

        if not action_lines:
            action_lines.append("(none)")

        lines.extend([f"    • {line}" for line in action_lines])
        lines.append("")

    return "\n".join(lines).rstrip()


@server.tool(
    title="Manage Gmail Filter",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_gmail_filter", service_type="gmail")
@require_google_service("gmail", "gmail_settings_basic")
async def manage_gmail_filter(
    service,
    user_google_email: str,
    action: str,
    criteria: Optional[JsonDict] = None,
    filter_action: Optional[JsonDict] = None,
    filter_id: Optional[str] = None,
) -> str:
    """
    Manages Gmail filters. Supports creating and deleting filters.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): Action to perform - "create" or "delete".
        criteria (Optional[Dict[str, Any]]): Filter criteria object (required for create).
        filter_action (Optional[Dict[str, Any]]): Filter action object (required for create). Named 'filter_action' to avoid shadowing the 'action' parameter.
        filter_id (Optional[str]): ID of the filter to delete (required for delete).

    Returns:
        str: Confirmation message with filter details.
    """
    action_lower = action.lower().strip()
    if action_lower == "create":
        if not criteria or not filter_action:
            raise ValueError(
                "criteria and filter_action are required for create action"
            )
        logger.info("[manage_gmail_filter] Creating filter")
        filter_body = {"criteria": criteria, "action": filter_action}
        created_filter = await asyncio.to_thread(
            service.users()
            .settings()
            .filters()
            .create(userId="me", body=filter_body)
            .execute
        )
        fid = created_filter.get("id", "(unknown)")
        return f"Filter created successfully!\nFilter ID: {fid}"
    elif action_lower == "delete":
        if not filter_id:
            raise ValueError("filter_id is required for delete action")
        logger.info(f"[manage_gmail_filter] Deleting filter {filter_id}")
        filter_details = await asyncio.to_thread(
            service.users().settings().filters().get(userId="me", id=filter_id).execute
        )
        await asyncio.to_thread(
            service.users()
            .settings()
            .filters()
            .delete(userId="me", id=filter_id)
            .execute
        )
        criteria_info = filter_details.get("criteria", {})
        action_info = filter_details.get("action", {})
        return (
            "Filter deleted successfully!\n"
            f"Filter ID: {filter_id}\n"
            f"Criteria: {criteria_info or '(none)'}\n"
            f"Action: {action_info or '(none)'}"
        )
    else:
        raise ValueError(
            f"Invalid action '{action_lower}'. Must be 'create' or 'delete'."
        )


@server.tool(
    title="Modify Gmail Message Labels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("modify_gmail_message_labels", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def modify_gmail_message_labels(
    service,
    user_google_email: str,
    message_id: str,
    add_label_ids: _OptionalLabelIdList = None,
    remove_label_ids: _OptionalLabelIdList = None,
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


def _format_id_list(ids: List[str], limit: int = 10) -> str:
    """Join IDs for a result message, truncating very long lists."""
    shown = ", ".join(ids[:limit])
    if len(ids) > limit:
        shown += f", … (+{len(ids) - limit} more)"
    return shown


async def _verify_batch_label_changes(
    service,
    message_ids: List[str],
    add_label_ids: Optional[List[str]],
    remove_label_ids: Optional[List[str]],
) -> Dict[str, str]:
    """Read the messages back to see which requested label changes actually landed.

    ``users.messages.batchModify`` answers ``204 No Content`` and silently
    ignores IDs it does not recognise, so its own response cannot distinguish a
    sweep that changed everything from one that changed nothing. This re-reads
    each message with ``format="minimal"`` (``labelIds`` only) and classifies it.

    Returns a per-ID status: ``applied`` (end state matches the request),
    ``not_applied`` (message exists, labels do not match), ``unresolved``
    (the ID does not currently resolve to a message), or ``unverified`` (the
    read-back itself failed, so nothing can be claimed either way).
    """
    wanted = set(add_label_ids or ())
    unwanted = set(remove_label_ids or ())
    statuses: Dict[str, str] = {}

    for chunk_start in range(0, len(message_ids), GMAIL_BATCH_SIZE):
        chunk_ids = message_ids[chunk_start : chunk_start + GMAIL_BATCH_SIZE]
        results: Dict[str, Dict] = {}
        batch_completed = False

        def _batch_callback(request_id, response, exception):
            results[request_id] = {"data": response, "error": exception}

        try:
            batch = service.new_batch_http_request(callback=_batch_callback)
            for mid in chunk_ids:
                batch.add(
                    service.users()
                    .messages()
                    .get(userId="me", id=mid, format="minimal"),
                    request_id=mid,
                )
            await asyncio.to_thread(batch.execute)
            batch_completed = True
        except Exception as batch_error:
            # Same fallback the read tools use: sequential reads with a small
            # delay, to avoid exhausting connections when the batch endpoint is
            # unavailable.
            logger.warning(
                f"[batch_modify_gmail_message_labels] Verification batch failed, "
                f"falling back to sequential reads: {batch_error}"
            )
            for mid in chunk_ids:
                mid_result, data, error = await _fetch_with_retry(
                    build_request=lambda mid=mid: (
                        service.users()
                        .messages()
                        .get(userId="me", id=mid, format="minimal")
                    ),
                    item_id=mid,
                    item_label="message",
                    log_prefix="batch_modify_gmail_message_labels",
                )
                results[mid_result] = {"data": data, "error": error}
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        retryable_ids = (
            _retryable_result_ids(results, chunk_ids) if batch_completed else []
        )
        if retryable_ids:
            logger.warning(
                f"[batch_modify_gmail_message_labels] "
                f"{len(retryable_ids)}/{len(chunk_ids)} verification reads failed "
                f"with retryable errors; re-fetching: {retryable_ids}"
            )
            await asyncio.sleep(GMAIL_RATE_LIMIT_BACKOFF)
            for mid in retryable_ids:
                mid_result, data, error = await _fetch_with_retry(
                    build_request=lambda mid=mid: (
                        service.users()
                        .messages()
                        .get(userId="me", id=mid, format="minimal")
                    ),
                    item_id=mid,
                    item_label="message",
                    log_prefix="batch_modify_gmail_message_labels",
                )
                results[mid_result] = {"data": data, "error": error}
                await asyncio.sleep(GMAIL_REQUEST_DELAY)

        for mid in chunk_ids:
            entry = results.get(mid)
            if not entry or (entry.get("data") is None and entry.get("error") is None):
                statuses[mid] = "unverified"
                continue
            error = entry.get("error")
            if error is not None:
                statuses[mid] = (
                    "unresolved" if _http_error_status(error) == 404 else "unverified"
                )
                continue
            labels = set((entry.get("data") or {}).get("labelIds") or ())
            statuses[mid] = (
                "applied"
                if wanted <= labels and not (unwanted & labels)
                else "not_applied"
            )

    return statuses


@server.tool(
    title="Batch Modify Gmail Message Labels",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("batch_modify_gmail_message_labels", service_type="gmail")
@require_google_service("gmail", GMAIL_MODIFY_SCOPE)
async def batch_modify_gmail_message_labels(
    service,
    user_google_email: str,
    message_ids: StringList,
    add_label_ids: _OptionalLabelIdList = None,
    remove_label_ids: _OptionalLabelIdList = None,
    verify: bool = True,
) -> str:
    """
    Adds or removes labels from multiple Gmail messages in a single batch request.

    Takes MESSAGE ids, not thread ids. Gmail's batch endpoint returns no
    per-message result and silently ignores ids it does not recognise, so by
    default this reads the messages back afterwards and reports which ids
    actually changed.

    Args:
        user_google_email (str): The user's Google email address. Required.
        message_ids (List[str]): A list of message IDs to modify.
        add_label_ids (Optional[List[str]]): List of label IDs to add to the messages.
        remove_label_ids (Optional[List[str]]): List of label IDs to remove from the messages.
        verify (bool): Read the messages back and report per-id outcomes. Costs
            one extra (batched) read per id. Set False for very large sweeps
            where that cost matters and an unverified result is acceptable.

    Returns:
        str: Which label changes were applied, and which ids did not resolve.
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
    action_summary = "; ".join(actions)

    total = len(message_ids)

    if not verify:
        return (
            f"Requested label changes for {total} message ID(s): {action_summary}\n"
            "NOT VERIFIED: Gmail's batchModify returns no per-message result and "
            "silently ignores IDs it does not recognise, so this records what was "
            "asked for, not what changed. Re-run with verify=True to confirm."
        )

    statuses = await _verify_batch_label_changes(
        service, message_ids, add_label_ids, remove_label_ids
    )
    applied = [mid for mid in message_ids if statuses.get(mid) == "applied"]
    not_applied = [mid for mid in message_ids if statuses.get(mid) == "not_applied"]
    unresolved = [mid for mid in message_ids if statuses.get(mid) == "unresolved"]
    unverified = [mid for mid in message_ids if statuses.get(mid) == "unverified"]

    lines = [
        f"Label changes for {total} message ID(s): {action_summary}",
        f"Applied: {len(applied)}/{total}",
    ]
    if unresolved:
        lines.append(
            f"No such message ({len(unresolved)}): {_format_id_list(unresolved)}\n"
            "  These IDs do not currently resolve to messages in this mailbox, "
            "so the requested change could not be verified. One possible cause "
            "is passing a THREAD id where a MESSAGE id is required."
        )
    if not_applied:
        lines.append(
            f"Unchanged ({len(not_applied)}): {_format_id_list(not_applied)}\n"
            "  These messages exist but their labels do not match the request."
        )
    if unverified:
        lines.append(
            f"Could not verify ({len(unverified)}): {_format_id_list(unverified)}\n"
            "  The read-back failed; the change may or may not have been applied."
        )

    return "\n".join(lines)
