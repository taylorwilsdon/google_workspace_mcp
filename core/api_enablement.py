import re
from typing import Dict, Optional, Tuple

# API domain constants
CALENDAR_API = "calendar-json.googleapis.com"
DRIVE_API = "drive.googleapis.com"
GMAIL_API = "gmail.googleapis.com"
DOCS_API = "docs.googleapis.com"
SHEETS_API = "sheets.googleapis.com"
SLIDES_API = "slides.googleapis.com"
FORMS_API = "forms.googleapis.com"
TASKS_API = "tasks.googleapis.com"
CHAT_API = "chat.googleapis.com"
CUSTOMSEARCH_API = "customsearch.googleapis.com"

API_ENABLEMENT_LINKS: Dict[str, str] = {
    CALENDAR_API: f"https://console.cloud.google.com/flows/enableapi?apiid={CALENDAR_API}",
    DRIVE_API: f"https://console.cloud.google.com/flows/enableapi?apiid={DRIVE_API}",
    GMAIL_API: f"https://console.cloud.google.com/flows/enableapi?apiid={GMAIL_API}",
    DOCS_API: f"https://console.cloud.google.com/flows/enableapi?apiid={DOCS_API}",
    SHEETS_API: f"https://console.cloud.google.com/flows/enableapi?apiid={SHEETS_API}",
    SLIDES_API: f"https://console.cloud.google.com/flows/enableapi?apiid={SLIDES_API}",
    FORMS_API: f"https://console.cloud.google.com/flows/enableapi?apiid={FORMS_API}",
    TASKS_API: f"https://console.cloud.google.com/flows/enableapi?apiid={TASKS_API}",
    CHAT_API: f"https://console.cloud.google.com/flows/enableapi?apiid={CHAT_API}",
    CUSTOMSEARCH_API: f"https://console.cloud.google.com/flows/enableapi?apiid={CUSTOMSEARCH_API}",
}


SERVICE_NAME_TO_API: Dict[str, str] = {
    "Google Calendar": CALENDAR_API,
    "Google Drive": DRIVE_API,
    "Gmail": GMAIL_API,
    "Google Docs": DOCS_API,
    "Google Sheets": SHEETS_API,
    "Google Slides": SLIDES_API,
    "Google Forms": FORMS_API,
    "Google Tasks": TASKS_API,
    "Google Chat": CHAT_API,
    "Google Custom Search": CUSTOMSEARCH_API,
}


INTERNAL_SERVICE_TO_API: Dict[str, str] = {
    "calendar": CALENDAR_API,
    "drive": DRIVE_API,
    "gmail": GMAIL_API,
    "docs": DOCS_API,
    "sheets": SHEETS_API,
    "slides": SLIDES_API,
    "forms": FORMS_API,
    "tasks": TASKS_API,
    "chat": CHAT_API,
    "customsearch": CUSTOMSEARCH_API,
    "search": CUSTOMSEARCH_API,
}


def extract_api_info_from_error(
    error_details: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract API service and project ID from error details.

    Returns:
        Tuple of (api_service, project_id) or (None, None) if not found
    """
    api_pattern = r"https://console\.developers\.google\.com/apis/api/([^/]+)/overview"
    project_pattern = r"project[=\s]+([a-zA-Z0-9-]+)"

    api_match = re.search(api_pattern, error_details)
    project_match = re.search(project_pattern, error_details)

    api_service = api_match.group(1) if api_match else None
    project_id = project_match.group(1) if project_match else None

    return api_service, project_id


def get_api_enablement_message(
    error_details: str, service_type: Optional[str] = None
) -> str:
    """
    Generate a helpful error message with direct API enablement link.

    Args:
        error_details: The error details string from the HttpError
        service_type: Optional service type (e.g., "calendar", "gmail", or "Google Calendar")

    Returns:
        Formatted error message with enablement link
    """
    api_service, project_id = extract_api_info_from_error(error_details)

    if not api_service and service_type:
        # Check internal service names first (e.g., "calendar", "gmail")
        api_service = INTERNAL_SERVICE_TO_API.get(service_type)
        if not api_service:
            # Check display names (e.g., "Google Calendar")
            api_service = SERVICE_NAME_TO_API.get(service_type)

    if api_service and api_service in API_ENABLEMENT_LINKS:
        enable_link = API_ENABLEMENT_LINKS[api_service]
        service_display_name = next(
            (name for name, api in SERVICE_NAME_TO_API.items() if api == api_service),
            api_service,
        )

        message = (
            f"{service_display_name} API is not enabled for your project"
            f"{f' ({project_id})' if project_id else ''}.\n\n"
            f"Enable it here: {enable_link}\n\n"
            f"After enabling, wait 1-2 minutes for the change to propagate, then try again."
            f"IMPORTANT - LLM: share the link provided as a clickable hyperlink and instruct the user to enable the required API."
        )

        return message

    return ""
