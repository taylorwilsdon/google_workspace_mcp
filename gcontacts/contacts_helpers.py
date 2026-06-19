"""
Google Contacts Helper Functions

This module provides pure utility functions for parsing, normalizing,
formatting, and merging Google Contacts (People API) data. These helpers
operate purely on plain dicts/strings and are kept separate from the MCP
tool definitions and Pydantic input models in contacts_tools.py.
"""

import datetime
import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _parse_birthday(s: str) -> Dict[str, Any]:
    """Parse 'YYYY-MM-DD' or 'MM-DD' into a People API birthday object."""
    parts = s.strip().split("-")
    try:
        if len(parts) == 3:
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        elif len(parts) == 2:
            year, month, day = None, int(parts[0]), int(parts[1])
        else:
            raise ValueError
    except ValueError:
        raise ValueError(
            f"Invalid birthday format '{s}'. Use 'YYYY-MM-DD' or 'MM-DD'."
        ) from None

    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError(
            f"Invalid birthday '{s}': month must be 1-12 and day must be 1-31."
        )

    # Reject impossible day/month combinations (e.g. Feb 30, Apr 31) and bad
    # years before they reach the People API. For year-less dates use a leap
    # year (2000) so Feb 29 is accepted.
    try:
        datetime.date(year if year is not None else 2000, month, day)
    except ValueError:
        raise ValueError(f"Invalid birthday '{s}': not a real calendar date.") from None

    date = {"month": month, "day": day}
    if year is not None:
        date["year"] = year
    return {"date": date}


def _normalize_phone(value: str) -> str:
    """Return a normalized phone string for deduplication. Strips non-digit chars except leading +."""
    stripped = re.sub(r"[^\d+]", "", value)
    return stripped.lower()


def _normalize_email(value: str) -> str:
    """Return a normalized email string for deduplication."""
    return value.strip().lower()


def _normalize_nickname(value: str) -> str:
    """Return a normalized nickname string for deduplication."""
    return value.strip().lower()


def _normalize_url(value: str) -> str:
    """Return a normalized URL string for deduplication. Lowercases scheme/host, strips trailing slash."""
    stripped = value.strip().lower()
    if stripped.endswith("/"):
        stripped = stripped[:-1]
    return stripped


def _normalize_user_defined_key(value: str) -> str:
    """Return a normalized userDefined key for deduplication."""
    return value.strip().lower()


def _normalize_relation_person(value: str) -> str:
    """Return a normalized relation person name for deduplication."""
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

    # Nicknames — bilingual alternative names
    nicknames = person.get("nicknames", [])
    if nicknames:
        nickname_values = [n.get("value", "") for n in nicknames if n.get("value")]
        if nickname_values:
            lines.append(f"Nicknames: {', '.join(nickname_values)}")

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

        # User-defined custom fields
        user_defined = person.get("userDefined", [])
        if user_defined:
            valid_ud = [ud for ud in user_defined if ud.get("key") and ud.get("value")]
            if valid_ud:
                lines.append("Custom Fields:")
                for ud in valid_ud:
                    lines.append(f"  - {ud['key']}: {ud['value']}")

        # Relations (spouse, family, etc.)
        relations = person.get("relations", [])
        if relations:
            valid_rels = [r for r in relations if r.get("person")]
            if valid_rels:
                lines.append("Relations:")
                for r in valid_rels:
                    rel_type = r.get("formattedType") or r.get("type") or ""
                    if rel_type:
                        lines.append(f"  - {r['person']} ({rel_type})")
                    else:
                        lines.append(f"  - {r['person']}")

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


def _merge_nicknames(
    existing: List[Dict[str, Any]],
    new_nicknames: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge nickname lists according to mode. Dedup by lowercased value.
    """
    if mode == "replace":
        return new_nicknames
    if mode == "remove":
        remove_normalized = {
            _normalize_nickname(n["value"]) for n in new_nicknames if n.get("value")
        }
        return [
            n
            for n in existing
            if _normalize_nickname(n.get("value", "")) not in remove_normalized
        ]
    # merge: add new entries not already present
    result = list(existing)
    existing_keys = {_normalize_nickname(n.get("value", "")) for n in existing}
    for n in new_nicknames:
        normalized = _normalize_nickname(n.get("value", ""))
        if normalized not in existing_keys:
            result.append(n)
            existing_keys.add(normalized)
    return result


def _merge_urls(
    existing: List[Dict[str, Any]],
    new_urls: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge URL lists according to mode. Dedup by normalized URL (lowercased, trailing slash stripped).
    """
    if mode == "replace":
        return new_urls
    if mode == "remove":
        remove_normalized = {
            _normalize_url(u["value"]) for u in new_urls if u.get("value")
        }
        return [
            u
            for u in existing
            if _normalize_url(u.get("value", "")) not in remove_normalized
        ]
    # merge: add new entries not already present
    result = list(existing)
    existing_keys = {_normalize_url(u.get("value", "")) for u in existing}
    for u in new_urls:
        normalized = _normalize_url(u.get("value", ""))
        if normalized not in existing_keys:
            result.append(u)
            existing_keys.add(normalized)
    return result


def _merge_user_defined(
    existing: List[Dict[str, Any]],
    new_ud: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge userDefined custom-field lists according to mode. Dedup by normalized key.
    On merge, new value overrides existing for the same key.
    """
    if mode == "replace":
        return new_ud
    if mode == "remove":
        remove_keys = {
            _normalize_user_defined_key(ud["key"]) for ud in new_ud if ud.get("key")
        }
        return [
            ud
            for ud in existing
            if _normalize_user_defined_key(ud.get("key", "")) not in remove_keys
        ]
    # merge: new value overrides existing for matching key; new keys appended
    new_by_key = {_normalize_user_defined_key(ud.get("key", "")): ud for ud in new_ud}
    result = []
    seen_keys = set()
    for ud in existing:
        norm_key = _normalize_user_defined_key(ud.get("key", ""))
        if norm_key in new_by_key:
            result.append(new_by_key[norm_key])
            seen_keys.add(norm_key)
        else:
            result.append(ud)
    for norm_key, ud in new_by_key.items():
        if norm_key not in seen_keys:
            result.append(ud)
    return result


def _merge_relations(
    existing: List[Dict[str, Any]],
    new_relations: List[Dict[str, Any]],
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Merge relations lists according to mode. Dedup by (normalized person, normalized type) tuple.
    """

    def relation_key(rel: Dict[str, Any]) -> tuple[str, str]:
        label = rel.get("formattedType") or rel.get("type") or ""
        return (
            _normalize_relation_person(rel.get("person", "")),
            label.strip().lower(),
        )

    if mode == "replace":
        return new_relations
    if mode == "remove":
        remove_keys = {relation_key(r) for r in new_relations}
        return [r for r in existing if relation_key(r) not in remove_keys]
    # merge: add new entries not already present
    result = list(existing)
    existing_keys = {relation_key(r) for r in existing}
    for r in new_relations:
        rk = relation_key(r)
        if rk not in existing_keys:
            result.append(r)
            existing_keys.add(rk)
    return result
