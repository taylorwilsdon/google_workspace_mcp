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
# Requested only from a domain-wide delegation service-account token for the
# opt-in admin-gmail-delegates service; never part of an OAuth consent.
GMAIL_SETTINGS_SHARING_SCOPE = "https://www.googleapis.com/auth/gmail.settings.sharing"

# Google Chat API scopes
CHAT_READONLY_SCOPE = "https://www.googleapis.com/auth/chat.messages.readonly"
CHAT_WRITE_SCOPE = "https://www.googleapis.com/auth/chat.messages"
CHAT_SPACES_SCOPE = "https://www.googleapis.com/auth/chat.spaces"
CHAT_SPACES_READONLY_SCOPE = "https://www.googleapis.com/auth/chat.spaces.readonly"
CHAT_MEMBERSHIPS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/chat.memberships.readonly"
)

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
SCRIPT_EXTERNAL_REQUEST_SCOPE = (
    "https://www.googleapis.com/auth/script.external_request"
)
SCRIPT_SCRIPTAPP_SCOPE = "https://www.googleapis.com/auth/script.scriptapp"

# Admin SDK Directory scopes (opt-in admin-directory service only)
ADMIN_DIRECTORY_USER_SCOPE = "https://www.googleapis.com/auth/admin.directory.user"
ADMIN_DIRECTORY_USER_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.user.readonly"
)
ADMIN_DIRECTORY_USER_SECURITY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.user.security"
)
ADMIN_DIRECTORY_GROUP_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.group.readonly"
)
ADMIN_DIRECTORY_GROUP_MEMBER_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.group.member"
)
ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.rolemanagement.readonly"
)
ADMIN_DIRECTORY_DOMAIN_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.domain.readonly"
)
ADMIN_DIRECTORY_CUSTOMER_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.customer.readonly"
)
ADMIN_DIRECTORY_ORGUNIT_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.orgunit.readonly"
)
ADMIN_DIRECTORY_USERSCHEMA_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.userschema.readonly"
)
ADMIN_DIRECTORY_GROUP_SCOPE = "https://www.googleapis.com/auth/admin.directory.group"
ADMIN_DIRECTORY_ORGUNIT_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.orgunit"
)
ADMIN_DIRECTORY_CUSTOMER_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.customer"
)
ADMIN_DIRECTORY_USERSCHEMA_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.userschema"
)
ADMIN_DIRECTORY_ROLEMANAGEMENT_SCOPE = (
    "https://www.googleapis.com/auth/admin.directory.rolemanagement"
)

# Directory device, calendar resource, and Chrome printer scopes (opt-in
# admin-directory-devices, admin-directory-resources, and
# admin-directory-printers services only).
_DIRECTORY = "https://www.googleapis.com/auth/admin.directory."
ADMIN_DIRECTORY_DEVICE_CHROMEOS_SCOPE = _DIRECTORY + "device.chromeos"
ADMIN_DIRECTORY_DEVICE_CHROMEOS_READONLY_SCOPE = _DIRECTORY + "device.chromeos.readonly"
ADMIN_DIRECTORY_DEVICE_MOBILE_SCOPE = _DIRECTORY + "device.mobile"
ADMIN_DIRECTORY_DEVICE_MOBILE_ACTION_SCOPE = _DIRECTORY + "device.mobile.action"
ADMIN_DIRECTORY_DEVICE_MOBILE_READONLY_SCOPE = _DIRECTORY + "device.mobile.readonly"
ADMIN_DIRECTORY_RESOURCE_CALENDAR_SCOPE = _DIRECTORY + "resource.calendar"
ADMIN_DIRECTORY_RESOURCE_CALENDAR_READONLY_SCOPE = (
    _DIRECTORY + "resource.calendar.readonly"
)
ADMIN_CHROME_PRINTERS_SCOPE = "https://www.googleapis.com/auth/admin.chrome.printers"
ADMIN_CHROME_PRINTERS_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.chrome.printers.readonly"
)

# Admin SDK Data Transfer and Enterprise License Manager scopes (opt-in
# admin-datatransfer and admin-licensing services only). Licensing has no
# read-only scope.
ADMIN_DATATRANSFER_SCOPE = "https://www.googleapis.com/auth/admin.datatransfer"
ADMIN_DATATRANSFER_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.datatransfer.readonly"
)
LICENSING_SCOPE = "https://www.googleapis.com/auth/apps.licensing"

# Reports, Vault, Alert Center, and Groups Settings scopes (opt-in admin-reports,
# admin-vault, admin-alertcenter, and admin-groupssettings services only). Alert
# Center and Groups Settings have no read-only scope.
REPORTS_AUDIT_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.reports.audit.readonly"
)
REPORTS_USAGE_READONLY_SCOPE = (
    "https://www.googleapis.com/auth/admin.reports.usage.readonly"
)
VAULT_SCOPE = "https://www.googleapis.com/auth/ediscovery"
VAULT_READONLY_SCOPE = "https://www.googleapis.com/auth/ediscovery.readonly"
ALERTCENTER_SCOPE = "https://www.googleapis.com/auth/apps.alerts"
GROUPS_SETTINGS_SCOPE = "https://www.googleapis.com/auth/apps.groups.settings"

# Cloud Identity scopes (opt-in admin-cloudidentity-* services only). The broad
# cloud-platform scope that discovery also lists is never requested.
_CLOUD_IDENTITY = "https://www.googleapis.com/auth/cloud-identity."
CLOUD_IDENTITY_GROUPS_SCOPE = _CLOUD_IDENTITY + "groups"
CLOUD_IDENTITY_GROUPS_READONLY_SCOPE = _CLOUD_IDENTITY + "groups.readonly"
CLOUD_IDENTITY_DEVICES_SCOPE = _CLOUD_IDENTITY + "devices"
CLOUD_IDENTITY_DEVICES_READONLY_SCOPE = _CLOUD_IDENTITY + "devices.readonly"
CLOUD_IDENTITY_INBOUNDSSO_SCOPE = _CLOUD_IDENTITY + "inboundsso"
CLOUD_IDENTITY_INBOUNDSSO_READONLY_SCOPE = _CLOUD_IDENTITY + "inboundsso.readonly"
CLOUD_IDENTITY_POLICIES_SCOPE = _CLOUD_IDENTITY + "policies"
CLOUD_IDENTITY_POLICIES_READONLY_SCOPE = _CLOUD_IDENTITY + "policies.readonly"
CLOUD_IDENTITY_USERINVITATIONS_SCOPE = _CLOUD_IDENTITY + "userinvitations"
CLOUD_IDENTITY_USERINVITATIONS_READONLY_SCOPE = (
    _CLOUD_IDENTITY + "userinvitations.readonly"
)
CLOUD_IDENTITY_ALLOWLISTEDDOMAINS_SCOPE = _CLOUD_IDENTITY + "allowlisteddomains"
CLOUD_IDENTITY_ALLOWLISTEDDOMAINS_READONLY_SCOPE = (
    _CLOUD_IDENTITY + "allowlisteddomains.readonly"
)
CLOUD_IDENTITY_ORGUNITS_SCOPE = _CLOUD_IDENTITY + "orgunits"
CLOUD_IDENTITY_ORGUNITS_READONLY_SCOPE = _CLOUD_IDENTITY + "orgunits.readonly"

# Chrome Management and Chrome Policy scopes (opt-in admin-chrome-* services only).
_CHROME_MANAGEMENT = "https://www.googleapis.com/auth/chrome.management."
CHROME_MANAGEMENT_REPORTS_READONLY_SCOPE = _CHROME_MANAGEMENT + "reports.readonly"
CHROME_MANAGEMENT_APPDETAILS_READONLY_SCOPE = _CHROME_MANAGEMENT + "appdetails.readonly"
CHROME_MANAGEMENT_TELEMETRY_READONLY_SCOPE = _CHROME_MANAGEMENT + "telemetry.readonly"
CHROME_MANAGEMENT_PROFILES_SCOPE = _CHROME_MANAGEMENT + "profiles"
CHROME_MANAGEMENT_PROFILES_READONLY_SCOPE = _CHROME_MANAGEMENT + "profiles.readonly"
CHROME_MANAGEMENT_SECURITYINSIGHTS_SCOPE = _CHROME_MANAGEMENT + "securityinsights"
CHROME_MANAGEMENT_SECURITYINSIGHTS_READONLY_SCOPE = (
    _CHROME_MANAGEMENT + "securityinsights.readonly"
)
CHROME_MANAGEMENT_POLICY_SCOPE = _CHROME_MANAGEMENT + "policy"
CHROME_MANAGEMENT_POLICY_READONLY_SCOPE = _CHROME_MANAGEMENT + "policy.readonly"

# Access Context Manager offers no narrower scope. It is requested only by the
# opt-in admin-access-context service, whose registered operations all read.
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

# Contact Delegation (admin.googleapis.com/admin/contacts/v1).
CONTACT_DELEGATION_SCOPE = "https://www.googleapis.com/auth/admin.contact.delegation"
CONTACT_DELEGATION_READONLY_SCOPE = CONTACT_DELEGATION_SCOPE + ".readonly"

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
    ADMIN_DIRECTORY_USER_SCOPE: {ADMIN_DIRECTORY_USER_READONLY_SCOPE},
    ADMIN_DIRECTORY_GROUP_SCOPE: {ADMIN_DIRECTORY_GROUP_READONLY_SCOPE},
    ADMIN_DIRECTORY_ORGUNIT_SCOPE: {ADMIN_DIRECTORY_ORGUNIT_READONLY_SCOPE},
    ADMIN_DIRECTORY_ROLEMANAGEMENT_SCOPE: {
        ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE
    },
    ADMIN_DIRECTORY_CUSTOMER_SCOPE: {ADMIN_DIRECTORY_CUSTOMER_READONLY_SCOPE},
    ADMIN_DIRECTORY_USERSCHEMA_SCOPE: {ADMIN_DIRECTORY_USERSCHEMA_READONLY_SCOPE},
    ADMIN_DIRECTORY_DEVICE_CHROMEOS_SCOPE: {
        ADMIN_DIRECTORY_DEVICE_CHROMEOS_READONLY_SCOPE
    },
    ADMIN_DIRECTORY_DEVICE_MOBILE_SCOPE: {
        ADMIN_DIRECTORY_DEVICE_MOBILE_ACTION_SCOPE,
        ADMIN_DIRECTORY_DEVICE_MOBILE_READONLY_SCOPE,
    },
    ADMIN_DIRECTORY_DEVICE_MOBILE_ACTION_SCOPE: {
        ADMIN_DIRECTORY_DEVICE_MOBILE_READONLY_SCOPE
    },
    ADMIN_DIRECTORY_RESOURCE_CALENDAR_SCOPE: {
        ADMIN_DIRECTORY_RESOURCE_CALENDAR_READONLY_SCOPE
    },
    ADMIN_CHROME_PRINTERS_SCOPE: {ADMIN_CHROME_PRINTERS_READONLY_SCOPE},
    ADMIN_DATATRANSFER_SCOPE: {ADMIN_DATATRANSFER_READONLY_SCOPE},
    VAULT_SCOPE: {VAULT_READONLY_SCOPE},
    CLOUD_IDENTITY_GROUPS_SCOPE: {CLOUD_IDENTITY_GROUPS_READONLY_SCOPE},
    CLOUD_IDENTITY_DEVICES_SCOPE: {CLOUD_IDENTITY_DEVICES_READONLY_SCOPE},
    CLOUD_IDENTITY_INBOUNDSSO_SCOPE: {CLOUD_IDENTITY_INBOUNDSSO_READONLY_SCOPE},
    CLOUD_IDENTITY_POLICIES_SCOPE: {CLOUD_IDENTITY_POLICIES_READONLY_SCOPE},
    CLOUD_IDENTITY_USERINVITATIONS_SCOPE: {
        CLOUD_IDENTITY_USERINVITATIONS_READONLY_SCOPE
    },
    CLOUD_IDENTITY_ALLOWLISTEDDOMAINS_SCOPE: {
        CLOUD_IDENTITY_ALLOWLISTEDDOMAINS_READONLY_SCOPE
    },
    CLOUD_IDENTITY_ORGUNITS_SCOPE: {CLOUD_IDENTITY_ORGUNITS_READONLY_SCOPE},
    CHROME_MANAGEMENT_PROFILES_SCOPE: {CHROME_MANAGEMENT_PROFILES_READONLY_SCOPE},
    CHROME_MANAGEMENT_SECURITYINSIGHTS_SCOPE: {
        CHROME_MANAGEMENT_SECURITYINSIGHTS_READONLY_SCOPE
    },
    CHROME_MANAGEMENT_POLICY_SCOPE: {CHROME_MANAGEMENT_POLICY_READONLY_SCOPE},
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

# Minimal scopes required to accept an MCP bearer token at the protocol layer.
PROTOCOL_AUTH_SCOPES = [USERINFO_EMAIL_SCOPE, OPENID_SCOPE]

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
    CHAT_MEMBERSHIPS_READONLY_SCOPE,  # Names DMs after their members
    CONTACTS_READONLY_SCOPE,  # Resolves sender and member names via People API
]

SHEETS_SCOPES = [
    SHEETS_READONLY_SCOPE,
    SHEETS_WRITE_SCOPE,
    DRIVE_READONLY_SCOPE,
    DRIVE_FILE_SCOPE,  # create_spreadsheet places new files in a folder
]

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
    SCRIPT_PROCESSES_READONLY_SCOPE,  # Required for get_script_activity (processes)
    SCRIPT_METRICS_SCOPE,  # Required for get_script_activity (metrics)
    SCRIPT_EXTERNAL_REQUEST_SCOPE,  # Required for scripts.run (execution API)
    SCRIPT_SCRIPTAPP_SCOPE,  # Required for scripts.run (execution API)
    DRIVE_SCOPE,  # Required for manage_script_project delete (uses Drive API)
]

ADMIN_DIRECTORY_READONLY_SCOPES = [
    ADMIN_DIRECTORY_USER_READONLY_SCOPE,
    ADMIN_DIRECTORY_GROUP_READONLY_SCOPE,
    ADMIN_DIRECTORY_ROLEMANAGEMENT_READONLY_SCOPE,
    ADMIN_DIRECTORY_DOMAIN_READONLY_SCOPE,
    ADMIN_DIRECTORY_CUSTOMER_READONLY_SCOPE,
    ADMIN_DIRECTORY_ORGUNIT_READONLY_SCOPE,
    ADMIN_DIRECTORY_USERSCHEMA_READONLY_SCOPE,
]

ADMIN_DIRECTORY_MANAGE_SCOPES = [
    ADMIN_DIRECTORY_USER_SCOPE,
    ADMIN_DIRECTORY_USER_SECURITY_SCOPE,
    ADMIN_DIRECTORY_GROUP_MEMBER_SCOPE,
    ADMIN_DIRECTORY_GROUP_SCOPE,
    ADMIN_DIRECTORY_ORGUNIT_SCOPE,
]

ADMIN_DIRECTORY_SCOPES = [
    *ADMIN_DIRECTORY_READONLY_SCOPES,
    *ADMIN_DIRECTORY_MANAGE_SCOPES,
    # Tenant, custom-schema, and role-definition writes are destructive.
    ADMIN_DIRECTORY_ROLEMANAGEMENT_SCOPE,
    ADMIN_DIRECTORY_CUSTOMER_SCOPE,
    ADMIN_DIRECTORY_USERSCHEMA_SCOPE,
]

# Directory device, calendar resource, and Chrome printer areas are opt-in
# services: (read-only scopes, manage scopes, destructive-only scopes). Only
# deleting a mobile device needs the full mobile scope.
DIRECTORY_AREA_SERVICES = {
    "admin-directory-devices": (
        [
            ADMIN_DIRECTORY_DEVICE_CHROMEOS_READONLY_SCOPE,
            ADMIN_DIRECTORY_DEVICE_MOBILE_READONLY_SCOPE,
        ],
        [
            ADMIN_DIRECTORY_DEVICE_CHROMEOS_SCOPE,
            ADMIN_DIRECTORY_DEVICE_MOBILE_ACTION_SCOPE,
        ],
        [ADMIN_DIRECTORY_DEVICE_MOBILE_SCOPE],
    ),
    "admin-directory-resources": (
        [ADMIN_DIRECTORY_RESOURCE_CALENDAR_READONLY_SCOPE],
        [ADMIN_DIRECTORY_RESOURCE_CALENDAR_SCOPE],
        [],
    ),
    "admin-directory-printers": (
        [ADMIN_CHROME_PRINTERS_READONLY_SCOPE],
        [ADMIN_CHROME_PRINTERS_SCOPE],
        [],
    ),
}

# Each Cloud Identity area is its own opt-in service: (read-only scopes, write
# scopes). SSO and policy targets are verified with a fresh group read.
CLOUD_IDENTITY_SERVICES = {
    "admin-cloudidentity-groups": (
        [CLOUD_IDENTITY_GROUPS_READONLY_SCOPE],
        [CLOUD_IDENTITY_GROUPS_SCOPE],
    ),
    "admin-cloudidentity-devices": (
        [CLOUD_IDENTITY_DEVICES_READONLY_SCOPE],
        [CLOUD_IDENTITY_DEVICES_SCOPE],
    ),
    "admin-cloudidentity-sso": (
        [
            CLOUD_IDENTITY_INBOUNDSSO_READONLY_SCOPE,
            CLOUD_IDENTITY_GROUPS_READONLY_SCOPE,
        ],
        [CLOUD_IDENTITY_INBOUNDSSO_SCOPE],
    ),
    "admin-cloudidentity-policies": (
        [CLOUD_IDENTITY_POLICIES_READONLY_SCOPE, CLOUD_IDENTITY_GROUPS_READONLY_SCOPE],
        [CLOUD_IDENTITY_POLICIES_SCOPE],
    ),
    "admin-cloudidentity-invitations": (
        [CLOUD_IDENTITY_USERINVITATIONS_READONLY_SCOPE],
        [CLOUD_IDENTITY_USERINVITATIONS_SCOPE],
    ),
    "admin-cloudidentity-domains": (
        [CLOUD_IDENTITY_ALLOWLISTEDDOMAINS_READONLY_SCOPE],
        [CLOUD_IDENTITY_ALLOWLISTEDDOMAINS_SCOPE],
    ),
    "admin-cloudidentity-orgunits": (
        [CLOUD_IDENTITY_ORGUNITS_READONLY_SCOPE],
        [CLOUD_IDENTITY_ORGUNITS_SCOPE],
    ),
}

# Chrome Management areas and Chrome Policy are opt-in services in the same
# (read-only scopes, write scopes) form. Reports and telemetry are read-only.
CHROME_SERVICES = {
    "admin-chrome-reports": (
        [
            CHROME_MANAGEMENT_REPORTS_READONLY_SCOPE,
            CHROME_MANAGEMENT_APPDETAILS_READONLY_SCOPE,
        ],
        [],
    ),
    "admin-chrome-telemetry": ([CHROME_MANAGEMENT_TELEMETRY_READONLY_SCOPE], []),
    "admin-chrome-profiles": (
        [CHROME_MANAGEMENT_PROFILES_READONLY_SCOPE],
        [CHROME_MANAGEMENT_PROFILES_SCOPE],
    ),
    "admin-chrome-insights": (
        [CHROME_MANAGEMENT_SECURITYINSIGHTS_READONLY_SCOPE],
        [CHROME_MANAGEMENT_SECURITYINSIGHTS_SCOPE],
    ),
    "admin-chrome-policy": (
        [CHROME_MANAGEMENT_POLICY_READONLY_SCOPE],
        [CHROME_MANAGEMENT_POLICY_SCOPE],
    ),
}
# Context-aware access levels are read-only here: Google warns Workspace
# customers to change them only in the Admin console.
ACCESS_CONTEXT_SERVICES = {"admin-access-context": ([CLOUD_PLATFORM_SCOPE], [])}
CONTACT_DELEGATION_SERVICES = {
    "admin-contact-delegation": (
        [CONTACT_DELEGATION_READONLY_SCOPE],
        [CONTACT_DELEGATION_SCOPE],
    )
}
READ_WRITE_SERVICES = {
    **CLOUD_IDENTITY_SERVICES,
    **CHROME_SERVICES,
    **ACCESS_CONTEXT_SERVICES,
    **CONTACT_DELEGATION_SERVICES,
}
# Services whose (read-only, write) scopes are requested only from a domain-wide
# delegation service-account token for one verified user, never by an OAuth
# consent: their OAuth scope lists and permission levels are empty.
DELEGATED_SERVICES = {
    "admin-gmail-delegates": (
        [GMAIL_SETTINGS_BASIC_SCOPE],
        [GMAIL_SETTINGS_SHARING_SCOPE],
    )
}

# Services that are loaded, and whose scopes are requested, only when selected
# explicitly. They never join the "all services" default.
OPT_IN_SERVICES = frozenset(
    {
        "admin-directory",
        "admin-datatransfer",
        "admin-licensing",
        "admin-reports",
        "admin-vault",
        "admin-alertcenter",
        "admin-groupssettings",
        *DIRECTORY_AREA_SERVICES,
        *READ_WRITE_SERVICES,
        *DELEGATED_SERVICES,
    }
)

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
    "admin-directory": ADMIN_DIRECTORY_SCOPES,
    "admin-datatransfer": [ADMIN_DATATRANSFER_READONLY_SCOPE, ADMIN_DATATRANSFER_SCOPE],
    "admin-licensing": [LICENSING_SCOPE],
    "admin-reports": [REPORTS_AUDIT_READONLY_SCOPE, REPORTS_USAGE_READONLY_SCOPE],
    "admin-vault": [VAULT_READONLY_SCOPE, VAULT_SCOPE],
    "admin-alertcenter": [ALERTCENTER_SCOPE],
    "admin-groupssettings": [GROUPS_SETTINGS_SCOPE],
    **{
        service: [*read, *manage, *destructive]
        for service, (read, manage, destructive) in DIRECTORY_AREA_SERVICES.items()
    },
    **{
        service: [*read, *write]
        for service, (read, write) in READ_WRITE_SERVICES.items()
    },
    **{service: [] for service in DELEGATED_SERVICES},
}

# Tool-to-read-only-scopes mapping
TOOL_READONLY_SCOPES_MAP = {
    "gmail": [GMAIL_READONLY_SCOPE],
    "drive": [DRIVE_READONLY_SCOPE],
    "calendar": [CALENDAR_READONLY_SCOPE],
    "docs": [DOCS_READONLY_SCOPE, DRIVE_READONLY_SCOPE],
    "sheets": [SHEETS_READONLY_SCOPE, DRIVE_READONLY_SCOPE],
    "chat": [
        CHAT_READONLY_SCOPE,
        CHAT_SPACES_READONLY_SCOPE,
        CHAT_MEMBERSHIPS_READONLY_SCOPE,
        CONTACTS_READONLY_SCOPE,
    ],
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
    "admin-directory": ADMIN_DIRECTORY_READONLY_SCOPES,
    "admin-datatransfer": [ADMIN_DATATRANSFER_READONLY_SCOPE],
    "admin-licensing": [],
    "admin-reports": [REPORTS_AUDIT_READONLY_SCOPE, REPORTS_USAGE_READONLY_SCOPE],
    "admin-vault": [VAULT_READONLY_SCOPE],
    "admin-alertcenter": [],
    "admin-groupssettings": [],
    **{service: list(read) for service, (read, *_) in DIRECTORY_AREA_SERVICES.items()},
    **{service: list(read) for service, (read, _) in READ_WRITE_SERVICES.items()},
    **{service: [] for service in DELEGATED_SERVICES},
}


def set_enabled_tools(enabled_tools):
    """
    Set the globally enabled tools list.

    Args:
        enabled_tools: List of enabled tool names.
    """
    global _ENABLED_TOOLS
    _ENABLED_TOOLS = enabled_tools
    # Debug level: the startup screen already reports the loaded service count.
    logger.debug(f"Scope management active for {len(enabled_tools)} services")


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
    # Debug level: the startup banner already flags read-only mode.
    logger.debug(f"Read-only mode set to: {enabled}")


def is_read_only_mode() -> bool:
    """Check if read-only mode is enabled."""
    return _READ_ONLY_MODE


def is_service_enabled(service: str) -> bool:
    """True only when *service* was explicitly selected for this server."""
    return _ENABLED_TOOLS is not None and service in _ENABLED_TOOLS


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
        # Default behavior - all scopes except opt-in services
        enabled_tools = [t for t in TOOL_SCOPES_MAP if t not in OPT_IN_SERVICES]

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
