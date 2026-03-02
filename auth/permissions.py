"""
Granular per-service permission levels.

Each service has named permission levels (cumulative), mapping to a list of
OAuth scopes. The levels for a service are ordered from least to most
permissive — requesting level N implicitly includes all scopes from levels < N.

Usage:
    --permissions gmail:organize drive:readonly

Gmail levels: readonly, organize, drafts, send, full
Other services: readonly, full (extensible by adding entries to SERVICE_PERMISSION_LEVELS)
"""

import logging
from typing import Dict, List, Optional, Tuple

from auth.scopes import (
    GMAIL_READONLY_SCOPE,
    GMAIL_LABELS_SCOPE,
    GMAIL_MODIFY_SCOPE,
    GMAIL_COMPOSE_SCOPE,
    GMAIL_SEND_SCOPE,
    GMAIL_SETTINGS_BASIC_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_FILE_SCOPE,
    DRIVE_SCOPE,
    CALENDAR_READONLY_SCOPE,
    CALENDAR_EVENTS_SCOPE,
    CALENDAR_SCOPE,
    DOCS_READONLY_SCOPE,
    DOCS_WRITE_SCOPE,
    SHEETS_READONLY_SCOPE,
    SHEETS_WRITE_SCOPE,
    CHAT_READONLY_SCOPE,
    CHAT_WRITE_SCOPE,
    CHAT_SPACES_SCOPE,
    CHAT_SPACES_READONLY_SCOPE,
    FORMS_BODY_SCOPE,
    FORMS_BODY_READONLY_SCOPE,
    FORMS_RESPONSES_READONLY_SCOPE,
    SLIDES_SCOPE,
    SLIDES_READONLY_SCOPE,
    TASKS_SCOPE,
    TASKS_READONLY_SCOPE,
    CONTACTS_SCOPE,
    CONTACTS_READONLY_SCOPE,
    CUSTOM_SEARCH_SCOPE,
    SCRIPT_PROJECTS_SCOPE,
    SCRIPT_PROJECTS_READONLY_SCOPE,
    SCRIPT_DEPLOYMENTS_SCOPE,
    SCRIPT_DEPLOYMENTS_READONLY_SCOPE,
    SCRIPT_PROCESSES_READONLY_SCOPE,
    SCRIPT_METRICS_SCOPE,
)

logger = logging.getLogger(__name__)

# Ordered permission levels per service.
# Each entry is (level_name, [additional_scopes_at_this_level]).
# Scopes are CUMULATIVE: level N includes all scopes from levels 0..N.
SERVICE_PERMISSION_LEVELS: Dict[str, List[Tuple[str, List[str]]]] = {
    "gmail": [
        ("readonly", [GMAIL_READONLY_SCOPE]),
        ("organize", [GMAIL_LABELS_SCOPE, GMAIL_MODIFY_SCOPE]),
        ("drafts", [GMAIL_COMPOSE_SCOPE]),
        ("send", [GMAIL_SEND_SCOPE]),
        ("full", [GMAIL_SETTINGS_BASIC_SCOPE]),
    ],
    "drive": [
        ("readonly", [DRIVE_READONLY_SCOPE]),
        ("full", [DRIVE_SCOPE, DRIVE_FILE_SCOPE]),
    ],
    "calendar": [
        ("readonly", [CALENDAR_READONLY_SCOPE]),
        ("full", [CALENDAR_SCOPE, CALENDAR_EVENTS_SCOPE]),
    ],
    "docs": [
        ("readonly", [DOCS_READONLY_SCOPE, DRIVE_READONLY_SCOPE]),
        ("full", [DOCS_WRITE_SCOPE, DRIVE_READONLY_SCOPE, DRIVE_FILE_SCOPE]),
    ],
    "sheets": [
        ("readonly", [SHEETS_READONLY_SCOPE, DRIVE_READONLY_SCOPE]),
        ("full", [SHEETS_WRITE_SCOPE, DRIVE_READONLY_SCOPE]),
    ],
    "chat": [
        ("readonly", [CHAT_READONLY_SCOPE, CHAT_SPACES_READONLY_SCOPE]),
        ("full", [CHAT_WRITE_SCOPE, CHAT_SPACES_SCOPE]),
    ],
    "forms": [
        ("readonly", [FORMS_BODY_READONLY_SCOPE, FORMS_RESPONSES_READONLY_SCOPE]),
        ("full", [FORMS_BODY_SCOPE, FORMS_RESPONSES_READONLY_SCOPE]),
    ],
    "slides": [
        ("readonly", [SLIDES_READONLY_SCOPE]),
        ("full", [SLIDES_SCOPE]),
    ],
    "tasks": [
        ("readonly", [TASKS_READONLY_SCOPE]),
        ("full", [TASKS_SCOPE]),
    ],
    "contacts": [
        ("readonly", [CONTACTS_READONLY_SCOPE]),
        ("full", [CONTACTS_SCOPE]),
    ],
    "search": [
        ("readonly", [CUSTOM_SEARCH_SCOPE]),
        ("full", [CUSTOM_SEARCH_SCOPE]),
    ],
    "appscript": [
        (
            "readonly",
            [
                SCRIPT_PROJECTS_READONLY_SCOPE,
                SCRIPT_DEPLOYMENTS_READONLY_SCOPE,
                SCRIPT_PROCESSES_READONLY_SCOPE,
                SCRIPT_METRICS_SCOPE,
                DRIVE_READONLY_SCOPE,
            ],
        ),
        (
            "full",
            [
                SCRIPT_PROJECTS_SCOPE,
                SCRIPT_DEPLOYMENTS_SCOPE,
                SCRIPT_PROCESSES_READONLY_SCOPE,
                SCRIPT_METRICS_SCOPE,
                DRIVE_FILE_SCOPE,
            ],
        ),
    ],
}

# Module-level state: parsed --permissions config
# Dict mapping service_name -> level_name, e.g. {"gmail": "organize"}
_PERMISSIONS: Optional[Dict[str, str]] = None


def set_permissions(permissions: Dict[str, str]) -> None:
    """Set granular permissions from parsed --permissions argument."""
    global _PERMISSIONS
    _PERMISSIONS = permissions
    logger.info("Granular permissions set: %s", permissions)


def get_permissions() -> Optional[Dict[str, str]]:
    """Return current permissions dict, or None if not using granular mode."""
    return _PERMISSIONS


def is_permissions_mode() -> bool:
    """Check if granular permissions mode is active."""
    return _PERMISSIONS is not None


def get_scopes_for_permission(service: str, level: str) -> List[str]:
    """
    Get cumulative scopes for a service at a given permission level.

    Returns all scopes up to and including the named level.
    Raises ValueError if service or level is unknown.
    """
    levels = SERVICE_PERMISSION_LEVELS.get(service)
    if levels is None:
        raise ValueError(f"Unknown service: '{service}'")

    cumulative: List[str] = []
    found = False
    for level_name, level_scopes in levels:
        cumulative.extend(level_scopes)
        if level_name == level:
            found = True
            break

    if not found:
        valid = [name for name, _ in levels]
        raise ValueError(
            f"Unknown permission level '{level}' for service '{service}'. "
            f"Valid levels: {valid}"
        )

    return sorted(set(cumulative))


def get_all_permission_scopes() -> List[str]:
    """
    Get the combined scopes for all services at their configured permission levels.

    Only meaningful when is_permissions_mode() is True.
    """
    if _PERMISSIONS is None:
        return []

    all_scopes: set = set()
    for service, level in _PERMISSIONS.items():
        all_scopes.update(get_scopes_for_permission(service, level))
    return list(all_scopes)


def get_allowed_scopes_set() -> Optional[set]:
    """
    Get the set of allowed scopes under permissions mode (for tool filtering).

    Returns None if permissions mode is not active.
    """
    if _PERMISSIONS is None:
        return None
    return set(get_all_permission_scopes())


def get_valid_levels(service: str) -> List[str]:
    """Get valid permission level names for a service."""
    levels = SERVICE_PERMISSION_LEVELS.get(service)
    if levels is None:
        return []
    return [name for name, _ in levels]


def parse_permissions_arg(permissions_list: List[str]) -> Dict[str, str]:
    """
    Parse --permissions arguments like ["gmail:organize", "drive:full"].

    Returns dict mapping service -> level.
    Raises ValueError on parse errors (unknown service, invalid level, bad format).
    """
    result: Dict[str, str] = {}
    for entry in permissions_list:
        if ":" not in entry:
            raise ValueError(
                f"Invalid permission format: '{entry}'. "
                f"Expected 'service:level' (e.g., 'gmail:organize', 'drive:readonly')"
            )
        service, level = entry.split(":", 1)
        if service in result:
            raise ValueError(f"Duplicate service in permissions: '{service}'")
        if service not in SERVICE_PERMISSION_LEVELS:
            raise ValueError(
                f"Unknown service: '{service}'. "
                f"Valid services: {sorted(SERVICE_PERMISSION_LEVELS.keys())}"
            )
        valid = get_valid_levels(service)
        if level not in valid:
            raise ValueError(
                f"Unknown level '{level}' for service '{service}'. "
                f"Valid levels: {valid}"
            )
        result[service] = level
    return result
