"""
Google Contacts MCP Tools (People API)

This module provides MCP tools for interacting with Google Contacts via the People API.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError
from mcp import Resource

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

# Default person fields for list/search operations
DEFAULT_PERSON_FIELDS = "names,emailAddresses,phoneNumbers,organizations"

# Detailed person fields for get operations
DETAILED_PERSON_FIELDS = (
    "names,emailAddresses,phoneNumbers,organizations,biographies,"
    "addresses,birthdays,urls,photos,metadata,memberships"
)

# Contact group fields
CONTACT_GROUP_FIELDS = "name,groupType,memberCount,metadata"

# Cache warmup tracking
_search_cache_warmed_up: Dict[str, bool] = {}


def _format_contact(person: Dict[str, Any], detailed: bool = False) -> str:
    """
    Format a Person resource into a readable string.

    Args:
        person: The Person resource from the People API.
        detailed: Whether to include detailed fields.

    Returns:
        Formatted string representation of the contact.
    """
    resource_name = person.get("resourceName", "Unknown")
    contact_id = resource_name.replace("people/", "") if resource_name else "Unknown"

    lines = [f"Contact ID: {contact_id}"]

    # Names
    names = person.get("names", [])
    if names:
        primary_name = names[0]
        display_name = primary_name.get("displayName", "")
        if display_name:
            lines.append(f"Name: {display_name}")

    # Email addresses
    emails = person.get("emailAddresses", [])
    if emails:
        email_list = [e.get("value", "") for e in emails if e.get("value")]
        if email_list:
            lines.append(f"Email: {', '.join(email_list)}")

    # Phone numbers
    phones = person.get("phoneNumbers", [])
    if phones:
        phone_list = [p.get("value", "") for p in phones if p.get("value")]
        if phone_list:
            lines.append(f"Phone: {', '.join(phone_list)}")

    # Organizations
    orgs = person.get("organizations", [])
    if orgs:
        org = orgs[0]
        org_parts = []
        if org.get("title"):
            org_parts.append(org["title"])
        if org.get("name"):
            org_parts.append(f"at {org['name']}")
        if org_parts:
            lines.append(f"Organization: {' '.join(org_parts)}")

    if detailed:
        # Addresses
        addresses = person.get("addresses", [])
        if addresses:
            addr = addresses[0]
            formatted_addr = addr.get("formattedValue", "")
            if formatted_addr:
                lines.append(f"Address: {formatted_addr}")

        # Birthday
        birthdays = person.get("birthdays", [])
        if birthdays:
            bday = birthdays[0].get("date", {})
            if bday:
                bday_str = f"{bday.get('month', '?')}/{bday.get('day', '?')}"
                if bday.get("year"):
                    bday_str = f"{bday.get('year')}/{bday_str}"
                lines.append(f"Birthday: {bday_str}")

        # URLs
        urls = person.get("urls", [])
        if urls:
            url_list = [u.get("value", "") for u in urls if u.get("value")]
            if url_list:
                lines.append(f"URLs: {', '.join(url_list)}")

        # Biography/Notes
        bios = person.get("biographies", [])
        if bios:
            bio = bios[0].get("value", "")
            if bio:
                # Truncate long bios
                if len(bio) > 200:
                    bio = bio[:200] + "..."
                lines.append(f"Notes: {bio}")

        # Metadata
        metadata = person.get("metadata", {})
        if metadata:
            sources = metadata.get("sources", [])
            if sources:
                source_types = [s.get("type", "") for s in sources]
                if source_types:
                    lines.append(f"Sources: {', '.join(source_types)}")

    return "\n".join(lines)


def _build_person_body(
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    organization: Optional[str] = None,
    job_title: Optional[str] = None,
    notes: Optional[str] = None,
    address: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a Person resource body for create/update operations.

    Args:
        given_name: First name.
        family_name: Last name.
        email: Email address.
        phone: Phone number.
        organization: Company/organization name.
        job_title: Job title.
        notes: Additional notes/biography.
        address: Street address.

    Returns:
        Person resource body dictionary.
    """
    body: Dict[str, Any] = {}

    if given_name or family_name:
        body["names"] = [
            {
                "givenName": given_name or "",
                "familyName": family_name or "",
            }
        ]

    if email:
        body["emailAddresses"] = [{"value": email}]

    if phone:
        body["phoneNumbers"] = [{"value": phone}]

    if organization or job_title:
        org_entry: Dict[str, str] = {}
        if organization:
            org_entry["name"] = organization
        if job_title:
            org_entry["title"] = job_title
        body["organizations"] = [org_entry]

    if notes:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]

    if address:
        body["addresses"] = [{"formattedValue": address}]

    return body


async def _warmup_search_cache(service: Resource, user_google_email: str) -> None:
    """
    Warm up the People API search cache.

    The People API requires an initial empty query to warm up the search cache
    before searches will return results.

    Args:
        service: Authenticated People API service.
        user_google_email: User's email for tracking.
    """
    global _search_cache_warmed_up

    if _search_cache_warmed_up.get(user_google_email):
        return

    try:
        logger.debug(f"[contacts] Warming up search cache for {user_google_email}")
        await asyncio.to_thread(
            service.people()
            .searchContacts(query="", readMask="names", pageSize=1)
            .execute
        )
        _search_cache_warmed_up[user_google_email] = True
        logger.debug(f"[contacts] Search cache warmed up for {user_google_email}")
    except HttpError as e:
        # Warmup failure is non-fatal, search may still work
        logger.warning(f"[contacts] Search cache warmup failed: {e}")


# =============================================================================
# Core Tier Tools
# =============================================================================


@server.tool()
@require_google_service("people", "contacts_read")
@handle_http_errors("list_contacts", service_type="people")
async def list_contacts(
    service: Resource,
    user_google_email: str,
    page_size: int = 100,
    page_token: Optional[str] = None,
    sort_order: Optional[str] = None,
) -> str:
    """
    List contacts for the authenticated user.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Maximum number of contacts to return (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination.
        sort_order (Optional[str]): Sort order: "LAST_MODIFIED_ASCENDING", "LAST_MODIFIED_DESCENDING", "FIRST_NAME_ASCENDING", or "LAST_NAME_ASCENDING".

    Returns:
        str: List of contacts with their basic information.
    """
    logger.info(f"[list_contacts] Invoked. Email: '{user_google_email}'")

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "resourceName": "people/me",
        "personFields": DEFAULT_PERSON_FIELDS,
        "pageSize": page_size,
    }

    if page_token:
        params["pageToken"] = page_token
    if sort_order:
        params["sortOrder"] = sort_order

    result = await asyncio.to_thread(
        service.people().connections().list(**params).execute
    )

    connections = result.get("connections", [])
    next_page_token = result.get("nextPageToken")
    total_people = result.get("totalPeople", len(connections))

    if not connections:
        return f"No contacts found for {user_google_email}."

    response = (
        f"Contacts for {user_google_email} ({len(connections)} of {total_people}):\n\n"
    )

    for person in connections:
        response += _format_contact(person) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(connections)} contacts for {user_google_email}")
    return response


@server.tool()
@require_google_service("people", "contacts_read")
@handle_http_errors("get_contact", service_type="people")
async def get_contact(
    service: Resource,
    user_google_email: str,
    contact_id: str,
) -> str:
    """
    Get detailed information about a specific contact.

    Args:
        user_google_email (str): The user's Google email address. Required.
        contact_id (str): The contact ID (e.g., "c1234567890" or full resource name "people/c1234567890").

    Returns:
        str: Detailed contact information.
    """
    # Normalize resource name
    if not contact_id.startswith("people/"):
        resource_name = f"people/{contact_id}"
    else:
        resource_name = contact_id

    logger.info(
        f"[get_contact] Invoked. Email: '{user_google_email}', Contact: {resource_name}"
    )

    person = await asyncio.to_thread(
        service.people()
        .get(resourceName=resource_name, personFields=DETAILED_PERSON_FIELDS)
        .execute
    )

    response = f"Contact Details for {user_google_email}:\n\n"
    response += _format_contact(person, detailed=True)

    logger.info(f"Retrieved contact {resource_name} for {user_google_email}")
    return response


@server.tool()
@require_google_service("people", "contacts_read")
@handle_http_errors("search_contacts", service_type="people")
async def search_contacts(
    service: Resource,
    user_google_email: str,
    query: str,
    page_size: int = 30,
) -> str:
    """
    Search contacts by name, email, phone number, or other fields.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): Search query string (searches names, emails, phone numbers).
        page_size (int): Maximum number of results to return (default: 30, max: 30).

    Returns:
        str: Matching contacts with their basic information.
    """
    logger.info(
        f"[search_contacts] Invoked. Email: '{user_google_email}', Query: '{query}'"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 30)

    # Warm up the search cache if needed
    await _warmup_search_cache(service, user_google_email)

    result = await asyncio.to_thread(
        service.people()
        .searchContacts(
            query=query,
            readMask=DEFAULT_PERSON_FIELDS,
            pageSize=page_size,
        )
        .execute
    )

    results = result.get("results", [])

    if not results:
        return f"No contacts found matching '{query}' for {user_google_email}."

    response = f"Search Results for '{query}' ({len(results)} found):\n\n"

    for item in results:
        person = item.get("person", {})
        response += _format_contact(person) + "\n\n"

    logger.info(
        f"Found {len(results)} contacts matching '{query}' for {user_google_email}"
    )
    return response


@server.tool()
@require_google_service("people", "contacts")
@handle_http_errors("manage_contact", service_type="people")
async def manage_contact(
    service: Resource,
    user_google_email: str,
    action: str,
    contact_id: Optional[str] = None,
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    organization: Optional[str] = None,
    job_title: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """
    Create, update, or delete a contact. Consolidated tool replacing create_contact,
    update_contact, and delete_contact.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): The action to perform: "create", "update", or "delete".
        contact_id (Optional[str]): The contact ID. Required for "update" and "delete" actions.
        given_name (Optional[str]): First name (for create/update).
        family_name (Optional[str]): Last name (for create/update).
        email (Optional[str]): Email address (for create/update).
        phone (Optional[str]): Phone number (for create/update).
        organization (Optional[str]): Company/organization name (for create/update).
        job_title (Optional[str]): Job title (for create/update).
        notes (Optional[str]): Additional notes (for create/update).

    Returns:
        str: Result of the action performed.
    """
    action = action.lower().strip()
    if action not in ("create", "update", "delete"):
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'create', 'update', or 'delete'."
        )

    logger.info(
        f"[manage_contact] Invoked. Action: '{action}', Email: '{user_google_email}'"
    )

    if action == "create":
        body = _build_person_body(
            given_name=given_name,
            family_name=family_name,
            email=email,
            phone=phone,
            organization=organization,
            job_title=job_title,
            notes=notes,
        )

        if not body:
            raise UserInputError(
                "At least one field (name, email, phone, etc.) must be provided."
            )

        result = await asyncio.to_thread(
            service.people()
            .createContact(body=body, personFields=DETAILED_PERSON_FIELDS)
            .execute
        )

        response = f"Contact Created for {user_google_email}:\n\n"
        response += _format_contact(result, detailed=True)

        created_id = result.get("resourceName", "").replace("people/", "")
        logger.info(f"Created contact {created_id} for {user_google_email}")
        return response

    # update and delete both require contact_id
    if not contact_id:
        raise UserInputError(f"contact_id is required for '{action}' action.")

    # Normalize resource name
    if not contact_id.startswith("people/"):
        resource_name = f"people/{contact_id}"
    else:
        resource_name = contact_id

    if action == "update":
        # Fetch the contact to get the etag
        current = await asyncio.to_thread(
            service.people()
            .get(resourceName=resource_name, personFields=DETAILED_PERSON_FIELDS)
            .execute
        )

        etag = current.get("etag")
        if not etag:
            raise Exception("Unable to get contact etag for update.")

        body = _build_person_body(
            given_name=given_name,
            family_name=family_name,
            email=email,
            phone=phone,
            organization=organization,
            job_title=job_title,
            notes=notes,
        )

        if not body:
            raise UserInputError(
                "At least one field (name, email, phone, etc.) must be provided."
            )

        body["etag"] = etag

        update_person_fields = []
        if "names" in body:
            update_person_fields.append("names")
        if "emailAddresses" in body:
            update_person_fields.append("emailAddresses")
        if "phoneNumbers" in body:
            update_person_fields.append("phoneNumbers")
        if "organizations" in body:
            update_person_fields.append("organizations")
        if "biographies" in body:
            update_person_fields.append("biographies")
        if "addresses" in body:
            update_person_fields.append("addresses")

        result = await asyncio.to_thread(
            service.people()
            .updateContact(
                resourceName=resource_name,
                body=body,
                updatePersonFields=",".join(update_person_fields),
                personFields=DETAILED_PERSON_FIELDS,
            )
            .execute
        )

        response = f"Contact Updated for {user_google_email}:\n\n"
        response += _format_contact(result, detailed=True)

        logger.info(f"Updated contact {resource_name} for {user_google_email}")
        return response

    # action == "delete"
    await asyncio.to_thread(
        service.people().deleteContact(resourceName=resource_name).execute
    )

    response = f"Contact {contact_id} has been deleted for {user_google_email}."
    logger.info(f"Deleted contact {resource_name} for {user_google_email}")
    return response


# =============================================================================
# Extended Tier Tools
# =============================================================================


@server.tool()
@require_google_service("people", "contacts_read")
@handle_http_errors("list_contact_groups", service_type="people")
async def list_contact_groups(
    service: Resource,
    user_google_email: str,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> str:
    """
    List contact groups (labels) for the user.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Maximum number of groups to return (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: List of contact groups with their details.
    """
    logger.info(f"[list_contact_groups] Invoked. Email: '{user_google_email}'")

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "pageSize": page_size,
        "groupFields": CONTACT_GROUP_FIELDS,
    }

    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(service.contactGroups().list(**params).execute)

    groups = result.get("contactGroups", [])
    next_page_token = result.get("nextPageToken")

    if not groups:
        return f"No contact groups found for {user_google_email}."

    response = f"Contact Groups for {user_google_email}:\n\n"

    for group in groups:
        resource_name = group.get("resourceName", "")
        group_id = resource_name.replace("contactGroups/", "")
        name = group.get("name", "Unnamed")
        group_type = group.get("groupType", "USER_CONTACT_GROUP")
        member_count = group.get("memberCount", 0)

        response += f"- {name}\n"
        response += f"  ID: {group_id}\n"
        response += f"  Type: {group_type}\n"
        response += f"  Members: {member_count}\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(groups)} contact groups for {user_google_email}")
    return response


@server.tool()
@require_google_service("people", "contacts_read")
@handle_http_errors("get_contact_group", service_type="people")
async def get_contact_group(
    service: Resource,
    user_google_email: str,
    group_id: str,
    max_members: int = 100,
) -> str:
    """
    Get details of a specific contact group including its members.

    Args:
        user_google_email (str): The user's Google email address. Required.
        group_id (str): The contact group ID.
        max_members (int): Maximum number of members to return (default: 100, max: 1000).

    Returns:
        str: Contact group details including members.
    """
    # Normalize resource name
    if not group_id.startswith("contactGroups/"):
        resource_name = f"contactGroups/{group_id}"
    else:
        resource_name = group_id

    logger.info(
        f"[get_contact_group] Invoked. Email: '{user_google_email}', Group: {resource_name}"
    )

    if max_members < 1:
        raise UserInputError("max_members must be >= 1")
    max_members = min(max_members, 1000)

    result = await asyncio.to_thread(
        service.contactGroups()
        .get(
            resourceName=resource_name,
            maxMembers=max_members,
            groupFields=CONTACT_GROUP_FIELDS,
        )
        .execute
    )

    name = result.get("name", "Unnamed")
    group_type = result.get("groupType", "USER_CONTACT_GROUP")
    member_count = result.get("memberCount", 0)
    member_resource_names = result.get("memberResourceNames", [])

    response = f"Contact Group Details for {user_google_email}:\n\n"
    response += f"Name: {name}\n"
    response += f"ID: {group_id}\n"
    response += f"Type: {group_type}\n"
    response += f"Total Members: {member_count}\n"

    if member_resource_names:
        response += f"\nMembers ({len(member_resource_names)} shown):\n"
        for member in member_resource_names:
            contact_id = member.replace("people/", "")
            response += f"  - {contact_id}\n"

    logger.info(f"Retrieved contact group {resource_name} for {user_google_email}")
    return response


# =============================================================================
# Complete Tier Tools
# =============================================================================


@server.tool()
@require_google_service("people", "contacts")
@handle_http_errors("manage_contacts_batch", service_type="people")
async def manage_contacts_batch(
    service: Resource,
    user_google_email: str,
    action: str,
    contacts: Optional[List[Dict[str, str]]] = None,
    updates: Optional[List[Dict[str, str]]] = None,
    contact_ids: Optional[List[str]] = None,
) -> str:
    """
    Batch create, update, or delete contacts. Consolidated tool replacing
    batch_create_contacts, batch_update_contacts, and batch_delete_contacts.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): The action to perform: "create", "update", or "delete".
        contacts (Optional[List[Dict[str, str]]]): List of contact dicts for "create" action.
            Each dict may contain: given_name, family_name, email, phone, organization, job_title.
        updates (Optional[List[Dict[str, str]]]): List of update dicts for "update" action.
            Each dict must contain contact_id and may contain: given_name, family_name,
            email, phone, organization, job_title.
        contact_ids (Optional[List[str]]): List of contact IDs for "delete" action.

    Returns:
        str: Result of the batch action performed.
    """
    action = action.lower().strip()
    if action not in ("create", "update", "delete"):
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'create', 'update', or 'delete'."
        )

    logger.info(
        f"[manage_contacts_batch] Invoked. Action: '{action}', Email: '{user_google_email}'"
    )

    if action == "create":
        if not contacts:
            raise UserInputError("contacts parameter is required for 'create' action.")

        if len(contacts) > 200:
            raise UserInputError("Maximum 200 contacts can be created in a batch.")

        contact_bodies = []
        for contact in contacts:
            body = _build_person_body(
                given_name=contact.get("given_name"),
                family_name=contact.get("family_name"),
                email=contact.get("email"),
                phone=contact.get("phone"),
                organization=contact.get("organization"),
                job_title=contact.get("job_title"),
            )
            if body:
                contact_bodies.append({"contactPerson": body})

        if not contact_bodies:
            raise UserInputError("No valid contact data provided.")

        batch_body = {
            "contacts": contact_bodies,
            "readMask": DEFAULT_PERSON_FIELDS,
        }

        result = await asyncio.to_thread(
            service.people().batchCreateContacts(body=batch_body).execute
        )

        created_people = result.get("createdPeople", [])

        response = f"Batch Create Results for {user_google_email}:\n\n"
        response += f"Created {len(created_people)} contacts:\n\n"

        for item in created_people:
            person = item.get("person", {})
            response += _format_contact(person) + "\n\n"

        logger.info(
            f"Batch created {len(created_people)} contacts for {user_google_email}"
        )
        return response

    if action == "update":
        if not updates:
            raise UserInputError("updates parameter is required for 'update' action.")

        if len(updates) > 200:
            raise UserInputError("Maximum 200 contacts can be updated in a batch.")

        # Fetch all contacts to get their etags
        resource_names = []
        for update in updates:
            cid = update.get("contact_id")
            if not cid:
                raise UserInputError("Each update must include a contact_id.")
            if not cid.startswith("people/"):
                cid = f"people/{cid}"
            resource_names.append(cid)

        batch_get_result = await asyncio.to_thread(
            service.people()
            .getBatchGet(
                resourceNames=resource_names,
                personFields="metadata",
            )
            .execute
        )

        etags = {}
        for resp in batch_get_result.get("responses", []):
            person = resp.get("person", {})
            rname = person.get("resourceName")
            etag = person.get("etag")
            if rname and etag:
                etags[rname] = etag

        update_bodies = []
        update_fields_set: set = set()

        for update in updates:
            cid = update.get("contact_id", "")
            if not cid.startswith("people/"):
                cid = f"people/{cid}"

            etag = etags.get(cid)
            if not etag:
                logger.warning(f"No etag found for {cid}, skipping")
                continue

            body = _build_person_body(
                given_name=update.get("given_name"),
                family_name=update.get("family_name"),
                email=update.get("email"),
                phone=update.get("phone"),
                organization=update.get("organization"),
                job_title=update.get("job_title"),
            )

            if body:
                body["resourceName"] = cid
                body["etag"] = etag
                update_bodies.append({"person": body})

                if "names" in body:
                    update_fields_set.add("names")
                if "emailAddresses" in body:
                    update_fields_set.add("emailAddresses")
                if "phoneNumbers" in body:
                    update_fields_set.add("phoneNumbers")
                if "organizations" in body:
                    update_fields_set.add("organizations")

        if not update_bodies:
            raise UserInputError("No valid update data provided.")

        batch_body = {
            "contacts": update_bodies,
            "updateMask": ",".join(update_fields_set),
            "readMask": DEFAULT_PERSON_FIELDS,
        }

        result = await asyncio.to_thread(
            service.people().batchUpdateContacts(body=batch_body).execute
        )

        update_results = result.get("updateResult", {})

        response = f"Batch Update Results for {user_google_email}:\n\n"
        response += f"Updated {len(update_results)} contacts:\n\n"

        for rname, update_result in update_results.items():
            person = update_result.get("person", {})
            response += _format_contact(person) + "\n\n"

        logger.info(
            f"Batch updated {len(update_results)} contacts for {user_google_email}"
        )
        return response

    # action == "delete"
    if not contact_ids:
        raise UserInputError("contact_ids parameter is required for 'delete' action.")

    if len(contact_ids) > 500:
        raise UserInputError("Maximum 500 contacts can be deleted in a batch.")

    resource_names = []
    for cid in contact_ids:
        if not cid.startswith("people/"):
            resource_names.append(f"people/{cid}")
        else:
            resource_names.append(cid)

    batch_body = {"resourceNames": resource_names}

    await asyncio.to_thread(
        service.people().batchDeleteContacts(body=batch_body).execute
    )

    response = f"Batch deleted {len(contact_ids)} contacts for {user_google_email}."
    logger.info(f"Batch deleted {len(contact_ids)} contacts for {user_google_email}")
    return response


@server.tool()
@require_google_service("people", "contacts")
@handle_http_errors("manage_contact_group", service_type="people")
async def manage_contact_group(
    service: Resource,
    user_google_email: str,
    action: str,
    group_id: Optional[str] = None,
    name: Optional[str] = None,
    delete_contacts: bool = False,
    add_contact_ids: Optional[List[str]] = None,
    remove_contact_ids: Optional[List[str]] = None,
) -> str:
    """
    Create, update, delete a contact group, or modify its members. Consolidated tool
    replacing create_contact_group, update_contact_group, delete_contact_group, and
    modify_contact_group_members.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): The action to perform: "create", "update", "delete", or "modify_members".
        group_id (Optional[str]): The contact group ID. Required for "update", "delete",
            and "modify_members" actions.
        name (Optional[str]): The group name. Required for "create" and "update" actions.
        delete_contacts (bool): If True and action is "delete", also delete contacts in
            the group (default: False).
        add_contact_ids (Optional[List[str]]): Contact IDs to add (for "modify_members").
        remove_contact_ids (Optional[List[str]]): Contact IDs to remove (for "modify_members").

    Returns:
        str: Result of the action performed.
    """
    action = action.lower().strip()
    if action not in ("create", "update", "delete", "modify_members"):
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'create', 'update', 'delete', or 'modify_members'."
        )

    logger.info(
        f"[manage_contact_group] Invoked. Action: '{action}', Email: '{user_google_email}'"
    )

    if action == "create":
        if not name:
            raise UserInputError("name is required for 'create' action.")

        body = {"contactGroup": {"name": name}}

        result = await asyncio.to_thread(
            service.contactGroups().create(body=body).execute
        )

        resource_name = result.get("resourceName", "")
        created_group_id = resource_name.replace("contactGroups/", "")
        created_name = result.get("name", name)

        response = f"Contact Group Created for {user_google_email}:\n\n"
        response += f"Name: {created_name}\n"
        response += f"ID: {created_group_id}\n"
        response += f"Type: {result.get('groupType', 'USER_CONTACT_GROUP')}\n"

        logger.info(f"Created contact group '{name}' for {user_google_email}")
        return response

    # All other actions require group_id
    if not group_id:
        raise UserInputError(f"group_id is required for '{action}' action.")

    # Normalize resource name
    if not group_id.startswith("contactGroups/"):
        resource_name = f"contactGroups/{group_id}"
    else:
        resource_name = group_id

    if action == "update":
        if not name:
            raise UserInputError("name is required for 'update' action.")

        body = {"contactGroup": {"name": name}}

        result = await asyncio.to_thread(
            service.contactGroups()
            .update(resourceName=resource_name, body=body)
            .execute
        )

        updated_name = result.get("name", name)

        response = f"Contact Group Updated for {user_google_email}:\n\n"
        response += f"Name: {updated_name}\n"
        response += f"ID: {group_id}\n"

        logger.info(f"Updated contact group {resource_name} for {user_google_email}")
        return response

    if action == "delete":
        await asyncio.to_thread(
            service.contactGroups()
            .delete(resourceName=resource_name, deleteContacts=delete_contacts)
            .execute
        )

        response = f"Contact group {group_id} has been deleted for {user_google_email}."
        if delete_contacts:
            response += " Contacts in the group were also deleted."
        else:
            response += " Contacts in the group were preserved."

        logger.info(f"Deleted contact group {resource_name} for {user_google_email}")
        return response

    # action == "modify_members"
    if not add_contact_ids and not remove_contact_ids:
        raise UserInputError(
            "At least one of add_contact_ids or remove_contact_ids must be provided."
        )

    modify_body: Dict[str, Any] = {}

    if add_contact_ids:
        add_names = []
        for contact_id in add_contact_ids:
            if not contact_id.startswith("people/"):
                add_names.append(f"people/{contact_id}")
            else:
                add_names.append(contact_id)
        modify_body["resourceNamesToAdd"] = add_names

    if remove_contact_ids:
        remove_names = []
        for contact_id in remove_contact_ids:
            if not contact_id.startswith("people/"):
                remove_names.append(f"people/{contact_id}")
            else:
                remove_names.append(contact_id)
        modify_body["resourceNamesToRemove"] = remove_names

    result = await asyncio.to_thread(
        service.contactGroups()
        .members()
        .modify(resourceName=resource_name, body=modify_body)
        .execute
    )

    not_found = result.get("notFoundResourceNames", [])
    cannot_remove = result.get("canNotRemoveLastContactGroupResourceNames", [])

    response = f"Contact Group Members Modified for {user_google_email}:\n\n"
    response += f"Group: {group_id}\n"

    if add_contact_ids:
        response += f"Added: {len(add_contact_ids)} contacts\n"
    if remove_contact_ids:
        response += f"Removed: {len(remove_contact_ids)} contacts\n"

    if not_found:
        response += f"\nNot found: {', '.join(not_found)}\n"
    if cannot_remove:
        response += f"\nCannot remove (last group): {', '.join(cannot_remove)}\n"

    logger.info(
        f"Modified contact group members for {resource_name} for {user_google_email}"
    )
    return response
