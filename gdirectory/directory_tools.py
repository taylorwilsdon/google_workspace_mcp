"""
Google Directory MCP Tools (People API)

The "directory" is everyone in the user's Google Workspace organization. This
module exposes read-only lookups over the domain directory so a colleague's
name can be resolved to an email address — useful for recipient lookup.

Directory data only exists for Google Workspace accounts with a populated
domain directory; personal Google accounts return empty results. Read-only.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from mcp import Resource
from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

# Minimum fields for recipient resolution (name -> email). Widening this does
# not change the OAuth scope; keep it minimal.
DIRECTORY_READ_MASK = "names,emailAddresses"

# Domain profiles (the org's Workspace users). DIRECTORY_SOURCE_TYPE_DOMAIN_CONTACT
# (shared external contacts) is intentionally excluded — recipient lookup wants
# org people, and each extra source widens the surface without adding scope.
DIRECTORY_SOURCES = ["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"]


def _format_directory_person(person: Dict[str, Any]) -> str:
    """
    Format a directory Person as name + ALL emails + resourceName.

    Returns every email address (not just the first) so the consumer can
    disambiguate a person that has multiple addresses — flattening to one
    silently drops valid recipients.

    Args:
        person: A People API Person resource.

    Returns:
        str: A formatted, human-readable block for one directory person.
    """
    names = person.get("names") or [{}]
    name = names[0].get("displayName", "Unknown") or "Unknown"
    emails: List[str] = [
        email.get("value", "")
        for email in person.get("emailAddresses", [])
        if email.get("value")
    ]
    resource_name = person.get("resourceName", "")

    emails_str = ", ".join(emails) if emails else "(none)"
    return f"- {name}\n  Emails: {emails_str}\n  Resource: {resource_name}"


@server.tool(
    title="Search Directory People",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("people", "directory_read")
@handle_http_errors("search_directory_people", service_type="people")
async def search_directory_people(
    service: Resource,
    user_google_email: str,
    query: str,
    page_size: int = 30,
    page_token: Optional[str] = None,
) -> str:
    """
    Search the Google Workspace domain directory by name or email.

    Only returns results for Workspace accounts with a populated domain
    directory; personal Google accounts return empty results.

    Unlike `otherContacts.search`, `people.searchDirectoryPeople` DOES support
    pagination via a `nextPageToken`.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): Search query (matches names and email addresses).
        page_size (int): Maximum number of results to return (default: 30, max: 500).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Matching directory people with name, all emails, and resource name.
    """
    logger.info(
        f"[search_directory_people] Invoked. Email: '{user_google_email}', query_len={len(query)}"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 500)

    params: Dict[str, Any] = {
        "query": query,
        "readMask": DIRECTORY_READ_MASK,
        "sources": DIRECTORY_SOURCES,
        "pageSize": page_size,
    }
    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(
        service.people().searchDirectoryPeople(**params).execute
    )

    people = result.get("people", [])
    next_page_token = result.get("nextPageToken")

    if not people:
        return f"No directory people found matching '{query}' for {user_google_email}."

    response = f"Directory People matching '{query}' ({len(people)} found):\n\n"

    for person in people:
        response += _format_directory_person(person) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(people)} directory people for {user_google_email}")
    return response


@server.tool(
    title="List Directory People",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("people", "directory_read")
@handle_http_errors("list_directory_people", service_type="people")
async def list_directory_people(
    service: Resource,
    user_google_email: str,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> str:
    """
    List people in the Google Workspace domain directory.

    Only returns results for Workspace accounts with a populated domain
    directory; personal Google accounts return empty results. Supports
    pagination via a `nextPageToken`.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Maximum number of results to return (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Directory people with name, all emails, and resource name.
    """
    logger.info(f"[list_directory_people] Invoked. Email: '{user_google_email}'")

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "readMask": DIRECTORY_READ_MASK,
        "sources": DIRECTORY_SOURCES,
        "pageSize": page_size,
    }
    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(
        service.people().listDirectoryPeople(**params).execute
    )

    people = result.get("people", [])
    next_page_token = result.get("nextPageToken")

    if not people:
        return f"No directory people found for {user_google_email}."

    response = f"Directory People for {user_google_email} ({len(people)}):\n\n"

    for person in people:
        response += _format_directory_person(person) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(people)} directory people for {user_google_email}")
    return response
