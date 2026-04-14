"""
Google Contacts MCP Tools (People API)

This module provides MCP tools for interacting with Google Contacts via the People API.
"""

import asyncio
import logging
import re
import warnings
from typing import Any, Dict, List, Literal, Optional

from googleapiclient.errors import HttpError
from mcp import Resource
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors, StringList

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

# Known phone types supported by Google People API (custom types also allowed)
KNOWN_PHONE_TYPES = {
    "home",
    "work",
    "mobile",
    "homeFax",
    "workFax",
    "otherFax",
    "pager",
    "workMobile",
    "workPager",
    "main",
    "googleVoice",
    "other",
    "internal",
}


class PhoneInput(BaseModel):
    """Typed input for a phone entry."""

    model_config = ConfigDict(extra="forbid")

    number: Optional[str] = Field(
        default=None,
        description="Phone number value.",
    )
    value: Optional[str] = Field(
        default=None,
        description="Backward-compatible alias for the phone number value.",
    )
    type: Optional[str] = Field(
        default=None,
        description="Phone type such as mobile, work, home, or internal.",
    )


class EmailInput(BaseModel):
    """Typed input for an email entry."""

    model_config = ConfigDict(extra="forbid")

    address: Optional[str] = Field(
        default=None,
        description="Email address value.",
    )
    value: Optional[str] = Field(
        default=None,
        description="Backward-compatible alias for the email address value.",
    )
    type: Optional[str] = Field(
        default=None,
        description="Email type such as work, home, or other.",
    )


class OrganizationInput(BaseModel):
    """Typed input for an organization entry."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, description="Organization name.")
    title: Optional[str] = Field(default=None, description="Job title.")
    department: Optional[str] = Field(default=None, description="Department name.")
    jobDescription: Optional[str] = Field(
        default=None,
        description="Optional organization job description.",
        validation_alias=AliasChoices("jobDescription", "description"),
    )
    type: Optional[str] = Field(
        default=None,
        description="Organization type such as work or school.",
    )


class ContactInput(BaseModel):
    """Typed batch-create input for a contact."""

    model_config = ConfigDict(extra="forbid")

    given_name: Optional[str] = None
    family_name: Optional[str] = None
    phones: Optional[List[PhoneInput]] = None
    emails: Optional[List[EmailInput]] = None
    organizations: Optional[List[OrganizationInput]] = None
    notes: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    organization: Optional[str] = None
    job_title: Optional[str] = None


class ContactUpdateInput(ContactInput):
    """Typed batch-update input for a contact."""

    contact_id: str = Field(
        description='Contact ID like "c123" or full resource name like "people/c123".'
    )


def _coerce_phone_input(phone: Any) -> PhoneInput:
    if isinstance(phone, PhoneInput):
        return phone
    if isinstance(phone, dict):
        phone = dict(phone)
        if not phone.get("type") and phone.get("label"):
            phone["type"] = phone["label"]
        phone.pop("label", None)
    return PhoneInput.model_validate(phone)


def _coerce_email_input(email: Any) -> EmailInput:
    if isinstance(email, EmailInput):
        return email
    if isinstance(email, dict):
        email = dict(email)
        if not email.get("type") and email.get("label"):
            email["type"] = email["label"]
        email.pop("label", None)
    return EmailInput.model_validate(email)


def _coerce_organization_input(org: Any) -> OrganizationInput:
    if isinstance(org, OrganizationInput):
        return org
    return OrganizationInput.model_validate(org)


def _coerce_contact_input(contact: Any) -> ContactInput:
    if isinstance(contact, ContactInput):
        return contact
    return ContactInput.model_validate(contact)


def _coerce_contact_update_input(update: Any) -> ContactUpdateInput:
    if isinstance(update, ContactUpdateInput):
        return update
    return ContactUpdateInput.model_validate(update)


def _normalize_phone(value: str) -> str:
    """Return a normalized phone string for deduplication. Strips non-digit chars except leading +."""
    stripped = re.sub(r"[^\d+]", "", value)
    return stripped.lower()


def _normalize_email(value: str) -> str:
    """Return a normalized email string for deduplication."""
    return value.strip().lower()


def _format_phone_line(phone: Dict[str, Any]) -> str:
    """
    Format a single phone entry into a display line.

    Returns e.g. '+79270000000 (mobile)' or '250 (Internal)'.
    """
    value = phone.get("value", "")
    phone_type = phone.get("type", "")
    formatted_type = phone.get("formattedType", "")

    if phone_type == "internal":
        label = "Internal"
    elif formatted_type:
        label = formatted_type
    elif phone_type:
        label = phone_type
    else:
        label = ""

    if label:
        return f"{value} ({label})"
    return value


def _format_email_line(email: Dict[str, Any]) -> str:
    """
    Format a single email entry into a display line.

    Returns e.g. 'user@example.com (work)'.
    """
    value = email.get("value", "")
    email_type = email.get("type", "")
    formatted_type = email.get("formattedType", "")

    label = formatted_type or email_type or ""
    if label:
        return f"{value} ({label})"
    return value


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

    # Email addresses — each on its own line with type label
    emails = person.get("emailAddresses", [])
    if emails:
        valid_emails = [e for e in emails if e.get("value")]
        if valid_emails:
            if len(valid_emails) == 1:
                lines.append(f"Email: {_format_email_line(valid_emails[0])}")
            else:
                lines.append("Emails:")
                for e in valid_emails:
                    lines.append(f"  - {_format_email_line(e)}")

    # Phone numbers — each on its own line with type label
    phones = person.get("phoneNumbers", [])
    if phones:
        valid_phones = [p for p in phones if p.get("value")]
        if valid_phones:
            if len(valid_phones) == 1:
                lines.append(f"Phone: {_format_phone_line(valid_phones[0])}")
            else:
                lines.append("Phones:")
                for p in valid_phones:
                    lines.append(f"  - {_format_phone_line(p)}")

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
    # New multi-value params
    phones: Optional[List[PhoneInput]] = None,
    emails: Optional[List[EmailInput]] = None,
    organizations: Optional[List[OrganizationInput]] = None,
    notes: Optional[str] = None,
    address: Optional[str] = None,
    # Deprecated single-value aliases
    email: Optional[str] = None,
    phone: Optional[str] = None,
    organization: Optional[str] = None,
    job_title: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a Person resource body for create/update operations.

    Accepts both new list-based params (phones, emails, organizations) and
    deprecated single-value aliases (phone, email, organization, job_title).

    Args:
        given_name: First name.
        family_name: Last name.
        phones: List of PhoneInput items {number, value?, type?}.
            Supported types: mobile, work, home, main, workMobile, internal, other, etc.
            Use type="internal" for PBX/ATS short numbers (e.g. 250, 301).
        emails: List of EmailInput items {address, value?, type?}.
        organizations: List of OrganizationInput items {name?, title?, department?, jobDescription?, type?}.
        notes: Additional notes/biography.
        address: Street address.
        email: [DEPRECATED] Single email address. Use emails instead.
        phone: [DEPRECATED] Single phone number. Use phones instead.
        organization: [DEPRECATED] Company/organization name. Use organizations instead.
        job_title: [DEPRECATED] Job title. Use organizations instead.

    Returns:
        Person resource body dictionary.
    """
    body: Dict[str, Any] = {}

    if phones is not None:
        phones = [_coerce_phone_input(phone) for phone in phones]
    if emails is not None:
        emails = [_coerce_email_input(email_entry) for email_entry in emails]
    if organizations is not None:
        organizations = [_coerce_organization_input(org) for org in organizations]

    if given_name or family_name:
        body["names"] = [
            {
                "givenName": given_name or "",
                "familyName": family_name or "",
            }
        ]

    # --- Emails ---
    if emails is not None and email is not None:
        warnings.warn(
            "Parameter 'email' ignored because 'emails' was provided",
            DeprecationWarning,
            stacklevel=3,
        )
    if emails is None and email is not None:
        warnings.warn(
            "Parameter 'email' is deprecated. Use 'emails=[{\"address\": ..., \"type\": ...}]' instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        emails = [EmailInput(address=email, type="other")]

    if emails is not None:
        email_entries = []
        for e in emails:
            entry: Dict[str, Any] = {"value": e.address or e.value or ""}
            if e.type:
                entry["type"] = e.type
            if entry["value"]:
                email_entries.append(entry)
        body["emailAddresses"] = email_entries

    # --- Phones ---
    if phones is not None and phone is not None:
        warnings.warn(
            "Parameter 'phone' ignored because 'phones' was provided",
            DeprecationWarning,
            stacklevel=3,
        )
    if phones is None and phone is not None:
        warnings.warn(
            "Parameter 'phone' is deprecated. Use 'phones=[{\"number\": ..., \"type\": ...}]' instead.",
            DeprecationWarning,
            stacklevel=3,
        )
        phones = [PhoneInput(number=phone, type="mobile")]

    if phones is not None:
        phone_entries = []
        for p in phones:
            number = p.number or p.value or ""
            if not number:
                continue
            entry = {"value": number}
            if p.type:
                entry["type"] = p.type
            phone_entries.append(entry)
        body["phoneNumbers"] = phone_entries

    # --- Organizations ---
    if organizations is not None and (
        organization is not None or job_title is not None
    ):
        ignored_params = []
        if organization is not None:
            ignored_params.append("'organization'")
        if job_title is not None:
            ignored_params.append("'job_title'")
        ignored = " and ".join(ignored_params)
        parameter_label = "Parameter" if len(ignored_params) == 1 else "Parameters"
        warnings.warn(
            f"{parameter_label} {ignored} ignored because 'organizations' was provided",
            DeprecationWarning,
            stacklevel=3,
        )
    if organizations is None and (organization is not None or job_title is not None):
        if organization is not None:
            warnings.warn(
                "Parameter 'organization' is deprecated. Use 'organizations=[{\"name\": ..., \"type\": ...}]' instead.",
                DeprecationWarning,
                stacklevel=3,
            )
        if job_title is not None:
            warnings.warn(
                "Parameter 'job_title' is deprecated. Use 'organizations=[{\"title\": ...}]' instead.",
                DeprecationWarning,
                stacklevel=3,
            )
        organizations = [OrganizationInput(name=organization, title=job_title)]

    if organizations is not None:
        org_entries = []
        for org in organizations:
            entry = {}
            if org.name:
                entry["name"] = org.name
            if org.title:
                entry["title"] = org.title
            if org.department:
                entry["department"] = org.department
            if org.jobDescription:
                entry["jobDescription"] = org.jobDescription
            if org.type:
                entry["type"] = org.type
            if entry:
                org_entries.append(entry)
        body["organizations"] = org_entries

    if notes:
        body["biographies"] = [{"value": notes, "contentType": "TEXT_PLAIN"}]

    if address:
        body["addresses"] = [{"formattedValue": address}]

    return body


def _merge_phones(
    existing: List[Dict[str, Any]],
    new_phones: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge phone lists according to mode.

    Args:
        existing: Current phoneNumbers from People API.
        new_phones: New phone entries (API format: {value, type?, ...}).
        mode: 'merge', 'replace', or 'remove'.

    Returns:
        Merged phone list.
    """

    def phone_key(phone: Dict[str, Any]) -> str:
        return _normalize_phone(phone.get("canonicalForm") or phone.get("value", ""))

    if mode == "replace":
        return new_phones
    if mode == "remove":
        remove_normalized = {
            _normalize_phone(p["value"]) for p in new_phones if p.get("value")
        }
        return [
            p
            for p in existing
            if _normalize_phone(p.get("value", "")) not in remove_normalized
        ]
    # merge: add new entries that don't already exist (dedup by canonicalForm or normalized value)
    result = list(existing)
    existing_keys = set()
    for p in existing:
        existing_keys.add(phone_key(p))
    for p in new_phones:
        canonical = phone_key(p)
        if canonical not in existing_keys:
            result.append(p)
            existing_keys.add(canonical)
    return result


def _merge_emails(
    existing: List[Dict[str, Any]],
    new_emails: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge email lists according to mode.

    Args:
        existing: Current emailAddresses from People API.
        new_emails: New email entries (API format: {value, type?, ...}).
        mode: 'merge', 'replace', or 'remove'.

    Returns:
        Merged email list.
    """
    if mode == "replace":
        return new_emails
    if mode == "remove":
        remove_normalized = {
            _normalize_email(e["value"]) for e in new_emails if e.get("value")
        }
        return [
            e
            for e in existing
            if _normalize_email(e.get("value", "")) not in remove_normalized
        ]
    # merge: add new entries not already present (dedup by lowercased address)
    result = list(existing)
    existing_keys = {_normalize_email(e.get("value", "")) for e in existing}
    for e in new_emails:
        normalized = _normalize_email(e.get("value", ""))
        if normalized not in existing_keys:
            result.append(e)
            existing_keys.add(normalized)
    return result


def _merge_organizations(
    existing: List[Dict[str, Any]],
    new_orgs: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge organization lists according to mode.

    Args:
        existing: Current organizations from People API.
        new_orgs: New org entries (API format).
        mode: 'merge', 'replace', or 'remove'.

    Returns:
        Merged org list.
    """

    def organization_key(org: Dict[str, Any]) -> tuple[str, str, str, str, str]:
        job_description = org.get("jobDescription") or org.get("description") or ""
        return (
            ((org.get("name") or "").strip().lower()),
            ((org.get("title") or "").strip().lower()),
            ((org.get("department") or "").strip().lower()),
            job_description.strip().lower(),
            ((org.get("type") or "").strip().lower()),
        )

    if mode == "replace":
        return new_orgs
    if mode == "remove":
        remove_keys = {organization_key(org) for org in new_orgs}
        return [org for org in existing if organization_key(org) not in remove_keys]
    # merge: add orgs whose identifying fields don't already exist
    result = list(existing)
    existing_keys = {organization_key(org) for org in existing}
    for org in new_orgs:
        org_key = organization_key(org)
        if org_key not in existing_keys:
            result.append(org)
            existing_keys.add(org_key)
    return result


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
    action: Literal["create", "update", "delete"],
    contact_id: Optional[str] = None,
    given_name: Optional[str] = None,
    family_name: Optional[str] = None,
    # New multi-value params
    phones: Optional[List[PhoneInput]] = None,
    emails: Optional[List[EmailInput]] = None,
    organizations: Optional[List[OrganizationInput]] = None,
    notes: Optional[str] = None,
    address: Optional[str] = None,
    # Merge modes for update action
    phones_mode: Literal["merge", "replace", "remove"] = "merge",
    emails_mode: Literal["merge", "replace", "remove"] = "merge",
    organizations_mode: Literal["merge", "replace", "remove"] = "merge",
    # Deprecated single-value aliases
    phone: Optional[str] = None,
    email: Optional[str] = None,
    organization: Optional[str] = None,
    job_title: Optional[str] = None,
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
        phones (Optional[List[Dict]]): List of phone dicts {number, type?}.
            Supported types: mobile, work, home, main, workMobile, internal, other, etc.
            Use type="internal" for internal PBX/ATS short numbers (e.g. 250, 301) — stored
            as a standalone number without + prefix, displayed as "Internal: 250".
        emails (Optional[List[Dict]]): List of email dicts {address, type?}.
        organizations (Optional[List[Dict]]): List of org dicts {name?, title?, department?, jobDescription?, type?}.
        notes (Optional[str]): Additional notes (for create/update).
        address (Optional[str]): Street address (for create/update).
        phones_mode (str): How to update phones on "update": "merge" (default), "replace", or "remove".
            merge = read-modify-write with dedup by canonicalForm/normalized value.
            replace = overwrite all phones with provided list.
            remove = delete phones matching provided numbers.
        emails_mode (str): How to update emails on "update": "merge" (default), "replace", or "remove".
        organizations_mode (str): How to update orgs on "update": "merge" (default), "replace", or "remove".
        phone (Optional[str]): [DEPRECATED] Single phone number. Use phones=[{"number":..., "type":"mobile"}].
        email (Optional[str]): [DEPRECATED] Email address. Use emails=[{"address":..., "type":"other"}].
        organization (Optional[str]): [DEPRECATED] Company name. Use organizations=[{"name":...}].
        job_title (Optional[str]): [DEPRECATED] Job title. Use organizations=[{"title":...}].

    Returns:
        str: Result of the action performed.
    """
    action = action.lower().strip()
    if action not in ("create", "update", "delete"):
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'create', 'update', or 'delete'."
        )

    for mode_name, mode_val in [
        ("phones_mode", phones_mode),
        ("emails_mode", emails_mode),
        ("organizations_mode", organizations_mode),
    ]:
        if mode_val not in ("merge", "replace", "remove"):
            raise UserInputError(
                f"Invalid {mode_name} '{mode_val}'. Must be 'merge', 'replace', or 'remove'."
            )

    logger.info(
        f"[manage_contact] Invoked. Action: '{action}', Email: '{user_google_email}'"
    )

    if action == "create":
        body = _build_person_body(
            given_name=given_name,
            family_name=family_name,
            phones=phones,
            emails=emails,
            organizations=organizations,
            notes=notes,
            address=address,
            phone=phone,
            email=email,
            organization=organization,
            job_title=job_title,
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
        # Retry loop for etag conflicts (412 Precondition Failed)
        max_retries = 3
        for attempt in range(max_retries):
            # Fetch the contact to get current state and etag
            current = await asyncio.to_thread(
                service.people()
                .get(resourceName=resource_name, personFields=DETAILED_PERSON_FIELDS)
                .execute
            )

            etag = current.get("etag")
            if not etag:
                raise Exception("Unable to get contact etag for update.")

            # Build body from provided params (returns new values only)
            new_body = _build_person_body(
                given_name=given_name,
                family_name=family_name,
                phones=phones,
                emails=emails,
                organizations=organizations,
                notes=notes,
                address=address,
                phone=phone,
                email=email,
                organization=organization,
                job_title=job_title,
            )

            if not new_body:
                raise UserInputError(
                    "At least one field (name, email, phone, etc.) must be provided."
                )

            # Apply merge modes for array fields
            merged_body: Dict[str, Any] = dict(new_body)

            if "phoneNumbers" in new_body:
                merged_body["phoneNumbers"] = _merge_phones(
                    current.get("phoneNumbers", []),
                    new_body["phoneNumbers"],
                    phones_mode,
                )

            if "emailAddresses" in new_body:
                merged_body["emailAddresses"] = _merge_emails(
                    current.get("emailAddresses", []),
                    new_body["emailAddresses"],
                    emails_mode,
                )

            if "organizations" in new_body:
                merged_body["organizations"] = _merge_organizations(
                    current.get("organizations", []),
                    new_body["organizations"],
                    organizations_mode,
                )

            merged_body["etag"] = etag

            update_person_fields = []
            if "names" in merged_body:
                update_person_fields.append("names")
            if "emailAddresses" in merged_body:
                update_person_fields.append("emailAddresses")
            if "phoneNumbers" in merged_body:
                update_person_fields.append("phoneNumbers")
            if "organizations" in merged_body:
                update_person_fields.append("organizations")
            if "biographies" in merged_body:
                update_person_fields.append("biographies")
            if "addresses" in merged_body:
                update_person_fields.append("addresses")

            try:
                result = await asyncio.to_thread(
                    service.people()
                    .updateContact(
                        resourceName=resource_name,
                        body=merged_body,
                        updatePersonFields=",".join(update_person_fields),
                        personFields=DETAILED_PERSON_FIELDS,
                    )
                    .execute
                )

                response = f"Contact Updated for {user_google_email}:\n\n"
                response += _format_contact(result, detailed=True)

                logger.info(f"Updated contact {resource_name} for {user_google_email}")
                return response

            except HttpError as e:
                if e.resp.status == 412 and attempt < max_retries - 1:
                    logger.warning(
                        f"[manage_contact] etag conflict on attempt {attempt + 1}, retrying..."
                    )
                    continue
                raise

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
    action: Literal["create", "update", "delete"],
    contacts: Optional[List[ContactInput]] = None,
    updates: Optional[List[ContactUpdateInput]] = None,
    contact_ids: Optional[StringList] = None,
    field: Optional[
        Literal[
            "names",
            "phoneNumbers",
            "emailAddresses",
            "organizations",
            "biographies",
            "addresses",
        ]
    ] = None,
) -> str:
    """
    Batch create, update, or delete contacts. Consolidated tool replacing
    batch_create_contacts, batch_update_contacts, and batch_delete_contacts.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): The action to perform: "create", "update", or "delete".
        contacts (Optional[List[Dict]]): List of contact dicts for "create" action.
            Each dict may contain: given_name, family_name, phones, emails, organizations,
            notes, address. Deprecated: phone, email, organization, job_title.
        updates (Optional[List[Dict]]): List of update dicts for "update" action.
            Each dict must contain contact_id and may contain the same fields as contacts.
        contact_ids (Optional[List[str]]): List of contact IDs for "delete" action.
        field (str): For "update" action — the single People API field to update across
            all contacts in this batch. Required. Must be one of: names, phoneNumbers,
            emailAddresses, organizations, biographies, addresses.
            Using a single field per batch call prevents unintentional data loss from
            a union updateMask overwriting unrelated fields.

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

    if contacts is not None:
        contacts = [_coerce_contact_input(contact) for contact in contacts]
    if updates is not None:
        updates = [_coerce_contact_update_input(update) for update in updates]

    if action == "create":
        if not contacts:
            raise UserInputError("contacts parameter is required for 'create' action.")

        if len(contacts) > 200:
            raise UserInputError("Maximum 200 contacts can be created in a batch.")

        contact_bodies = []
        for contact in contacts:
            body = _build_person_body(
                given_name=contact.given_name,
                family_name=contact.family_name,
                phones=contact.phones,
                emails=contact.emails,
                organizations=contact.organizations,
                notes=contact.notes,
                address=contact.address,
                # deprecated aliases
                phone=contact.phone,
                email=contact.email,
                organization=contact.organization,
                job_title=contact.job_title,
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

        # Validate field param — required to avoid multi-field mask issues
        valid_fields = {
            "names",
            "phoneNumbers",
            "emailAddresses",
            "organizations",
            "biographies",
            "addresses",
        }
        if not field:
            raise UserInputError(
                "field parameter is required for batch 'update' action. "
                "Must be one of: names, phoneNumbers, emailAddresses, organizations, "
                "biographies, addresses. Use a single field per batch call to avoid "
                "unintentional data loss from a union updateMask."
            )
        if field not in valid_fields:
            raise UserInputError(
                f"Invalid field '{field}'. Must be one of: {', '.join(sorted(valid_fields))}."
            )

        # Collect resource names for batch etag fetch
        resource_names = []
        for update in updates:
            cid = update.contact_id
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

        # Map field name to body key produced by _build_person_body
        field_to_body_key = {
            "names": "names",
            "phoneNumbers": "phoneNumbers",
            "emailAddresses": "emailAddresses",
            "organizations": "organizations",
            "biographies": "biographies",
            "addresses": "addresses",
        }
        body_key = field_to_body_key[field]

        # Build contacts map (Dict[resourceName, Person]) — required by batchUpdateContacts API
        contacts_map: Dict[str, Any] = {}

        for update in updates:
            cid = update.contact_id
            if not cid.startswith("people/"):
                cid = f"people/{cid}"

            etag = etags.get(cid)
            if not etag:
                logger.warning(f"No etag found for {cid}, skipping")
                continue

            body = _build_person_body(
                given_name=update.given_name,
                family_name=update.family_name,
                phones=update.phones,
                emails=update.emails,
                organizations=update.organizations,
                notes=update.notes,
                address=update.address,
                # deprecated aliases
                phone=update.phone,
                email=update.email,
                organization=update.organization,
                job_title=update.job_title,
            )

            if body_key not in body:
                logger.warning(
                    f"Field '{field}' (key '{body_key}') not present in update for {cid}, skipping"
                )
                continue

            person_body = {
                "etag": etag,
                body_key: body[body_key],
            }
            contacts_map[cid] = person_body

        if not contacts_map:
            raise UserInputError("No valid update data provided.")

        batch_body = {
            "contacts": contacts_map,
            "updateMask": field,
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
    add_contact_ids: Optional[StringList] = None,
    remove_contact_ids: Optional[StringList] = None,
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
