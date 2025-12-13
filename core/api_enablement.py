import re
from typing import Dict, Optional, Tuple


API_ENABLEMENT_LINKS: Dict[str, str] = {
    "calendar-json.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com",
    "drive.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com",
    "gmail.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com",
    "docs.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=docs.googleapis.com",
    "sheets.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=sheets.googleapis.com",
    "slides.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=slides.googleapis.com",
    "forms.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=forms.googleapis.com",
    "tasks.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=tasks.googleapis.com",
    "chat.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=chat.googleapis.com",
    "customsearch.googleapis.com": "https://console.cloud.google.com/flows/enableapi?apiid=customsearch.googleapis.com",
}


SERVICE_NAME_TO_API: Dict[str, str] = {
    "Google Calendar": "calendar-json.googleapis.com",
    "Google Drive": "drive.googleapis.com",
    "Gmail": "gmail.googleapis.com",
    "Google Docs": "docs.googleapis.com",
    "Google Sheets": "sheets.googleapis.com",
    "Google Slides": "slides.googleapis.com",
    "Google Forms": "forms.googleapis.com",
    "Google Tasks": "tasks.googleapis.com",
    "Google Chat": "chat.googleapis.com",
    "Google Custom Search": "customsearch.googleapis.com",
}


INTERNAL_SERVICE_TO_API: Dict[str, str] = {
    "calendar": "calendar-json.googleapis.com",
    "drive": "drive.googleapis.com",
    "gmail": "gmail.googleapis.com",
    "docs": "docs.googleapis.com",
    "sheets": "sheets.googleapis.com",
    "slides": "slides.googleapis.com",
    "forms": "forms.googleapis.com",
    "tasks": "tasks.googleapis.com",
    "chat": "chat.googleapis.com",
    "customsearch": "customsearch.googleapis.com",
    "search": "customsearch.googleapis.com",
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
