"""
Google Chat Helper Functions

Request execution, argument normalization, message parsing, and name
resolution for Chat senders and spaces via the People API.
"""

import asyncio
import logging
import ssl
from typing import Dict, List, Optional

from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

_SENDER_CACHE_MAX_SIZE = 256
_sender_name_cache: Dict[str, str] = {}
_UNNAMED_SPACE_FALLBACKS = {
    "DIRECT_MESSAGE": "Direct message",
    "GROUP_CHAT": "Group chat",
}
_SPACE_NAME_MAX_MEMBERS = 3
_PEOPLE_BATCH_SIZE = 200  # people.getBatchGet limit
_MAX_CONSECUTIVE_MEMBER_LOOKUP_FAILURES = 2
_SCOPE_ERROR_REASON = "ACCESS_TOKEN_SCOPE_INSUFFICIENT"
_SEARCH_MESSAGES_RETRY_BASE_DELAY_SECONDS = 1


def _cache_sender(user_id: str, name: str) -> None:
    """Store a resolved sender name, evicting oldest entries if cache is full."""
    if len(_sender_name_cache) >= _SENDER_CACHE_MAX_SIZE:
        to_remove = list(_sender_name_cache.keys())[: _SENDER_CACHE_MAX_SIZE // 2]
        for k in to_remove:
            del _sender_name_cache[k]
    _sender_name_cache[user_id] = name


def _person_name(person: dict) -> Optional[str]:
    """Return a People API person's display name, falling back to their email."""
    names = person.get("names") or [{}]
    emails = person.get("emailAddresses") or [{}]
    return names[0].get("displayName") or emails[0].get("value")


async def _resolve_sender(people_service, sender_obj: dict) -> str:
    """Resolve a Chat message sender to a display name.

    Fast path: use displayName if the API already provided it.
    Slow path: look up the user via the People API directory and cache the result.
    """
    display_name = sender_obj.get("displayName")
    if display_name:
        return display_name

    user_id = sender_obj.get("name", "")  # e.g. "users/123456789"
    if not user_id:
        return "Unknown Sender"

    if user_id in _sender_name_cache:
        return _sender_name_cache[user_id]

    # Chat API uses "users/ID" but People API expects "people/ID"
    people_resource = user_id.replace("users/", "people/", 1)
    if people_service:
        try:
            person = await asyncio.to_thread(
                people_service.people()
                .get(resourceName=people_resource, personFields="names,emailAddresses")
                .execute
            )
            resolved = _person_name(person)
            if resolved:
                _cache_sender(user_id, resolved)
                return resolved
        except HttpError as e:
            logger.debug(f"People API lookup failed for {user_id}: {e}")
        except Exception as e:
            logger.debug(f"Unexpected error resolving {user_id}: {e}")

    return user_id


async def _lookup_people_names(people_service, user_ids: List[str]) -> Dict[str, str]:
    """Resolve Chat user ids to display names with batched People API calls."""
    names = {}
    missing = []
    for user_id in user_ids:
        if user_id in _sender_name_cache:
            names[user_id] = _sender_name_cache[user_id]
        else:
            missing.append(user_id)
    for start in range(0, len(missing), _PEOPLE_BATCH_SIZE):
        chunk = missing[start : start + _PEOPLE_BATCH_SIZE]
        try:
            response = await asyncio.to_thread(
                people_service.people()
                .getBatchGet(
                    resourceNames=[
                        user_id.replace("users/", "people/", 1) for user_id in chunk
                    ],
                    personFields="names,emailAddresses",
                )
                .execute
            )
        except Exception as e:
            logger.debug(f"People API batch lookup failed: {e}")
            continue
        for entry in response.get("responses", []):
            name = _person_name(entry.get("person") or {})
            requested = entry.get("requestedResourceName")
            if name and requested:
                user_id = requested.replace("people/", "users/", 1)
                names[user_id] = name
                _cache_sender(user_id, name)
    return names


def _is_scope_error(error: HttpError) -> bool:
    """Return True when an HttpError carries an ACCESS_TOKEN_SCOPE_INSUFFICIENT detail."""
    details = error.error_details
    return isinstance(details, list) and any(
        isinstance(d, dict) and d.get("reason") == _SCOPE_ERROR_REASON for d in details
    )


async def _list_memberships(
    chat_service, space_name: str, limit: Optional[int] = None, **list_params
) -> List[dict]:
    """Return the memberships of a space, following nextPageToken.

    With a limit, stop after limit + 1 memberships, so the caller can tell that
    more exist without paging through the whole space.
    """
    memberships = []
    # pageSize stays the same on every page: the API expects follow-up page
    # requests to repeat the original parameters.
    page_size = 100 if limit is None else min(100, limit + 1)
    params = {"parent": space_name, "pageSize": page_size, **list_params}
    while True:
        response = await asyncio.to_thread(
            chat_service.spaces().members().list(**params).execute
        )
        memberships.extend(response.get("memberships", []))
        if not response.get("nextPageToken") or (
            limit is not None and len(memberships) > limit
        ):
            return memberships
        params["pageToken"] = response["nextPageToken"]


async def _name_spaces(
    chat_service, people_service, spaces: List[dict]
) -> Dict[str, str]:
    """Map space resource names to labels, naming unnamed spaces after their members."""
    labels = {}
    unnamed = []
    for space in spaces:
        space_name = space.get("name")
        if space_name in labels:
            continue
        if space.get("displayName"):
            labels[space_name] = space["displayName"]
        else:
            labels[space_name] = _UNNAMED_SPACE_FALLBACKS.get(
                space.get("spaceType"), "Unnamed Space"
            )
            unnamed.append(space_name)
    if not unnamed:
        return labels

    # Without the caller's id their own name would end up in every label.
    try:
        me = await asyncio.to_thread(
            people_service.people()
            .get(resourceName="people/me", personFields="metadata")
            .execute
        )
    except Exception as e:
        logger.debug(f"Could not look up the calling user: {e}")
        return labels
    if not me.get("resourceName"):
        return labels
    me_id = me["resourceName"].replace("people/", "users/", 1)

    others_by_space = {}
    consecutive_failures = 0
    for space_name in unnamed:
        try:
            memberships = await _list_memberships(chat_service, space_name)
        except HttpError as e:
            logger.debug(f"Could not list members of {space_name}: {e}")
            # Missing scope or throttling would fail every remaining space too.
            if e.resp.status == 429 or _is_scope_error(e):
                break
            continue
        except Exception as e:
            logger.debug(f"Could not list members of {space_name}: {e}")
            # Repeated network failures would make every remaining space wait too.
            consecutive_failures += 1
            if consecutive_failures >= _MAX_CONSECUTIVE_MEMBER_LOOKUP_FAILURES:
                break
            continue
        consecutive_failures = 0
        others = []
        for membership in memberships:
            member = membership.get("member", {})
            if member.get("type") == "HUMAN" and member.get("name") != me_id:
                others.append(member.get("name"))
        if others:
            others_by_space[space_name] = others

    shown_ids = []
    for others in others_by_space.values():
        for user_id in others[:_SPACE_NAME_MAX_MEMBERS]:
            if user_id not in shown_ids:
                shown_ids.append(user_id)
    people_names = await _lookup_people_names(people_service, shown_ids)

    for space_name, others in others_by_space.items():
        shown = others[:_SPACE_NAME_MAX_MEMBERS]
        label = ", ".join(people_names.get(user_id, user_id) for user_id in shown)
        remaining = len(others) - len(shown)
        if remaining:
            label += f" and {remaining} other{'s' if remaining > 1 else ''}"
        labels[space_name] = label
    return labels


_MEMBER_ROLE_LABELS = {
    "ROLE_MANAGER": "manager",
    "ROLE_ASSISTANT_MANAGER": "assistant manager",
    "ROLE_MEMBER": "member",
}


async def _format_space_members(
    chat_service, people_service, space_id: str, limit: int
) -> str:
    """List up to limit members of a space (people, apps and groups), including
    pending invites."""
    space = await asyncio.to_thread(chat_service.spaces().get(name=space_id).execute)
    space_label = (await _name_spaces(chat_service, people_service, [space]))[
        space.get("name")
    ]
    try:
        memberships = await _list_memberships(
            chat_service, space_id, limit, showInvited=True, showGroups=True
        )
    except HttpError as e:
        if _is_scope_error(e):
            raise ToolError(
                "Listing members needs the chat.memberships.readonly scope, which "
                "this token lacks. Re-authenticate with the default Chat scopes "
                "and retry."
            ) from e
        raise

    has_more = len(memberships) > limit
    memberships = memberships[:limit]
    user_ids = [
        m["member"]["name"]
        for m in memberships
        if m.get("member", {}).get("type") == "HUMAN" and m["member"].get("name")
    ]
    people_names = await _lookup_people_names(people_service, user_ids)

    count = (
        f"first {len(memberships)} shown, more exist (raise page_size to see more)"
        if has_more
        else str(len(memberships))
    )
    lines = [f"Members of '{space_label}' (ID: {space_id}): {count}"]
    for membership in memberships:
        role = _MEMBER_ROLE_LABELS.get(membership.get("role"), "unknown")
        state = membership.get("state")
        suffix = ", invited" if state == "INVITED" else ""
        if "groupMember" in membership:
            group_id = membership["groupMember"].get("name", "")
            lines.append(f"- Group {group_id}" + (" (invited)" if suffix else ""))
            continue
        member = membership.get("member", {})
        member_id = member.get("name", "")
        name = (
            people_names.get(member_id)
            or member.get("displayName")
            or member_id
            or "Unknown"
        )
        kind = " [app]" if member.get("type") == "BOT" else ""
        detail = (
            f"{role}{suffix}" if name == member_id else f"{member_id}, {role}{suffix}"
        )
        lines.append(f"- {name}{kind} ({detail})")
    return "\n".join(lines)


def _none_if_null_sentinel(value: Optional[str]) -> Optional[str]:
    """Map the literal "null"/"None" some clients send for an omitted arg to None."""
    if value is not None and value.strip().lower() in ("null", "none"):
        return None
    return value


def _none_if_blank(value: Optional[str]) -> Optional[str]:
    """Treat a null sentinel or whitespace-only string as an omitted arg."""
    value = _none_if_null_sentinel(value)
    return value if value and value.strip() else None


async def _execute_chat_request(
    request_factory,
    *,
    request_label: str,
    retries: int = 1,
    semaphore: Optional[asyncio.Semaphore] = None,
):
    """Execute a Chat API request in a worker thread with optional SSL retries."""
    for attempt in range(retries):
        try:
            if semaphore is None:
                return await asyncio.to_thread(lambda: request_factory().execute())
            async with semaphore:
                return await asyncio.to_thread(lambda: request_factory().execute())
        except ssl.SSLError as e:
            if attempt == retries - 1:
                raise
            delay = _SEARCH_MESSAGES_RETRY_BASE_DELAY_SECONDS * (2**attempt)
            logger.warning(
                "[search_messages] SSL error during %s on attempt %s/%s: %s. Retrying in %s seconds.",
                request_label,
                attempt + 1,
                retries,
                e,
                delay,
            )
            await asyncio.sleep(delay)


def _extract_rich_links(msg: dict) -> List[str]:
    """Extract URLs from RICH_LINK annotations (smart chips).

    When a user pastes a Google Workspace URL in Chat and it renders as a
    smart chip, the URL is NOT in the text field; it's only available in
    the annotations array as a RICH_LINK with richLinkMetadata.uri.
    """
    text = msg.get("text", "")
    urls = []
    for ann in msg.get("annotations", []):
        if ann.get("type") == "RICH_LINK":
            uri = ann.get("richLinkMetadata", {}).get("uri", "")
            if uri and uri not in text:
                urls.append(uri)
    return urls
