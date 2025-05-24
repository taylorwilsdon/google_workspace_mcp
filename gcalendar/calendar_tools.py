"""
Google Calendar MCP Tools

This module provides MCP tools for interacting with Google Calendar API.
"""

import datetime
import logging
import asyncio
import os
import sys
from typing import List, Optional, Dict, Any

from mcp import types
from fastapi import Header

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from auth.google_auth import (
    get_credentials,
    start_auth_flow,
    CONFIG_CLIENT_SECRETS_PATH,
)

# Import the server directly (will be initialized before this module is imported)
# Also import the OAUTH_STATE_TO_SESSION_ID_MAP for linking OAuth state to MCP session
from core.server import (
    server,
    OAUTH_REDIRECT_URI,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE,
)


# Configure module logger
logger = logging.getLogger(__name__)


# Helper function to ensure time strings for API calls are correctly formatted
def _correct_time_format_for_api(
    time_str: Optional[str], param_name: str
) -> Optional[str]:
    if not time_str:
        return None

    # Log the incoming time string for debugging
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


# --- Helper for Authentication and Service Initialization ---


async def _get_authenticated_calendar_service(
    tool_name: str,
    user_google_email: Optional[str],
    mcp_session_id: Optional[str],
    required_scopes: List[str],
) -> tuple[Any, str] | types.CallToolResult:
    """
    Handles common authentication and Google Calendar service initialization logic.
    Returns a tuple of (service, log_user_email) on success, or CallToolResult on auth failure.
    """
    logger.info(
        f"[{tool_name}] Attempting to get authenticated calendar service. Email: '{user_google_email}', Session: '{mcp_session_id}', Session Type: {type(mcp_session_id)}"
    )

    credentials = await asyncio.to_thread(
        get_credentials,
        user_google_email=user_google_email,
        required_scopes=required_scopes,
        client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,
        session_id=mcp_session_id,
    )

    if not credentials or not credentials.valid:
        logger.warning(
            f"[{tool_name}] No valid credentials. Session: '{mcp_session_id}', Email: '{user_google_email}'."
        )
        if user_google_email and "@" in user_google_email:
            logger.info(
                f"[{tool_name}] Valid email '{user_google_email}' provided, initiating auth flow."
            )
            # This call will return a CallToolResult which should be propagated
            return await start_auth_flow(
                mcp_session_id=mcp_session_id,
                user_google_email=user_google_email,
                service_name="Google Calendar",
                redirect_uri=OAUTH_REDIRECT_URI,
            )
        else:
            error_msg = f"Authentication required for {tool_name}. No active authenticated session, and no valid 'user_google_email' provided. LLM: Please ask the user for their Google email address and retry, or use the 'start_google_auth' tool with their email and service_name='Google Calendar'."
            logger.info(f"[{tool_name}] {error_msg}")
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=error_msg)]
            )

    try:
        service = build("calendar", "v3", credentials=credentials)
        log_user_email = user_google_email or "Unknown"

        # Try to get email from id_token if it's a dict, otherwise use user_google_email or "Unknown"
        if not user_google_email and credentials and credentials.id_token:
            try:
                if isinstance(credentials.id_token, dict):
                    log_user_email = credentials.id_token.get("email", "Unknown")
                # If id_token is a string (JWT), we'd need to decode it, but for logging purposes "Unknown" is fine
            except Exception:
                pass  # Keep log_user_email as "Unknown"

        logger.info(
            f"[{tool_name}] Successfully built calendar service. User associated with creds: {log_user_email}"
        )
        return service, log_user_email
    except Exception as e:
        message = f"[{tool_name}] Unexpected error building calendar service: {e}."
        logger.exception(message)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )


# --- Tool Implementations ---

@server.tool()
async def list_calendars(
    user_google_email: Optional[str] = None,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Retrieves a list of calendars accessible to the authenticated user.
    Prioritizes authentication via the active MCP session (`mcp_session_id`).
    If the session isn't authenticated for Calendar, it falls back to using `user_google_email`.
    If neither provides valid credentials, it returns a message guiding the LLM to request the user's email
    or initiate the authentication flow via the `start_google_auth` tool (provide service_name='Google Calendar').

    Args:
        user_google_email (Optional[str]): The user's Google email address. Required if the MCP session is not already authenticated for Calendar access.
        mcp_session_id (Optional[str]): The active MCP session ID (automatically injected by FastMCP from the Mcp-Session-Id header). Used for session-based authentication.

    Returns:
        types.CallToolResult: Contains a list of the user's calendars (summary, ID, primary status),
                               an error message if the API call fails,
                               or an authentication guidance message if credentials are required.
    """

    logger.info(
        f"[list_calendars] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}'"
    )
    auth_result = await _get_authenticated_calendar_service(
        tool_name="list_calendars",
        user_google_email=user_google_email,
        mcp_session_id=mcp_session_id,
        required_scopes=[CALENDAR_READONLY_SCOPE],
    )
    if isinstance(auth_result, types.CallToolResult):
        return auth_result  # Propagate auth error or auth initiation message
    service, log_user_email = auth_result

    try:
        calendar_list_response = await asyncio.to_thread(
            service.calendarList().list().execute
        )
        items = calendar_list_response.get("items", [])
        if not items:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text", text=f"No calendars found for {log_user_email}."
                    )
                ]
            )

        calendars_summary_list = [
            f"- \"{cal.get('summary', 'No Summary')}\"{' (Primary)' if cal.get('primary') else ''} (ID: {cal['id']})"
            for cal in items
        ]
        text_output = (
            f"Successfully listed {len(items)} calendars for {log_user_email}:\n"
            + "\n".join(calendars_summary_list)
        )
        logger.info(f"Successfully listed {len(items)} calendars for {log_user_email}.")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text_output)]
        )
    except HttpError as error:
        message = f"API error listing calendars: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with user's email and service_name='Google Calendar'."
        logger.error(message, exc_info=True)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )
    except Exception as e:
        message = f"Unexpected error listing calendars: {e}."
        logger.exception(message)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )


@server.tool()
async def get_events(
    user_google_email: Optional[str] = None,
    calendar_id: str = "primary",
    time_min: Optional[str] = None,
    time_max: Optional[str] = None,
    max_results: int = 25,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Retrieves a list of events from a specified Google Calendar within a given time range.
    Prioritizes authentication via the active MCP session (`mcp_session_id`).
    If the session isn't authenticated for Calendar, it falls back to using `user_google_email`.
    If neither provides valid credentials, it returns a message guiding the LLM to request the user's email
    or initiate the authentication flow via the `start_google_auth` tool (provide service_name='Google Calendar').

    Args:
        user_google_email (Optional[str]): The user's Google email address. Required if the MCP session is not already authenticated for Calendar access.
        calendar_id (str): The ID of the calendar to query. Use 'primary' for the user's primary calendar. Defaults to 'primary'. Calendar IDs can be obtained using `list_calendars`.
        time_min (Optional[str]): The start of the time range (inclusive) in RFC3339 format (e.g., '2024-05-12T10:00:00Z' or '2024-05-12'). If omitted, defaults to the current time.
        time_max (Optional[str]): The end of the time range (exclusive) in RFC3339 format. If omitted, events starting from `time_min` onwards are considered (up to `max_results`).
        max_results (int): The maximum number of events to return. Defaults to 25.
        mcp_session_id (Optional[str]): The active MCP session ID (automatically injected by FastMCP from the Mcp-Session-Id header). Used for session-based authentication.

    Returns:
        types.CallToolResult: Contains a list of events (summary, start time, link) within the specified range,
                               an error message if the API call fails,
                               or an authentication guidance message if credentials are required.
    """
    auth_result = await _get_authenticated_calendar_service(
        tool_name="get_events",
        user_google_email=user_google_email,
        mcp_session_id=mcp_session_id,
        required_scopes=[CALENDAR_READONLY_SCOPE],
    )
    if isinstance(auth_result, types.CallToolResult):
        return auth_result
    service, log_user_email = auth_result

    try:
        logger.info(
            f"[get_events] Raw time parameters - time_min: '{time_min}', time_max: '{time_max}'"
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

        # Log the final API call parameters
        logger.info(
            f"[get_events] Final API parameters - calendarId: '{calendar_id}', timeMin: '{effective_time_min}', timeMax: '{effective_time_max}', maxResults: {max_results}"
        )

        events_result = await asyncio.to_thread(
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=effective_time_min,
                timeMax=effective_time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute
        )
        items = events_result.get("items", [])
        if not items:
            return types.CallToolResult(
                content=[
                    types.TextContent(
                        type="text",
                        text=f"No events found in calendar '{calendar_id}' for {log_user_email} for the specified time range.",
                    )
                ]
            )

        event_details_list = []
        for item in items:
            summary = item.get("summary", "No Title")
            start = item["start"].get("dateTime", item["start"].get("date"))
            link = item.get("htmlLink", "No Link")
            event_id = item.get("id", "No ID")
            # Include the event ID in the output so users can copy it for modify/delete operations
            event_details_list.append(
                f'- "{summary}" (Starts: {start}) ID: {event_id} | Link: {link}'
            )

        text_output = (
            f"Successfully retrieved {len(items)} events from calendar '{calendar_id}' for {log_user_email}:\n"
            + "\n".join(event_details_list)
        )
        logger.info(f"Successfully retrieved {len(items)} events for {log_user_email}.")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text_output)]
        )
    except HttpError as error:
        message = f"API error getting events: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with user's email and service_name='Google Calendar'."
        logger.error(message, exc_info=True)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )
    except Exception as e:
        message = f"Unexpected error getting events: {e}."
        logger.exception(message)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )


@server.tool()
async def create_event(
    summary: str,
    start_time: str,
    end_time: str,
    user_google_email: Optional[str] = None,
    calendar_id: str = "primary",
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    timezone: Optional[str] = None,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Creates a new event. Prioritizes authenticated MCP session, then `user_google_email`.
    If no valid authentication is found, guides the LLM to obtain user's email or use `start_google_auth` (provide service_name='Google Calendar').

    Args:
        summary (str): Event title.
        start_time (str): Start time (RFC3339, e.g., "2023-10-27T10:00:00-07:00" or "2023-10-27" for all-day).
        end_time (str): End time (RFC3339, e.g., "2023-10-27T11:00:00-07:00" or "2023-10-28" for all-day).
        user_google_email (Optional[str]): User's Google email. Used if session isn't authenticated.
        calendar_id (str): Calendar ID (default: 'primary').
        description (Optional[str]): Event description.
        location (Optional[str]): Event location.
        attendees (Optional[List[str]]): Attendee email addresses.
        timezone (Optional[str]): Timezone (e.g., "America/New_York").
        mcp_session_id (Optional[str]): Active MCP session ID (injected by FastMCP from Mcp-Session-Id header).

    Returns:
        A CallToolResult confirming creation or an error/auth guidance message.
    """
    logger.info(
        f"[create_event] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}', Summary: {summary}"
    )
    auth_result = await _get_authenticated_calendar_service(
        tool_name="create_event",
        user_google_email=user_google_email,
        mcp_session_id=mcp_session_id,
        required_scopes=[CALENDAR_EVENTS_SCOPE],
    )
    if isinstance(auth_result, types.CallToolResult):
        return auth_result
    service, log_user_email = auth_result

    try:

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

        created_event = await asyncio.to_thread(
            service.events().insert(calendarId=calendar_id, body=event_body).execute
        )

        link = created_event.get("htmlLink", "No link available")
        # Corrected confirmation_message to use log_user_email
        confirmation_message = f"Successfully created event '{created_event.get('summary', summary)}' for {log_user_email}. Link: {link}"
        # Corrected logger to use log_user_email and include event ID
        logger.info(
            f"Event created successfully for {log_user_email}. ID: {created_event.get('id')}, Link: {link}"
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=confirmation_message)]
        )
    except HttpError as error:
        # Corrected error message to use log_user_email and provide better guidance
        # log_user_email_for_error is now log_user_email from the helper or the original user_google_email
        message = f"API error creating event: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({log_user_email if log_user_email != 'Unknown' else 'target Google account'}) and service_name='Google Calendar'."
        logger.error(message, exc_info=True)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )
    except Exception as e:
        message = f"Unexpected error creating event: {e}."
        logger.exception(message)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )


@server.tool()
async def modify_event(
    event_id: str,
    user_google_email: Optional[str] = None,
    calendar_id: str = "primary",
    summary: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    description: Optional[str] = None,
    location: Optional[str] = None,
    attendees: Optional[List[str]] = None,
    timezone: Optional[str] = None,
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Modifies an existing event. Prioritizes authenticated MCP session, then `user_google_email`.
    If no valid authentication is found, guides the LLM to obtain user's email or use `start_google_auth` (provide service_name='Google Calendar').

    Args:
        event_id (str): The ID of the event to modify.
        user_google_email (Optional[str]): User's Google email. Used if session isn't authenticated.
        calendar_id (str): Calendar ID (default: 'primary').
        summary (Optional[str]): New event title.
        start_time (Optional[str]): New start time (RFC3339, e.g., "2023-10-27T10:00:00-07:00" or "2023-10-27" for all-day).
        end_time (Optional[str]): New end time (RFC3339, e.g., "2023-10-27T11:00:00-07:00" or "2023-10-28" for all-day).
        description (Optional[str]): New event description.
        location (Optional[str]): New event location.
        attendees (Optional[List[str]]): New attendee email addresses.
        timezone (Optional[str]): New timezone (e.g., "America/New_York").
        mcp_session_id (Optional[str]): Active MCP session ID (injected by FastMCP from Mcp-Session-Id header).

    Returns:
        A CallToolResult confirming modification or an error/auth guidance message.
    """
    logger.info(
        f"[modify_event] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}', Event ID: {event_id}"
    )
    auth_result = await _get_authenticated_calendar_service(
        tool_name="modify_event",
        user_google_email=user_google_email,
        mcp_session_id=mcp_session_id,
        required_scopes=[CALENDAR_EVENTS_SCOPE],
    )
    if isinstance(auth_result, types.CallToolResult):
        return auth_result
    service, log_user_email = auth_result

    try:

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
                f"[modify_event] Timezone provided but start_time and end_time are missing. Timezone will not be applied unless start/end times are also provided."
            )

        if not event_body:
            message = "No fields provided to modify the event."
            logger.warning(f"[modify_event] {message}")
            return types.CallToolResult(
                isError=True, content=[types.TextContent(type="text", text=message)]
            )

        # Log the event ID for debugging
        logger.info(
            f"[modify_event] Attempting to update event with ID: '{event_id}' in calendar '{calendar_id}'"
        )

        # Try to get the event first to verify it exists
        try:
            await asyncio.to_thread(
                service.events().get(calendarId=calendar_id, eventId=event_id).execute
            )
            logger.info(
                f"[modify_event] Successfully verified event exists before update"
            )
        except HttpError as get_error:
            if get_error.resp.status == 404:
                logger.error(
                    f"[modify_event] Event not found during pre-update verification: {get_error}"
                )
                message = f"Event not found during verification. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. This may be due to incorrect ID format or the event no longer exists."
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=message)]
                )
            else:
                logger.warning(
                    f"[modify_event] Error during pre-update verification, but proceeding with update: {get_error}"
                )

        # Proceed with the update
        updated_event = await asyncio.to_thread(
            service.events()
            .update(calendarId=calendar_id, eventId=event_id, body=event_body)
            .execute
        )

        link = updated_event.get("htmlLink", "No link available")
        confirmation_message = f"Successfully modified event '{updated_event.get('summary', summary)}' (ID: {event_id}) for {log_user_email}. Link: {link}"
        logger.info(
            f"Event modified successfully for {log_user_email}. ID: {updated_event.get('id')}, Link: {link}"
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=confirmation_message)]
        )
    except HttpError as error:
        # Check for 404 Not Found error specifically
        if error.resp.status == 404:
            message = f"Event not found. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. LLM: The event may have been deleted, or the event ID might be incorrect. Verify the event exists using 'get_events' before attempting to modify it."
            logger.error(f"[modify_event] {message}")
        else:
            message = f"API error modifying event (ID: {event_id}): {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({log_user_email if log_user_email != 'Unknown' else 'target Google account'}) and service_name='Google Calendar'."
            logger.error(message, exc_info=True)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )
    except Exception as e:
        message = f"Unexpected error modifying event (ID: {event_id}): {e}."
        logger.exception(message)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )


@server.tool()
async def delete_event(
    event_id: str,
    user_google_email: Optional[str] = None,
    calendar_id: str = "primary",
    mcp_session_id: Optional[str] = Header(None, alias="Mcp-Session-Id"),
) -> types.CallToolResult:
    """
    Deletes an existing event. Prioritizes authenticated MCP session, then `user_google_email`.
    If no valid authentication is found, guides the LLM to obtain user's email or use `start_google_auth` (provide service_name='Google Calendar').

    Args:
        event_id (str): The ID of the event to delete.
        user_google_email (Optional[str]): User's Google email. Used if session isn't authenticated.
        calendar_id (str): Calendar ID (default: 'primary').
        mcp_session_id (Optional[str]): Active MCP session ID (injected by FastMCP from Mcp-Session-Id header).

    Returns:
        A CallToolResult confirming deletion or an error/auth guidance message.
    """
    logger.info(
        f"[delete_event] Invoked. Session: '{mcp_session_id}', Email: '{user_google_email}', Event ID: {event_id}"
    )
    auth_result = await _get_authenticated_calendar_service(
        tool_name="delete_event",
        user_google_email=user_google_email,
        mcp_session_id=mcp_session_id,
        required_scopes=[CALENDAR_EVENTS_SCOPE],
    )
    if isinstance(auth_result, types.CallToolResult):
        return auth_result
    service, log_user_email = auth_result

    try:

        # Log the event ID for debugging
        logger.info(
            f"[delete_event] Attempting to delete event with ID: '{event_id}' in calendar '{calendar_id}'"
        )

        # Try to get the event first to verify it exists
        try:
            await asyncio.to_thread(
                service.events().get(calendarId=calendar_id, eventId=event_id).execute
            )
            logger.info(
                f"[delete_event] Successfully verified event exists before deletion"
            )
        except HttpError as get_error:
            if get_error.resp.status == 404:
                logger.error(
                    f"[delete_event] Event not found during pre-delete verification: {get_error}"
                )
                message = f"Event not found during verification. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. This may be due to incorrect ID format or the event no longer exists."
                return types.CallToolResult(
                    isError=True, content=[types.TextContent(type="text", text=message)]
                )
            else:
                logger.warning(
                    f"[delete_event] Error during pre-delete verification, but proceeding with deletion: {get_error}"
                )

        # Proceed with the deletion
        await asyncio.to_thread(
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute
        )

        confirmation_message = f"Successfully deleted event (ID: {event_id}) from calendar '{calendar_id}' for {log_user_email}."
        logger.info(f"Event deleted successfully for {log_user_email}. ID: {event_id}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=confirmation_message)]
        )
    except HttpError as error:
        # Check for 404 Not Found error specifically
        if error.resp.status == 404:
            message = f"Event not found. The event with ID '{event_id}' could not be found in calendar '{calendar_id}'. LLM: The event may have been deleted already, or the event ID might be incorrect."
            logger.error(f"[delete_event] {message}")
        else:
            message = f"API error deleting event (ID: {event_id}): {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({log_user_email if log_user_email != 'Unknown' else 'target Google account'}) and service_name='Google Calendar'."
            logger.error(message, exc_info=True)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )
    except Exception as e:
        message = f"Unexpected error deleting event (ID: {event_id}): {e}."
        logger.exception(message)
        return types.CallToolResult(
            isError=True, content=[types.TextContent(type="text", text=message)]
        )
