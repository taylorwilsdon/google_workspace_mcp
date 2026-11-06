"""
Google Chat Helper Functions

Request execution, argument normalization, message parsing, and name
resolution for Chat senders and spaces via the People API.
"""

import asyncio
import logging
import ssl
import uuid
from typing import Dict, List, Optional

from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError

from core.utils import UserInputError

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
_SPACE_SETUP_MAX_MEMBERS = 49  # spaces.setup limit, not counting the caller
_SPACE_DISPLAY_NAME_MAX_LENGTH = 128
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


async def _list_memberships(chat_service, space_name: str) -> List[dict]:
    """Return every membership of a space, following nextPageToken."""
    memberships = []
    params = {"parent": space_name, "pageSize": 100}
    while True:
        response = await asyncio.to_thread(
            chat_service.spaces().members().list(**params).execute
        )
        memberships.extend(response.get("memberships", []))
        if not response.get("nextPageToken"):
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


def _validate_space_target(
    space_id: Optional[str],
    message_name: Optional[str],
    create_space_name: Optional[str],
    member_emails: Optional[List[str]],
    thread_name: Optional[str] = None,
    caller_email: Optional[str] = None,
) -> None:
    """Require exactly one message target: an existing space or a space to create."""
    if create_space_name is not None:
        if space_id or message_name is not None or thread_name:
            raise UserInputError(
                "create_space_name cannot be combined with space_id, message_name "
                "or thread_name: a new space has no existing messages or threads "
                "to target."
            )
        if len(create_space_name) > _SPACE_DISPLAY_NAME_MAX_LENGTH:
            raise UserInputError(
                f"create_space_name is limited to {_SPACE_DISPLAY_NAME_MAX_LENGTH} "
                "characters."
            )
        emails = _clean_emails(member_emails, caller_email)
        invalid = [e for e in emails if "@" not in e]
        if invalid:
            raise UserInputError(
                f"member_emails must be email addresses of people: {', '.join(invalid)}"
            )
        if len(emails) > _SPACE_SETUP_MAX_MEMBERS:
            raise UserInputError(
                f"A new space can start with at most {_SPACE_SETUP_MAX_MEMBERS} "
                f"members besides you; {len(emails)} were given."
            )
    elif space_id is None:
        raise UserInputError(
            "Provide space_id, or create_space_name to create a space."
        )
    elif member_emails:
        raise UserInputError(
            "member_emails is only used with create_space_name, when creating a space."
        )


def _clean_emails(
    member_emails: Optional[List[str]], caller_email: Optional[str] = None
) -> List[str]:
    """Normalize member emails: drop blanks, a users/ prefix, duplicates and the
    caller, whom spaces.setup adds on its own."""
    seen = {(caller_email or "").strip().lower()}
    emails = []
    for email in member_emails or []:
        email = (email or "").strip().removeprefix("users/")
        if email and email.lower() not in seen:
            seen.add(email.lower())
            emails.append(email)
    return emails


async def _setup_space(
    chat_service,
    display_name: str,
    member_emails: Optional[List[str]] = None,
    caller_email: Optional[str] = None,
) -> dict:
    """Create a named space via spaces.setup; the caller is added as a manager.

    A transport failure leaves it unknown whether the space was created, so the
    request is retried once with the same requestId, which makes the API return
    the space it already created instead of failing on the taken display name.
    """
    body = {
        "space": {"spaceType": "SPACE", "displayName": display_name},
        "requestId": str(uuid.uuid4()),
    }
    emails = _clean_emails(member_emails, caller_email)
    if emails:
        body["memberships"] = [
            {"member": {"name": f"users/{email}", "type": "HUMAN"}} for email in emails
        ]
    try:
        try:
            return await asyncio.to_thread(
                chat_service.spaces().setup(body=body).execute
            )
        except OSError as e:
            logger.warning(f"spaces.setup interrupted ({e}); retrying with requestId")
            try:
                return await asyncio.to_thread(
                    chat_service.spaces().setup(body=body).execute
                )
            except OSError as retry_error:
                raise ToolError(
                    f"Creating space '{display_name}' was interrupted twice "
                    f"({retry_error}), so it may already exist. Check list_spaces "
                    "before retrying."
                ) from retry_error
    except HttpError as e:
        if _is_scope_error(e):
            raise ToolError(
                "Creating a space needs the chat.spaces scope, which this token "
                "lacks. Re-authenticate with the default Chat scopes and retry."
            ) from e
        raise


async def _unconfirmed_members(
    chat_service,
    space_id: str,
    member_emails: Optional[List[str]],
    caller_email: Optional[str] = None,
) -> str:
    """Check each requested member of a new space; describe any not confirmed.

    The spaces.setup response does not list memberships, so each email is looked
    up with members.get, which accepts an email as the member ID. People outside
    the organization are invited rather than added, so they are listed as pending.
    Failures are reported rather than raised: the space already exists, and an
    error would prompt the client to create it again.
    """
    unconfirmed = []
    invited = []
    for email in _clean_emails(member_emails, caller_email):
        try:
            membership = await asyncio.to_thread(
                chat_service.spaces()
                .members()
                .get(name=f"{space_id}/members/{email}")
                .execute
            )
            state = membership.get("state")
            if state == "INVITED":
                invited.append(email)
            elif state != "JOINED":
                unconfirmed.append(email)
        except HttpError as e:
            if _is_scope_error(e):
                return (
                    "Member additions not confirmed: checking them needs the "
                    "chat.memberships.readonly scope, which this token lacks."
                )
            unconfirmed.append(email)
        except Exception as e:
            logger.debug(f"Could not confirm member {email} of {space_id}: {e}")
            unconfirmed.append(email)
    notes = []
    if invited:
        notes.append(f"Invited (pending acceptance): {', '.join(invited)}.")
    if unconfirmed:
        notes.append(
            f"Member additions not confirmed for: {', '.join(unconfirmed)}. "
            "They may not have been added to the space."
        )
    return "\n".join(notes)


async def _create_message(chat_service, request_params: dict, created_space: bool):
    """Create a message; when its space was just created, name that space on failure.

    The space already exists at that point, and its display name is now taken, so
    retrying with create_space_name would fail. The caller needs the space ID to
    retry with space_id instead.
    """
    try:
        return await asyncio.to_thread(
            chat_service.spaces().messages().create(**request_params).execute
        )
    except HttpError as e:
        if not created_space:
            raise
        space_id = request_params["parent"]
        raise ToolError(
            f"Created space '{space_id}', but sending the message failed: "
            f"{getattr(e, 'reason', None) or e}. Retry with space_id='{space_id}'."
        ) from e
    except OSError as e:
        if not created_space:
            raise
        # A dropped connection leaves delivery unknown; name the new space so the
        # retry targets it instead of creating another one.
        space_id = request_params["parent"]
        raise ToolError(
            f"Created space '{space_id}', but the message send was interrupted "
            f"({e}), so it may or may not have been delivered. Check the space, "
            f"then retry with space_id='{space_id}' if needed."
        ) from e


async def _pin_message(chat_service, space_id: str, message_name: str) -> str:
    """Pin a message in its space and return a one-line outcome for the tool reply.

    A pin failure is reported instead of raised: the message itself has already
    been sent or edited, and raising would invite the caller to send it again.
    """
    try:
        pin = await asyncio.to_thread(
            chat_service.spaces()
            .messagePins()
            .create(parent=space_id, body={"message": message_name})
            .execute
        )
    except HttpError as e:
        if e.resp.status == 409:
            return "Already pinned."
        logger.warning(f"Pinning '{message_name}' failed: {e}")
        if _is_scope_error(e):
            return (
                "Pin failed (message was still delivered): pinning needs the "
                "chat.spaces scope, which this token lacks."
            )
        reason = getattr(e, "reason", None) or str(e)
        return f"Pin failed (message was still delivered): {reason}"
    except Exception as e:
        logger.warning(f"Pinning '{message_name}' failed unexpectedly: {e}")
        return f"Pin failed (message was still delivered): {e}"
    return f"Pinned. Pin ID: {pin.get('name', '')}"
