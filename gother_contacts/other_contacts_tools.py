"""
Google Other Contacts MCP Tools (People API)

"Other contacts" are addresses Google auto-collects from people the user has
interacted with (e.g. emailed) but never explicitly saved. This module exposes
read-only lookups over that bucket so a name can be resolved to an email
address — useful for recipient lookup.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError
from mcp import Resource
from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

# Minimum fields for recipient resolution (name -> email). Widening this does
# not change the OAuth scope; keep it minimal.
OTHER_CONTACTS_READ_MASK = "names,emailAddresses"

# Tracks per-user warmup so the empty-query priming request is sent only once.
_search_cache_warmed_up: Dict[str, bool] = {}


async def _warmup_search_cache(service: Resource, user_google_email: str) -> None:
    """
    Warm up the People API "other contacts" search cache.

    The People API needs an initial empty-query request to prime the search
    cache before `otherContacts.search` returns results — the same requirement
    as `people.searchContacts`. Best-effort: a warmup failure is non-fatal and
    logged, and the real search still runs.
    """
    global _search_cache_warmed_up
    if _search_cache_warmed_up.get(user_google_email):
        return
    try:
        await asyncio.to_thread(
            service.otherContacts()
            .search(query="", readMask="names", pageSize=1)
            .execute
        )
        _search_cache_warmed_up[user_google_email] = True
    except HttpError as e:
        logger.warning(f"[other_contacts] Search cache warmup failed: {e}")


def _format_other_contact(person: Dict[str, Any]) -> str:
    """
    Format an "other contact" Person as name + ALL emails + resourceName.

    Returns every email address (not just the first) so the consumer can
    disambiguate a contact that has multiple addresses — flattening to one
    silently drops valid recipients.

    Args:
        person: A People API Person resource.

    Returns:
        str: A formatted, human-readable block for one contact.
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
    title="Search Other Contacts",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("people", "other_contacts_read")
@handle_http_errors("search_other_contacts", is_read_only=True, service_type="people")
async def search_other_contacts(
    service: Resource,
    user_google_email: str,
    query: str,
    page_size: int = 30,
) -> str:
    """
    Search the user's "other contacts" (Google auto-collected addresses).

    Other contacts are addresses Google auto-collects from people the user has
    emailed but never saved. Read-only.

    Note: the People API `otherContacts.search` endpoint does NOT support
    pagination — there is no page token, and results are capped at
    pageSize <= 30. Use `list_other_contacts` to page through the full set.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): Search query (matches names and email addresses).
        page_size (int): Maximum number of results to return (default: 30, max: 30).

    Returns:
        str: Matching other contacts with name, all emails, and resource name.
    """
    logger.info(
        f"[search_other_contacts] Invoked. Email: '{user_google_email}', query_len={len(query)}"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 30)

    # Prime the People API search cache: otherContacts.search needs an empty-query
    # warmup or the first search can return no results.
    await _warmup_search_cache(service, user_google_email)

    result = await asyncio.to_thread(
        service.otherContacts()
        .search(
            query=query,
            readMask=OTHER_CONTACTS_READ_MASK,
            pageSize=page_size,
        )
        .execute
    )

    results = result.get("results", [])

    if not results:
        return f"No other contacts found matching '{query}' for {user_google_email}."

    response = f"Other Contacts matching '{query}' ({len(results)} found):\n\n"

    for item in results:
        person = item.get("person", {})
        response += _format_other_contact(person) + "\n\n"

    logger.info(f"Found {len(results)} other contacts for {user_google_email}")
    return response


@server.tool(
    title="List Other Contacts",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@require_google_service("people", "other_contacts_read")
@handle_http_errors("list_other_contacts", is_read_only=True, service_type="people")
async def list_other_contacts(
    service: Resource,
    user_google_email: str,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> str:
    """
    List the user's "other contacts" (Google auto-collected addresses).

    Unlike `search_other_contacts`, `otherContacts.list` supports pagination via
    a `nextPageToken`.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Maximum number of results to return (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Other contacts with name, all emails, and resource name.
    """
    logger.info(f"[list_other_contacts] Invoked. Email: '{user_google_email}'")

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "readMask": OTHER_CONTACTS_READ_MASK,
        "pageSize": page_size,
    }
    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(service.otherContacts().list(**params).execute)

    other_contacts = result.get("otherContacts", [])
    next_page_token = result.get("nextPageToken")

    if not other_contacts:
        return f"No other contacts found for {user_google_email}."

    response = f"Other Contacts for {user_google_email} ({len(other_contacts)}):\n\n"

    for person in other_contacts:
        response += _format_other_contact(person) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(other_contacts)} other contacts for {user_google_email}")
    return response
