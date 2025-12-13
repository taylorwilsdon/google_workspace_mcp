"""
Google Chat MCP Tools

This module provides MCP tools for interacting with Google Chat API.
"""

import logging
import asyncio
from typing import Optional

from googleapiclient.errors import HttpError

# Auth & server utilities
from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)


@server.tool()
@require_google_service("chat", "chat_read")
@handle_http_errors("list_spaces", service_type="chat")
async def list_spaces(
    service,
    user_google_email: str,
    page_size: int = 100,
    space_type: str = "all",  # "all", "room", "dm"
) -> str:
    """
    Lists Google Chat spaces (rooms and direct messages) accessible to the user.

    Returns:
        str: A formatted list of Google Chat spaces accessible to the user.
    """
    logger.info(f"[list_spaces] Email={user_google_email}, Type={space_type}")

    # Build filter based on space_type
    filter_param = None
    if space_type == "room":
        filter_param = "spaceType = SPACE"
    elif space_type == "dm":
        filter_param = "spaceType = DIRECT_MESSAGE"

    request_params = {"pageSize": page_size}
    if filter_param:
        request_params["filter"] = filter_param

    response = await asyncio.to_thread(service.spaces().list(**request_params).execute)

    spaces = response.get("spaces", [])
    if not spaces:
        return f"No Chat spaces found for type '{space_type}'."

    output = [f"Found {len(spaces)} Chat spaces (type: {space_type}):"]
    for space in spaces:
        space_name = space.get("displayName", "Unnamed Space")
        space_id = space.get("name", "")
        space_type_actual = space.get("spaceType", "UNKNOWN")
        output.append(f"- {space_name} (ID: {space_id}, Type: {space_type_actual})")

    return "\n".join(output)


@server.tool()
@require_google_service("chat", "chat_read")
@handle_http_errors("get_messages", service_type="chat")
async def get_messages(
    service,
    user_google_email: str,
    space_id: str,
    page_size: int = 50,
    order_by: str = "createTime desc",
) -> str:
    """
    Retrieves messages from a Google Chat space.

    Returns:
        str: Formatted messages from the specified space.
    """
    logger.info(f"[get_messages] Space ID: '{space_id}' for user '{user_google_email}'")

    # Get space info first
    space_info = await asyncio.to_thread(service.spaces().get(name=space_id).execute)
    space_name = space_info.get("displayName", "Unknown Space")

    # Get messages
    response = await asyncio.to_thread(
        service.spaces()
        .messages()
        .list(parent=space_id, pageSize=page_size, orderBy=order_by)
        .execute
    )

    messages = response.get("messages", [])
    if not messages:
        return f"No messages found in space '{space_name}' (ID: {space_id})."

    output = [f"Messages from '{space_name}' (ID: {space_id}):\n"]
    for msg in messages:
        sender = msg.get("sender", {}).get("displayName", "Unknown Sender")
        create_time = msg.get("createTime", "Unknown Time")
        text_content = msg.get("text", "No text content")
        msg_name = msg.get("name", "")

        output.append(f"[{create_time}] {sender}:")
        output.append(f"  {text_content}")
        output.append(f"  (Message ID: {msg_name})\n")

    return "\n".join(output)


@server.tool()
@require_google_service("chat", "chat_write")
@handle_http_errors("send_message", service_type="chat")
async def send_message(
    service,
    user_google_email: str,
    space_id: str,
    message_text: str,
    thread_key: Optional[str] = None,
) -> str:
    """
    Sends a message to a Google Chat space.

    Returns:
        str: Confirmation message with sent message details.
    """
    logger.info(f"[send_message] Email: '{user_google_email}', Space: '{space_id}'")

    message_body = {"text": message_text}

    # Add thread key if provided (for threaded replies)
    request_params = {"parent": space_id, "body": message_body}
    if thread_key:
        request_params["threadKey"] = thread_key

    message = await asyncio.to_thread(
        service.spaces().messages().create(**request_params).execute
    )

    message_name = message.get("name", "")
    create_time = message.get("createTime", "")

    msg = f"Message sent to space '{space_id}' by {user_google_email}. Message ID: {message_name}, Time: {create_time}"
    logger.info(
        f"Successfully sent message to space '{space_id}' by {user_google_email}"
    )
    return msg


@server.tool()
@require_google_service("chat", "chat_read")
@handle_http_errors("search_messages", service_type="chat")
async def search_messages(
    service,
    user_google_email: str,
    query: str,
    space_id: Optional[str] = None,
    page_size: int = 25,
) -> str:
    """
    Searches for messages in Google Chat spaces by text content.

    Returns:
        str: A formatted list of messages matching the search query.
    """
    logger.info(f"[search_messages] Email={user_google_email}, Query='{query}'")

    # If specific space provided, search within that space
    if space_id:
        response = await asyncio.to_thread(
            service.spaces()
            .messages()
            .list(parent=space_id, pageSize=page_size, filter=f'text:"{query}"')
            .execute
        )
        messages = response.get("messages", [])
        context = f"space '{space_id}'"
    else:
        # Search across all accessible spaces (this may require iterating through spaces)
        # For simplicity, we'll search the user's spaces first
        spaces_response = await asyncio.to_thread(
            service.spaces().list(pageSize=100).execute
        )
        spaces = spaces_response.get("spaces", [])

        messages = []
        for space in spaces[:10]:  # Limit to first 10 spaces to avoid timeout
            try:
                space_messages = await asyncio.to_thread(
                    service.spaces()
                    .messages()
                    .list(
                        parent=space.get("name"), pageSize=5, filter=f'text:"{query}"'
                    )
                    .execute
                )
                space_msgs = space_messages.get("messages", [])
                for msg in space_msgs:
                    msg["_space_name"] = space.get("displayName", "Unknown")
                messages.extend(space_msgs)
            except HttpError:
                continue  # Skip spaces we can't access
        context = "all accessible spaces"

    if not messages:
        return f"No messages found matching '{query}' in {context}."

    output = [f"Found {len(messages)} messages matching '{query}' in {context}:"]
    for msg in messages:
        sender = msg.get("sender", {}).get("displayName", "Unknown Sender")
        create_time = msg.get("createTime", "Unknown Time")
        text_content = msg.get("text", "No text content")
        space_name = msg.get("_space_name", "Unknown Space")

        # Truncate long messages
        if len(text_content) > 100:
            text_content = text_content[:100] + "..."

        output.append(f"- [{create_time}] {sender} in '{space_name}': {text_content}")

    return "\n".join(output)
