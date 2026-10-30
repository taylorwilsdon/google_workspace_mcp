"""
Google Calendar MCP Tools

This module provides MCP tools for interacting with Google Calendar API.
"""

import datetime
import logging
import asyncio
import uuid
from typing import List, Optional, Dict, Any, Union

import pytz
from googleapiclient.errors import HttpError

from auth.service_decorator import require_google_service
from core.utils import handle_http_errors, StringList, StringOrDictList, UserInputError
from gcalendar.calendar_helpers import (
    _apply_transparency_if_valid,
    _apply_visibility_if_valid,
    _build_attachment_entries,
    _build_time_boundary,
    _correct_time_format_for_api,
    _format_event_detail_lines,
    _format_event_time,
    _get_meeting_link,
    _normalize_attendees,
    _parse_reminders_json,
    _preserve_existing_fields,
    _resolve_color_id,
    _resolve_conference_data,
    _saved_event_times,
    _split_recurring_event,
    _status_event_time_entry,
    _validate_auto_decline_mode,
    _validate_chat_status,
)

from mcp.types import ToolAnnotations

from core.server import server


# Configure module logger
logger = logging.getLogger(__name__)


@server.tool(
    title="List Calendars",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("list_calendars", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def list_calendars(
    service,
    user_google_email: str,
    max_results: Optional[int] = None,
    page_token: Optional[str] = None,
) -> str:
    """
    Retrieves a list of calendars accessible to the authenticated user.

    Args:
        user_google_email (str): The user's Google email address. Required.
        max_results (Optional[int]): Maximum calendars to return in one page. Omit to use the API default.
        page_token (Optional[str]): Token for the next page, taken from a previous response.

    Returns:
        str: A formatted list of the user's calendars (summary, ID, primary status, time zone).
            When more calendars remain, a "Next page token" line is appended.
    """
    logger.info(f"[list_calendars] Invoked. Email: '{user_google_email}'")

    params: Dict[str, Any] = {}
    if max_results is not None:
        params["maxResults"] = max_results
    if page_token:
        params["pageToken"] = page_token

    calendar_list_response = await asyncio.to_thread(
        lambda: service.calendarList().list(**params).execute()
    )
    items = calendar_list_response.get("items", [])
    next_page_token = calendar_list_response.get("nextPageToken")
    if not items:
        if next_page_token:
            return (
                f"No calendars on this page for {user_google_email}, and more pages remain."
                f"\nNext page token: {next_page_token}"
            )
        return f"No calendars found for {user_google_email}."

    calendars_summary_list = []
    for cal in items:
        line = f'- "{cal.get("summary", "No Summary")}"{" (Primary)" if cal.get("primary") else ""} (ID: {cal["id"]})'
        if cal.get("timeZone"):
            line += f" (Time zone: {cal['timeZone']})"
        calendars_summary_list.append(line)
    text_output = (
        f"Successfully listed {len(items)} calendars for {user_google_email}:\n"
        + "\n".join(calendars_summary_list)
    )
    if next_page_token:
        text_output += f"\n\nNext page token: {next_page_token}"
    logger.info(f"Successfully listed {len(items)} calendars for {user_google_email}.")
    return text_output


@server.tool(
    title="Get Events",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_events", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def get_events(
    service,
    user_google_email: str,
    calendar_id: str = "primary",
    event_id: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 25,
    query: Optional[str] = None,
    detailed: bool = False,
    include_attachments: bool = False,
    single_events: bool = True,
    page_token: Optional[str] = None,
) -> str:
    """
    Retrieves events from a specified Google Calendar. Can retrieve a single event by ID or multiple events within a time range.
    You can also search for events by keyword by supplying the optional "query" param.

    Args:
        user_google_email (str): The user's Google email address. Required.
        calendar_id (str): The ID of the calendar to query. Use 'primary' for the user's primary calendar. Defaults to 'primary'. Calendar IDs can be obtained using `list_calendars`.
        event_id (Optional[str]): The ID of a specific event to retrieve. If provided, retrieves only this event and ignores time filtering parameters.
        time_min (Optional[str]): The start of the time range (inclusive) in RFC3339 format (e.g., '2024-05-12T10:00:00Z' or '2024-05-12'). If omitted, defaults to the current time when single_events=True. It is omitted from unexpanded queries so recurring masters that began in the past but still have future occurrences remain discoverable. Ignored if event_id is provided.
        time_max (Optional[str]): The end of the time range (exclusive) in RFC3339 format. If omitted, events starting from `time_min` onwards are considered (up to `max_results`). Ignored if event_id is provided.
        max_results (int): The maximum number of events to return in one page. Defaults to 25. Ignored if event_id is provided.
        page_token (Optional[str]): Token for the next page, taken from a previous response. When single_events=True, also pass the response's Pagination time_min as time_min, even if omitted on the first call. Keep all other query parameters unchanged. Ignored if event_id is provided.
        query (Optional[str]): A keyword to search for within event fields (summary, description, location). Ignored if event_id is provided.
        detailed (bool): Whether to return detailed event information including description, location, colour (colorId), attendees, and attendee details (response status, organizer, optional flags). Recurring instances also report the parent series ID needed to edit the whole series; recurring masters report their raw RFC5545 recurrence rules; and events that are not ordinary confirmed meetings report their event type (outOfOffice, workingLocation, focusTime) and status. Defaults to False.
        include_attachments (bool): Whether to include attachment information in detailed event output. When True, shows attachment details (fileId, fileUrl, mimeType, title) for events that have attachments. Only applies when detailed=True. Set this to True when you need to view or access files that have been attached to calendar events, such as meeting documents, presentations, or other shared files. Defaults to False.
        single_events (bool): Whether to expand recurring series into individual instances. Defaults to True for backwards compatibility. Set to False with detailed=True to retrieve recurring master events and their exact RFC5545 recurrence rules instead of inferring cadence from expanded instances.

    Returns:
        str: A formatted list of events (summary, start and end times, link) within the specified range, or detailed information for a single event if event_id is provided. When more events remain beyond max_results, a "Next page token" line is appended.
    """
    logger.info(
        f"[get_events] Raw parameters - event_id: '{event_id}', time_min: '{time_min}', time_max: '{time_max}', query_len={len(query) if query else 0}, detailed: {detailed}, include_attachments: {include_attachments}, single_events: {single_events}"
    )

    next_page_token: Optional[str] = None
    pagination_info = ""

    # Handle single event retrieval
    if event_id:
        logger.info(f"[get_events] Retrieving single event with ID: {event_id}")
        event = await asyncio.to_thread(
            lambda: (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )
        )
        items = [event]
    else:
        # Handle multiple events retrieval with time filtering
        # Normalize before validating: blank and null-like strings also default
        # to "now", which would change the query between pages.
        formatted_time_min = _correct_time_format_for_api(time_min, "time_min", None)
        if page_token and formatted_time_min is None and single_events:
            raise UserInputError(
                "[get_events] page_token requires time_min. Pass the Pagination "
                "time_min from the previous response to preserve the original range."
            )

        if formatted_time_min:
            effective_time_min = formatted_time_min
        elif single_events:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            effective_time_min = utc_now.isoformat().replace("+00:00", "Z")
        else:
            effective_time_min = None
        if time_min is None:
            if single_events:
                logger.info(
                    f"time_min not provided, defaulting to current UTC time: {effective_time_min}"
                )
            else:
                logger.info(
                    "time_min not provided for unexpanded events; omitting it so older recurring masters remain discoverable"
                )
        else:
            logger.info(
                f"time_min processing: original='{time_min}', formatted='{formatted_time_min}', effective='{effective_time_min}'"
            )

        effective_time_max = _correct_time_format_for_api(time_max, "time_max", None)
        if time_max:
            logger.info(
                f"time_max processing: original='{time_max}', formatted='{effective_time_max}'"
            )

        logger.info(
            f"[get_events] Final API parameters - calendarId: '{calendar_id}', timeMin: '{effective_time_min}', timeMax: '{effective_time_max}', maxResults: {max_results}, query_len={len(query) if query else 0}"
        )

        # Build the request parameters dynamically
        request_params = {
            "calendarId": calendar_id,
            "timeMax": effective_time_max,
            "maxResults": max_results,
            "singleEvents": single_events,
        }

        if effective_time_min:
            request_params["timeMin"] = effective_time_min

        # The Calendar API only permits start-time ordering when recurring
        # series are expanded. Unexpanded results retain the API's stable
        # default ordering and expose the master event's recurrence array.
        if single_events:
            request_params["orderBy"] = "startTime"

        if query:
            request_params["q"] = query

        if page_token:
            request_params["pageToken"] = page_token

        events_result = await asyncio.to_thread(
            lambda: service.events().list(**request_params).execute()
        )
        items = events_result.get("items", [])
        next_page_token = events_result.get("nextPageToken")
        if next_page_token:
            pagination_info = f"Next page token: {next_page_token}"
            if effective_time_min:
                pagination_info += f"\nPagination time_min: {effective_time_min}"
    if not items:
        if event_id:
            return f"Event with ID '{event_id}' not found in calendar '{calendar_id}' for {user_google_email}."
        elif next_page_token:
            # The API can return an empty page with more pages behind it, so
            # "no events" would be untrue here.
            return (
                f"No events on this page in calendar '{calendar_id}' for {user_google_email}"
                f" for the specified time range, and more pages remain."
                f"\n{pagination_info}"
            )
        else:
            return f"No events found in calendar '{calendar_id}' for {user_google_email} for the specified time range."

    # Handle returning detailed output for a single event when requested
    if event_id and detailed:
        item = items[0]
        summary = item.get("summary", "No Title")
        start = _format_event_time(item, "start")
        end = _format_event_time(item, "end")
        link = item.get("htmlLink", "No Link")

        event_details = (
            f"Event Details:\n- Title: {summary}\n- Starts: {start}\n- Ends: {end}\n"
        )
        event_details += _format_event_detail_lines(
            item,
            prefix="- ",
            indent="  ",
            include_attachments=include_attachments,
        )
        event_details += f"- Event ID: {event_id}\n- Link: {link}"
        logger.info(
            f"[get_events] Successfully retrieved detailed event {event_id} for {user_google_email}."
        )
        return event_details

    # Handle multiple events or single event with basic output
    event_details_list = []
    for item in items:
        summary = item.get("summary", "No Title")
        start_time = _format_event_time(item, "start")
        end_time = _format_event_time(item, "end")
        link = item.get("htmlLink", "No Link")
        item_event_id = item.get("id", "No ID")

        if detailed:
            # Add detailed information for multiple events
            event_detail_parts = (
                f'- "{summary}" (Starts: {start_time}, Ends: {end_time})\n'
                + _format_event_detail_lines(
                    item,
                    prefix="  ",
                    indent="    ",
                    include_attachments=include_attachments,
                )
                + f"  ID: {item_event_id} | Link: {link}"
            )
            event_details_list.append(event_detail_parts)
        else:
            # Basic output format
            meeting_link = _get_meeting_link(item)
            basic_line = f'- "{summary}" (Starts: {start_time}, Ends: {end_time})'
            if item.get("status") == "cancelled":
                basic_line += " Status: cancelled"
            if meeting_link:
                basic_line += f" Meeting: {meeting_link}"
            basic_line += f" ID: {item_event_id} | Link: {link}"
            event_details_list.append(basic_line)

    if event_id:
        # Single event basic output
        text_output = (
            f"Successfully retrieved event from calendar '{calendar_id}' for {user_google_email}:\n"
            + "\n".join(event_details_list)
        )
    else:
        # Multiple events output
        text_output = (
            f"Successfully retrieved {len(items)} events from calendar '{calendar_id}' for {user_google_email}:\n"
            + "\n".join(event_details_list)
        )
        if next_page_token:
            text_output += f"\n\n{pagination_info}"

    logger.info(f"Successfully retrieved {len(items)} events for {user_google_email}.")
    return text_output


# ---------------------------------------------------------------------------
# Internal implementation functions for event create/modify/delete.
# These are called by both the consolidated ``manage_event`` tool and the
# legacy single-action tools.
# ---------------------------------------------------------------------------


async def _create_event_impl(
    service,
    user_google_email: str,
    summary: str,
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[Union[str, Dict[str, Any]]]] = None,
    timezone: Optional[str] = None,
    attachments: Optional[List[str]] = None,
    add_google_meet: bool = False,
    conference_data: Optional[Dict[str, Any]] = None,
    reminders: Optional[Union[str, List[Dict[str, Any]]]] = None,
    use_default_reminders: bool = True,
    transparency: Optional[str] = None,
    visibility: Optional[str] = None,
    color_id: Optional[str] = None,
    recurrence: Optional[List[str]] = None,
    guests_can_modify: Optional[bool] = None,
    guests_can_invite_others: Optional[bool] = None,
    guests_can_see_other_guests: Optional[bool] = None,
    send_updates: str = "all",
    *,
    start_timezone: Optional[str] = None,
    end_timezone: Optional[str] = None,
) -> str:
    """Internal implementation for creating a calendar event."""
    logger.info(
        f"[create_event] Invoked. Email: '{user_google_email}', summary_len={len(summary) if summary else 0}"
    )
    logger.debug(f"[create_event] Incoming attachments param: {attachments}")
    # If attachments value is a string, split by comma and strip whitespace
    if attachments and isinstance(attachments, str):
        attachments = [a.strip() for a in attachments.split(",") if a.strip()]
        logger.debug(
            f"[create_event] Parsed attachments list from string: {attachments}"
        )
    # Each boundary resolves its own zone, falling back to the event-wide timezone.
    event_body: Dict[str, Any] = {
        "summary": summary,
        "start": _build_time_boundary(
            start_time, start_timezone if start_timezone is not None else timezone
        ),
        "end": _build_time_boundary(
            end_time, end_timezone if end_timezone is not None else timezone
        ),
    }
    if recurrence:
        event_body["recurrence"] = recurrence
    if location:
        event_body["location"] = location
    if description:
        event_body["description"] = description
    normalized_attendees = _normalize_attendees(attendees)
    if normalized_attendees is not None:
        event_body["attendees"] = normalized_attendees
    if color_id is not None:
        event_body["colorId"] = color_id

    # Handle reminders
    if reminders is not None or not use_default_reminders:
        # If custom reminders are provided, automatically disable default reminders
        effective_use_default = use_default_reminders and reminders is None

        reminder_data = {"useDefault": effective_use_default}
        if reminders is not None:
            validated_reminders = _parse_reminders_json(reminders, "create_event")
            if validated_reminders:
                reminder_data["overrides"] = validated_reminders
                logger.info(
                    f"[create_event] Added {len(validated_reminders)} custom reminders"
                )
                if use_default_reminders:
                    logger.info(
                        "[create_event] Custom reminders provided - disabling default reminders"
                    )

        event_body["reminders"] = reminder_data

    # Handle transparency validation
    _apply_transparency_if_valid(event_body, transparency, "create_event")

    # Handle visibility validation
    _apply_visibility_if_valid(event_body, visibility, "create_event")

    # Handle guest permissions
    if guests_can_modify is not None:
        event_body["guestsCanModify"] = guests_can_modify
        logger.info(f"[create_event] Set guestsCanModify to {guests_can_modify}")
    if guests_can_invite_others is not None:
        event_body["guestsCanInviteOthers"] = guests_can_invite_others
        logger.info(
            f"[create_event] Set guestsCanInviteOthers to {guests_can_invite_others}"
        )
    if guests_can_see_other_guests is not None:
        event_body["guestsCanSeeOtherGuests"] = guests_can_see_other_guests
        logger.info(
            f"[create_event] Set guestsCanSeeOtherGuests to {guests_can_see_other_guests}"
        )

    if add_google_meet:
        request_id = str(uuid.uuid4())
        event_body["conferenceData"] = {
            "createRequest": {
                "requestId": request_id,
                "conferenceSolutionKey": {"type": "hangoutsMeet"},
            }
        }
        logger.info(
            f"[create_event] Adding Google Meet conference with request ID: {request_id}"
        )
    elif conference_data is not None:
        event_body["conferenceData"] = conference_data
        logger.info("[create_event] Attaching pre-generated conference data")

    # conferenceDataVersion=1 is required whenever conferenceData is present,
    # whether it's a native Meet create request or a pre-generated add-on payload.
    conference_data_version = (
        1 if (add_google_meet or conference_data is not None) else 0
    )

    if attachments:
        event_body["attachments"] = await _build_attachment_entries(
            service, attachments, "create_event"
        )
        created_event = await asyncio.to_thread(
            lambda: (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body=event_body,
                    supportsAttachments=True,
                    conferenceDataVersion=conference_data_version,
                    sendUpdates=send_updates,
                )
                .execute()
            )
        )
    else:
        created_event = await asyncio.to_thread(
            lambda: (
                service.events()
                .insert(
                    calendarId=calendar_id,
                    body=event_body,
                    conferenceDataVersion=conference_data_version,
                    sendUpdates=send_updates,
                )
                .execute()
            )
        )
    link = created_event.get("htmlLink", "No link available")
    confirmation_message = f"Successfully created event '{created_event.get('summary', summary)}' for {user_google_email}. Link: {link}"

    confirmation_message += _saved_event_times(created_event)

    # Surface the conferencing link (native Meet or third-party add-on) if present
    if add_google_meet or conference_data is not None:
        meeting_link = _get_meeting_link(created_event)
        if meeting_link:
            label = "Google Meet" if add_google_meet else "Conference"
            confirmation_message += f" {label}: {meeting_link}"

    logger.info(
        f"Event created successfully for {user_google_email}. ID: {created_event.get('id')}, Link: {link}"
    )
    return confirmation_message


async def _modify_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
    summary: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[Union[str, Dict[str, Any]]]] = None,
    timezone: Optional[str] = None,
    add_google_meet: Optional[bool] = None,
    conference_data: Optional[Dict[str, Any]] = None,
    reminders: Optional[Union[str, List[Dict[str, Any]]]] = None,
    use_default_reminders: Optional[bool] = None,
    transparency: Optional[str] = None,
    visibility: Optional[str] = None,
    color_id: Optional[str] = None,
    recurrence: Optional[List[str]] = None,
    guests_can_modify: Optional[bool] = None,
    guests_can_invite_others: Optional[bool] = None,
    guests_can_see_other_guests: Optional[bool] = None,
    attachments: Optional[Union[str, List[str]]] = None,
    send_updates: str = "all",
    *,
    start_timezone: Optional[str] = None,
    end_timezone: Optional[str] = None,
    this_and_following: bool = False,
) -> str:
    """Internal implementation for modifying a calendar event."""
    logger.info(
        f"[modify_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    # Build the event body with only the fields that are provided
    event_body: Dict[str, Any] = {}
    if summary is not None:
        event_body["summary"] = summary
    if start_time is not None:
        event_body["start"] = _build_time_boundary(
            start_time, start_timezone if start_timezone is not None else timezone
        )
    if end_time is not None:
        event_body["end"] = _build_time_boundary(
            end_time, end_timezone if end_timezone is not None else timezone
        )
    if description is not None:
        event_body["description"] = description
    if location is not None:
        event_body["location"] = location

    # Normalize attendees - accepts both email strings and full attendee objects
    normalized_attendees = _normalize_attendees(attendees)
    if normalized_attendees is not None:
        event_body["attendees"] = normalized_attendees

    if color_id is not None:
        event_body["colorId"] = color_id
    if recurrence is not None:
        event_body["recurrence"] = recurrence

    # Handle reminders
    if reminders is not None or use_default_reminders is not None:
        reminder_data = {}
        if use_default_reminders is not None:
            reminder_data["useDefault"] = use_default_reminders
        else:
            # Preserve existing event's useDefault value if not explicitly specified
            try:
                existing_event = await asyncio.to_thread(
                    service.events()
                    .get(calendarId=calendar_id, eventId=event_id)
                    .execute
                )
                reminder_data["useDefault"] = existing_event.get("reminders", {}).get(
                    "useDefault", True
                )
            except Exception as e:
                logger.warning(
                    f"[modify_event] Could not fetch existing event for reminders: {e}"
                )
                reminder_data["useDefault"] = (
                    True  # Fallback to True if unable to fetch
                )

        # If custom reminders are provided, automatically disable default reminders
        if reminders is not None:
            if reminder_data.get("useDefault", False):
                reminder_data["useDefault"] = False
                logger.info(
                    "[modify_event] Custom reminders provided - disabling default reminders"
                )

            validated_reminders = _parse_reminders_json(reminders, "modify_event")
            if reminders and not validated_reminders:
                logger.warning(
                    "[modify_event] Reminders provided but failed validation. No custom reminders will be set."
                )
            elif validated_reminders:
                reminder_data["overrides"] = validated_reminders
                logger.info(
                    f"[modify_event] Updated reminders with {len(validated_reminders)} custom reminders"
                )

        event_body["reminders"] = reminder_data

    # Handle transparency validation
    _apply_transparency_if_valid(event_body, transparency, "modify_event")

    # Handle visibility validation
    _apply_visibility_if_valid(event_body, visibility, "modify_event")

    # Handle guest permissions
    if guests_can_modify is not None:
        event_body["guestsCanModify"] = guests_can_modify
        logger.info(f"[modify_event] Set guestsCanModify to {guests_can_modify}")
    if guests_can_invite_others is not None:
        event_body["guestsCanInviteOthers"] = guests_can_invite_others
        logger.info(
            f"[modify_event] Set guestsCanInviteOthers to {guests_can_invite_others}"
        )
    if guests_can_see_other_guests is not None:
        event_body["guestsCanSeeOtherGuests"] = guests_can_see_other_guests
        logger.info(
            f"[modify_event] Set guestsCanSeeOtherGuests to {guests_can_see_other_guests}"
        )

    # Handle conference data
    if conference_data is not None:
        # Attach a pre-generated third-party conference (Zoom/Webex/Teams add-on)
        event_body["conferenceData"] = conference_data
        logger.info("[modify_event] Attaching pre-generated conference data")
    elif add_google_meet is not None:
        if add_google_meet:
            request_id = str(uuid.uuid4())
            event_body["conferenceData"] = {
                "createRequest": {
                    "requestId": request_id,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
            logger.info(
                f"[modify_event] Adding Google Meet conference with request ID: {request_id}"
            )
        else:
            # Remove Google Meet by setting conferenceData to JSON null.
            event_body["conferenceData"] = None
            logger.info("[modify_event] Removing Google Meet conference")

    if timezone is not None and "start" not in event_body and "end" not in event_body:
        # If timezone is provided but start/end times are not, we need to fetch the existing event
        # to apply the timezone correctly. This is a simplification; a full implementation
        # might handle this more robustly or require start/end with timezone.
        # For now, we'll log a warning and skip applying timezone if start/end are missing.
        logger.warning(
            "[modify_event] Timezone provided but start_time and end_time are missing. Timezone will not be applied unless start/end times are also provided."
        )

    if attachments is not None:
        # patch replaces the array wholesale, so the list passed in is the final list.
        event_body["attachments"] = await _build_attachment_entries(
            service, attachments, "modify_event"
        )

    if not event_body:
        message = "No fields provided to modify the event."
        logger.warning(f"[modify_event] {message}")
        raise Exception(message)

    if this_and_following:
        new_series = await _split_recurring_event(
            service, calendar_id, event_id, event_body, send_updates
        )
        return (
            f"Successfully modified '{new_series.get('summary', summary)}' from "
            f"instance {event_id} onward for {user_google_email}. Earlier instances "
            "keep the original series; this and following instances are now series "
            f"ID {new_series.get('id')}. "
            f"Link: {new_series.get('htmlLink', 'No link available')}"
            + _saved_event_times(new_series)
        )

    # Log the event ID for debugging
    logger.info(
        f"[modify_event] Attempting to update event with ID: '{event_id}' in calendar '{calendar_id}'"
    )

    # Get the existing event to preserve fields that aren't being updated
    try:
        existing_event = await asyncio.to_thread(
            lambda: (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )
        )
        logger.info(
            "[modify_event] Successfully retrieved existing event before update"
        )

        # Preserve existing fields if not provided in the update
        _preserve_existing_fields(
            event_body,
            existing_event,
            {
                "summary": summary,
                "description": description,
                "location": location,
                # Use the already-normalized attendee objects (if provided); otherwise preserve existing
                "attendees": event_body.get("attendees"),
                "colorId": event_body.get("colorId"),
                "recurrence": recurrence,
            },
        )

        if add_google_meet is None and "conferenceData" in existing_event:
            logger.info(
                "[modify_event] Existing conference data preserved via patch (not copied)"
            )

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

    updated_event = await asyncio.to_thread(
        lambda: (
            service.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body=event_body,
                supportsAttachments=True,
                conferenceDataVersion=1,
                sendUpdates=send_updates,
            )
            .execute()
        )
    )

    link = updated_event.get("htmlLink", "No link available")
    confirmation_message = f"Successfully modified event '{updated_event.get('summary', summary)}' (ID: {event_id}) for {user_google_email}. Link: {link}"

    confirmation_message += _saved_event_times(updated_event)

    # Surface the conferencing link (native Meet or third-party add-on) if present
    if conference_data is not None:
        meeting_link = _get_meeting_link(updated_event)
        if meeting_link:
            confirmation_message += f" Conference: {meeting_link}"
    elif add_google_meet is True:
        meeting_link = _get_meeting_link(updated_event)
        if meeting_link:
            confirmation_message += f" Google Meet: {meeting_link}"
    elif add_google_meet is False:
        confirmation_message += " (Google Meet removed)"

    logger.info(
        f"Event modified successfully for {user_google_email}. ID: {updated_event.get('id')}, Link: {link}"
    )
    return confirmation_message


async def _delete_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
    send_updates: str = "all",
    only_if_no_attendees: bool = False,
) -> str:
    """Internal implementation for deleting a calendar event."""
    logger.info(
        f"[delete_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    # Log the event ID for debugging
    logger.info(
        f"[delete_event] Attempting to delete event with ID: '{event_id}' in calendar '{calendar_id}'"
    )

    # Try to get the event first to verify it exists
    try:
        existing_event = await asyncio.to_thread(
            lambda: (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )
        )
        logger.info("[delete_event] Successfully verified event exists before deletion")
        if only_if_no_attendees:
            try:
                calendar = await asyncio.to_thread(
                    lambda: service.calendarList().get(calendarId=calendar_id).execute()
                )
            except HttpError as calendar_error:
                raise UserInputError(
                    "Could not verify calendar ownership; refusing to delete."
                ) from calendar_error
            if calendar.get("accessRole") != "owner":
                raise UserInputError(
                    "Only the calendar owner can use this deletion guard; refusing to delete."
                )

            if existing_event.get("recurrence"):
                raise UserInputError(
                    "Cannot verify attendees for every instance in a recurring series; refusing to delete."
                )

            attendees = existing_event.get("attendees", [])
            other_attendees = [
                attendee
                for attendee in attendees
                if attendee.get("email")
                and attendee["email"].casefold() != user_google_email.casefold()
            ]
            if other_attendees:
                raise UserInputError(
                    f"Event has {len(other_attendees)} attendee(s) besides the calendar owner; refusing to delete."
                )
    except HttpError as get_error:
        if get_error.resp.status == 404:
            logger.error(
                f"[delete_event] Event not found during pre-delete verification: {get_error}"
            )
            message = f"Event not found during verification. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. This may be due to incorrect ID format or the event no longer exists."
            raise Exception(message)
        else:
            if only_if_no_attendees:
                raise UserInputError(
                    "Could not verify event attendees; refusing to delete."
                ) from get_error
            logger.warning(
                f"[delete_event] Error during pre-delete verification, but proceeding with deletion: {get_error}"
            )

    # Proceed with the deletion
    await asyncio.to_thread(
        lambda: (
            service.events()
            .delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendUpdates=send_updates,
            )
            .execute()
        )
    )

    confirmation_message = f"Successfully deleted event (ID: {event_id}) from calendar '{calendar_id}' for {user_google_email}."
    logger.info(f"Event deleted successfully for {user_google_email}. ID: {event_id}")
    return confirmation_message


async def _rsvp_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    response: str,
    calendar_id: str = "primary",
    comment: Optional[str] = None,
    send_updates: str = "all",
) -> str:
    """Internal implementation for responding to a calendar event invitation."""
    valid_responses = {"accepted", "declined", "tentative", "needsAction"}
    if response not in valid_responses:
        raise UserInputError(
            f"Invalid response '{response}'. Must be one of: {sorted(valid_responses)}"
        )

    existing_event = await asyncio.to_thread(
        lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    )

    attendees = existing_event.get("attendees")
    if not attendees:
        raise Exception("This event has no attendee list; cannot update RSVP.")

    if existing_event.get("organizer", {}).get("self"):
        raise Exception(
            "You are the organizer of this event. Organizers cannot respond to their own invitations."
        )

    user_index = next((i for i, a in enumerate(attendees) if a.get("self")), None)
    if user_index is None:
        raise Exception(
            f"{user_google_email} was not found in the event's attendee list."
        )

    updated_attendees = [dict(a) for a in attendees]
    updated_attendees[user_index]["responseStatus"] = response
    if comment is not None:
        updated_attendees[user_index]["comment"] = comment

    updated_event = await asyncio.to_thread(
        lambda: (
            service.events()
            .patch(
                calendarId=calendar_id,
                eventId=event_id,
                body={"attendees": updated_attendees},
                sendUpdates=send_updates,
            )
            .execute()
        )
    )

    summary = updated_event.get("summary", "Unknown event")
    logger.info(
        f"[rsvp_event] RSVP for event {event_id} set to '{response}' for {user_google_email}."
    )
    return f"Successfully updated RSVP for '{summary}' (ID: {event_id}) to '{response}' for {user_google_email}."


# ---------------------------------------------------------------------------
# Consolidated event management tool
# ---------------------------------------------------------------------------


@server.tool(
    title="Manage Event",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_event", service_type="calendar")
@require_google_service("calendar", ["calendar_events", "calendar_list_read"])
async def manage_event(
    service,
    user_google_email: str,
    action: str,
    summary: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    event_id: Optional[str] = None,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[StringOrDictList] = None,
    timezone: Optional[str] = None,
    attachments: Optional[StringList] = None,
    add_google_meet: Optional[bool] = None,
    conference_data: Optional[Dict[str, Any]] = None,
    conference_provider: Optional[str] = None,
    conference_uri: Optional[str] = None,
    conference_passcode: Optional[str] = None,
    conference_id: Optional[str] = None,
    reminders: Optional[Union[str, List[Dict[str, Any]]]] = None,
    use_default_reminders: Optional[bool] = None,
    transparency: Optional[str] = None,
    visibility: Optional[str] = None,
    color_id: Optional[str] = None,
    recurrence: Optional[StringList] = None,
    guests_can_modify: Optional[bool] = None,
    guests_can_invite_others: Optional[bool] = None,
    guests_can_see_other_guests: Optional[bool] = None,
    response: Optional[str] = None,
    rsvp_comment: Optional[str] = None,
    send_updates: Optional[str] = None,
    *,
    start_timezone: Optional[str] = None,
    end_timezone: Optional[str] = None,
    this_and_following: bool = False,
    only_if_no_attendees: bool = False,
) -> str:
    """
    Manages calendar events. Supports creating, updating, deleting, and RSVP.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): Action to perform - "create", "update", "delete", or "rsvp".
        summary (Optional[str]): Event title (required for create).
        start_time (Optional[str]): Start time (required for create). An RFC3339 UTC offset identifies the exact instant and is preserved. Without an offset, supply start_timezone or timezone. For a local wall-clock time, omit the offset and pass the zone so Google resolves daylight saving; a wrong offset moves the event. Date-only values create all-day events.
        end_time (Optional[str]): End time (required for create). An RFC3339 UTC offset identifies the exact instant and is preserved. Without an offset, supply end_timezone or timezone. All-day end dates are exclusive.
        event_id (Optional[str]): Event ID (required for update and delete).
        calendar_id (str): Calendar ID (default: 'primary').
        description (Optional[str]): Event description.
        location (Optional[str]): Event location.
        attendees (Optional[List[Union[str, Dict[str, Any]]]]): Attendee email addresses, attendee objects (e.g. {"email": ..., "responseStatus": "accepted"}), or a mix of both.
        timezone (Optional[str]): IANA timezone applied to both boundaries (e.g.,
            "America/New_York"). Converts offset-bearing timestamps without changing their
            instant; interprets offset-free timestamps as local times in this zone.
            Overridden per boundary by start_timezone/end_timezone.
        start_timezone (Optional[str]): IANA timezone for the start boundary only,
            overriding timezone. Use for events whose two ends sit in different zones -
            a flight departing 13:45 "Asia/Jerusalem" and landing 17:50
            "Europe/Amsterdam" is one event authored in two zones. Explicit timestamp
            offsets always preserve the instant, even when the zone differs.
        end_timezone (Optional[str]): IANA timezone for the end boundary only,
            overriding timezone. See start_timezone.
        attachments (Optional[List[str]]): List of Google Drive file URLs or IDs to attach.
            On action="update" this replaces the event's existing attachments rather than
            appending to them, matching the Calendar API's patch semantics.
        add_google_meet (Optional[bool]): Whether to add/remove native Google Meet.
        conference_data (Optional[Dict[str, Any]]): Raw Google Calendar `conferenceData`
            payload to attach a third-party conference (Zoom/Webex/Teams add-on). Use this
            for full control; mutually exclusive with the conference_provider helper params
            and with add_google_meet. (create/update only)
        conference_provider (Optional[str]): Higher-level helper: third-party provider name
            (e.g. "zoom", "webex", "teams"). Requires conference_uri. The MCP builds the
            addOn `conferenceData` block internally. (create/update only)
        conference_uri (Optional[str]): Join URL for the third-party conference (e.g.
            "https://zoom.us/j/123456789"). Required when conference_provider is set.
        conference_passcode (Optional[str]): Optional passcode for the third-party conference.
        conference_id (Optional[str]): Optional provider-side conference/meeting ID.
        reminders (Optional[Union[str, List[Dict[str, Any]]]]): Custom reminder objects.
        use_default_reminders (Optional[bool]): Whether to use default reminders.
        transparency (Optional[str]): "opaque" (busy) or "transparent" (free).
        visibility (Optional[str]): "default", "public", "private", or "confidential".
        color_id (Optional[str]): Event color, by name or ID: lavender (1), sage (2),
            grape (3), flamingo (4), banana (5), tangerine (6), peacock (7),
            graphite (8), blueberry (9), basil (10), tomato (11).
        recurrence (Optional[List[str]]): RFC5545 recurrence rules for a recurring event, e.g. ["RRULE:FREQ=WEEKLY;COUNT=10"].
        guests_can_modify (Optional[bool]): Whether attendees can modify.
        guests_can_invite_others (Optional[bool]): Whether attendees can invite others.
        guests_can_see_other_guests (Optional[bool]): Whether attendees can see other guests.
        response (Optional[str]): RSVP response — "accepted", "declined", "tentative", or "needsAction" (rsvp action only).
        rsvp_comment (Optional[str]): Optional message to include with the RSVP response (rsvp action only).
        send_updates (Optional[str]): Notification behavior for create, update, delete, and rsvp — "all" (default), "externalOnly", or "none".
        this_and_following (bool): Update only. With event_id set to a recurring instance ID,
            applies the change to that instance and every later one by ending the original
            series before it and starting a new series there; earlier instances are kept.
            Updating the series ID instead changes every instance, and moving its start or
            recurrence drops past instances that no longer match. Defaults to False.
        only_if_no_attendees (bool): Delete only if the event has no attendees besides the calendar owner.

    Returns:
        str: Confirmation message with event details.
    """
    action_lower = action.lower().strip()

    if send_updates is not None:
        valid_send_updates = {"all", "externalOnly", "none"}
        if send_updates not in valid_send_updates:
            raise UserInputError(
                f"Invalid send_updates '{send_updates}'. Must be one of: {sorted(valid_send_updates)}"
            )

    if this_and_following and action_lower != "update":
        raise UserInputError("this_and_following applies only to the update action.")

    # Resolve the conferencing inputs (raw payload or helper params) once for the
    # actions that build an event body.
    resolved_conference_data = None
    if action_lower in ("create", "update"):
        resolved_conference_data = _resolve_conference_data(
            conference_data,
            conference_provider,
            conference_uri,
            conference_passcode,
            conference_id,
            add_google_meet,
        )
        color_id = _resolve_color_id(color_id)

    if action_lower == "create":
        if not summary or not start_time or not end_time:
            raise UserInputError(
                "summary, start_time, and end_time are required for create action"
            )
        return await _create_event_impl(
            service=service,
            user_google_email=user_google_email,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id,
            description=description,
            location=location,
            attendees=attendees,
            timezone=timezone,
            start_timezone=start_timezone,
            end_timezone=end_timezone,
            attachments=attachments,
            add_google_meet=add_google_meet or False,
            conference_data=resolved_conference_data,
            reminders=reminders,
            use_default_reminders=use_default_reminders
            if use_default_reminders is not None
            else True,
            transparency=transparency,
            visibility=visibility,
            color_id=color_id,
            guests_can_modify=guests_can_modify,
            guests_can_invite_others=guests_can_invite_others,
            guests_can_see_other_guests=guests_can_see_other_guests,
            recurrence=recurrence,
            send_updates=send_updates or "all",
        )
    elif action_lower == "update":
        if not event_id:
            raise UserInputError("event_id is required for update action")
        return await _modify_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            calendar_id=calendar_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            attendees=attendees,
            timezone=timezone,
            start_timezone=start_timezone,
            end_timezone=end_timezone,
            add_google_meet=add_google_meet,
            conference_data=resolved_conference_data,
            reminders=reminders,
            use_default_reminders=use_default_reminders,
            transparency=transparency,
            visibility=visibility,
            color_id=color_id,
            recurrence=recurrence,
            guests_can_modify=guests_can_modify,
            guests_can_invite_others=guests_can_invite_others,
            guests_can_see_other_guests=guests_can_see_other_guests,
            attachments=attachments,
            send_updates=send_updates or "all",
            this_and_following=this_and_following,
        )
    elif action_lower == "delete":
        if not event_id:
            raise UserInputError("event_id is required for delete action")
        return await _delete_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            calendar_id=calendar_id,
            send_updates=send_updates or "all",
            only_if_no_attendees=only_if_no_attendees,
        )
    elif action_lower == "rsvp":
        if not event_id:
            raise UserInputError("event_id is required for rsvp action")
        if not response:
            raise UserInputError("response is required for rsvp action")
        return await _rsvp_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            response=response,
            calendar_id=calendar_id,
            comment=rsvp_comment,
            send_updates=send_updates or "all",
        )
    else:
        raise UserInputError(
            f"Invalid action '{action_lower}'. Must be 'create', 'update', 'delete', or 'rsvp'."
        )


# ---------------------------------------------------------------------------
# Out of Office event management
# ---------------------------------------------------------------------------


async def _create_ooo_event_impl(
    service,
    user_google_email: str,
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    summary: Optional[str] = None,
    auto_decline_mode: Optional[str] = None,
    decline_message: Optional[str] = None,
    recurrence: Optional[List[str]] = None,
    timezone: Optional[str] = None,
) -> str:
    """Internal implementation for creating an Out of Office calendar event."""
    logger.info(
        f"[create_ooo_event] Invoked. Email: '{user_google_email}', Start: {start_time}, End: {end_time}"
    )

    effective_summary = summary or "Out of Office"
    effective_decline_mode = _validate_auto_decline_mode(
        auto_decline_mode, "create_ooo_event"
    )

    event_body: Dict[str, Any] = {
        "eventType": "outOfOffice",
        "summary": effective_summary,
        "start": _status_event_time_entry(start_time, "Out of Office", timezone),
        "end": _status_event_time_entry(end_time, "Out of Office", timezone),
        "outOfOfficeProperties": {
            "autoDeclineMode": effective_decline_mode,
            "declineMessage": decline_message or "",
        },
        "transparency": "opaque",
    }
    if recurrence:
        event_body["recurrence"] = recurrence

    created_event = await asyncio.to_thread(
        lambda: (
            service.events().insert(calendarId=calendar_id, body=event_body).execute()
        )
    )

    event_id = created_event.get("id", "N/A")
    link = created_event.get("htmlLink", "N/A")

    start_display = created_event.get("start", {}).get(
        "date", created_event.get("start", {}).get("dateTime", "N/A")
    )
    end_display = created_event.get("end", {}).get(
        "date", created_event.get("end", {}).get("dateTime", "N/A")
    )

    confirmation = (
        f"Successfully created Out of Office event for {user_google_email}.\n"
        f"- Summary: {effective_summary}\n"
        f"- Start: {start_display}\n"
        f"- End: {end_display}\n"
        f"- Auto-decline: {effective_decline_mode}\n"
        f"- Decline message: {decline_message or '(none)'}\n"
        f"- Event ID: {event_id}\n"
        f"- Link: {link}"
    )

    logger.info(
        f"OOO event created successfully for {user_google_email}. ID: {event_id}"
    )
    return confirmation


async def _list_ooo_events_impl(
    service,
    user_google_email: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    timezone: Optional[str] = None,
    page_token: Optional[str] = None,
) -> str:
    """Internal implementation for listing Out of Office calendar events."""
    logger.info(
        f"[list_ooo_events] Invoked. Email: '{user_google_email}', time_min: {time_min}, time_max: {time_max}, timezone: {timezone}"
    )

    formatted_time_min = _correct_time_format_for_api(time_min, "time_min", timezone)
    if page_token and formatted_time_min is None:
        raise UserInputError(
            "[list_ooo_events] page_token requires time_min. Pass the Pagination "
            "time_min from the previous response to preserve the original range."
        )
    if formatted_time_min:
        effective_time_min = formatted_time_min
    else:
        if timezone:
            try:
                tz = pytz.timezone(timezone)
                now = datetime.datetime.now(tz)
                effective_time_min = (
                    now.astimezone(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(
                    f"Could not apply timezone '{timezone}', falling back to UTC"
                )
                utc_now = datetime.datetime.now(datetime.timezone.utc)
                effective_time_min = utc_now.isoformat().replace("+00:00", "Z")
        else:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            effective_time_min = utc_now.isoformat().replace("+00:00", "Z")

    effective_time_max = _correct_time_format_for_api(time_max, "time_max", timezone)

    request_params: Dict[str, Any] = {
        "calendarId": calendar_id,
        "timeMin": effective_time_min,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
        "eventTypes": ["outOfOffice"],
    }
    if effective_time_max:
        request_params["timeMax"] = effective_time_max
    if page_token:
        request_params["pageToken"] = page_token

    events_result = await asyncio.to_thread(
        lambda: service.events().list(**request_params).execute()
    )
    items = events_result.get("items", [])
    next_page_token = events_result.get("nextPageToken")
    pagination_info = ""
    if next_page_token:
        pagination_info = (
            f"Next page token: {next_page_token}"
            f"\nPagination time_min: {effective_time_min}"
        )

    if not items:
        if next_page_token:
            return (
                f"No out-of-office events on this page for {user_google_email}, and more pages remain."
                f"\n{pagination_info}"
            )
        return f"No out-of-office events found for {user_google_email}."

    lines = [f"Found {len(items)} out-of-office event(s) for {user_google_email}:\n"]
    for i, item in enumerate(items, 1):
        summary = item.get("summary", "Out of Office")
        start = item.get("start", {}).get(
            "date", item.get("start", {}).get("dateTime", "N/A")
        )
        end = item.get("end", {}).get(
            "date", item.get("end", {}).get("dateTime", "N/A")
        )
        event_id = item.get("id", "N/A")
        ooo_props = item.get("outOfOfficeProperties", {})
        decline_mode = ooo_props.get("autoDeclineMode", "N/A")
        decline_msg = ooo_props.get("declineMessage", "")

        lines.append(f'{i}. "{summary}" ({start} to {end})')
        lines.append(f"   Auto-decline: {decline_mode}")
        if decline_msg:
            lines.append(f"   Decline message: {decline_msg}")
        lines.append(f"   Event ID: {event_id}")
        lines.append("")

    if next_page_token:
        lines.append(pagination_info)
    return "\n".join(lines).rstrip()


async def _update_ooo_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    summary: Optional[str] = None,
    auto_decline_mode: Optional[str] = None,
    decline_message: Optional[str] = None,
    recurrence: Optional[List[str]] = None,
    timezone: Optional[str] = None,
) -> str:
    """Internal implementation for updating an Out of Office calendar event."""
    logger.info(
        f"[update_ooo_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    existing_event = await asyncio.to_thread(
        lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    )

    if existing_event.get("eventType") != "outOfOffice":
        raise UserInputError(
            f"Event '{event_id}' is not an Out of Office event (type: '{existing_event.get('eventType', 'default')}'). "
            f"Use manage_event to update regular events."
        )

    patch_body: Dict[str, Any] = {}

    if summary is not None:
        patch_body["summary"] = summary
    if start_time is not None:
        patch_body["start"] = _status_event_time_entry(
            start_time, "Out of Office", timezone
        )
    if end_time is not None:
        patch_body["end"] = _status_event_time_entry(
            end_time, "Out of Office", timezone
        )
    if recurrence is not None:
        patch_body["recurrence"] = recurrence

    if auto_decline_mode is not None or decline_message is not None:
        existing_ooo_props = existing_event.get("outOfOfficeProperties", {})
        patch_body["outOfOfficeProperties"] = {
            "autoDeclineMode": _validate_auto_decline_mode(
                auto_decline_mode, "update_ooo_event"
            )
            if auto_decline_mode is not None
            else existing_ooo_props.get(
                "autoDeclineMode", "declineAllConflictingInvitations"
            ),
            "declineMessage": decline_message
            if decline_message is not None
            else existing_ooo_props.get("declineMessage", ""),
        }

    if not patch_body:
        return f"No changes specified for Out of Office event '{event_id}'."

    updated_event = await asyncio.to_thread(
        lambda: (
            service.events()
            .patch(calendarId=calendar_id, eventId=event_id, body=patch_body)
            .execute()
        )
    )

    link = updated_event.get("htmlLink", "N/A")
    start_display = updated_event.get("start", {}).get(
        "date", updated_event.get("start", {}).get("dateTime", "N/A")
    )
    end_display = updated_event.get("end", {}).get(
        "date", updated_event.get("end", {}).get("dateTime", "N/A")
    )

    confirmation = (
        f"Successfully updated Out of Office event (ID: {event_id}) for {user_google_email}.\n"
        f"- Summary: {updated_event.get('summary', 'Out of Office')}\n"
        f"- Start: {start_display}\n"
        f"- End: {end_display}\n"
        f"- Link: {link}"
    )

    logger.info(
        f"OOO event updated successfully for {user_google_email}. ID: {event_id}"
    )
    return confirmation


async def _delete_ooo_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
) -> str:
    """Internal implementation for deleting an Out of Office calendar event."""
    logger.info(
        f"[delete_ooo_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    try:
        existing_event = await asyncio.to_thread(
            lambda: (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )
        )
        if existing_event.get("eventType") != "outOfOffice":
            raise UserInputError(
                f"Event '{event_id}' is not an Out of Office event (type: '{existing_event.get('eventType', 'default')}'). "
                f"Use manage_event to delete regular events."
            )
    except HttpError as get_error:
        if get_error.resp.status == 404:
            raise Exception(
                f"Event not found. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'."
            )
        else:
            raise

    await asyncio.to_thread(
        lambda: (
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        )
    )

    confirmation = f"Successfully deleted Out of Office event (ID: {event_id}) from calendar '{calendar_id}' for {user_google_email}."
    logger.info(
        f"OOO event deleted successfully for {user_google_email}. ID: {event_id}"
    )
    return confirmation


@server.tool(
    title="Manage Out of Office",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_out_of_office", service_type="calendar")
@require_google_service("calendar", "calendar_events")
async def manage_out_of_office(
    service,
    user_google_email: str,
    action: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    summary: Optional[str] = None,
    auto_decline_mode: Optional[str] = None,
    decline_message: Optional[str] = None,
    recurrence: Optional[StringList] = None,
    timezone: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    event_id: Optional[str] = None,
    calendar_id: str = "primary",
    page_token: Optional[str] = None,
) -> str:
    """
    Manages Out of Office events on Google Calendar. These special events auto-decline
    meeting invitations and set the user's status to "Out of office" across Google Workspace.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): Action to perform - "create", "list", "update", or "delete".
        start_time (Optional[str]): Start date/time. Use 'YYYY-MM-DD' for full-day or RFC3339 for partial-day (e.g., '2024-04-05T09:00:00Z'). Date-only values are auto-converted to dateTime (midnight-to-midnight). Required for create.
        end_time (Optional[str]): End date/time (exclusive). Same format as start_time. For a single full day on April 5, use start_time='2026-04-05' and end_time='2026-04-06'. Required for create.
        summary (Optional[str]): Display text on the calendar. Defaults to "Out of Office".
        auto_decline_mode (Optional[str]): How to handle conflicting invitations. One of: "declineAllConflictingInvitations" (default), "declineOnlyNewConflictingInvitations", "declineNone".
        decline_message (Optional[str]): Message included when auto-declining invitations.
        recurrence (Optional[List[str]]): RFC5545 recurrence rules for a recurring Out of Office series, e.g. ["RRULE:FREQ=WEEKLY;COUNT=10"].
        timezone (Optional[str]): Timezone for the event (e.g., "America/New_York", "Europe/London"). Required when using date-only values or dateTime values without an explicit UTC offset.
        time_min (Optional[str]): For "list" action: start of time range. Defaults to current time. Recurring series are expanded into individual instances in the requested range.
        time_max (Optional[str]): For "list" action: end of time range.
        max_results (int): For "list" action: maximum events to return in one page. Defaults to 10.
        event_id (Optional[str]): Event ID. Required for "update" and "delete" actions.
        calendar_id (str): Calendar ID. Defaults to 'primary'. Out of Office status events live on primary calendars, so use 'primary' or a user's primary calendar ID/email rather than a secondary calendar ID.
        page_token (Optional[str]): For "list" action: token for the next page, taken from a previous response. Also pass the response's Pagination time_min as time_min, even if omitted on the first call. Keep all other query parameters unchanged.

    Returns:
        str: Confirmation message with event details, or a formatted list of OOO events.
    """
    action_lower = action.lower().strip()
    if action_lower == "create":
        if not start_time or not end_time:
            raise UserInputError(
                "start_time and end_time are required for create action"
            )
        return await _create_ooo_event_impl(
            service=service,
            user_google_email=user_google_email,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id,
            summary=summary,
            auto_decline_mode=auto_decline_mode,
            decline_message=decline_message,
            recurrence=recurrence,
            timezone=timezone,
        )
    elif action_lower == "list":
        return await _list_ooo_events_impl(
            service=service,
            user_google_email=user_google_email,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
            timezone=timezone,
            page_token=page_token,
        )
    elif action_lower == "update":
        if not event_id:
            raise UserInputError("event_id is required for update action")
        return await _update_ooo_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            calendar_id=calendar_id,
            start_time=start_time,
            end_time=end_time,
            summary=summary,
            auto_decline_mode=auto_decline_mode,
            decline_message=decline_message,
            recurrence=recurrence,
            timezone=timezone,
        )
    elif action_lower == "delete":
        if not event_id:
            raise UserInputError("event_id is required for delete action")
        return await _delete_ooo_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            calendar_id=calendar_id,
        )
    else:
        raise UserInputError(
            f"Invalid action '{action_lower}'. Must be 'create', 'list', 'update', or 'delete'."
        )


# ---------------------------------------------------------------------------
# Focus Time event management
# ---------------------------------------------------------------------------


async def _create_focus_time_event_impl(
    service,
    user_google_email: str,
    start_time: str,
    end_time: str,
    calendar_id: str = "primary",
    summary: Optional[str] = None,
    description: Optional[str] = None,
    auto_decline_mode: Optional[str] = None,
    decline_message: Optional[str] = None,
    chat_status: Optional[str] = None,
    recurrence: Optional[List[str]] = None,
    timezone: Optional[str] = None,
) -> str:
    """Internal implementation for creating a Focus Time calendar event."""
    logger.info(
        f"[create_focus_time_event] Invoked. Email: '{user_google_email}', Start: {start_time}, End: {end_time}"
    )

    effective_summary = summary or "Focus Time"
    effective_decline_mode = _validate_auto_decline_mode(
        auto_decline_mode, "create_focus_time_event"
    )
    validated_chat_status = _validate_chat_status(
        chat_status or "doNotDisturb", "create_focus_time_event"
    )

    focus_time_props: Dict[str, str] = {
        "autoDeclineMode": effective_decline_mode,
        "declineMessage": decline_message or "",
    }
    if validated_chat_status:
        focus_time_props["chatStatus"] = validated_chat_status

    event_body: Dict[str, Any] = {
        "eventType": "focusTime",
        "summary": effective_summary,
        "start": _status_event_time_entry(start_time, "Focus Time", timezone),
        "end": _status_event_time_entry(end_time, "Focus Time", timezone),
        "focusTimeProperties": focus_time_props,
        "transparency": "opaque",
    }
    if description:
        event_body["description"] = description
    if recurrence:
        event_body["recurrence"] = recurrence

    created_event = await asyncio.to_thread(
        lambda: (
            service.events().insert(calendarId=calendar_id, body=event_body).execute()
        )
    )

    event_id = created_event.get("id", "N/A")
    link = created_event.get("htmlLink", "N/A")

    start_display = created_event.get("start", {}).get(
        "date", created_event.get("start", {}).get("dateTime", "N/A")
    )
    end_display = created_event.get("end", {}).get(
        "date", created_event.get("end", {}).get("dateTime", "N/A")
    )

    confirmation = (
        f"Successfully created Focus Time event for {user_google_email}.\n"
        f"- Summary: {effective_summary}\n"
        f"- Start: {start_display}\n"
        f"- End: {end_display}\n"
        f"- Auto-decline: {effective_decline_mode}\n"
        f"- Decline message: {decline_message or '(none)'}\n"
        f"- Chat status: {validated_chat_status or '(default)'}\n"
        f"- Event ID: {event_id}\n"
        f"- Link: {link}"
    )

    logger.info(
        f"Focus Time event created successfully for {user_google_email}. ID: {event_id}"
    )
    return confirmation


async def _list_focus_time_events_impl(
    service,
    user_google_email: str,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    timezone: Optional[str] = None,
    page_token: Optional[str] = None,
) -> str:
    """Internal implementation for listing Focus Time calendar events."""
    logger.info(
        f"[list_focus_time_events] Invoked. Email: '{user_google_email}', time_min: {time_min}, time_max: {time_max}, timezone: {timezone}"
    )

    formatted_time_min = _correct_time_format_for_api(time_min, "time_min", timezone)
    if page_token and formatted_time_min is None:
        raise UserInputError(
            "[list_focus_time_events] page_token requires time_min. Pass the Pagination "
            "time_min from the previous response to preserve the original range."
        )
    if formatted_time_min:
        effective_time_min = formatted_time_min
    else:
        if timezone:
            try:
                tz = pytz.timezone(timezone)
                now = datetime.datetime.now(tz)
                effective_time_min = (
                    now.astimezone(datetime.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(
                    f"Could not apply timezone '{timezone}', falling back to UTC"
                )
                utc_now = datetime.datetime.now(datetime.timezone.utc)
                effective_time_min = utc_now.isoformat().replace("+00:00", "Z")
        else:
            utc_now = datetime.datetime.now(datetime.timezone.utc)
            effective_time_min = utc_now.isoformat().replace("+00:00", "Z")

    effective_time_max = _correct_time_format_for_api(time_max, "time_max", timezone)

    request_params: Dict[str, Any] = {
        "calendarId": calendar_id,
        "timeMin": effective_time_min,
        "maxResults": max_results,
        "singleEvents": True,
        "orderBy": "startTime",
        "eventTypes": ["focusTime"],
    }
    if effective_time_max:
        request_params["timeMax"] = effective_time_max
    if page_token:
        request_params["pageToken"] = page_token

    events_result = await asyncio.to_thread(
        lambda: service.events().list(**request_params).execute()
    )
    items = events_result.get("items", [])
    next_page_token = events_result.get("nextPageToken")
    pagination_info = ""
    if next_page_token:
        pagination_info = (
            f"Next page token: {next_page_token}"
            f"\nPagination time_min: {effective_time_min}"
        )

    if not items:
        if next_page_token:
            return (
                f"No Focus Time events on this page for {user_google_email}, and more pages remain."
                f"\n{pagination_info}"
            )
        return f"No Focus Time events found for {user_google_email}."

    lines = [f"Found {len(items)} Focus Time event(s) for {user_google_email}:\n"]
    for i, item in enumerate(items, 1):
        summary = item.get("summary", "Focus Time")
        start = item.get("start", {}).get(
            "date", item.get("start", {}).get("dateTime", "N/A")
        )
        end = item.get("end", {}).get(
            "date", item.get("end", {}).get("dateTime", "N/A")
        )
        event_id = item.get("id", "N/A")
        ft_props = item.get("focusTimeProperties", {})
        decline_mode = ft_props.get("autoDeclineMode", "N/A")
        decline_msg = ft_props.get("declineMessage", "")
        chat_st = ft_props.get("chatStatus", "")

        lines.append(f'{i}. "{summary}" ({start} to {end})')
        lines.append(f"   Auto-decline: {decline_mode}")
        if decline_msg:
            lines.append(f"   Decline message: {decline_msg}")
        if chat_st:
            lines.append(f"   Chat status: {chat_st}")
        lines.append(f"   Event ID: {event_id}")
        lines.append("")

    if next_page_token:
        lines.append(pagination_info)
    return "\n".join(lines).rstrip()


async def _update_focus_time_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    auto_decline_mode: Optional[str] = None,
    decline_message: Optional[str] = None,
    chat_status: Optional[str] = None,
    recurrence: Optional[List[str]] = None,
    timezone: Optional[str] = None,
) -> str:
    """Internal implementation for updating a Focus Time calendar event."""
    logger.info(
        f"[update_focus_time_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    existing_event = await asyncio.to_thread(
        lambda: service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    )

    if existing_event.get("eventType") != "focusTime":
        raise UserInputError(
            f"Event '{event_id}' is not a Focus Time event (type: '{existing_event.get('eventType', 'default')}'). "
            f"Use manage_event to update regular events."
        )

    patch_body: Dict[str, Any] = {}

    if summary is not None:
        patch_body["summary"] = summary
    if description is not None:
        patch_body["description"] = description
    if start_time is not None:
        patch_body["start"] = _status_event_time_entry(
            start_time, "Focus Time", timezone
        )
    if end_time is not None:
        patch_body["end"] = _status_event_time_entry(end_time, "Focus Time", timezone)
    if recurrence is not None:
        patch_body["recurrence"] = recurrence

    if (
        auto_decline_mode is not None
        or decline_message is not None
        or chat_status is not None
    ):
        existing_ft_props = existing_event.get("focusTimeProperties", {})
        updated_ft_props: Dict[str, str] = {
            "autoDeclineMode": _validate_auto_decline_mode(
                auto_decline_mode, "update_focus_time_event"
            )
            if auto_decline_mode is not None
            else existing_ft_props.get(
                "autoDeclineMode", "declineAllConflictingInvitations"
            ),
            "declineMessage": decline_message
            if decline_message is not None
            else existing_ft_props.get("declineMessage", ""),
        }
        if chat_status is not None:
            validated = _validate_chat_status(chat_status, "update_focus_time_event")
            updated_ft_props["chatStatus"] = validated
        elif existing_ft_props.get("chatStatus"):
            updated_ft_props["chatStatus"] = existing_ft_props["chatStatus"]
        patch_body["focusTimeProperties"] = updated_ft_props

    if not patch_body:
        return f"No changes specified for Focus Time event '{event_id}'."

    updated_event = await asyncio.to_thread(
        lambda: (
            service.events()
            .patch(calendarId=calendar_id, eventId=event_id, body=patch_body)
            .execute()
        )
    )

    link = updated_event.get("htmlLink", "N/A")
    start_display = updated_event.get("start", {}).get(
        "date", updated_event.get("start", {}).get("dateTime", "N/A")
    )
    end_display = updated_event.get("end", {}).get(
        "date", updated_event.get("end", {}).get("dateTime", "N/A")
    )

    confirmation = (
        f"Successfully updated Focus Time event (ID: {event_id}) for {user_google_email}.\n"
        f"- Summary: {updated_event.get('summary', 'Focus Time')}\n"
        f"- Start: {start_display}\n"
        f"- End: {end_display}\n"
        f"- Link: {link}"
    )

    logger.info(
        f"Focus Time event updated successfully for {user_google_email}. ID: {event_id}"
    )
    return confirmation


async def _delete_focus_time_event_impl(
    service,
    user_google_email: str,
    event_id: str,
    calendar_id: str = "primary",
) -> str:
    """Internal implementation for deleting a Focus Time calendar event."""
    logger.info(
        f"[delete_focus_time_event] Invoked. Email: '{user_google_email}', Event ID: {event_id}"
    )

    try:
        existing_event = await asyncio.to_thread(
            lambda: (
                service.events().get(calendarId=calendar_id, eventId=event_id).execute()
            )
        )
        if existing_event.get("eventType") != "focusTime":
            raise UserInputError(
                f"Event '{event_id}' is not a Focus Time event (type: '{existing_event.get('eventType', 'default')}'). "
                f"Use manage_event to delete regular events."
            )
    except HttpError as get_error:
        if get_error.resp.status == 404:
            raise Exception(
                f"Event not found. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'."
            )
        else:
            raise

    await asyncio.to_thread(
        lambda: (
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        )
    )

    confirmation = f"Successfully deleted Focus Time event (ID: {event_id}) from calendar '{calendar_id}' for {user_google_email}."
    logger.info(
        f"Focus Time event deleted successfully for {user_google_email}. ID: {event_id}"
    )
    return confirmation


@server.tool(
    title="Manage Focus Time",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_focus_time", service_type="calendar")
@require_google_service("calendar", "calendar_events")
async def manage_focus_time(
    service,
    user_google_email: str,
    action: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    summary: Optional[str] = None,
    description: Optional[str] = None,
    auto_decline_mode: Optional[str] = None,
    decline_message: Optional[str] = None,
    chat_status: Optional[str] = None,
    recurrence: Optional[StringList] = None,
    timezone: Optional[str] = None,
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 10,
    event_id: Optional[str] = None,
    calendar_id: str = "primary",
    page_token: Optional[str] = None,
) -> str:
    """
    Manages Focus Time events on Google Calendar. These special events auto-decline
    meeting invitations and, by default, set the user's chat status to Do Not
    Disturb, helping protect blocks of uninterrupted work time.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): Action to perform - "create", "list", "update", or "delete".
        start_time (Optional[str]): Start date/time. Use 'YYYY-MM-DD' for full-day or RFC3339 for partial-day (e.g., '2024-04-05T09:00:00Z'). Date-only values are auto-converted to dateTime (midnight-to-midnight). Required for create.
        end_time (Optional[str]): End date/time (exclusive). Same format as start_time. For a single full day on April 5, use start_time='2026-04-05' and end_time='2026-04-06'. Required for create.
        summary (Optional[str]): Display text on the calendar. Defaults to "Focus Time".
        description (Optional[str]): Event description. Useful for adding context about what the focus time is for.
        auto_decline_mode (Optional[str]): How to handle conflicting invitations. One of: "declineAllConflictingInvitations" (default), "declineOnlyNewConflictingInvitations", "declineNone".
        decline_message (Optional[str]): Message included when auto-declining invitations.
        chat_status (Optional[str]): Google Chat status during the focus time. Supports "doNotDisturb" (default) and "available".
        recurrence (Optional[List[str]]): RFC5545 recurrence rules for a recurring Focus Time series, e.g. ["RRULE:FREQ=WEEKLY;COUNT=10"].
        timezone (Optional[str]): Timezone for the event (e.g., "America/New_York", "Europe/London"). Required when using date-only values or dateTime values without an explicit UTC offset.
        time_min (Optional[str]): For "list" action: start of time range. Defaults to current time. Recurring series are expanded into individual instances in the requested range.
        time_max (Optional[str]): For "list" action: end of time range.
        max_results (int): For "list" action: maximum events to return in one page. Defaults to 10.
        event_id (Optional[str]): Event ID. Required for "update" and "delete" actions.
        calendar_id (str): Calendar ID. Defaults to 'primary'. Focus Time status events live on primary calendars, so use 'primary' or a user's primary calendar ID/email rather than a secondary calendar ID.
        page_token (Optional[str]): For "list" action: token for the next page, taken from a previous response. Also pass the response's Pagination time_min as time_min, even if omitted on the first call. Keep all other query parameters unchanged.

    Returns:
        str: Confirmation message with event details, or a formatted list of Focus Time events.
    """
    action_lower = action.lower().strip()
    if action_lower == "create":
        if not start_time or not end_time:
            raise UserInputError(
                "start_time and end_time are required for create action"
            )
        return await _create_focus_time_event_impl(
            service=service,
            user_google_email=user_google_email,
            start_time=start_time,
            end_time=end_time,
            calendar_id=calendar_id,
            summary=summary,
            description=description,
            auto_decline_mode=auto_decline_mode,
            decline_message=decline_message,
            chat_status=chat_status,
            recurrence=recurrence,
            timezone=timezone,
        )
    elif action_lower == "list":
        return await _list_focus_time_events_impl(
            service=service,
            user_google_email=user_google_email,
            calendar_id=calendar_id,
            time_min=time_min,
            time_max=time_max,
            max_results=max_results,
            timezone=timezone,
            page_token=page_token,
        )
    elif action_lower == "update":
        if not event_id:
            raise UserInputError("event_id is required for update action")
        return await _update_focus_time_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            calendar_id=calendar_id,
            start_time=start_time,
            end_time=end_time,
            summary=summary,
            description=description,
            auto_decline_mode=auto_decline_mode,
            decline_message=decline_message,
            chat_status=chat_status,
            recurrence=recurrence,
            timezone=timezone,
        )
    elif action_lower == "delete":
        if not event_id:
            raise UserInputError("event_id is required for delete action")
        return await _delete_focus_time_event_impl(
            service=service,
            user_google_email=user_google_email,
            event_id=event_id,
            calendar_id=calendar_id,
        )
    else:
        raise UserInputError(
            f"Invalid action '{action_lower}'. Must be 'create', 'list', 'update', or 'delete'."
        )


# ---------------------------------------------------------------------------
# Legacy single-action tools (deprecated -- prefer ``manage_event``)
# ---------------------------------------------------------------------------


@server.tool(
    title="Query Freebusy",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("query_freebusy", is_read_only=True, service_type="calendar")
@require_google_service("calendar", "calendar_read")
async def query_freebusy(
    service,
    user_google_email: str,
    time_min: str,
    time_max: str,
    calendar_ids: Optional[StringList] = None,
    group_expansion_max: Optional[int] = None,
    calendar_expansion_max: Optional[int] = None,
) -> str:
    """
    Returns free/busy information for a set of calendars.

    Args:
        user_google_email (str): The user's Google email address. Required.
        time_min (str): The start of the interval for the query in RFC3339 format (e.g., '2024-05-12T10:00:00Z' or '2024-05-12').
        time_max (str): The end of the interval for the query in RFC3339 format (e.g., '2024-05-12T18:00:00Z' or '2024-05-12').
        calendar_ids (Optional[List[str]]): List of calendar identifiers to query. If not provided, queries the primary calendar. Use 'primary' for the user's primary calendar or specific calendar IDs obtained from `list_calendars`.
        group_expansion_max (Optional[int]): Maximum number of calendar identifiers to be provided for a single group. Optional. An error is returned for a group with more members than this value. Maximum value is 100.
        calendar_expansion_max (Optional[int]): Maximum number of calendars for which FreeBusy information is to be provided. Optional. Maximum value is 50.

    Returns:
        str: A formatted response showing free/busy information for each requested calendar, including busy time periods.
    """
    logger.info(
        f"[query_freebusy] Invoked. Email: '{user_google_email}', time_min: '{time_min}', time_max: '{time_max}'"
    )

    # Format time parameters
    formatted_time_min = _correct_time_format_for_api(time_min, "time_min", None)
    formatted_time_max = _correct_time_format_for_api(time_max, "time_max", None)

    # Default to primary calendar if no calendar IDs provided
    if not calendar_ids:
        calendar_ids = ["primary"]

    # Build the request body
    request_body: Dict[str, Any] = {
        "timeMin": formatted_time_min,
        "timeMax": formatted_time_max,
        "items": [{"id": cal_id} for cal_id in calendar_ids],
    }

    if group_expansion_max is not None:
        request_body["groupExpansionMax"] = group_expansion_max
    if calendar_expansion_max is not None:
        request_body["calendarExpansionMax"] = calendar_expansion_max

    logger.info(
        f"[query_freebusy] Request body: timeMin={formatted_time_min}, timeMax={formatted_time_max}, calendar_count={len(calendar_ids)}"
    )

    # Execute the freebusy query
    freebusy_result = await asyncio.to_thread(
        lambda: service.freebusy().query(body=request_body).execute()
    )

    # Parse the response
    calendars = freebusy_result.get("calendars", {})
    time_min_result = freebusy_result.get("timeMin", formatted_time_min)
    time_max_result = freebusy_result.get("timeMax", formatted_time_max)

    if not calendars:
        return f"No free/busy information found for the requested calendars for {user_google_email}."

    # Format the output
    output_lines = [
        f"Free/Busy information for {user_google_email}:",
        f"Time range: {time_min_result} to {time_max_result}",
        "",
    ]

    for cal_id, cal_data in calendars.items():
        output_lines.append(f"Calendar: {cal_id}")

        # Check for errors
        errors = cal_data.get("errors", [])
        if errors:
            output_lines.append("  Errors:")
            for error in errors:
                domain = error.get("domain", "unknown")
                reason = error.get("reason", "unknown")
                output_lines.append(f"    - {domain}: {reason}")
            output_lines.append("")
            continue

        # Get busy periods
        busy_periods = cal_data.get("busy", [])
        if not busy_periods:
            output_lines.append("  Status: Free (no busy periods)")
        else:
            output_lines.append(f"  Busy periods: {len(busy_periods)}")
            for period in busy_periods:
                start = period.get("start", "Unknown")
                end = period.get("end", "Unknown")
                output_lines.append(f"    - {start} to {end}")

        output_lines.append("")

    result_text = "\n".join(output_lines)
    logger.info(
        f"[query_freebusy] Successfully retrieved free/busy information for {len(calendars)} calendar(s)"
    )
    return result_text


@server.tool(
    title="Create Calendar",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("create_calendar", is_read_only=False, service_type="calendar")
@require_google_service("calendar", "calendar")
async def create_calendar(
    service,
    user_google_email: str,
    summary: str,
    description: Optional[str] = None,
    timezone: Optional[str] = None,
) -> str:
    """
    Creates a new secondary Google Calendar.

    Args:
        user_google_email (str): The user's Google email address. Required.
        summary (str): The title/name of the new calendar.
        description (Optional[str]): An optional description for the calendar.
        timezone (Optional[str]): IANA timezone for the calendar (e.g. 'America/New_York').

    Returns:
        str: The ID and summary of the newly created calendar.
    """
    logger.info(
        f"[create_calendar] Invoked. Email: '{user_google_email}', summary_len={len(summary)}"
    )

    body: Dict[str, Any] = {"summary": summary}
    if description:
        body["description"] = description
    if timezone:
        body["timeZone"] = timezone

    result = await asyncio.to_thread(
        lambda: service.calendars().insert(body=body).execute()
    )

    calendar_id = result["id"]
    calendar_summary = result.get("summary", summary)
    logger.info(f"[create_calendar] Created calendar with ID: {calendar_id}")
    return f"Created calendar '{calendar_summary}' (ID: {calendar_id})"
