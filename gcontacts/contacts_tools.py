"""
Google Contacts (People API) MCP Tools

This module provides MCP tools for interacting with Google People API (Contacts).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError  # type: ignore
from mcp import Resource

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)

LIST_CONTACTS_MAX_RESULTS_DEFAULT = 100
LIST_CONTACTS_MAX_RESULTS_MAX = 1000


@server.tool()  # type: ignore
@require_google_service("people", "contacts_read")  # type: ignore
@handle_http_errors("list_contacts", is_read_only=True, service_type="contacts")  # type: ignore
async def list_contacts(
    service: Resource,
    user_google_email: str,
    page_size: int = LIST_CONTACTS_MAX_RESULTS_DEFAULT,
    page_token: Optional[str] = None,
) -> str:
    """
    List all contacts for the user.

    Args:
        user_google_email (str): The user's Google email address. Required.
        page_size (int): Number of contacts to return per page (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination to get the next page of results.

    Returns:
        str: JSON string containing list of contacts with names, email addresses, phone numbers, and pagination info.
    """
    logger.info(f"[list_contacts] Invoked. Email: '{user_google_email}'")

    try:
        # Limit page_size to maximum
        page_size = min(page_size, LIST_CONTACTS_MAX_RESULTS_MAX)

        params: Dict[str, Any] = {
            "resourceName": "people/me",
            "pageSize": page_size,
            "personFields": "names,emailAddresses,phoneNumbers,organizations,photos,addresses,birthdays,biographies",
        }

        if page_token:
            params["pageToken"] = page_token

        result = await asyncio.to_thread(
            service.people().connections().list(**params).execute
        )

        connections = result.get("connections", [])
        next_page_token = result.get("nextPageToken")
        total_people = result.get("totalPeople", 0)

        contacts_info = []
        for person in connections:
            contact_info = {
                "resourceName": person.get("resourceName"),
                "etag": person.get("etag"),
            }

            # Extract names
            names = person.get("names", [])
            if names:
                primary_name = names[0]
                contact_info["displayName"] = primary_name.get("displayName")
                contact_info["givenName"] = primary_name.get("givenName")
                contact_info["familyName"] = primary_name.get("familyName")

            # Extract email addresses
            emails = person.get("emailAddresses", [])
            if emails:
                contact_info["emails"] = [
                    {"value": email.get("value"), "type": email.get("type")}
                    for email in emails
                ]

            # Extract phone numbers
            phones = person.get("phoneNumbers", [])
            if phones:
                contact_info["phoneNumbers"] = [
                    {"value": phone.get("value"), "type": phone.get("type")}
                    for phone in phones
                ]

            # Extract organizations
            orgs = person.get("organizations", [])
            if orgs:
                contact_info["organizations"] = [
                    {"name": org.get("name"), "title": org.get("title")}
                    for org in orgs
                ]

            # Extract addresses
            addresses = person.get("addresses", [])
            if addresses:
                contact_info["addresses"] = [
                    {"formattedValue": addr.get("formattedValue"), "type": addr.get("type")}
                    for addr in addresses
                ]

            contacts_info.append(contact_info)

        response = {
            "contacts": contacts_info,
            "totalContacts": total_people,
            "contactsInPage": len(connections),
        }

        if next_page_token:
            response["nextPageToken"] = next_page_token

        import json
        return json.dumps(response, indent=2)

    except HttpError as e:
        logger.error(f"[list_contacts] HttpError: {e}")
        raise
    except Exception as e:
        logger.error(f"[list_contacts] Unexpected error: {e}", exc_info=True)
        raise


@server.tool()  # type: ignore
@require_google_service("people", "contacts_read")  # type: ignore
@handle_http_errors("search_contacts", is_read_only=True, service_type="contacts")  # type: ignore
async def search_contacts(
    service: Resource,
    user_google_email: str,
    query: str,
    page_size: int = 50,
) -> str:
    """
    Search contacts by name, email, or phone number.

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): Search query string to match against contact names, emails, or phone numbers.
        page_size (int): Number of results to return (default: 50, max: 50).

    Returns:
        str: JSON string containing matching contacts.
    """
    logger.info(f"[search_contacts] Invoked. Email: '{user_google_email}', Query: '{query}'")

    try:
        # People API search is limited to 50 results
        page_size = min(page_size, 50)

        params: Dict[str, Any] = {
            "query": query,
            "pageSize": page_size,
            "readMask": "names,emailAddresses,phoneNumbers,organizations,photos",
        }

        result = await asyncio.to_thread(
            service.people().searchContacts(**params).execute
        )

        results = result.get("results", [])

        contacts_info = []
        for item in results:
            person = item.get("person", {})
            contact_info = {
                "resourceName": person.get("resourceName"),
            }

            # Extract names
            names = person.get("names", [])
            if names:
                primary_name = names[0]
                contact_info["displayName"] = primary_name.get("displayName")
                contact_info["givenName"] = primary_name.get("givenName")
                contact_info["familyName"] = primary_name.get("familyName")

            # Extract email addresses
            emails = person.get("emailAddresses", [])
            if emails:
                contact_info["emails"] = [
                    {"value": email.get("value"), "type": email.get("type")}
                    for email in emails
                ]

            # Extract phone numbers
            phones = person.get("phoneNumbers", [])
            if phones:
                contact_info["phoneNumbers"] = [
                    {"value": phone.get("value"), "type": phone.get("type")}
                    for phone in phones
                ]

            contacts_info.append(contact_info)

        response = {
            "query": query,
            "results": contacts_info,
            "resultCount": len(contacts_info),
        }

        import json
        return json.dumps(response, indent=2)

    except HttpError as e:
        logger.error(f"[search_contacts] HttpError: {e}")
        raise
    except Exception as e:
        logger.error(f"[search_contacts] Unexpected error: {e}", exc_info=True)
        raise


@server.tool()  # type: ignore
@require_google_service("people", "contacts_read")  # type: ignore
@handle_http_errors("get_contact", is_read_only=True, service_type="contacts")  # type: ignore
async def get_contact(
    service: Resource,
    user_google_email: str,
    resource_name: str,
) -> str:
    """
    Get detailed information about a specific contact.

    Args:
        user_google_email (str): The user's Google email address. Required.
        resource_name (str): The resource name of the contact (e.g., 'people/c1234567890').

    Returns:
        str: JSON string containing detailed contact information.
    """
    logger.info(f"[get_contact] Invoked. Email: '{user_google_email}', ResourceName: '{resource_name}'")

    try:
        params: Dict[str, Any] = {
            "resourceName": resource_name,
            "personFields": "names,emailAddresses,phoneNumbers,organizations,photos,addresses,birthdays,biographies,urls,relations,events",
        }

        result = await asyncio.to_thread(
            service.people().get(**params).execute
        )

        contact_info = {
            "resourceName": result.get("resourceName"),
            "etag": result.get("etag"),
        }

        # Extract all available fields
        names = result.get("names", [])
        if names:
            primary_name = names[0]
            contact_info["displayName"] = primary_name.get("displayName")
            contact_info["givenName"] = primary_name.get("givenName")
            contact_info["familyName"] = primary_name.get("familyName")
            contact_info["middleName"] = primary_name.get("middleName")

        emails = result.get("emailAddresses", [])
        if emails:
            contact_info["emails"] = emails

        phones = result.get("phoneNumbers", [])
        if phones:
            contact_info["phoneNumbers"] = phones

        orgs = result.get("organizations", [])
        if orgs:
            contact_info["organizations"] = orgs

        addresses = result.get("addresses", [])
        if addresses:
            contact_info["addresses"] = addresses

        birthdays = result.get("birthdays", [])
        if birthdays:
            contact_info["birthdays"] = birthdays

        bios = result.get("biographies", [])
        if bios:
            contact_info["biographies"] = bios

        urls = result.get("urls", [])
        if urls:
            contact_info["urls"] = urls

        relations = result.get("relations", [])
        if relations:
            contact_info["relations"] = relations

        events = result.get("events", [])
        if events:
            contact_info["events"] = events

        import json
        return json.dumps(contact_info, indent=2)

    except HttpError as e:
        logger.error(f"[get_contact] HttpError: {e}")
        raise
    except Exception as e:
        logger.error(f"[get_contact] Unexpected error: {e}", exc_info=True)
        raise


@server.tool()  # type: ignore
@require_google_service("people", "contacts_write")  # type: ignore
@handle_http_errors("create_contact", service_type="contacts")  # type: ignore
async def create_contact(
    service: Resource,
    user_google_email: str,
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    organization: Optional[str] = None,
    job_title: Optional[str] = None,
) -> str:
    """
    Create a new contact.

    Args:
        user_google_email (str): The user's Google email address. Required.
        given_name (Optional[str]): First name of the contact.
        family_name (Optional[str]): Last name of the contact.
        email (Optional[str]): Email address of the contact.
        phone (Optional[str]): Phone number of the contact.
        organization (Optional[str]): Organization/company name.
        job_title (Optional[str]): Job title at the organization.

    Returns:
        str: JSON string containing the created contact's information including resource name.
    """
    logger.info(f"[create_contact] Invoked. Email: '{user_google_email}'")

    try:
        person = {}

        # Add name if provided
        if given_name or family_name:
            person["names"] = [{
                "givenName": given_name or "",
                "familyName": family_name or "",
            }]

        # Add email if provided
        if email:
            person["emailAddresses"] = [{
                "value": email,
            }]

        # Add phone if provided
        if phone:
            person["phoneNumbers"] = [{
                "value": phone,
            }]

        # Add organization if provided
        if organization or job_title:
            person["organizations"] = [{
                "name": organization or "",
                "title": job_title or "",
            }]

        params: Dict[str, Any] = {
            "body": person,
            "personFields": "names,emailAddresses,phoneNumbers,organizations",
        }

        result = await asyncio.to_thread(
            service.people().createContact(**params).execute
        )

        contact_info = {
            "resourceName": result.get("resourceName"),
            "etag": result.get("etag"),
            "message": "Contact created successfully",
        }

        # Extract created contact details
        names = result.get("names", [])
        if names:
            contact_info["displayName"] = names[0].get("displayName")

        emails = result.get("emailAddresses", [])
        if emails:
            contact_info["email"] = emails[0].get("value")

        import json
        return json.dumps(contact_info, indent=2)

    except HttpError as e:
        logger.error(f"[create_contact] HttpError: {e}")
        raise
    except Exception as e:
        logger.error(f"[create_contact] Unexpected error: {e}", exc_info=True)
        raise


@server.tool()  # type: ignore
@require_google_service("people", "contacts_write")  # type: ignore
@handle_http_errors("update_contact", service_type="contacts")  # type: ignore
async def update_contact(
    service: Resource,
    user_google_email: str,
    resource_name: str,
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    organization: Optional[str] = None,
    job_title: Optional[str] = None,
) -> str:
    """
    Update an existing contact.

    Note: This operation replaces the specified fields. To preserve existing data,
    retrieve the contact first with get_contact, modify the desired fields, and
    include all fields you want to keep in this update call.

    Args:
        user_google_email (str): The user's Google email address. Required.
        resource_name (str): The resource name of the contact to update (e.g., 'people/c1234567890').
        given_name (Optional[str]): Updated first name.
        family_name (Optional[str]): Updated last name.
        email (Optional[str]): Updated email address.
        phone (Optional[str]): Updated phone number.
        organization (Optional[str]): Updated organization/company name.
        job_title (Optional[str]): Updated job title.

    Returns:
        str: JSON string containing the updated contact's information.
    """
    logger.info(f"[update_contact] Invoked. Email: '{user_google_email}', ResourceName: '{resource_name}'")

    try:
        person = {}
        update_mask = []

        # Update name if provided
        if given_name is not None or family_name is not None:
            person["names"] = [{
                "givenName": given_name or "",
                "familyName": family_name or "",
            }]
            update_mask.append("names")

        # Update email if provided
        if email is not None:
            person["emailAddresses"] = [{
                "value": email,
            }]
            update_mask.append("emailAddresses")

        # Update phone if provided
        if phone is not None:
            person["phoneNumbers"] = [{
                "value": phone,
            }]
            update_mask.append("phoneNumbers")

        # Update organization if provided
        if organization is not None or job_title is not None:
            person["organizations"] = [{
                "name": organization or "",
                "title": job_title or "",
            }]
            update_mask.append("organizations")

        if not update_mask:
            return json.dumps({
                "error": "No fields to update. Please provide at least one field to update."
            }, indent=2)

        params: Dict[str, Any] = {
            "resourceName": resource_name,
            "body": person,
            "updatePersonFields": ",".join(update_mask),
            "personFields": "names,emailAddresses,phoneNumbers,organizations",
        }

        result = await asyncio.to_thread(
            service.people().updateContact(**params).execute
        )

        contact_info = {
            "resourceName": result.get("resourceName"),
            "etag": result.get("etag"),
            "message": "Contact updated successfully",
            "updatedFields": update_mask,
        }

        # Extract updated contact details
        names = result.get("names", [])
        if names:
            contact_info["displayName"] = names[0].get("displayName")

        import json
        return json.dumps(contact_info, indent=2)

    except HttpError as e:
        logger.error(f"[update_contact] HttpError: {e}")
        raise
    except Exception as e:
        logger.error(f"[update_contact] Unexpected error: {e}", exc_info=True)
        raise


@server.tool()  # type: ignore
@require_google_service("people", "contacts_write")  # type: ignore
@handle_http_errors("delete_contact", service_type="contacts")  # type: ignore
async def delete_contact(
    service: Resource,
    user_google_email: str,
    resource_name: str,
) -> str:
    """
    Delete a contact permanently.

    Warning: This action cannot be undone. The contact will be permanently removed
    from the user's Google Contacts.

    Args:
        user_google_email (str): The user's Google email address. Required.
        resource_name (str): The resource name of the contact to delete (e.g., 'people/c1234567890').

    Returns:
        str: JSON string confirming the deletion.
    """
    logger.info(f"[delete_contact] Invoked. Email: '{user_google_email}', ResourceName: '{resource_name}'")

    try:
        await asyncio.to_thread(
            service.people().deleteContact(resourceName=resource_name).execute
        )

        response = {
            "message": "Contact deleted successfully",
            "resourceName": resource_name,
        }

        import json
        return json.dumps(response, indent=2)

    except HttpError as e:
        logger.error(f"[delete_contact] HttpError: {e}")
        raise
    except Exception as e:
        logger.error(f"[delete_contact] Unexpected error: {e}", exc_info=True)
        raise
