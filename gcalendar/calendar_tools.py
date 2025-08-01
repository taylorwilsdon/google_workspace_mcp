"""
Google Calendar MCP Tools

This module provides MCP tools for interacting with Google Calendar API.
"""

import datetime
import logging
import asyncio
import re
import uuid
from typing import List, Optional, Dict, Any

from googleapiclient.errors import HttpError
from googleapiclient.discovery import build

from auth.service_decorator import require_google_service
from core.utils import handle_http_errors

from core.server import server


# Configure module logger
logger = logging.getLogger(__name__)


def _preserve_existing_fields(event_body: Dict[str, Any], existing_event: Dict[str, Any], field_mappings: Dict[str, Any]) -> None:
    """
    Helper function to preserve existing event fields when not explicitly provided.

    Args:
        event_body: The event body being built for the API call
        existing_event: The existing event data from the API
        field_mappings: Dict mapping field names to their new values (None means preserve existing)
    """
    for field_name, new_value in field_mappings.items():
        if new_value is None and field_name in existing_event:
            event_body[field_name] = existing_event[field_name]
            logger.info(f"[modify_event] Preserving existing {field_name}")
        elif new_value is not None:
            event_body[field_name] = new_value


# Helper function to ensure time strings for API calls are correctly formatted
def _correct_time_format_for_api(
    time_str: Optional[str], param_name: str
) -> Optional[str]:
    if not time_str:
        return None

    logger.info(
        f"_correct_time_format_for_api: Processing {param_name} with value '{time_str}'"
    )

    # Handle date-only format (YYYY-MM-DD)
    if len(time_str) == 10 and time_str.count("-") == 2:
        try:
            # Validate it's a proper date
            datetime.datetime.strptime(time_str, "%Y-%m-%d")
            # For date-only, append T00:00:00Z to make it RFC3339 compliant
            formatted = f"{time_str}T00:00:00Z"
            logger.info(
                f"Formatting date-only {param_name} '{time_str}' to RFC3339: '{formatted}'"
            )
            return formatted
        except ValueError:
            logger.warning(
                f"{param_name} '{time_str}' looks like a date but is not valid YYYY-MM-DD. Using as is."
            )
            return time_str

    # Specifically address YYYY-MM-DDTHH:MM:SS by appending 'Z'
    if (
        len(time_str) == 19
        and time_str[10] == "T"
        and time_str.count(":") == 2
        and not (
            time_str.endswith("Z") or ("+" in time_str[10:]) or ("-" in time_str[10:])
        )
    ):
        try:
            # Validate the format before appending 'Z'
            datetime.datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%S")
            logger.info(
                f"Formatting {param_name} '{time_str}' by appending 'Z' for UTC."
            )
            return time_str + "Z"
        except ValueError:
            logger.warning(
                f"{param_name} '{time_str}' looks like it needs 'Z' but is not valid YYYY-MM-DDTHH:MM:SS. Using as is."
            )
            return time_str

    # If it already has timezone info or doesn't match our patterns, return as is
    logger.info(f"{param_name} '{time_str}' doesn't need formatting, using as is.")
    return time_str


@server.tool()
@handle_http_errors("list_calendars", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def list_calendars(service, user_google_email: str) -> str:
    """
    Retrieves a list of calendars accessible to the authenticated user.

    Args:
        user_google_email (str): The user's Google email address. Required.

    Returns:
        str: A formatted list of the user's calendars (summary, ID, primary status).
    """
    logger.info(f"[list_calendars] Invoked. Email: '{user_google_email}'")

    calendar_list_response = await asyncio.to_thread(
        lambda: service.calendarList().list().execute()
    )
    items = calendar_list_response.get("items", [])
    if not items:
        return f"No calendars found for {user_google_email}."

    calendars_summary_list = [
        f"- \"{cal.get('summary', 'No Summary')}\"{' (Primary)' if cal.get('primary') else ''} (ID: {cal['id']})"
        for cal in items
    ]
    text_output = (
        f"Successfully listed {len(items)} calendars for {user_google_email}:\n"
        + "\n".join(calendars_summary_list)
    )
    logger.info(f"Successfully listed {len(items)} calendars for {user_google_email}.")
    return text_output


@server.tool()
@handle_http_errors("get_events", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def get_events(
    service,
    user_google_email: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 25,
    query: Optional[str] = None,
) -> str:
    """
    Retrieves a list of events from a specified Google Calendar within a given time range.
    You can also search for events by keyword by supplying the optional "query" param.

    Args:
        user_google_email (str): The user's Google email address. Required.
        calendar_id (str): The ID of the calendar to query. Use 'primary' for the user's primary calendar. Defaults to 'primary'. Calendar IDs can be obtained using `list_calendars`.
        time_min (Optional[str]): The start of the time range (inclusive) in RFC3339 format (e.g., '2024-05-12T10:00:00Z' or '2024-05-12'). If omitted, defaults to the current time.
        time_max (Optional[str]): The end of the time range (exclusive) in RFC3339 format. If omitted, events starting from `time_min` onwards are considered (up to `max_results`).
        max_results (int): The maximum number of events to return. Defaults to 25.
        query (Optional[str]): A keyword to search for within event fields (summary, description, location).

    Returns:
        str: A formatted list of events (summary, start and end times, link) within the specified range.
    """
    logger.info(
        f"[get_events] Raw time parameters - time_min: '{time_min}', time_max: '{time_max}', query: '{query}'"
    )

    # Ensure time_min and time_max are correctly formatted for the API
    formatted_time_min = _correct_time_format_for_api(time_min, "time_min")
    effective_time_min = formatted_time_min or (
        datetime.datetime.utcnow().isoformat() + "Z"
    )
    if time_min is None:
        logger.info(
            f"time_min not provided, defaulting to current UTC time: {effective_time_min}"
        )
    else:
        logger.info(
            f"time_min processing: original='{time_min}', formatted='{formatted_time_min}', effective='{effective_time_min}'"
        )

    effective_time_max = _correct_time_format_for_api(time_max, "time_max")
    if time_max:
        logger.info(
            f"time_max processing: original='{time_max}', formatted='{effective_time_max}'"
        )

    logger.info(
        f"[get_events] Final API parameters - calendarId: '{calendar_id}', timeMin: '{effective_time_min}', timeMax: '{effective_time_max}', maxResults: {max_results}, query: '{query}'"
    )

    # Build the request parameters dynamically
    request_params = {
        "calendarId": calendar_id,
        "timeMin": effective_time_min,
        "timeMax": effective_time_max,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
    }

    if query:
        request_params["q"] = query

    events_result = await asyncio.to_thread(
        lambda: service.events()
        .list(**request_params)
        .execute()
    )
    items = events_result.get("items", [])
    if not items:
        return f"No events found in calendar '{calendar_id}' for {user_google_email} for the specified time range."

    event_details_list = []
    for item in items:
        summary = item.get("summary", "No Title")
        start_time = item["start"].get("dateTime", item["start"].get("date"))
        end_time = item["end"].get("dateTime", item["end"].get("date"))
        link = item.get("htmlLink", "No Link")
        event_id = item.get("id", "No ID")
        # Include the start/end date, and event ID in the output so users can copy it for modify/delete operations
        event_details_list.append(
            f'- "{summary}" (Starts: {start_time}, Ends: {end_time}) ID: {event_id} | Link: {link}'
        )

    text_output = (
        f"Successfully retrieved {len(items)} events from calendar '{calendar_id}' for {user_google_email}:\n"
        + "\n".join(event_details_list)
    )
    logger.info(f"Successfully retrieved {len(items)} events for {user_google_email}.")
    return text_output


@server.tool()
@handle_http_errors("create_event", service_type="calendar")
@require_google_service("calendar", "calendar_events")
async def create_event(
    service,
    user_google_email: str,
    summary: str,
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    timezone: Optional[str] = None,
    attachments: Optional[List[str]] = None,
    add_google_meet: bool = False,
) -> str:
    """
    Creates a new event.

    Args:
        user_google_email (str): The user's Google email address. Required.
        summary (str): Event title.
        start_time (str): Start time (RFC3339, e.g., "2023-10-27T10:00:00-07:00" or "2023-10-27" for all-day).
        end_time (str): End time (RFC3339, e.g., "2023-10-27T11:00:00-07:00" or "2023-10-28" for all-day).
        calendar_id (str): Calendar ID (default: 'primary').
        description (Optional[str]): Event description.
        location (Optional[str]): Event location.
        attendees (Optional[List[str]]): Attendee email addresses.
        timezone (Optional[str]): Timezone (e.g., "America/New_York").
        attachments (Optional[List[str]]): List of Google Drive file URLs or IDs to attach to the event.
        add_google_meet (bool): Whether to add a Google Meet video conference to the event. Defaults to False.

    Returns:
        str: Confirmation message of the successful event creation with event link.
    """
    logger.info(
        f"[create_event] Invoked. Email: '{user_google_email}', Summary: {summary}"
    )
    logger.info(f"[create_event] Incoming attachments param: {attachments}")
    # If attachments value is a string, split by comma and strip whitespace
    if attachments and isinstance(attachments, str):
        attachments = [a.strip() for a in attachments.split(',') if a.strip()]
        logger.info(f"[create_event] Parsed attachments list from string: {attachments}")
    event_body: Dict[str, Any] = {
        "summary": summary,
        "start": (
            {"date": start_time}
            if "T" not in start_time
            else {"dateTime": start_time}
        ),
        "end": (
            {"date": end_time} if "T" not in end_time else {"dateTime": end_time}
        ),
    }
    if location:
        event_body["location"] = location
    if description:
        event_body["description"] = description
    if timezone:
        if "dateTime" in event_body["start"]:
            event_body["start"]["timeZone"] = timezone
        if "dateTime" in event_body["end"]:
            event_body["end"]["timeZone"] = timezone
    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    if add_google_meet:
        request_id = str(uuid.uuid4())
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": request_id,
                "conferenceSolutionKey": {
                    "type": "hangoutsMeet"
                }
            }
        }
        logger.info(f"[create_event] Adding Google Meet conference with request ID: {request_id}")

    if attachments:
        # Accept both file URLs and file IDs. If a URL, extract the fileId.
        event_body["attachments"] = []
        drive_service = None
        try:
            drive_service = service._http and build("drive", "v3", http=service._http)
        except Exception as e:
            logger.warning(f"Could not build Drive service for MIME type lookup: {e}")
        for att in attachments:
            file_id = None
            if att.startswith("https://"):
                # Match /d/<id>, /file/d/<id>, ?id=<id>
                match = re.search(r"(?:/d/|/file/d/|id=)([\w-]+)", att)
                file_id = match.group(1) if match else None
                logger.info(f"[create_event] Extracted file_id '{file_id}' from attachment URL '{att}'")
            else:
                file_id = att
                logger.info(f"[create_event] Using direct file_id '{file_id}' for attachment")
            if file_id:
                file_url = f"https://drive.google.com/open?id={file_id}"
                mime_type = "application/vnd.google-apps.drive-sdk"
                title = "Drive Attachment"
                # Try to get the actual MIME type and filename from Drive
                if drive_service:
                    try:
                        file_metadata = await asyncio.to_thread(
                            lambda: drive_service.files().get(fileId=file_id, fields="mimeType,name").execute()
                        )
                        mime_type = file_metadata.get("mimeType", mime_type)
                        filename = file_metadata.get("name")
                        if filename:
                            title = filename
                            logger.info(f"[create_event] Using filename '{filename}' as attachment title")
                        else:
                            logger.info("[create_event] No filename found, using generic title")
                    except Exception as e:
                        logger.warning(f"Could not fetch metadata for file {file_id}: {e}")
                event_body["attachments"].append({
                    "fileUrl": file_url,
                    "title": title,
                    "mimeType": mime_type,
                })
        created_event = await asyncio.to_thread(
            lambda: service.events().insert(
                calendarId=calendar_id, body=event_body, supportsAttachments=True,
                conferenceDataVersion=1 if add_google_meet else 0
            ).execute()
        )
    else:
        created_event = await asyncio.to_thread(
            lambda: service.events().insert(
                calendarId=calendar_id, body=event_body,
                conferenceDataVersion=1 if add_google_meet else 0
            ).execute()
        )
    link = created_event.get("htmlLink", "No link available")
    confirmation_message = f"Successfully created event '{created_event.get('summary', summary)}' for {user_google_email}. Link: {link}"

    # Add Google Meet information if conference was created
    if add_google_meet and "conferenceData" in created_event:
        conference_data = created_event["conferenceData"]
        if "entryPoints" in conference_data:
            for entry_point in conference_data["entryPoints"]:
                if entry_point.get("entryPointType") == "video":
                    meet_link = entry_point.get("uri", "")
                    if meet_link:
                        confirmation_message += f" Google Meet: {meet_link}"
                        break

    logger.info(
            f"Event created successfully for {user_google_email}. ID: {created_event.get('id')}, Link: {link}"
        )
    return confirmation_message


@server.tool()
@handle_http_errors("modify_event", service_type="calendar")
@require_google_service("calendar", "calendar_events")
async def modify_event(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
    summary: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    timezone: Optional[str] = None,
    add_google_meet: Optional[bool] = None,
) -> str:
    """
    Modifies an existing event.

    Args:
        user_google_email (str): The user's Google email address. Required.
        event_id (str): The ID of the event to modify.
        calendar_id (str): Calendar ID (default: 'primary').
        summary (Optional[str]): New event title.
        start_time (Optional[str]): New start time (RFC3339, e.g., "2023-10-27T10:00:00-07:00" or "2023-10-27" for all-day).
        end_time (Optional[str]): New end time (RFC3339, e.g., "2023-10-27T11:00:00-07:00" or "2023-10-28" for all-day).
        description (Optional[str]): New event description.
        location (Optional[str]): New event location.
        attendees (Optional[List[str]]): New attendee email addresses.
        timezone (Optional[str]): New timezone (e.g., "America/New_York").
        add_google_meet (Optional[bool]): Whether to add or remove Google Meet video conference. If True, adds Google Meet; if False, removes it; if None, leaves unchanged.

    Returns:
        str: Confirmation message of the successful event modification with event link.
    """
    logger.info(
        f"[modify_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    # Build the event body with only the fields that are provided
    event_body: Dict[str, Any] = {}
    if summary is not None:
        event_body["summary"] = summary
    if start_time is not None:
        event_body["start"] = (
            {"date": start_time}
            if "T" not in start_time
            else {"dateTime": start_time}
        )
        if timezone is not None and "dateTime" in event_body["start"]:
            event_body["start"]["timeZone"] = timezone
    if end_time is not None:
        event_body["end"] = (
            {"date": end_time} if "T" not in end_time else {"dateTime": end_time}
        )
        if timezone is not None and "dateTime" in event_body["end"]:
            event_body["end"]["timeZone"] = timezone
    if description is not None:
        event_body["description"] = description
    if location is not None:
        event_body["location"] = location
    if attendees is not None:
        event_body["attendees"] = [{"email": email} for email in attendees]
    if (
        timezone is not None
        and "start" not in event_body
        and "end" not in event_body
    ):
        # If timezone is provided but start/end times are not, we need to fetch the existing event
        # to apply the timezone correctly. This is a simplification; a full implementation
        # might handle this more robustly or require start/end with timezone.
        # For now, we'll log a warning and skip applying timezone if start/end are missing.
        logger.warning(
            "[modify_event] Timezone provided but start_time and end_time are missing. Timezone will not be applied unless start/end times are also provided."
        )

    if not event_body:
        message = "No fields provided to modify the event."
        logger.warning(f"[modify_event] {message}")
        raise Exception(message)

    # Log the event ID for debugging
    logger.info(
        f"[modify_event] Attempting to update event with ID: '{event_id}' in calendar '{calendar_id}'"
    )

    # Get the existing event to preserve fields that aren't being updated
    try:
        existing_event = await asyncio.to_thread(
            lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        )
        logger.info(
            "[modify_event] Successfully retrieved existing event before update"
        )

        # Preserve existing fields if not provided in the update
        _preserve_existing_fields(event_body, existing_event, {
            "summary": summary,
            "description": description,
            "location": location,
            "attendees": attendees
        })

        # Handle Google Meet conference data
        if add_google_meet is not None:
            if add_google_meet:
                # Add Google Meet
                request_id = str(uuid.uuid4())
                event_body["conferenceData"] = {
                    "createRequest": {
                        "requestId": request_id,
                        "conferenceSolutionKey": {
                            "type": "hangoutsMeet"
                        }
                    }
                }
                logger.info(f"[modify_event] Adding Google Meet conference with request ID: {request_id}")
            else:
                # Remove Google Meet by setting conferenceData to empty
                event_body["conferenceData"] = {}
                logger.info("[modify_event] Removing Google Meet conference")
        elif 'conferenceData' in existing_event:
            # Preserve existing conference data if not specified
            event_body["conferenceData"] = existing_event["conferenceData"]
            logger.info("[modify_event] Preserving existing conference data")

    except HttpError as get_error:
        if get_error.resp.status == 404:
            logger.error(
                f"[modify_event] Event not found during pre-update verification: {get_error}"
            )
            message = f"Event not found during verification. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. This may be due to incorrect ID format or the event no longer exists."
            raise Exception(message)
        else:
            logger.warning(
                f"[modify_event] Error during pre-update verification, but proceeding with update: {get_error}"
            )

    # Proceed with the update
    updated_event = await asyncio.to_thread(
        lambda: service.events()
        .update(calendarId=calendar_id, eventId=event_id, body=event_body, conferenceDataVersion=1)
        .execute()
    )

    link = updated_event.get("htmlLink", "No link available")
    confirmation_message = f"Successfully modified event '{updated_event.get('summary', summary)}' (ID: {event_id}) for {user_google_email}. Link: {link}"

    # Add Google Meet information if conference was added
    if add_google_meet is True and "conferenceData" in updated_event:
        conference_data = updated_event["conferenceData"]
        if "entryPoints" in conference_data:
            for entry_point in conference_data["entryPoints"]:
                if entry_point.get("entryPointType") == "video":
                    meet_link = entry_point.get("uri", "")
                    if meet_link:
                        confirmation_message += f" Google Meet: {meet_link}"
                        break
    elif add_google_meet is False:
        confirmation_message += " (Google Meet removed)"

    logger.info(
        f"Event modified successfully for {user_google_email}. ID: {updated_event.get('id')}, Link: {link}"
    )
    return confirmation_message


@server.tool()
@handle_http_errors("delete_event", service_type="calendar")
@require_google_service("calendar", "calendar_events")
async def delete_event(service, user_google_email: str, event_id: str, calendar_id: str = "primary") -> str:
    """
    Deletes an existing event.

    Args:
        user_google_email (str): The user's Google email address. Required.
        event_id (str): The ID of the event to delete.
        calendar_id (str): Calendar ID (default: 'primary').

    Returns:
        str: Confirmation message of the successful event deletion.
    """
    logger.info(
        f"[delete_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    # Log the event ID for debugging
    logger.info(
        f"[delete_event] Attempting to delete event with ID: '{event_id}' in calendar '{calendar_id}'"
    )

    # Try to get the event first to verify it exists
    try:
        await asyncio.to_thread(
            lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        )
        logger.info(
            "[delete_event] Successfully verified event exists before deletion"
        )
    except HttpError as get_error:
        if get_error.resp.status == 404:
            logger.error(
                f"[delete_event] Event not found during pre-delete verification: {get_error}"
            )
            message = f"Event not found during verification. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. This may be due to incorrect ID format or the event no longer exists."
            raise Exception(message)
        else:
            logger.warning(
                f"[delete_event] Error during pre-delete verification, but proceeding with deletion: {get_error}"
            )

    # Proceed with the deletion
    await asyncio.to_thread(
        lambda: service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    )

    confirmation_message = f"Successfully deleted event (ID: {event_id}) from calendar '{calendar_id}' for {user_google_email}."
    logger.info(f"Event deleted successfully for {user_google_email}. ID: {event_id}")
    return confirmation_message


@server.tool()
@handle_http_errors("get_event", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def get_event(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary"
) -> str:
    """
    Retrieves the details of a single event by its ID from a specified Google Calendar.

    Args:
        user_google_email (str): The user's Google email address. Required.
        event_id (str): The ID of the event to retrieve. Required.
        calendar_id (str): The ID of the calendar to query. Defaults to 'primary'.

    Returns:
        str: A formatted string with the event's details.
    """
    logger.info(f"[get_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}")
    event = await asyncio.to_thread(
        lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    )
    summary = event.get("summary", "No Title")
    start = event["start"].get("dateTime", event["start"].get("date"))
    end = event["end"].get("dateTime", event["end"].get("date"))
    link = event.get("htmlLink", "No Link")
    description = event.get("description", "No Description")
    location = event.get("location", "No Location")
    attendees = event.get("attendees", [])
    attendee_emails = ", ".join([a.get("email", "") for a in attendees]) if attendees else "None"
    event_details = (
        f'Event Details:\n'
        f'- Title: {summary}\n'
        f'- Starts: {start}\n'
        f'- Ends: {end}\n'
        f'- Description: {description}\n'
        f'- Location: {location}\n'
        f'- Attendees: {attendee_emails}\n'
        f'- Event ID: {event_id}\n'
        f'- Link: {link}'
    )
    logger.info(f"[get_event] Successfully retrieved event {event_id} for {user_google_email}.")
    return event_details
