"""
Granular per-service permission levels.

Each service has named permission levels (cumulative), mapping to a list of
OAuth scopes. The levels for a service are ordered from least to most
permissive — requesting level N implicitly includes all scopes from levels < N.

Usage:
    --permissions gmail:organize drive:readonly

Gmail levels: readonly, organize, drafts, send, full
Tasks levels: readonly, manage, full
Other services: readonly, full (extensible by adding entries to SERVICE_PERMISSION_LEVELS)

Non-cumulative (union) mode — separate levels with ``+`` to request only the
additive scopes of the named levels, skipping the cumulative ladder:

    --permissions gmail:readonly+send

This grants exactly ``{gmail.readonly, gmail.send}`` — useful when a downstream
allowlists a narrow tool set (e.g. read + send only) and wants the OAuth grant
to match. Each level contributes only the scopes added at that level, not the
scopes inherited from earlier levels. Level names must match those declared in
``SERVICE_PERMISSION_LEVELS``.
"""

import logging
from typing import Dict, FrozenSet, List, Optional, Tuple, Union

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
        ("manage", [TASKS_SCOPE]),
        ("full", []),
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

# Actions denied at specific permission levels.
# Maps service -> level -> frozenset of denied action names.
# Levels not listed here (or services without entries) deny nothing.
SERVICE_DENIED_ACTIONS: Dict[str, Dict[str, FrozenSet[str]]] = {
    "tasks": {
        "manage": frozenset({"delete", "clear_completed"}),
    },
}


def is_action_denied(service: str, action: str) -> bool:
    """Check whether *action* is denied for *service* under current permissions.

    Returns ``False`` when granular permissions mode is not active, when the
    service has no permission entry, or when the configured level does not
    deny the action. In union mode (when the entry is a frozenset of level
    names), the action is denied if *any* selected level denies it — most
    restrictive wins, so explicitly including a level always carries that
    level's exclusions.
    """
    if _PERMISSIONS is None:
        return False
    level = _PERMISSIONS.get(service)
    if level is None:
        return False
    service_denied = SERVICE_DENIED_ACTIONS.get(service, {})
    if isinstance(level, frozenset):
        return any(action in service_denied.get(lvl, frozenset()) for lvl in level)
    return action in service_denied.get(level, frozenset())


# Type alias: a per-service permission value is either a single cumulative
# level name (str) or a frozenset of level names for non-cumulative union mode.
PermissionValue = Union[str, FrozenSet[str]]

# Module-level state: parsed --permissions config
# Dict mapping service_name -> level (cumulative str) or frozenset of level
# names (union mode), e.g. {"gmail": "organize"} or {"gmail": frozenset({"readonly", "send"})}
_PERMISSIONS: Optional[Dict[str, PermissionValue]] = None


def set_permissions(permissions: Optional[Dict[str, PermissionValue]]) -> None:
    """Set granular permissions from parsed --permissions argument."""
    global _PERMISSIONS
    _PERMISSIONS = permissions
    if permissions is not None:
        logger.info("Granular permissions set: %s", permissions)


def get_permissions() -> Optional[Dict[str, PermissionValue]]:
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


def get_additive_scopes_for_level(service: str, level: str) -> List[str]:
    """
    Get only the scopes *added* at a given level (non-cumulative).

    Unlike :func:`get_scopes_for_permission`, this returns just the additive
    delta declared at the named level — it does not include scopes from
    earlier levels. Used by union mode (``--permissions gmail:readonly+send``)
    where the caller explicitly composes a scope set from selected level deltas.

    Raises ValueError if service or level is unknown.
    """
    levels = SERVICE_PERMISSION_LEVELS.get(service)
    if levels is None:
        raise ValueError(f"Unknown service: '{service}'")

    for level_name, level_scopes in levels:
        if level_name == level:
            return sorted(set(level_scopes))

    valid = [name for name, _ in levels]
    raise ValueError(
        f"Unknown permission level '{level}' for service '{service}'. "
        f"Valid levels: {valid}"
    )


def get_scopes_for_union(service: str, levels: FrozenSet[str]) -> List[str]:
    """
    Get the union of additive scopes for a set of levels (non-cumulative).

    Each level contributes only its own additive scopes — the caller is
    responsible for including ``readonly`` if read access is desired.

    Raises ValueError if any level is unknown.
    """
    if not levels:
        raise ValueError(f"Empty level set for service '{service}'")

    combined: set = set()
    for level in levels:
        combined.update(get_additive_scopes_for_level(service, level))
    return sorted(combined)


def get_all_permission_scopes() -> List[str]:
    """
    Get the combined scopes for all services at their configured permission levels.

    Handles both cumulative (str) and union-mode (frozenset) entries.
    Only meaningful when is_permissions_mode() is True.
    """
    if _PERMISSIONS is None:
        return []

    all_scopes: set = set()
    for service, value in _PERMISSIONS.items():
        if isinstance(value, frozenset):
            all_scopes.update(get_scopes_for_union(service, value))
        else:
            all_scopes.update(get_scopes_for_permission(service, value))
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


def parse_permissions_arg(permissions_list: List[str]) -> Dict[str, PermissionValue]:
    """
    Parse --permissions arguments like ["gmail:organize", "drive:full"] or
    ["gmail:readonly+send"] for non-cumulative union mode.

    Returns dict mapping service -> level. The value is a ``str`` for the
    cumulative ladder form (``gmail:organize``) or a ``frozenset[str]`` of
    level names for the union form (``gmail:readonly+send``).

    Raises ValueError on parse errors (unknown service, invalid level, bad format).
    """
    result: Dict[str, PermissionValue] = {}
    for entry in permissions_list:
        if ":" not in entry:
            raise ValueError(
                f"Invalid permission format: '{entry}'. "
                f"Expected 'service:level' (e.g., 'gmail:organize', 'drive:readonly')"
            )
        service, level_spec = entry.split(":", 1)
        if service in result:
            raise ValueError(f"Duplicate service in permissions: '{service}'")
        if service not in SERVICE_PERMISSION_LEVELS:
            raise ValueError(
                f"Unknown service: '{service}'. "
                f"Valid services: {sorted(SERVICE_PERMISSION_LEVELS.keys())}"
            )
        valid = get_valid_levels(service)

        if "+" in level_spec:
            parts = [p.strip() for p in level_spec.split("+")]
            if any(not p for p in parts):
                raise ValueError(
                    f"Invalid union spec for service '{service}': '{level_spec}'. "
                    f"Expected non-empty level names separated by '+' "
                    f"(e.g., 'gmail:readonly+send')"
                )
            for part in parts:
                if part not in valid:
                    raise ValueError(
                        f"Unknown level '{part}' for service '{service}'. "
                        f"Valid levels: {valid}"
                    )
            result[service] = frozenset(parts)
        else:
            if level_spec not in valid:
                raise ValueError(
                    f"Unknown level '{level_spec}' for service '{service}'. "
                    f"Valid levels: {valid}"
                )
            result[service] = level_spec
    return result
