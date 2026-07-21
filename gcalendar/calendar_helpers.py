"""
Google Calendar Helper Functions

This module provides utility functions for formatting Google Calendar
event data for display.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_meeting_link(item: Dict[str, Any]) -> str:
    """Extract video meeting link from event conference data or hangoutLink."""
    conference_data = item.get("conferenceData")
    if conference_data and "entryPoints" in conference_data:
        for entry_point in conference_data["entryPoints"]:
            if entry_point.get("entryPointType") == "video":
                uri = entry_point.get("uri", "")
                if uri:
                    return uri
    hangout_link = item.get("hangoutLink", "")
    if hangout_link:
        return hangout_link
    return ""


def _format_attendee_details(
    attendees: List[Dict[str, Any]], indent: str = "  "
) -> str:
    """
      Format attendee details including response status, organizer, and optional flags.

      Example output format:
      "  user@example.com: accepted
    manager@example.com: declined (organizer)
    optional-person@example.com: tentative (optional)"

      Args:
          attendees: List of attendee dictionaries from Google Calendar API
          indent: Indentation to use for newline-separated attendees (default: "  ")

      Returns:
          Formatted string with attendee details, or "None" if no attendees
    """
    if not attendees:
        return "None"

    attendee_details_list = []
    for a in attendees:
        email = a.get("email", "unknown")
        response_status = a.get("responseStatus", "unknown")
        optional = a.get("optional", False)
        organizer = a.get("organizer", False)

        detail_parts = [f"{email}: {response_status}"]
        if organizer:
            detail_parts.append("(organizer)")
        if optional:
            detail_parts.append("(optional)")

        attendee_details_list.append(" ".join(detail_parts))

    return f"\n{indent}".join(attendee_details_list)


def _format_attachment_details(
    attachments: List[Dict[str, Any]], indent: str = "  "
) -> str:
    """
    Format attachment details including file information.


    Args:
        attachments: List of attachment dictionaries from Google Calendar API
        indent: Indentation to use for newline-separated attachments (default: "  ")

    Returns:
        Formatted string with attachment details, or "None" if no attachments
    """
    if not attachments:
        return "None"

    attachment_details_list = []
    for att in attachments:
        title = att.get("title", "Untitled")
        file_url = att.get("fileUrl", "No URL")
        file_id = att.get("fileId", "No ID")
        mime_type = att.get("mimeType", "Unknown")

        attachment_info = (
            f"{title}\n"
            f"{indent}File URL: {file_url}\n"
            f"{indent}File ID: {file_id}\n"
            f"{indent}MIME Type: {mime_type}"
        )
        attachment_details_list.append(attachment_info)

    return f"\n{indent}".join(attachment_details_list)


def _format_reminder_overrides(overrides: List[Dict[str, Any]]) -> str:
    """Format a list of reminder override objects (method + minutes) for display.

    Each override looks like ``{"method": "popup", "minutes": 10}``. Returns a
    comma-separated summary such as ``"popup 10 min before, email 1440 min before"``,
    or ``"None"`` if there are no overrides.
    """
    if not overrides:
        return "None"

    parts = []
    for override in overrides:
        method = override.get("method", "unknown")
        minutes = override.get("minutes")
        if minutes is None:
            parts.append(method)
        else:
            parts.append(f"{method} {minutes} min before")
    return ", ".join(parts)


def _format_reminders(reminders: Optional[Dict[str, Any]]) -> str:
    """Format an event's ``reminders`` object (useDefault + overrides) for display.

    The Google Calendar API returns reminders as
    ``{"useDefault": true}`` or ``{"useDefault": false, "overrides": [...]}``.
    When ``useDefault`` is true the calendar's default reminders apply and any
    overrides are ignored; when it is false the overrides (if any) are the
    event's reminders.

    Returns a human-readable summary, or ``"None"`` if no reminders data is present.
    """
    if not reminders:
        return "None"

    use_default = reminders.get("useDefault", False)
    overrides = reminders.get("overrides", [])

    if use_default:
        return "Using calendar default reminders"
    if overrides:
        return _format_reminder_overrides(overrides)
    return "No reminders"


def _format_person(person: Optional[Dict[str, Any]]) -> Optional[str]:
    """Format a Google Calendar person dict (creator or organizer) for display."""
    if not person:
        return None
    name = (person.get("displayName") or "").strip()
    email = (person.get("email") or "").strip()
    if name and email:
        return f"{name} <{email}>"
    if name:
        return name
    if email:
        return f"<{email}>"
    return None
