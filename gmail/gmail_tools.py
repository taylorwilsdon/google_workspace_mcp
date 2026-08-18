"""
Google Gmail MCP Tools

This module provides MCP tools for interacting with the Gmail API.
"""

import logging
import asyncio
import base64
import binascii
import re
import mimetypes
import html
from html.parser import HTMLParser
from pathlib import Path
from typing import Annotated, Optional, List, Dict, Literal, Any
from urllib.parse import unquote, urlparse, urlunsplit

from email.header import Header
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import formataddr, getaddresses, parseaddr

import httpx
from mcp.types import ToolAnnotations

from pydantic import Field
from googleapiclient.errors import HttpError

from auth.oauth_config import is_stateless_mode
from auth.service_decorator import (
    require_google_service,
    require_multiple_services,
)
from core.attachment_storage import (
    get_attachment_storage,
    get_attachment_url,
    STORAGE_DIR,
)
from core.config import (
    get_transport_mode,
    WORKSPACE_EXTERNAL_URL,
    WORKSPACE_MCP_BASE_URI,
    WORKSPACE_MCP_PORT,
)
from gmail.gmail_send_transport import dispatch_transmit, resolve_effective_transport
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
    CONTACTS_READONLY_SCOPE,
    CONTACTS_OTHER_READONLY_SCOPE,
    DIRECTORY_READONLY_SCOPE,
)
from gmail.gmail_helpers import (
    GMAIL_METADATA_HEADERS,
    RAW_BODY_TRUNCATE_LIMIT,
    _analyze_thread_ownership_impl,
    _derive_reply_all_recipients,
    _derive_reply_headers,
    _fetch_with_retry,
    _is_benign_signature_http_error,
    _parse_date_header,
    _retryable_result_ids,
    _signature_fetch_tool_error,
    _signature_html_to_text,
    html_to_text_preserving_breaks,
)
from gmail.gmail_web_mime import (
    assemble_alternative,
    assemble_web_message,
    build_forwarded_container_html,
    build_forwarded_plain,
    base_text_direction,
    build_quote_container_html,
    build_quote_plain,
    encode_raw,
    format_attribution_html,
    format_attribution_plain,
    format_display_address,
    gmail_boundary,
    new_message_html,
    normalize_reply_subject,
    plain_body_to_html,
    render_forward_recipients_html,
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

    # Encode + write on a worker thread so a large export doesn't block the event loop.
    # Cap the sender-controlled subject so a pathologically long Subject can't overflow
    # the filesystem's filename limit, and surface a clean error if the write fails.
    storage = get_attachment_storage()

    def _save_export():
        return storage.save_attachment(
            base64_data=base64.urlsafe_b64encode(content_bytes).decode("ascii"),
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


# A body whose markup arrived HTML-entity-escaped ("&lt;div ...&gt;" rather than
# "<div ...>"). The opening-tag pattern is anchored at the first non-space
# character so a body that merely mentions an escaped tag mid-sentence is not
# matched.
_ESCAPED_HTML_OPENING_TAG = re.compile(r"^\s*&lt;\s*[A-Za-z][A-Za-z0-9-]*(?:\s|/|&gt;)")
_RAW_HTML_TAG = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9-]*(?:\s|/|>)")


def _reject_entity_escaped_html_body(
    body: Optional[str], body_format: Literal["plain", "html"]
) -> None:
    """Reject an HTML body whose markup was entity-escaped by the caller.

    A caller that writes ``&lt;div&gt;...&lt;/div&gt;`` into an ``html`` body
    means the markup, not those literal characters -- but Gmail renders it as
    visible tags, so the recipient receives the raw markup as text. Failing here
    costs one retry; sending is unrecoverable once it lands in someone's inbox.

    The test is deliberately narrow: the body must OPEN with an escaped tag and
    contain no raw tag anywhere. A genuine HTML body that quotes ``&lt;div&gt;``
    as sample text also carries real markup, so it is left alone.
    """
    if not body or body_format.lower() != "html" or "&lt;" not in body:
        return
    if not _ESCAPED_HTML_OPENING_TAG.match(body) or _RAW_HTML_TAG.search(body):
        return
    raise UserInputError(
        "body_format='html' but the body's markup is HTML-entity-escaped: it "
        "opens with '&lt;' and contains no real tag, so the recipient would see "
        "the tags as literal text. Resend with unescaped markup (write '<div "
        "dir=\"rtl\">', not '&lt;div dir=\"rtl\"&gt;'), or pass body_format='plain' "
        "if those entities are intentional."
    )


async def _get_send_as_entries(service) -> List[Dict[str, Any]]:
    """Fetch Gmail send-as entries.

    Returns [] when the settings endpoint is unavailable due to a benign
    auth/scope error; raises a tool error for non-benign failures. Each entry
    carries both the configured ``signature`` and the ``displayName`` Gmail web
    renders in the From line.
    """
    try:
        response = await asyncio.to_thread(
            service.users().settings().sendAs().list(userId="me").execute
        )
    except HttpError as e:
        if _is_benign_signature_http_error(e):
            logger.info(
                "Skipping Gmail send-as fetch: missing auth/scope for settings endpoint."
            )
            return []
        logger.error(f"Failed to fetch Gmail send-as settings: {e}", exc_info=True)
        raise _signature_fetch_tool_error(e) from e
    except Exception as e:
        logger.error(f"Failed to fetch Gmail send-as settings: {e}", exc_info=True)
        raise _signature_fetch_tool_error(e) from e

    if not isinstance(response, dict):
        return []
    return response.get("sendAs", [])


def _match_send_as_entry(
    entries: List[Dict[str, Any]], from_email: Optional[str]
) -> Optional[Dict[str, Any]]:
    """Pick the send-as entry for ``from_email`` (exact match > primary > first)."""
    if not entries:
        return None
    if from_email:
        from_email_normalized = from_email.strip().lower()
        for entry in entries:
            if entry.get("sendAsEmail", "").strip().lower() == from_email_normalized:
                return entry
    for entry in entries:
        if entry.get("isPrimary"):
            return entry
    return entries[0]


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


# People API readMask for name lookups: names + the emails to match against.
_PEOPLE_NAME_READ_MASK = "names,emailAddresses"

# Human-readable labels for the name-resolution scopes, used in the fallback note.
_NAME_SCOPE_LABELS = {
    CONTACTS_READONLY_SCOPE: "contacts.readonly (your saved contacts)",
    CONTACTS_OTHER_READONLY_SCOPE: (
        "contacts.other.readonly (auto-collected 'Other contacts')"
    ),
    DIRECTORY_READONLY_SCOPE: "directory.readonly (your Workspace directory)",
}


def _harvest_thread_display_names(messages: List[Dict[str, Any]]) -> Dict[str, str]:
    """Map ``email -> display name`` from every thread participant's headers.

    Mirrors Gmail's reply behavior: when you reply without editing recipients,
    the names written into To/Cc come from the conversation's own
    From/Reply-To/To/Cc headers (sender-supplied). First non-empty name seen per
    address wins.
    """
    names: Dict[str, str] = {}
    for msg in messages or []:
        for field in ("from", "reply_to", "to", "cc"):
            for existing_name, addr in getaddresses([msg.get(field) or ""]):
                if not addr:
                    continue
                key = addr.strip().lower()
                candidate = (existing_name or "").strip()
                if candidate and key not in names:
                    names[key] = candidate
    return names


def _match_person_name(persons: List[Dict[str, Any]], key: str) -> Optional[str]:
    """Return the displayName of the person whose emails include ``key``."""
    for person in persons:
        emails = [
            (e.get("value") or "").strip().lower()
            for e in person.get("emailAddresses", [])
        ]
        if key in emails:
            names = person.get("names", [])
            if names:
                candidate = (names[0].get("displayName") or "").strip()
                if candidate:
                    return candidate
            return None
    return None


async def _people_search_tier(
    request_factory,
    params: Dict[str, Any],
    key: str,
    *,
    results_key: str,
    wrap_key: Optional[str],
    scope: str,
    missing_scopes: Optional[set],
) -> Optional[str]:
    """Run one People search tier and extract a matching display name.

    Best-effort: a 403 (scope not granted) records ``scope`` in ``missing_scopes``
    and returns None; any other failure is logged and returns None. ``wrap_key``
    is the per-result wrapper field (``person`` for searchContacts/otherContacts,
    None for searchDirectoryPeople where results are person objects directly).
    """
    try:
        result = await asyncio.to_thread(request_factory(**params).execute)
    except HttpError as e:
        status = getattr(getattr(e, "resp", None), "status", None)
        if status in (401, 403):
            if missing_scopes is not None:
                missing_scopes.add(scope)
        else:
            logger.info("People name lookup tier failed: %s", e)
        return None
    except Exception as e:
        logger.info("People name lookup tier failed: %s", e)
        return None

    if not isinstance(result, dict):
        return None
    items = result.get(results_key, []) or []
    persons = [
        (item.get(wrap_key, {}) if wrap_key else item)
        for item in items
        if isinstance(item, dict)
    ]
    return _match_person_name(persons, key)


async def _people_contacts_tier(
    people_service, email: str, key: str, missing_scopes: Optional[set]
) -> Optional[str]:
    """Saved-contacts lookup (highest People tier; Gmail's documented winner)."""
    return await _people_search_tier(
        people_service.people().searchContacts,
        {"query": email, "readMask": _PEOPLE_NAME_READ_MASK},
        key,
        results_key="results",
        wrap_key="person",
        scope=CONTACTS_READONLY_SCOPE,
        missing_scopes=missing_scopes,
    )


async def _people_other_directory_tiers(
    people_service, email: str, key: str, missing_scopes: Optional[set]
) -> Optional[str]:
    """Auto-collected 'Other contacts' then Workspace directory, in that order.

    Used for addresses not in saved contacts and not in the conversation (a
    typed/added recipient -- Scenario 2). Each tier is best-effort; first hit wins.
    """
    name = await _people_search_tier(
        people_service.otherContacts().search,
        {"query": email, "readMask": _PEOPLE_NAME_READ_MASK},
        key,
        results_key="results",
        wrap_key="person",
        scope=CONTACTS_OTHER_READONLY_SCOPE,
        missing_scopes=missing_scopes,
    )
    if name:
        return name
    return await _people_search_tier(
        people_service.people().searchDirectoryPeople,
        {
            "query": email,
            "readMask": _PEOPLE_NAME_READ_MASK,
            "sources": [
                "DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE",
                "DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT",
            ],
        },
        key,
        results_key="people",
        wrap_key=None,
        scope=DIRECTORY_READONLY_SCOPE,
        missing_scopes=missing_scopes,
    )


async def _warmup_people_cache(people_service) -> None:
    """Issue empty-query searches so the first real lookup hits a warm cache.

    Google recommends a warmup request before searchContacts/otherContacts.search
    (a freshly authorized token has a cold people index). Fully best-effort.
    """
    for request_factory in (
        people_service.people().searchContacts,
        people_service.otherContacts().search,
    ):
        try:
            await asyncio.to_thread(
                request_factory(query="", readMask=_PEOPLE_NAME_READ_MASK).execute
            )
        except Exception:
            pass


async def _lookup_display_name(
    people_service,
    email: str,
    cache: Dict[str, Optional[str]],
    thread_names: Optional[Dict[str, str]] = None,
    missing_scopes: Optional[set] = None,
) -> Optional[str]:
    """Resolve a display name for ``email`` the way Gmail does, best-effort.

    Priority (highest first), per Google's documented compose/reply behavior:
      1. Saved Contacts (People ``searchContacts``) -- Gmail re-resolves the chip
         against your contacts at compose AND reply time, so a saved name wins
         even over the thread's sender-supplied name (subject to a ~24h lag).
      2. Thread-participant header name (``thread_names``) -- the sender-supplied
         name seeded into the reply chip for an address NOT in your contacts
         (covers replies to non-contacts like an external counterpart, no scope).
      3. People index for still-unresolved addresses (Scenario 2: typed/added):
         Other contacts -> Workspace directory.
      4. None -> caller emits the bare address.

    Never raises: any People failure (missing scope, network) degrades to the
    next tier or bare address. Results are memoized in ``cache``.
    """
    key = email.strip().lower()
    if key in cache:
        return cache[key]

    name: Optional[str] = None
    # 1. Saved contacts win (Google's documented compose/reply resolution).
    if people_service is not None and key:
        name = await _people_contacts_tier(people_service, email, key, missing_scopes)
    # 2. Thread/sender-supplied name for non-contacts in the conversation.
    if not name and thread_names and key in thread_names:
        name = thread_names[key]
    # 3. Other contacts then directory for typed/added addresses.
    if not name and people_service is not None and key:
        name = await _people_other_directory_tiers(
            people_service, email, key, missing_scopes
        )

    cache[key] = name
    return name


async def _format_address_list_with_names(
    people_service,
    header_value: Optional[str],
    cache: Dict[str, Optional[str]],
    thread_names: Optional[Dict[str, str]] = None,
    missing_scopes: Optional[set] = None,
) -> Optional[str]:
    """Format a To/Cc/Bcc header value as ``Display Name <addr>`` per address.

    Parses the header (RFC-correct, honoring existing display names), resolves a
    display name for each bare address (thread name, then people index), and
    re-emits the comma-separated list. Addresses that already carry a display
    name keep it. Unresolved addresses are emitted bare. Returns None when input
    is empty.
    """
    if not header_value or not header_value.strip():
        return None

    formatted: List[str] = []
    for existing_name, addr in getaddresses([header_value]):
        if not addr:
            continue
        name = existing_name.strip() if existing_name else None
        if not name:
            name = await _lookup_display_name(
                people_service,
                addr,
                cache,
                thread_names=thread_names,
                missing_scopes=missing_scopes,
            )
        formatted.append(format_display_address(name, addr))
    return ", ".join(formatted) if formatted else None


def _build_name_fallback_note(
    people_service_absent: bool, missing_scopes: Optional[set]
) -> str:
    """Build the result note when recipient names couldn't be resolved.

    Lists exactly the scopes that would have helped (all three when the People
    service is entirely absent; otherwise the specific tiers that returned a
    scope error), and how to grant them. Returns "" when nothing is actionable.
    """
    if people_service_absent:
        scopes = set(_NAME_SCOPE_LABELS)
    else:
        scopes = set(missing_scopes or set())
    if not scopes:
        return ""
    listed = "; ".join(
        label for scope, label in _NAME_SCOPE_LABELS.items() if scope in scopes
    )
    return (
        "\n\n[Heads up] Some recipient display names could not be resolved, so "
        "those addresses were sent as bare emails. For Gmail-web-style names, "
        f"grant: {listed}. Enable the 'contacts' tool (it requests these scopes) "
        "and re-authenticate by running start_google_auth for this account."
    )


async def _build_web_compose_raw(
    gmail_service,
    people_service,
    *,
    subject: Optional[str],
    body: str,
    body_format: Literal["plain", "html"],
    to: Optional[str],
    cc: Optional[str],
    bcc: Optional[str],
    from_email: str,
    from_name: Optional[str],
    thread_id: Optional[str],
    in_reply_to: Optional[str],
    references: Optional[str],
    quote_reply: bool = True,
    reply_target: Optional[Dict[str, Any]] = None,
    auto_thread: bool = True,
    thread_names: Optional[Dict[str, str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
    include_bcc_header: bool = True,
    direction: str = "auto",
) -> tuple[str, bool, set, int, List[str]]:
    """Assemble a Gmail-web faithful raw message for the send/draft tools.

    Returns ``(raw_message, had_unresolved, missing_scopes, attached_count,
    attachment_errors)``. ``had_unresolved`` is True when at least one
    looked-up recipient yielded no display name; ``missing_scopes`` holds the
    name-resolution scopes that returned a scope error. Together they drive the
    caller's scope-fallback note.

    Resolves display names (best-effort), auto-populates reply headers from the
    thread (when ``auto_thread`` and not pre-supplied), builds both body parts
    (appending a reply quote when in a thread and ``quote_reply`` is set), and
    delegates the deterministic MIME assembly to ``_prepare_gmail_message``'s web
    path. When ``reply_target`` is provided it is used for the quote instead of
    re-fetching the thread. ``thread_names`` supplies sender-supplied display
    names harvested from the conversation (used when the caller already fetched
    the thread). Never raises for name-resolution or parent-fetch failures.
    When ``attachments`` is provided they are passed through to the web MIME
    assembler so the resulting message is web-faithful even with attachments.
    """
    # Fetch the thread once (with bodies) when replying so a single round-trip
    # serves both auto-threading and the reply-quote trail.
    if auto_thread and thread_id and reply_target is None:
        try:
            context = await _fetch_thread_reply_context(
                gmail_service,
                thread_id,
                in_reply_to=in_reply_to,
                include_bodies=quote_reply,
            )
        except Exception as e:
            logger.info("Reply-context fetch failed: %s", e)
            context = None
        if context:
            if not in_reply_to or not references:
                in_reply_to, references = _derive_reply_headers(
                    context.get("message_ids", []), in_reply_to, references
                )
            reply_target = context.get("target")
            # Harvest participant names so reply recipients resolve like Gmail's
            # (sender-supplied names) even without any People scope.
            if thread_names is None:
                thread_names = _harvest_thread_display_names(
                    context.get("messages", [])
                )

    # Resolve the reply subject. When the caller omitted it (reply mode),
    # inherit the parent's exact subject so subject-sensitive clients keep the
    # message in-thread. Add a single "Re: " only when absent -- tag-aware, so
    # an inherited "[list] Re: ..." never gains a second Re: and tags like
    # [#123] are preserved verbatim.
    is_reply = bool(in_reply_to or thread_id)
    if (subject is None or not subject.strip()) and reply_target:
        subject = reply_target.get("subject") or subject
    if is_reply and subject and subject.strip():
        subject = normalize_reply_subject(subject)
    if subject is None or not subject.strip():
        raise UserInputError(
            "A subject is required. It may be omitted only when replying "
            "(thread_id/in_reply_to) to a message whose subject can be inherited."
        )

    # Resolve display names for the sender and each recipient list. Thread names
    # cover reply recipients (Scenario 1); the People index covers typed/added
    # addresses (Scenario 2). missing_scopes collects tiers that returned a scope
    # error so the caller can name them in the fallback note.
    name_cache: Dict[str, Optional[str]] = {}
    missing_scopes: set = set()
    if people_service is not None:
        await _warmup_people_cache(people_service)
    # Sender name is NOT contacts-resolved — it comes from Send-As only.
    # When from_name is None here, the From header renders as the bare address.
    to_fmt = await _format_address_list_with_names(
        people_service,
        to,
        name_cache,
        thread_names=thread_names,
        missing_scopes=missing_scopes,
    )
    cc_fmt = await _format_address_list_with_names(
        people_service,
        cc,
        name_cache,
        thread_names=thread_names,
        missing_scopes=missing_scopes,
    )
    bcc_fmt = await _format_address_list_with_names(
        people_service,
        bcc,
        name_cache,
        thread_names=thread_names,
        missing_scopes=missing_scopes,
    )

    # Build the new-message bodies (typed Gmail-web structure, no fingerprints).
    # Resolve the base paragraph direction: auto-detect from the body text
    # (first-strong-char per Unicode bidi) unless the caller forced ltr/rtl.
    if body_format == "html":
        resolved_dir = (
            base_text_direction(_html_to_text(body))
            if direction == "auto"
            else direction
        )
        new_html = (
            body
            if body.lstrip().startswith("<div dir=")
            else new_message_html(body, resolved_dir)
        )
        new_plain = _html_to_text(body).strip()
    else:
        new_plain = body
        resolved_dir = base_text_direction(body) if direction == "auto" else direction
        new_html = new_message_html(plain_body_to_html(body), resolved_dir)

    # Append the reply quote trail when this is a reply within a thread.
    if quote_reply:
        new_plain, new_html = await _build_web_reply_bodies(
            gmail_service,
            thread_id,
            in_reply_to,
            new_plain,
            new_html,
            reply_target=reply_target,
        )

    raw_message, _thread, attached_count, attachment_errors = _prepare_gmail_message(
        subject=subject,
        body=new_plain,
        html_body=new_html,
        to=to_fmt,
        cc=cc_fmt,
        bcc=bcc_fmt,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
        references=references,
        from_email=from_email,
        from_name=from_name,
        web_compose=True,
        attachments=attachments or None,
        include_bcc_header=include_bcc_header,
    )
    # A RECIPIENT is "unresolved" if we looked it up (no inline name) and got
    # nothing from contacts or the thread -- the signal for the contacts-scope
    # fallback note. The sender's own name is excluded: it comes from Send-As,
    # not contacts, so its absence must not trigger a contacts-scope note.
    sender_key = from_email.strip().lower()
    had_unresolved = any(
        value is None for key, value in name_cache.items() if key != sender_key
    )
    return (
        raw_message,
        had_unresolved,
        missing_scopes,
        attached_count,
        attachment_errors,
    )


async def _build_web_reply_bodies(
    service,
    thread_id: Optional[str],
    in_reply_to: Optional[str],
    new_plain: str,
    new_html: str,
    reply_target: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    """Append a Gmail ``gmail_quote`` reply trail to both body parts.

    Uses ``reply_target`` when supplied; otherwise fetches the parent message
    (its From display name/email, Date, and plain + html bodies). Appends the
    quote per the Gmail-web spec. If the parent cannot be fetched/lacks required
    fields, returns the bodies unchanged (send proceeds without a quote rather
    than failing).
    """
    if not thread_id:
        return new_plain, new_html

    target = reply_target
    if target is None:
        try:
            context = await _fetch_thread_reply_context(
                service, thread_id, in_reply_to=in_reply_to, include_bodies=True
            )
        except Exception as e:
            logger.info("Reply-quote parent fetch failed; sending without quote: %s", e)
            return new_plain, new_html
        target = context.get("target") if context else None
    if not target:
        return new_plain, new_html

    parent_from = target.get("from") or ""
    parent_name, parent_email = parseaddr(parent_from)
    if not parent_email:
        return new_plain, new_html
    parent_name = parent_name.strip() or parent_email

    _iso, parent_dt = _parse_date_header(target.get("date", ""), None)
    if parent_dt is None:
        return new_plain, new_html

    parent_text = target.get("text_body") or ""
    if not parent_text and target.get("html_body"):
        parent_text = _html_to_text(target["html_body"])
    parent_html = target.get("html_body") or ""
    if not parent_html and parent_text:
        parent_html = "<br>".join(html.escape(line) for line in parent_text.split("\n"))

    attr_plain = format_attribution_plain(parent_name, parent_email, parent_dt)
    attr_html = format_attribution_html(parent_name, parent_email, parent_dt)
    quoted_plain = build_quote_plain(parent_text)
    container = build_quote_container_html(attr_html, parent_html)

    reply_plain = f"{new_plain}\n\n{attr_plain}\n\n{quoted_plain}"
    reply_html = f"{new_html}<br>{container}"
    return reply_plain, reply_html


async def _fetch_thread_reply_context(
    service,
    thread_id: str,
    in_reply_to: Optional[str] = None,
    include_bodies: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch reply metadata for a thread, optionally including message bodies."""
    header_names = ["Message-ID", "Subject", "From", "Reply-To", "To", "Cc", "Date"]

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
    for msg in messages:
        # Skip trashed messages so auto-derived In-Reply-To never points at a
        # message that Gmail's UI cannot render
        if "TRASH" in msg.get("labelIds", []):
            continue
        payload = msg.get("payload", {})
        headers = _extract_headers(payload, header_names)
        context = {
            "message_id": headers.get("Message-ID"),
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

    target = None
    if in_reply_to:
        for msg in message_contexts:
            if msg.get("message_id") == in_reply_to:
                target = msg
                break
    if target is None and message_contexts:
        # message_contexts can be empty even though the thread itself has
        # messages, if every message in it is trashed (see the TRASH skip
        # above) -- message_contexts[-1] would then raise IndexError.
        # Leaving target as None is safe: callers already treat a missing
        # target as "no reply context available" and degrade gracefully
        # (e.g. draft_gmail_message falls back to an unthreaded draft).
        target = message_contexts[-1]

    return {
        "messages": message_contexts,
        "message_ids": [
            msg["message_id"] for msg in message_contexts if msg.get("message_id")
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


def _split_resolved_attachments(
    resolved: List[Dict[str, Any]],
) -> tuple[List[dict], List[dict], int, List[str]]:
    """Split a list of resolved attachments into inline and regular parts.

    Replicates the classification logic in ``_prepare_gmail_message``'s
    attachment loop so both the legacy EmailMessage path and the web-faithful
    MIME path share identical behaviour.

    Args:
        resolved: List of attachment dicts, each produced by
            ``_resolve_url_attachments`` (keys: ``_resolved_bytes``/``content``/
            ``data``, ``filename``, ``mime_type``, optional ``content_id``,
            optional ``error``).

    Returns:
        ``(inline_parts, attachment_parts, attached_count, attachment_errors)``

        ``inline_parts`` — dicts for ``assemble_web_message``'s inline_parts
            argument: ``{filename, mime_type, data: bytes, content_id: str}``.
        ``attachment_parts`` — dicts for the attachment_parts argument:
            ``{filename, mime_type, data: bytes}``.
        ``attached_count`` — total valid parts added across both lists.
        ``attachment_errors`` — user-facing error strings for skipped entries.
    """
    inline_parts: List[dict] = []
    attachment_parts: List[dict] = []
    attached_count = 0
    attachment_errors: List[str] = []
    seen_content_ids: set[str] = set()

    for attachment in resolved:
        if attachment.get("error"):
            attachment_errors.append(_format_resolved_attachment_error(attachment))
            continue

        filename = attachment.get("filename")
        mime_type = attachment.get("mime_type")
        content_id = attachment.get("content_id")

        # Accept bytes from four sources: pre-resolved URL bytes, a raw ``data``
        # bytes value (used by the forward path), base64-encoded content string,
        # or a local file path (read from disk here so the helper is self-contained
        # and works whether or not the caller pre-resolved file paths).
        resolved_bytes = attachment.get("_resolved_bytes")
        raw_data = attachment.get("data")
        content_base64 = attachment.get("content")
        file_path = attachment.get("path")

        try:
            if resolved_bytes is not None:
                file_data = resolved_bytes
                if not filename:
                    filename = "attachment"
                if not mime_type:
                    mime_type = "application/octet-stream"
            elif raw_data is not None:
                file_data = raw_data
                if not filename:
                    filename = "attachment"
                if not mime_type:
                    mime_type = "application/octet-stream"
            elif content_base64:
                if not filename:
                    logger.warning("Skipping attachment: missing filename")
                    attachment_errors.append(
                        "attachment: missing filename (content provided without a filename)"
                    )
                    continue
                file_data = base64.b64decode(content_base64)
                if not mime_type:
                    mime_type = "application/octet-stream"
            elif file_path:
                path_obj = validate_file_path(file_path)
                if not path_obj.exists():
                    logger.error("File not found: %s", file_path)
                    attachment_errors.append(f"{filename or file_path}: file not found")
                    continue
                with open(path_obj, "rb") as fh:
                    file_data = fh.read()
                if not filename:
                    filename = path_obj.name
                if not mime_type:
                    mime_type, _ = mimetypes.guess_type(str(path_obj))
                    if not mime_type:
                        mime_type = "application/octet-stream"
            else:
                logger.warning(
                    "Skipping attachment: no data, _resolved_bytes, content, or path"
                )
                attachment_errors.append(
                    f"{filename or 'attachment'}: no content, path, or data provided"
                )
                continue

            safe_filename = (
                (filename or "attachment")
                .replace("\r", "")
                .replace("\n", "")
                .replace("\x00", "")
            ) or "attachment"

            if not mime_type:
                mime_type = "application/octet-stream"

            if content_id:
                cid_value = _normalize_attachment_content_id(content_id)
                if cid_value in seen_content_ids:
                    logger.warning(
                        "Duplicate content_id %r on attachment %s; "
                        "email clients may only render one instance",
                        cid_value,
                        filename,
                    )
                seen_content_ids.add(cid_value)
                inline_parts.append(
                    {
                        "filename": safe_filename,
                        "mime_type": mime_type,
                        "data": file_data,
                        "content_id": cid_value,
                    }
                )
                logger.info(
                    "Classified inline (cid=%s): %s (%d bytes)",
                    cid_value,
                    safe_filename,
                    len(file_data),
                )
            else:
                attachment_parts.append(
                    {
                        "filename": safe_filename,
                        "mime_type": mime_type,
                        "data": file_data,
                    }
                )
                logger.info(
                    "Classified attachment: %s (%d bytes)",
                    safe_filename,
                    len(file_data),
                )
            attached_count += 1
        except (binascii.Error, ValueError) as e:
            logger.error("Failed to decode attachment %s: %s", filename or file_path, e)
            attachment_errors.append(_format_attachment_error(file_path, filename, e))
            continue
        except FileNotFoundError:
            logger.error("File not found: %s", file_path)
            attachment_errors.append(f"{filename or file_path}: file not found")
            continue
        except Exception as e:
            logger.error(
                "Failed to classify attachment %s: %s", filename or file_path, e
            )
            attachment_errors.append(_format_attachment_error(file_path, filename, e))
            continue

    return inline_parts, attachment_parts, attached_count, attachment_errors


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


def _prepare_gmail_message_web(
    subject: str,
    plain_body: str,
    html_body: str,
    to: Optional[str] = None,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None,
    date_header: Optional[str] = None,
    attachments: Optional[List[Dict]] = None,
    include_bcc_header: bool = True,
) -> tuple[str, int, List[str]]:
    """Assemble a Gmail-web faithful message.

    ``plain_body`` and ``html_body`` are the fully-assembled text/plain and
    text/html parts (including any reply quote trail) built by the async caller.
    Returns ``(raw_b64url, attached_count, attachment_errors)``.
    To/Cc/Bcc are expected pre-formatted; From is formatted here from
    ``from_email`` + optional ``from_name``.

    Selects the smallest sufficient MIME structure via ``assemble_web_message``:

    - No attachments → ``multipart/alternative``
    - Regular-only → ``multipart/mixed`` → [alternative, attachments...]
    - Inline-only → ``multipart/related`` → [alternative, inline...]
    - Both → ``multipart/mixed`` → [``multipart/related``, attachments...]

    ``attachments`` is a list of resolved attachment dicts (keys:
    ``_resolved_bytes``/``content``/``data``, ``filename``, ``mime_type``,
    optional ``content_id``, optional ``error``).
    """

    # Reject CR/LF in any user-controlled header value before assembly: bare
    # newlines would let a crafted subject/recipient inject extra headers
    # (RFC5322 header injection).
    def _safe_header(field: str, value: str) -> str:
        if "\r" in value or "\n" in value:
            raise ValueError(f"Invalid {field} header value: line breaks not allowed.")
        return value

    # Author headers in Gmail's order. Message-ID is intentionally NOT authored
    # (Gmail assigns it on send/draft).
    headers: List[tuple[str, str]] = [("MIME-Version", "1.0")]
    if date_header:
        headers.append(("Date", _safe_header("Date", date_header)))
    if references:
        # Fold the References chain with CRLF + TAB per RFC5322 continuation.
        folded = "\r\n\t".join(_safe_header("References", references).split())
        headers.append(("References", folded))
    if in_reply_to:
        headers.append(("In-Reply-To", _safe_header("In-Reply-To", in_reply_to)))
    if bcc and include_bcc_header:
        headers.append(("Bcc", _safe_header("Bcc", bcc)))
    # Guard the caller-supplied subject for header injection BEFORE encoding.
    # A long non-ASCII subject RFC2047-folds into a multi-line continuation; with
    # linesep="\r\n" that is a valid RFC5322 fold, but _safe_header would reject
    # its CRLF, so validate the raw input and append the encoded value directly.
    _safe_header("Subject", subject)
    subj_value = (
        subject
        if subject.isascii()
        else Header(subject, "utf-8").encode(maxlinelen=998, linesep="\r\n")
    )
    headers.append(("Subject", subj_value))
    if from_email:
        headers.append(
            (
                "From",
                _safe_header("From", format_display_address(from_name, from_email)),
            )
        )
    if to:
        headers.append(("To", _safe_header("To", to)))
    if cc:
        headers.append(("Cc", _safe_header("Cc", cc)))

    if not attachments:
        message = assemble_alternative(
            headers=headers,
            plain_text=plain_body,
            html_text=html_body,
            boundary=gmail_boundary(),
        )
        return encode_raw(message), 0, []

    inline_parts, attachment_parts, attached_count, attachment_errors = (
        _split_resolved_attachments(attachments)
    )

    boundary_alt = gmail_boundary()
    boundary_related = gmail_boundary() if inline_parts else None
    boundary_mixed = gmail_boundary() if attachment_parts or inline_parts else None

    message = assemble_web_message(
        headers=headers,
        plain_text=plain_body,
        html_text=html_body,
        inline_parts=inline_parts or None,
        attachment_parts=attachment_parts or None,
        boundary_alt=boundary_alt,
        boundary_related=boundary_related,
        boundary_mixed=boundary_mixed,
    )
    return encode_raw(message), attached_count, attachment_errors


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
    web_compose: bool = False,
    html_body: Optional[str] = None,
    date_header: Optional[str] = None,
    include_bcc_header: bool = True,
    direction: str = "auto",
) -> tuple[str, Optional[str], int, List[str]]:
    """
    Prepare a Gmail message with threading and attachment support.

    When ``web_compose`` is True the message is assembled to be byte-faithful
    to a Gmail-web compose via ``_prepare_gmail_message_web``.  ``body`` is the
    fully-assembled text/plain content and ``html_body`` is the fully-assembled
    text/html content (both built by the async caller, including any
    ``gmail_quote`` reply trail).  Attachments (inline or regular) are also
    handled on this path via ``assemble_web_message``.  ``to``/``cc``/``bcc``
    are expected pre-formatted (``Display Name <addr>``); ``from`` is formatted
    here from ``from_email`` + optional ``from_name``.

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
    # Handle reply subject formatting (tag-aware + idempotent, so an inherited
    # "[list] Re: ..." is not given a second Re:).
    reply_subject = normalize_reply_subject(subject) if in_reply_to else subject

    # Prepare the email
    normalized_format = body_format.lower()
    if normalized_format not in {"plain", "html"}:
        raise ValueError("body_format must be either 'plain' or 'html'.")

    # Gmail-web faithful path: build MIME by hand so charset casing, boundary
    # shape, and part ordering match a real web compose. Taken whenever
    # web_compose is set (regardless of whether attachments are present).
    if web_compose:
        # ``body`` is the assembled text/plain content. ``html_body`` is the
        # assembled text/html content; when the caller did not supply one, derive
        # it from ``body`` so the path still yields both parts.
        if html_body is not None:
            plain_part = body
            html_part = html_body
        elif normalized_format == "html":
            resolved_dir = (
                base_text_direction(_html_to_text(body))
                if direction == "auto"
                else direction
            )
            html_part = (
                body
                if body.lstrip().startswith("<div dir=")
                else new_message_html(body, resolved_dir)
            )
            plain_part = _html_to_text(body).strip()
        else:
            plain_part = body
            resolved_dir = (
                base_text_direction(body) if direction == "auto" else direction
            )
            html_part = new_message_html(plain_body_to_html(body), resolved_dir)

        raw_message, _web_count, _web_errors = _prepare_gmail_message_web(
            subject=reply_subject,
            plain_body=plain_part,
            html_body=html_part,
            to=to,
            cc=cc,
            bcc=bcc,
            in_reply_to=in_reply_to,
            references=references,
            from_email=from_email,
            from_name=from_name,
            date_header=date_header,
            attachments=attachments or None,
            include_bcc_header=include_bcc_header,
        )
        return raw_message, thread_id, _web_count, _web_errors

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
    if bcc and include_bcc_header:
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

    inline_parts, attachment_parts, attached_count, split_errors = (
        _split_resolved_attachments(list(attachments or []))
    )
    attachment_errors.extend(split_errors)

    # Build the EmailMessage tree from the classified parts.
    for ip in inline_parts:
        main_type, sub_type = (
            ip["mime_type"].split("/", 1)
            if "/" in ip["mime_type"]
            else ("application", "octet-stream")
        )
        cid_value = ip["content_id"]
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
            target = message
        target.add_related(
            ip["data"],
            maintype=main_type,
            subtype=sub_type,
            cid=f"<{cid_value}>",
            filename=ip["filename"],
            disposition="inline",
        )

    for ap in attachment_parts:
        main_type, sub_type = (
            ap["mime_type"].split("/", 1)
            if "/" in ap["mime_type"]
            else ("application", "octet-stream")
        )
        message.add_attachment(
            ap["data"],
            maintype=main_type,
            subtype=sub_type,
            filename=ap["filename"],
        )

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
        f"[search_gmail_messages] Email: '{user_google_email}', Query: '{query}', Page size: {page_size}"
    )

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
        return await _export_full_message(service, message_id, headers, body_format)

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
        for i, att in enumerate(attachments, 1):
            size_kb = att["size"] / 1024
            content_lines.append(
                f"{i}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                f"   Attachment ID: {att['attachmentId']}\n"
                f"   Use get_gmail_attachment_content(message_id='{message_id}', attachment_id='{att['attachmentId']}') to download"
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
                        for i, att in enumerate(attachments, 1):
                            size_kb = att["size"] / 1024
                            msg_output += (
                                f"{i}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                                f"   Attachment ID: {att['attachmentId']}\n"
                                f"   Use get_gmail_attachment_content(message_id='{mid}', attachment_id='{att['attachmentId']}') to download\n"
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

    Returns:
        str: Attachment metadata with either a local file path or download URL,
            optionally followed by a base64 content block when
            ``return_base64=True``.
    """
    logger.info(
        f"[get_gmail_attachment_content] Invoked. Message ID: '{message_id}', Email: '{user_google_email}'"
    )

    # Download attachment content first, then optionally re-fetch message metadata
    # to resolve filename and MIME type for the saved file.
    try:
        attachment = await asyncio.to_thread(
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
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
    size_bytes = attachment.get("size", 0)
    size_kb = size_bytes / 1024 if size_bytes else 0
    base64_data = attachment.get("data", "")

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

        # Try to get filename and mime type from message
        filename = None
        mime_type = None
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

            # First try exact attachmentId match
            for att in attachments:
                if att.get("attachmentId") == attachment_id:
                    filename = att.get("filename")
                    mime_type = att.get("mimeType")
                    break

            # Fallback: match by size if exactly one attachment matches (IDs are ephemeral)
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

            # Last resort: if only one attachment, use its name
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
@require_multiple_services(
    [
        {
            "service_type": "gmail",
            "scopes": ["gmail_read", GMAIL_SEND_SCOPE],
            "param_name": "service",
        },
        {
            "service_type": "people",
            "scopes": "contacts_read",
            "param_name": "people_service",
            # Optional: a missing contacts scope degrades to bare-address sends
            # (pre-feature behavior) with a note in the result, never a hard failure.
            "optional": True,
        },
    ]
)
async def send_gmail_message(
    service,
    user_google_email: str,
    *,
    people_service=None,
    to: Annotated[
        Optional[str],
        Field(
            description="Recipient email address. Optional when replying with reply_all=True, which derives it from the thread.",
        ),
    ] = None,
    subject: Annotated[
        Optional[str],
        Field(
            description="Email subject. Required for a new message; optional when replying (thread_id/in_reply_to), where it is inherited from the parent message (with a single tag-aware 'Re:'), or when forwarding (defaults to 'Fwd: <original subject>').",
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
            description="Format of the body content (and of the prepended note when forwarding). Use 'plain' for plaintext or 'html' for HTML content. With 'html', write real markup ('<div dir=\"rtl\">'); an entity-escaped body ('&lt;div&gt;') is rejected, since the recipient would see the tags as literal text.",
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
            description="Optional Gmail thread ID to reply within.",
        ),
    ] = None,
    in_reply_to: Annotated[
        Optional[str],
        Field(
            description="Optional RFC Message-ID of the message being replied to (e.g., '<message123@gmail.com>').",
        ),
    ] = None,
    references: Annotated[
        Optional[str],
        Field(
            description="Optional chain of Message-IDs for proper threading.",
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
    direction: Annotated[
        Literal["auto", "ltr", "rtl"],
        Field(
            description="Base text direction for the composed body. 'auto' (default) detects it from the body via the Unicode bidi first-strong-character rule (a right-to-left script \u2192 right-to-left, otherwise left-to-right); 'ltr'/'rtl' force it. Embedded opposite-direction runs (Latin words, numerals) always render correctly via the browser's bidi algorithm. Note: this orients the HTML body via a dir attribute, matching Gmail web's bare-fragment output. Some clients (e.g. Spark iOS) ignore dir on a wrapperless fragment and render right-to-left text left-aligned; this is a client-side limitation that likewise affects real Gmail-web-composed RTL mail.",
        ),
    ] = "auto",
    quote_original: Annotated[
        bool,
        Field(
            description="Whether to include the message being replied to as a quoted original. Only has an effect when thread_id is provided. Defaults to true, matching Gmail web, which always carries the reply trail on a threaded send; pass false to omit it.",
        ),
    ] = True,
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

    Args:
        to (str): Recipient email address.
        subject (str): Email subject. Required for a new message. Optional when replying
            (thread_id/in_reply_to) - inherited from the parent message, adding a single
            'Re:' only if absent and preserving tags like '[list]'/'[#123]'. Optional when
            forwarding (then defaults to 'Fwd: <original subject>').
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
        thread_id (Optional[str]): Optional Gmail thread ID to reply within. When provided, sends a reply.
        in_reply_to (Optional[str]): Optional RFC Message-ID of the message being replied to (e.g., '<message123@gmail.com>').
        references (Optional[str]): Optional chain of RFC Message-IDs for proper threading (e.g., '<msg1@gmail.com> <msg2@gmail.com>').
        include_signature (bool): Whether to append Gmail signature HTML from send-as settings.
            When include_signature is true and Gmail signature retrieval fails for benign reasons
            (e.g., missing gmail.settings.basic scope), the send proceeds without a signature.
            Non-benign failures such as quota/rate-limit or API errors raise ToolError and abort
            the send.
        quote_original (bool): Whether to append the message being replied to as a quoted
            original. Only has an effect when thread_id is provided. Defaults to true,
            matching Gmail web, which always carries the reply trail on a threaded send.
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
            thread_id="thread_123",
            in_reply_to="<message123@gmail.com>",
            references="<original@gmail.com> <message123@gmail.com>"
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
    # Checked before the forward branch so the prepended-note path is covered too.
    _reject_entity_escaped_html_body(body, body_format)

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
            f"[send_gmail_message] Forwarding message '{forward_message_id}' to '{to}' for '{user_google_email}'"
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
            direction=direction,
        )

    # 'subject' may be omitted when replying (thread_id/in_reply_to): it is then
    # inherited from the parent message. It is still required for a brand-new
    # message. 'body' is always required (optional only when forwarding, handled
    # above).
    is_reply = bool(thread_id or in_reply_to)
    if body is None or (subject is None and not is_reply):
        raise UserInputError(
            "'body' is required, and 'subject' is required unless replying "
            "(thread_id/in_reply_to, where it is inherited from the parent) or "
            "forwarding via 'forward_message_id'."
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
        f"[send_gmail_message] Invoked. Email: '{user_google_email}', Subject: '{subject}', Attachments: {len(attachments) if attachments else 0}"
    )

    # Prepare the email message
    # Use from_email (Send As alias) if provided, otherwise default to authenticated user
    sender_email = from_email or user_google_email

    # When signing, fetch send-as settings once: the entry carries both the
    # signature and the displayName Gmail web renders in the From line (the
    # sender's own name is not in contacts, so Send-As -- not People -- is the
    # right source). When signatures are disabled we must not touch the settings
    # endpoint at all (avoids requiring gmail.settings.basic); the From name then
    # falls back to thread/People resolution.
    signature_html = ""
    if include_signature:
        send_as_entry = _match_send_as_entry(
            await _get_send_as_entries(service), sender_email
        )
        if from_name is None and send_as_entry:
            from_name = (send_as_entry.get("displayName") or "").strip() or None
        if send_as_entry:
            signature_html = send_as_entry.get("signature", "") or ""
    send_body_content = _append_signature_to_body(body, body_format, signature_html)

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

    if thread_id and (not in_reply_to or not references):
        in_reply_to, references = _derive_reply_headers(
            reply_context.get("message_ids", []) if reply_context else [],
            in_reply_to,
            references,
        )

    target_reply = reply_context.get("target") if reply_context else None
    # Harvest sender-supplied names from the conversation so reply recipients
    # resolve like Gmail's, even with no People scope. Mirrors draft_gmail_message.
    send_thread_names = (
        _harvest_thread_display_names(reply_context.get("messages", []))
        if reply_context
        else None
    )
    if reply_all and target_reply:
        to, cc = _derive_reply_all_recipients(
            target_reply, {user_google_email, sender_email}, to, cc
        )
    if not to:
        raise UserInputError(
            f"Could not derive a recipient from thread '{thread_id}'. Pass 'to' explicitly."
        )

    resolved_attachments = await _resolve_url_attachments(attachments)

    # Resolve send transport before building the raw message so we know whether
    # to include the Bcc header (API keeps it; SMTP omits it — envelope-only).
    # Off-thread: with the GCS credential-store backend this can do a blocking
    # download, which must not stall the event loop.
    effective, transport_creds, fallback_note = await asyncio.to_thread(
        resolve_effective_transport, user_google_email
    )

    # Always use the Gmail-web faithful path for both the no-attachment and
    # with-attachment cases so every sent message carries the gmail_quote reply
    # trail and web-faithful MIME structure.
    # Subject prefixing/inheritance is centralized in _build_web_compose_raw
    # (it has the parent message in hand): a reply with subject omitted inherits
    # the parent's, and a single tag-aware "Re: " is applied there.
    (
        raw_message,
        had_unresolved,
        missing_scopes,
        attached_count,
        attachment_errors,
    ) = await _build_web_compose_raw(
        service,
        people_service,
        subject=subject,
        body=send_body_content,
        body_format=body_format,
        to=to,
        cc=cc,
        bcc=bcc,
        from_email=sender_email,
        from_name=from_name,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
        references=references,
        quote_reply=quote_original,
        reply_target=target_reply,
        thread_names=send_thread_names,
        attachments=resolved_attachments or None,
        include_bcc_header=(effective == "api"),
        direction=direction,
    )
    thread_id_final = thread_id
    # Note only when a recipient actually went unresolved AND more scope would
    # help (no People service at all, or a tier returned a scope error).
    name_note = (
        _build_name_fallback_note(people_service is None, missing_scopes)
        if had_unresolved
        else ""
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

    attachment_info = (
        _format_attachment_result(attached_count, requested_attachment_count)
        if requested_attachment_count > 0
        else ""
    )

    return await dispatch_transmit(
        service,
        effective=effective,
        creds=transport_creds,
        fallback_note=fallback_note,
        raw_message_b64=raw_message,
        thread_id_final=thread_id_final,
        sender=sender_email,
        to=[to] if to else None,
        cc=[cc] if cc else None,
        bcc=[bcc] if bcc else None,
        # Display/label only; the authoritative Subject header is already baked
        # into raw_message (inherited/prefixed inside _build_web_compose_raw).
        subject=subject or "",
        user_google_email=user_google_email,
        action_label="Email sent",
        attachment_info=attachment_info,
        trailing_note=name_note,
    )


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
    direction: str = "auto",
) -> str:
    """Build and send a forward of an existing Gmail message.

    Shared by send_gmail_message's forward path. An explicit ``subject`` overrides
    the auto-derived 'Fwd: <original subject>'. ``direction`` sets the base text
    direction of the prepended note ('auto' detects it from the note text).
    """
    # Fetch the original message with full payload
    original_message = await asyncio.to_thread(
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute
    )

    payload = original_message.get("payload", {})

    # --- Parse original message metadata ---
    orig_headers = _extract_headers(payload, ["Subject", "From", "Date", "To"])
    orig_subject = orig_headers.get("Subject", "(no subject)")
    orig_from_raw = orig_headers.get("From", "")
    orig_date_str = orig_headers.get("Date", "")
    orig_to_raw = orig_headers.get("To", "")
    orig_bodies = _extract_message_bodies(payload)
    orig_plain = orig_bodies.get("text", "")
    orig_html = orig_bodies.get("html", "")

    # Parse the original From into display name + email.
    orig_from_name, orig_from_email = parseaddr(orig_from_raw)
    orig_from_name = orig_from_name.strip() or None

    # Derive the forward subject, avoiding a double prefix for "Fwd:"/"FW:".
    if subject:
        forward_subject = subject
    elif orig_subject.lower().lstrip().startswith(("fwd:", "fw:")):
        forward_subject = orig_subject
    else:
        forward_subject = f"Fwd: {orig_subject}"

    # Handle attachments
    attachments_to_send = []
    if include_attachments:
        attachment_metadata = _extract_attachments(payload)
        failed_attachments = []
        for att in attachment_metadata:
            try:
                # Download attachment content
                attachment_data = await asyncio.to_thread(
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=att["attachmentId"])
                    .execute
                )
                # Gmail returns URL-safe base64 (often unpadded). Decode to bytes.
                urlsafe_data = attachment_data.get("data", "")
                padded = urlsafe_data + "=" * (-len(urlsafe_data) % 4)
                att_bytes = base64.urlsafe_b64decode(padded)
                attachments_to_send.append(
                    {
                        "data": att_bytes,
                        "filename": att["filename"],
                        "mime_type": att["mimeType"],
                    }
                )
                logger.info(
                    f"[forward_gmail_message] Downloaded attachment: {att['filename']}"
                )
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

    # --- Build forwarded bodies via Gmail-web faithful builders ---

    # When the original has no HTML body, synthesize one from plain text
    # (mirrors how _build_web_reply_bodies derives html from plain).
    if not orig_html and orig_plain:
        orig_html = "<br>".join(html.escape(line) for line in orig_plain.split("\n"))

    # Plain-text note from the user (if any).
    if forward_message and forward_message_format == "html":
        # Strip tags for the plain note portion.
        _extractor = _HTMLTextExtractor()
        _extractor.feed(forward_message)
        note_plain = _extractor.get_text()
        note_html = forward_message
    else:
        note_plain = forward_message or ""
        note_html = plain_body_to_html(forward_message) if forward_message else ""

    # Plain body: optional note + forwarded block (NOT > -quoted).
    fwd_plain_block = build_forwarded_plain(
        from_name=orig_from_name,
        from_email=orig_from_email or orig_from_raw,
        date_str=orig_date_str,
        subject=orig_subject,
        to_rendered_plain=orig_to_raw,
        orig_plain=orig_plain,
    )
    if note_plain:
        forward_plain = f"{note_plain}\n\n{fwd_plain_block}"
    else:
        forward_plain = fwd_plain_block

    # HTML body: note div + forwarded container (no blockquote).
    fwd_html_container = build_forwarded_container_html(
        from_name=orig_from_name,
        from_email=orig_from_email or orig_from_raw,
        date_str=orig_date_str,
        subject=orig_subject,
        to_rendered=render_forward_recipients_html(orig_to_raw),
        orig_html=orig_html,
    )
    if note_html:
        # Base direction follows the user's note; the forwarded original keeps
        # its own dir markup inside the container.
        note_dir = base_text_direction(note_plain) if direction == "auto" else direction
        forward_html = new_message_html(
            f"{note_html}<br><br>{fwd_html_container}", note_dir
        )
    else:
        # No note: nothing user-authored to orient, stay ltr (byte-identical).
        forward_html = new_message_html(f"<br>{fwd_html_container}")

    # --- Prepare and send the message ---
    sender_email = from_email or user_google_email

    # Resolve send transport before building the raw message so we know whether
    # to include the Bcc header (API keeps it; SMTP omits it — envelope-only).
    # Off-thread: with the GCS credential-store backend this can do a blocking
    # download, which must not stall the event loop.
    effective, transport_creds, fallback_note = await asyncio.to_thread(
        resolve_effective_transport, user_google_email
    )

    raw_message, _fwd_count, _fwd_errors = _prepare_gmail_message_web(
        subject=forward_subject,
        plain_body=forward_plain,
        html_body=forward_html,
        to=to,
        cc=cc,
        bcc=bcc,
        from_email=sender_email,
        from_name=from_name,
        attachments=attachments_to_send if attachments_to_send else None,
        include_bcc_header=(effective == "api"),
    )

    attachment_info = (
        _format_attachment_result(len(attachments_to_send), len(attachments_to_send))
        if attachments_to_send
        else ""
    )

    return await dispatch_transmit(
        service,
        effective=effective,
        creds=transport_creds,
        fallback_note=fallback_note,
        raw_message_b64=raw_message,
        thread_id_final=None,
        sender=sender_email,
        to=[to] if to else None,
        cc=[cc] if cc else None,
        bcc=[bcc] if bcc else None,
        subject=forward_subject,
        user_google_email=user_google_email,
        action_label="Email forwarded",
        attachment_info=attachment_info,
        trailing_note="",
    )


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
@require_multiple_services(
    [
        {
            "service_type": "gmail",
            "scopes": GMAIL_COMPOSE_SCOPE,
            "param_name": "service",
        },
        {
            "service_type": "people",
            "scopes": "contacts_read",
            "param_name": "people_service",
            # Optional: a missing contacts scope degrades to bare-address sends
            # (pre-feature behavior) with a note in the result, never a hard failure.
            "optional": True,
        },
    ]
)
async def draft_gmail_message(
    service,
    user_google_email: str,
    *,
    people_service=None,
    subject: Annotated[
        Optional[str],
        Field(
            description="Email subject. Optional when replying (thread_id/in_reply_to): if omitted, the parent message's subject is inherited, with a single 'Re:' added only when absent and existing tags like '[list]'/'[#123]' preserved.",
        ),
    ] = None,
    body: Annotated[str, Field(description="Email body (plain text).")],
    body_format: Annotated[
        Literal["plain", "html"],
        Field(
            description="Email body format. Use 'plain' for plaintext or 'html' for HTML content. With 'html', write real markup ('<div dir=\"rtl\">'); an entity-escaped body ('&lt;div&gt;') is rejected, since the recipient would see the tags as literal text.",
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
            description="Optional 'Send As' alias email address. Must be configured in Gmail settings (Settings > Accounts > Send mail as). If not provided, uses the authenticated user's email.",
        ),
    ] = None,
    thread_id: Annotated[
        Optional[str],
        Field(
            description="Optional Gmail thread ID to reply within.",
        ),
    ] = None,
    in_reply_to: Annotated[
        Optional[str],
        Field(
            description="Optional RFC Message-ID of the message being replied to (e.g., '<message123@gmail.com>').",
        ),
    ] = None,
    references: Annotated[
        Optional[str],
        Field(
            description="Optional chain of Message-IDs for proper threading.",
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
    direction: Annotated[
        Literal["auto", "ltr", "rtl"],
        Field(
            description="Base text direction for the composed body. 'auto' (default) detects it from the body via the Unicode bidi first-strong-character rule (a right-to-left script → right-to-left, otherwise left-to-right); 'ltr'/'rtl' force it. Embedded opposite-direction runs (Latin words, numerals) always render correctly via the browser's bidi algorithm. Note: this orients the HTML body via a dir attribute, matching Gmail web's bare-fragment output. Some clients (e.g. Spark iOS) ignore dir on a wrapperless fragment and render right-to-left text left-aligned; this is a client-side limitation that likewise affects real Gmail-web-composed RTL mail.",
        ),
    ] = "auto",
) -> str:
    """
    Creates a draft email in the user's Gmail account. Supports both new drafts and reply drafts with optional attachments.
    Supports Gmail's "Send As" feature to draft from configured alias addresses.

    Args:
        user_google_email (str): The user's Google email address. Required for authentication.
        subject (str): Email subject. Optional when replying (thread_id/in_reply_to):
            if omitted, inherited from the parent message, adding a single 'Re:' only if
            absent and preserving tags like '[list]'/'[#123]'.
        body (str): Email body (plain text).
        body_format (Literal['plain', 'html']): Email body format. Defaults to 'plain'.
        to (Optional[str]): Optional recipient email address. Can be left empty for drafts.
        cc (Optional[str]): Optional CC email address.
        bcc (Optional[str]): Optional BCC email address.
        from_name (Optional[str]): Optional sender display name. If provided, the From header will be formatted as 'Name <email>'.
        from_email (Optional[str]): Optional 'Send As' alias email address. The alias must be
            configured in Gmail settings (Settings > Accounts > Send mail as). If not provided,
            the draft will be from the authenticated user's primary email address.
        thread_id (Optional[str]): Optional Gmail thread ID to reply within. When provided, creates a reply draft.
        in_reply_to (Optional[str]): Optional RFC Message-ID of the message being replied to (e.g., '<message123@gmail.com>').
        references (Optional[str]): Optional chain of RFC Message-IDs for proper threading (e.g., '<msg1@gmail.com> <msg2@gmail.com>').
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
            (e.g., missing gmail.settings.basic scope), the draft proceeds without a signature.
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
            thread_id="thread_123",
            in_reply_to="<message123@gmail.com>",
            references="<original@gmail.com> <message123@gmail.com>"
        )

        # Create a reply draft in HTML
        draft_gmail_message(
            subject="Re: Meeting tomorrow",
            body="<strong>Thanks for the update!</strong>",
            body_format="html",
            to="user@example.com",
            thread_id="thread_123",
            in_reply_to="<message123@gmail.com>",
            references="<original@gmail.com> <message123@gmail.com>"
        )
    """
    _reject_entity_escaped_html_body(body, body_format)

    logger.info(
        f"[draft_gmail_message] Invoked. Email: '{user_google_email}', Subject: '{subject}'"
    )

    # Prepare the email message
    # Use from_email (Send As alias) if provided, otherwise default to authenticated user
    sender_email = from_email or user_google_email
    # Only touch the settings endpoint when signing (avoids requiring
    # gmail.settings.basic when disabled). The send-as entry carries both the
    # signature and the Gmail-web From displayName.
    draft_body = body
    signature_html = ""
    if include_signature:
        send_as_entry = _match_send_as_entry(
            await _get_send_as_entries(service), sender_email
        )
        if from_name is None and send_as_entry:
            from_name = (send_as_entry.get("displayName") or "").strip() or None
        if send_as_entry:
            signature_html = send_as_entry.get("signature", "") or ""

    reply_context = None
    if thread_id and (
        quote_original
        or not in_reply_to
        or not references
        or not to
        or not (subject and subject.strip())
    ):
        reply_context = await _fetch_thread_reply_context(
            service,
            thread_id,
            in_reply_to=in_reply_to,
            include_bodies=quote_original,
        )

    if thread_id and (not in_reply_to or not references):
        thread_message_ids = (
            reply_context.get("message_ids", []) if reply_context else []
        )
        in_reply_to, references = _derive_reply_headers(
            thread_message_ids, in_reply_to, references
        )

    target_reply = reply_context.get("target") if reply_context else None
    # Harvest sender-supplied names from the conversation so reply recipients
    # resolve like Gmail's, even with no People scope (Scenario 1).
    draft_thread_names = (
        _harvest_thread_display_names(reply_context.get("messages", []))
        if reply_context
        else None
    )
    if thread_id and not to and target_reply:
        to = target_reply.get("reply_to") or target_reply.get("from") or to
    # Subject inheritance (when omitted) and tag-aware, single "Re:" prefixing
    # are centralized in _build_web_compose_raw, which has the parent in hand.

    resolved_attachments = await _resolve_url_attachments(attachments)

    # Always use the Gmail-web faithful path (with or without attachments) so
    # every draft carries the gmail_quote reply trail and web-faithful MIME.
    draft_body = _append_signature_to_body(draft_body, body_format, signature_html)
    (
        raw_message,
        had_unresolved,
        missing_scopes,
        attached_count,
        attachment_errors,
    ) = await _build_web_compose_raw(
        service,
        people_service,
        subject=subject,
        body=draft_body,
        body_format=body_format,
        to=to,
        cc=cc,
        bcc=bcc,
        from_email=sender_email,
        from_name=from_name,
        thread_id=thread_id,
        in_reply_to=in_reply_to,
        references=references,
        quote_reply=quote_original,
        reply_target=target_reply,
        auto_thread=False,
        thread_names=draft_thread_names,
        attachments=resolved_attachments or None,
        direction=direction,
    )
    name_note = (
        _build_name_fallback_note(people_service is None, missing_scopes)
        if had_unresolved
        else ""
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
    return f"Draft created{attachment_info}! Draft ID: {draft_id}{name_note}"


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
            for j, att in enumerate(attachments, 1):
                size_kb = att["size"] / 1024
                content_lines.append(
                    f"{j}. {att['filename']} ({att['mimeType']}, {size_kb:.1f} KB)\n"
                    f"   Attachment ID: {att['attachmentId']}\n"
                    f"   Use get_gmail_attachment_content(message_id='{message_id}', attachment_id='{att['attachmentId']}') to download"
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
    add_label_ids: Annotated[
        Optional[StringList],
        Field(json_schema_extra={"type": "array", "items": {"type": "string"}}),
    ] = None,
    remove_label_ids: Annotated[
        Optional[StringList],
        Field(json_schema_extra={"type": "array", "items": {"type": "string"}}),
    ] = None,
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
    add_label_ids: Annotated[
        Optional[StringList],
        Field(json_schema_extra={"type": "array", "items": {"type": "string"}}),
    ] = None,
    remove_label_ids: Annotated[
        Optional[StringList],
        Field(json_schema_extra={"type": "array", "items": {"type": "string"}}),
    ] = None,
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
