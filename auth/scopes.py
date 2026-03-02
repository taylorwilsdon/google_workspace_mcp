"""
Google Workspace OAuth Scopes

This module centralizes OAuth scope definitions for Google Workspace integration.
Separated from service_decorator.py to avoid circular imports.
"""

import logging

logger = logging.getLogger(__name__)

# Global variable to store enabled tools (set by main.py)
_ENABLED_TOOLS = None

# Individual OAuth Scope Constants
USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
USERINFO_PROFILE_SCOPE = "https://www.googleapis.com/auth/userinfo.profile"
OPENID_SCOPE = "openid"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
CALENDAR_READONLY_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
CALENDAR_EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events"

# Google Drive scopes
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"

# Google Docs scopes
DOCS_READONLY_SCOPE = "https://www.googleapis.com/auth/documents.readonly"
DOCS_WRITE_SCOPE = "https://www.googleapis.com/auth/documents"

# Gmail API scopes
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_COMPOSE_SCOPE = "https://www.googleapis.com/auth/gmail.compose"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_LABELS_SCOPE = "https://www.googleapis.com/auth/gmail.labels"
GMAIL_SETTINGS_BASIC_SCOPE = "https://www.googleapis.com/auth/gmail.settings.basic"

# Google Chat API scopes
CHAT_READONLY_SCOPE = "https://www.googleapis.com/auth/chat.messages.readonly"
CHAT_WRITE_SCOPE = "https://www.googleapis.com/auth/chat.messages"
CHAT_SPACES_SCOPE = "https://www.googleapis.com/auth/chat.spaces"
CHAT_SPACES_READONLY_SCOPE = "https://www.googleapis.com/auth/chat.spaces.readonly"

# Google Sheets API scopes
SHEETS_READONLY_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
SHEETS_WRITE_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

# Google Forms API scopes
FORMS_BODY_SCOPE = "https://www.googleapis.com/auth/forms.body"
FORMS_BODY_READONLY_SCOPE = "https://www.googleapis.com/auth/forms.body.readonly"
FORMS_RESPONSES_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/forms.responses.readonly"
)

# Google Slides API scopes
SLIDES_SCOPE = "https://www.googleapis.com/auth/presentations"
SLIDES_READONLY_SCOPE = "https://www.googleapis.com/auth/presentations.readonly"

# Google Tasks API scopes
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"
TASKS_READONLY_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"

# Google Contacts (People API) scopes
CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts"
CONTACTS_READONLY_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"

# Google Custom Search API scope
CUSTOM_SEARCH_SCOPE = "https://www.googleapis.com/auth/cse"

# Google Apps Script API scopes
SCRIPT_PROJECTS_SCOPE = "https://www.googleapis.com/auth/script.projects"
SCRIPT_PROJECTS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/script.projects.readonly"
)
SCRIPT_DEPLOYMENTS_SCOPE = "https://www.googleapis.com/auth/script.deployments"
SCRIPT_DEPLOYMENTS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/script.deployments.readonly"
)
SCRIPT_PROCESSES_READONLY_SCOPE = "https://www.googleapis.com/auth/script.processes"
SCRIPT_METRICS_SCOPE = "https://www.googleapis.com/auth/script.metrics"

# Google scope hierarchy: broader scopes that implicitly cover narrower ones.
# See https://developers.google.com/gmail/api/auth/scopes,
# https://developers.google.com/drive/api/guides/api-specific-auth, etc.
SCOPE_HIERARCHY = {
    GMAIL_MODIFY_SCOPE: {
        GMAIL_READONLY_SCOPE,
        GMAIL_SEND_SCOPE,
        GMAIL_COMPOSE_SCOPE,
        GMAIL_LABELS_SCOPE,
    },
    DRIVE_SCOPE: {DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE},
    CALENDAR_SCOPE: {CALENDAR_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE},
    DOCS_WRITE_SCOPE: {DOCS_READONLY_SCOPE},
    SHEETS_WRITE_SCOPE: {SHEETS_READONLY_SCOPE},
    SLIDES_SCOPE: {SLIDES_READONLY_SCOPE},
    TASKS_SCOPE: {TASKS_READONLY_SCOPE},
    CONTACTS_SCOPE: {CONTACTS_READONLY_SCOPE},
    CHAT_WRITE_SCOPE: {CHAT_READONLY_SCOPE},
    CHAT_SPACES_SCOPE: {CHAT_SPACES_READONLY_SCOPE},
    FORMS_BODY_SCOPE: {FORMS_BODY_READONLY_SCOPE},
    SCRIPT_PROJECTS_SCOPE: {SCRIPT_PROJECTS_READONLY_SCOPE},
    SCRIPT_DEPLOYMENTS_SCOPE: {SCRIPT_DEPLOYMENTS_READONLY_SCOPE},
}


def has_required_scopes(available_scopes, required_scopes):
    """
    Check if available scopes satisfy all required scopes, accounting for
    Google's scope hierarchy (e.g., gmail.modify covers gmail.readonly).

    Args:
        available_scopes: Scopes the credentials have (set, list, or frozenset).
        required_scopes: Scopes that are required (set, list, or frozenset).

    Returns:
        True if all required scopes are satisfied.
    """
    available = set(available_scopes or [])
    required = set(required_scopes or [])
    # Expand available scopes with implied narrower scopes
    expanded = set(available)
    for broad_scope, covered in SCOPE_HIERARCHY.items():
        if broad_scope in available:
            expanded.update(covered)
    return all(scope in expanded for scope in required)


# Base OAuth scopes required for user identification
BASE_SCOPES = [USERINFO_EMAIL_SCOPE, USERINFO_PROFILE_SCOPE, OPENID_SCOPE]

# Service-specific scope groups
DOCS_SCOPES = [
    DOCS_READONLY_SCOPE,
    DOCS_WRITE_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_FILE_SCOPE,
]

CALENDAR_SCOPES = [CALENDAR_SCOPE, CALENDAR_READONLY_SCOPE, CALENDAR_EVENTS_SCOPE]

DRIVE_SCOPES = [DRIVE_SCOPE, DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE]

GMAIL_SCOPES = [
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_LABELS_SCOPE,
    GMAIL_SETTINGS_BASIC_SCOPE,
]

CHAT_SCOPES = [
    CHAT_READONLY_SCOPE,
    CHAT_WRITE_SCOPE,
    CHAT_SPACES_SCOPE,
    CHAT_SPACES_READONLY_SCOPE,
]

SHEETS_SCOPES = [SHEETS_READONLY_SCOPE, SHEETS_WRITE_SCOPE, DRIVE_READONLY_SCOPE]

FORMS_SCOPES = [
    FORMS_BODY_SCOPE,
    FORMS_BODY_READONLY_SCOPE,
    FORMS_RESPONSES_READONLY_SCOPE,
]

SLIDES_SCOPES = [SLIDES_SCOPE, SLIDES_READONLY_SCOPE]

TASKS_SCOPES = [TASKS_SCOPE, TASKS_READONLY_SCOPE]

CONTACTS_SCOPES = [CONTACTS_SCOPE, CONTACTS_READONLY_SCOPE]

CUSTOM_SEARCH_SCOPES = [CUSTOM_SEARCH_SCOPE]

SCRIPT_SCOPES = [
    SCRIPT_PROJECTS_SCOPE,
    SCRIPT_PROJECTS_READONLY_SCOPE,
    SCRIPT_DEPLOYMENTS_SCOPE,
    SCRIPT_DEPLOYMENTS_READONLY_SCOPE,
    SCRIPT_PROCESSES_READONLY_SCOPE,  # Required for list_script_processes
    SCRIPT_METRICS_SCOPE,  # Required for get_script_metrics
    DRIVE_FILE_SCOPE,  # Required for list/delete script projects (uses Drive API)
]

# Tool-to-scopes mapping
TOOL_SCOPES_MAP = {
    "gmail": GMAIL_SCOPES,
    "drive": DRIVE_SCOPES,
    "calendar": CALENDAR_SCOPES,
    "docs": DOCS_SCOPES,
    "sheets": SHEETS_SCOPES,
    "chat": CHAT_SCOPES,
    "forms": FORMS_SCOPES,
    "slides": SLIDES_SCOPES,
    "tasks": TASKS_SCOPES,
    "contacts": CONTACTS_SCOPES,
    "search": CUSTOM_SEARCH_SCOPES,
    "appscript": SCRIPT_SCOPES,
}

# Tool-to-read-only-scopes mapping
TOOL_READONLY_SCOPES_MAP = {
    "gmail": [GMAIL_READONLY_SCOPE],
    "drive": [DRIVE_READONLY_SCOPE],
    "calendar": [CALENDAR_READONLY_SCOPE],
    "docs": [DOCS_READONLY_SCOPE, DRIVE_READONLY_SCOPE],
    "sheets": [SHEETS_READONLY_SCOPE, DRIVE_READONLY_SCOPE],
    "chat": [CHAT_READONLY_SCOPE, CHAT_SPACES_READONLY_SCOPE],
    "forms": [FORMS_BODY_READONLY_SCOPE, FORMS_RESPONSES_READONLY_SCOPE],
    "slides": [SLIDES_READONLY_SCOPE],
    "tasks": [TASKS_READONLY_SCOPE],
    "contacts": [CONTACTS_READONLY_SCOPE],
    "search": CUSTOM_SEARCH_SCOPES,
    "appscript": [
        SCRIPT_PROJECTS_READONLY_SCOPE,
        SCRIPT_DEPLOYMENTS_READONLY_SCOPE,
        SCRIPT_PROCESSES_READONLY_SCOPE,
        SCRIPT_METRICS_SCOPE,
        DRIVE_READONLY_SCOPE,
    ],
}


def set_enabled_tools(enabled_tools):
    """
    Set the globally enabled tools list.

    Args:
        enabled_tools: List of enabled tool names.
    """
    global _ENABLED_TOOLS
    _ENABLED_TOOLS = enabled_tools
    logger.info(f"Enabled tools set for scope management: {enabled_tools}")


# Global variable to store read-only mode (set by main.py)
_READ_ONLY_MODE = False


def set_read_only(enabled: bool):
    """
    Set the global read-only mode.

    Args:
        enabled: Boolean indicating if read-only mode should be enabled.
    """
    global _READ_ONLY_MODE
    _READ_ONLY_MODE = enabled
    logger.info(f"Read-only mode set to: {enabled}")


def is_read_only_mode() -> bool:
    """Check if read-only mode is enabled."""
    return _READ_ONLY_MODE


def get_all_read_only_scopes() -> list[str]:
    """Get all possible read-only scopes across all tools."""
    all_scopes = set(BASE_SCOPES)
    for scopes in TOOL_READONLY_SCOPES_MAP.values():
        all_scopes.update(scopes)
    return list(all_scopes)


def get_current_scopes():
    """
    Returns scopes for currently enabled tools.
    Uses globally set enabled tools or all tools if not set.

    .. deprecated::
        This function is a thin wrapper around get_scopes_for_tools() and exists
        for backwards compatibility. Prefer using get_scopes_for_tools() directly
        for new code, which allows explicit control over the tool list parameter.

    Returns:
        List of unique scopes for the enabled tools plus base scopes.
    """
    return get_scopes_for_tools(_ENABLED_TOOLS)


def get_scopes_for_tools(enabled_tools=None):
    """
    Returns scopes for enabled tools only.

    Args:
        enabled_tools: List of enabled tool names. If None, returns all scopes.

    Returns:
        List of unique scopes for the enabled tools plus base scopes.
    """
    # Granular permissions mode overrides both full and read-only scope maps.
    # Lazy import with guard to avoid circular dependency during module init
    # (SCOPES = get_scopes_for_tools() runs at import time before auth.permissions
    # is fully loaded, but permissions mode is never active at that point).
    try:
        from auth.permissions import is_permissions_mode, get_all_permission_scopes

        if is_permissions_mode():
            scopes = BASE_SCOPES.copy()
            scopes.extend(get_all_permission_scopes())
            logger.debug(
                "Generated scopes from granular permissions: %d unique scopes",
                len(set(scopes)),
            )
            return list(set(scopes))
    except ImportError:
        pass

    if enabled_tools is None:
        # Default behavior - return all scopes
        enabled_tools = TOOL_SCOPES_MAP.keys()

    # Start with base scopes (always required)
    scopes = BASE_SCOPES.copy()

    # Determine which map to use based on read-only mode
    scope_map = TOOL_READONLY_SCOPES_MAP if _READ_ONLY_MODE else TOOL_SCOPES_MAP
    mode_str = "read-only" if _READ_ONLY_MODE else "full"

    # Add scopes for each enabled tool
    for tool in enabled_tools:
        if tool in scope_map:
            scopes.extend(scope_map[tool])

    logger.debug(
        f"Generated {mode_str} scopes for tools {list(enabled_tools)}: {len(set(scopes))} unique scopes"
    )
    # Return unique scopes
    return list(set(scopes))


# Combined scopes for all supported Google Workspace operations (backwards compatibility)
SCOPES = get_scopes_for_tools()
