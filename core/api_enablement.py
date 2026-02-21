import re
from typing import Dict, List, Optional, Tuple


SUPPORTED_APIS: Dict[str, Tuple[List[str], str]] = {
    "calendar-json.googleapis.com": (["calendar"], "Google Calendar"),
    "drive.googleapis.com": (["drive"], "Google Drive"),
    "gmail.googleapis.com": (["gmail"], "Gmail"),
    "keep.googleapis.com": (["keep"], "Google Keep"),
    "docs.googleapis.com": (["docs"], "Google Docs"),
    "sheets.googleapis.com": (["sheets"], "Google Sheets"),
    "slides.googleapis.com": (["slides"], "Google Slides"),
    "forms.googleapis.com": (["forms"], "Google Forms"),
    "tasks.googleapis.com": (["tasks"], "Google Tasks"),
    "chat.googleapis.com": (["chat"], "Google Chat"),
    "customsearch.googleapis.com": (["customsearch", "search"], "Google Custom Search"),
}


API_ENABLEMENT_LINK_BASE = "https://console.cloud.google.com/flows/enableapi?apiid={api}"


SERVICE_NAME_TO_API: Dict[str, str] = { name: api for api, (_, name) in SUPPORTED_APIS.items() }


INTERNAL_SERVICE_TO_API: Dict[str, str] = {}
for api, (internal_names, _) in SUPPORTED_APIS.items():
    for internal in internal_names:
        INTERNAL_SERVICE_TO_API[internal] = api


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

    if api_service and api_service in SUPPORTED_APIS:
        enable_link = API_ENABLEMENT_LINK_BASE.format(api=api_service)
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
