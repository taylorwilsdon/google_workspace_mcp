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


def _format_event_detail_lines(
    item: Dict[str, Any],
    prefix: str,
    indent: str,
    include_attachments: bool = False,
) -> str:
    """
    Format the shared body of a detailed event, one field per line.

    Both detailed output paths in `get_events` — the single-event lookup and the
    ranged listing — emit the same fields and differ only in line prefix and
    continuation indent. Formatting them here is what keeps the two in step.

    Args:
        item: Event resource from the Google Calendar API
        prefix: Prefix for each field line (e.g. "- " or "  ")
        indent: Continuation indent for multi-line values (attendees, attachments)
        include_attachments: Whether to append attachment details

    Returns:
        Newline-terminated block of detail lines
    """
    lines = [
        f"{prefix}Description: {item.get('description', 'No Description')}",
        f"{prefix}Location: {item.get('location', 'No Location')}",
        f"{prefix}Color ID: {item.get('colorId', 'None')}",
    ]

    recurring_event_id = item.get("recurringEventId")
    if recurring_event_id:
        lines.append(f"{prefix}Recurring Event ID: {recurring_event_id}")

    # eventType and status are omitted at their API defaults, so an ordinary
    # one-off meeting stays as compact as it was before these fields existed.
    event_type = item.get("eventType")
    if event_type and event_type != "default":
        lines.append(f"{prefix}Event Type: {event_type}")

    status = item.get("status")
    if status and status != "confirmed":
        lines.append(f"{prefix}Status: {status}")

    creator_str = _format_person(item.get("creator"))
    if creator_str:
        lines.append(f"{prefix}Creator: {creator_str}")

    organizer_str = _format_person(item.get("organizer"))
    if organizer_str:
        lines.append(f"{prefix}Organizer: {organizer_str}")

    meeting_link = _get_meeting_link(item)
    if meeting_link:
        lines.append(f"{prefix}Meeting Link: {meeting_link}")

    attendees = item.get("attendees", [])
    attendee_emails = (
        ", ".join([a.get("email", "") for a in attendees]) if attendees else "None"
    )
    lines.append(f"{prefix}Attendees: {attendee_emails}")
    lines.append(
        f"{prefix}Attendee Details: {_format_attendee_details(attendees, indent=indent)}"
    )

    if include_attachments:
        attachment_details = _format_attachment_details(
            item.get("attachments", []), indent=indent
        )
        lines.append(f"{prefix}Attachments: {attachment_details}")

    return "".join(f"{line}\n" for line in lines)
