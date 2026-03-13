"""
Google Groups MCP Tools (Cloud Identity API)

This module provides MCP tools for interacting with Google Groups via the
Cloud Identity Groups API (cloudidentity v1).
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from googleapiclient.errors import HttpError
from mcp import Resource

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

# Default label for standard Google Groups (discussion forum)
GOOGLE_GROUP_LABEL = "cloudidentity.googleapis.com/groups.discussion_forum"


def _format_group(group: Dict[str, Any]) -> str:
    """Format a Cloud Identity Group resource into a readable string."""
    name = group.get("name", "Unknown")
    group_id = name.replace("groups/", "") if name else "Unknown"

    lines = [f"Group ID: {group_id}"]

    group_key = group.get("groupKey", {})
    if group_key.get("id"):
        lines.append(f"Email: {group_key['id']}")

    display_name = group.get("displayName")
    if display_name:
        lines.append(f"Display Name: {display_name}")

    description = group.get("description")
    if description:
        if len(description) > 200:
            description = description[:200] + "..."
        lines.append(f"Description: {description}")

    parent = group.get("parent")
    if parent:
        lines.append(f"Parent: {parent}")

    labels = group.get("labels", {})
    if labels:
        label_names = [k.split("/")[-1] for k in labels.keys()]
        lines.append(f"Labels: {', '.join(label_names)}")

    create_time = group.get("createTime")
    if create_time:
        lines.append(f"Created: {create_time}")

    return "\n".join(lines)


def _format_membership(membership: Dict[str, Any]) -> str:
    """Format a Cloud Identity Membership resource into a readable string."""
    name = membership.get("name", "Unknown")
    membership_id = name.split("/")[-1] if name else "Unknown"

    lines = [f"Membership ID: {membership_id}"]

    member_key = membership.get("preferredMemberKey", {})
    if member_key.get("id"):
        lines.append(f"Member: {member_key['id']}")

    roles = membership.get("roles", [])
    if roles:
        role_names = [r.get("name", "UNKNOWN") for r in roles]
        lines.append(f"Roles: {', '.join(role_names)}")

    member_type = membership.get("type")
    if member_type:
        lines.append(f"Type: {member_type}")

    create_time = membership.get("createTime")
    if create_time:
        lines.append(f"Joined: {create_time}")

    return "\n".join(lines)


# =============================================================================
# Core Tier Tools
# =============================================================================


@server.tool()
@require_google_service("cloudidentity", "groups_read")
@handle_http_errors("search_groups", service_type="cloudidentity")
async def search_groups(
    service: Resource,
    user_google_email: str,
    query: str,
    page_size: int = 20,
    page_token: Optional[str] = None,
) -> str:
    """
    Search for Google Groups matching a query.

    The query uses CEL (Common Expression Language) syntax. Common examples:
    - parent == 'customers/{customer_id}' && 'cloudidentity.googleapis.com/groups.discussion_forum' in labels
    - parent == 'customers/{customer_id}' && groupKey.id == 'group@example.com'

    Args:
        user_google_email (str): The user's Google email address. Required.
        query (str): CEL query string to search for groups.
        page_size (int): Maximum number of groups to return (default: 20, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: Matching groups with their details.
    """
    logger.info(
        f"[search_groups] Invoked. Email: '{user_google_email}', Query: '{query}'"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "query": query,
        "pageSize": page_size,
        "view": "FULL",
    }

    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(service.groups().search(**params).execute)

    groups = result.get("groups", [])
    next_page_token = result.get("nextPageToken")

    if not groups:
        return f"No groups found matching query for {user_google_email}."

    response = f"Groups Search Results ({len(groups)} found):\n\n"
    for group in groups:
        response += _format_group(group) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(groups)} groups for {user_google_email}")
    return response


@server.tool()
@require_google_service("cloudidentity", "groups_read")
@handle_http_errors("get_group", service_type="cloudidentity")
async def get_group(
    service: Resource,
    user_google_email: str,
    group_id: str,
) -> str:
    """
    Get detailed information about a specific group.

    Args:
        user_google_email (str): The user's Google email address. Required.
        group_id (str): The group ID or full resource name (e.g., "groups/abc123" or "abc123").

    Returns:
        str: Detailed group information.
    """
    if not group_id.startswith("groups/"):
        resource_name = f"groups/{group_id}"
    else:
        resource_name = group_id

    logger.info(
        f"[get_group] Invoked. Email: '{user_google_email}', Group: {resource_name}"
    )

    group = await asyncio.to_thread(
        service.groups().get(name=resource_name).execute
    )

    response = f"Group Details:\n\n"
    response += _format_group(group)

    logger.info(f"Retrieved group {resource_name} for {user_google_email}")
    return response


@server.tool()
@require_google_service("cloudidentity", "groups_read")
@handle_http_errors("list_group_members", service_type="cloudidentity")
async def list_group_members(
    service: Resource,
    user_google_email: str,
    group_id: str,
    page_size: int = 100,
    page_token: Optional[str] = None,
) -> str:
    """
    List members of a specific group.

    Args:
        user_google_email (str): The user's Google email address. Required.
        group_id (str): The group ID or full resource name (e.g., "groups/abc123" or "abc123").
        page_size (int): Maximum number of members to return (default: 100, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: List of group members with their roles.
    """
    if not group_id.startswith("groups/"):
        parent = f"groups/{group_id}"
    else:
        parent = group_id

    logger.info(
        f"[list_group_members] Invoked. Email: '{user_google_email}', Group: {parent}"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "parent": parent,
        "pageSize": page_size,
    }

    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(
        service.groups().memberships().list(**params).execute
    )

    memberships = result.get("memberships", [])
    next_page_token = result.get("nextPageToken")

    if not memberships:
        return f"No members found in group {group_id}."

    response = f"Members of {group_id} ({len(memberships)} shown):\n\n"
    for membership in memberships:
        response += _format_membership(membership) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(
        f"Found {len(memberships)} members in group {group_id} for {user_google_email}"
    )
    return response


@server.tool()
@require_google_service("cloudidentity", "groups")
@handle_http_errors("manage_group", service_type="cloudidentity")
async def manage_group(
    service: Resource,
    user_google_email: str,
    action: str,
    group_id: Optional[str] = None,
    group_email: Optional[str] = None,
    display_name: Optional[str] = None,
    description: Optional[str] = None,
    parent: Optional[str] = None,
) -> str:
    """
    Create, update, or delete a Google Group.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): The action to perform: "create", "update", or "delete".
        group_id (Optional[str]): The group ID or resource name. Required for "update" and "delete".
        group_email (Optional[str]): The group's email address. Required for "create".
        display_name (Optional[str]): Display name for the group (for create/update).
        description (Optional[str]): Description of the group (for create/update).
        parent (Optional[str]): Parent resource for the group (e.g., "customers/C0123abc"). Required for "create".

    Returns:
        str: Result of the action performed.
    """
    action = action.lower().strip()
    if action not in ("create", "update", "delete"):
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'create', 'update', or 'delete'."
        )

    logger.info(
        f"[manage_group] Invoked. Action: '{action}', Email: '{user_google_email}'"
    )

    if action == "create":
        if not group_email:
            raise UserInputError("group_email is required for 'create' action.")
        if not parent:
            raise UserInputError(
                "parent is required for 'create' action "
                "(e.g., 'customers/C0123abc')."
            )

        body: Dict[str, Any] = {
            "groupKey": {"id": group_email},
            "parent": parent,
            "labels": {GOOGLE_GROUP_LABEL: ""},
        }
        if display_name:
            body["displayName"] = display_name
        if description:
            body["description"] = description

        result = await asyncio.to_thread(
            service.groups().create(body=body).execute
        )

        response = f"Group Created:\n\n"
        response += _format_group(result.get("response", result))

        logger.info(f"Created group {group_email} for {user_google_email}")
        return response

    if not group_id:
        raise UserInputError(f"group_id is required for '{action}' action.")

    if not group_id.startswith("groups/"):
        resource_name = f"groups/{group_id}"
    else:
        resource_name = group_id

    if action == "update":
        body = {}
        update_mask_parts = []

        if display_name is not None:
            body["displayName"] = display_name
            update_mask_parts.append("displayName")
        if description is not None:
            body["description"] = description
            update_mask_parts.append("description")

        if not update_mask_parts:
            raise UserInputError(
                "At least one of display_name or description must be provided for 'update'."
            )

        result = await asyncio.to_thread(
            service.groups()
            .patch(
                name=resource_name,
                body=body,
                updateMask=",".join(update_mask_parts),
            )
            .execute
        )

        response = f"Group Updated:\n\n"
        response += _format_group(result)

        logger.info(f"Updated group {resource_name} for {user_google_email}")
        return response

    # action == "delete"
    await asyncio.to_thread(
        service.groups().delete(name=resource_name).execute
    )

    logger.info(f"Deleted group {resource_name} for {user_google_email}")
    return f"Group {group_id} has been deleted."


# =============================================================================
# Extended Tier Tools
# =============================================================================


@server.tool()
@require_google_service("cloudidentity", "groups")
@handle_http_errors("manage_group_members", service_type="cloudidentity")
async def manage_group_members(
    service: Resource,
    user_google_email: str,
    action: str,
    group_id: str,
    member_email: Optional[str] = None,
    membership_id: Optional[str] = None,
    role: str = "MEMBER",
) -> str:
    """
    Add, remove, or modify roles of a group member.

    Args:
        user_google_email (str): The user's Google email address. Required.
        action (str): The action to perform: "add", "remove", or "modify_role".
        group_id (str): The group ID or full resource name.
        member_email (Optional[str]): Email of the member. Required for "add".
        membership_id (Optional[str]): Membership ID or full resource name. Required for "remove" and "modify_role".
        role (str): Role to assign: "MEMBER", "MANAGER", or "OWNER" (default: "MEMBER"). Used for "add" and "modify_role".

    Returns:
        str: Result of the action performed.
    """
    action = action.lower().strip()
    if action not in ("add", "remove", "modify_role"):
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'add', 'remove', or 'modify_role'."
        )

    if not group_id.startswith("groups/"):
        parent = f"groups/{group_id}"
    else:
        parent = group_id

    logger.info(
        f"[manage_group_members] Invoked. Action: '{action}', Group: {parent}, "
        f"Email: '{user_google_email}'"
    )

    role = role.upper().strip()
    if role not in ("MEMBER", "MANAGER", "OWNER"):
        raise UserInputError(
            f"Invalid role '{role}'. Must be 'MEMBER', 'MANAGER', or 'OWNER'."
        )

    if action == "add":
        if not member_email:
            raise UserInputError("member_email is required for 'add' action.")

        body: Dict[str, Any] = {
            "preferredMemberKey": {"id": member_email},
            "roles": [{"name": role}],
        }

        result = await asyncio.to_thread(
            service.groups()
            .memberships()
            .create(parent=parent, body=body)
            .execute
        )

        response = f"Member Added to {group_id}:\n\n"
        membership = result.get("response", result)
        response += _format_membership(membership)

        logger.info(
            f"Added {member_email} as {role} to {parent} for {user_google_email}"
        )
        return response

    if not membership_id:
        raise UserInputError(
            f"membership_id is required for '{action}' action."
        )

    if not membership_id.startswith("groups/"):
        membership_name = f"{parent}/memberships/{membership_id}"
    elif "/memberships/" not in membership_id:
        membership_name = f"{parent}/memberships/{membership_id}"
    else:
        membership_name = membership_id

    if action == "remove":
        await asyncio.to_thread(
            service.groups()
            .memberships()
            .delete(name=membership_name)
            .execute
        )

        logger.info(
            f"Removed membership {membership_name} from {parent} for {user_google_email}"
        )
        return f"Membership {membership_id} has been removed from group {group_id}."

    # action == "modify_role"
    body = {
        "addRoles": [{"name": role}],
        "removeRoles": [],
    }

    current_roles = {"MEMBER", "MANAGER", "OWNER"}
    current_roles.discard(role)
    body["removeRoles"] = [r for r in current_roles]

    result = await asyncio.to_thread(
        service.groups()
        .memberships()
        .modifyMembershipRoles(name=membership_name, body=body)
        .execute
    )

    response = f"Membership Role Updated:\n\n"
    response += _format_membership(result.get("membership", result))

    logger.info(
        f"Modified role to {role} for {membership_name} in {parent} for {user_google_email}"
    )
    return response


# =============================================================================
# Complete Tier Tools
# =============================================================================


@server.tool()
@require_google_service("cloudidentity", "groups_read")
@handle_http_errors("list_groups", service_type="cloudidentity")
async def list_groups(
    service: Resource,
    user_google_email: str,
    parent: str,
    page_size: int = 50,
    page_token: Optional[str] = None,
) -> str:
    """
    List all groups under a customer or namespace.

    Args:
        user_google_email (str): The user's Google email address. Required.
        parent (str): Parent resource (e.g., "customers/C0123abc" or "identitysources/{identity_source}").
        page_size (int): Maximum number of groups to return (default: 50, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: List of groups with their details.
    """
    logger.info(
        f"[list_groups] Invoked. Email: '{user_google_email}', Parent: '{parent}'"
    )

    if page_size < 1:
        raise UserInputError("page_size must be >= 1")
    page_size = min(page_size, 1000)

    params: Dict[str, Any] = {
        "parent": parent,
        "pageSize": page_size,
        "view": "FULL",
    }

    if page_token:
        params["pageToken"] = page_token

    result = await asyncio.to_thread(service.groups().list(**params).execute)

    groups = result.get("groups", [])
    next_page_token = result.get("nextPageToken")

    if not groups:
        return f"No groups found under {parent}."

    response = f"Groups under {parent} ({len(groups)} shown):\n\n"
    for group in groups:
        response += _format_group(group) + "\n\n"

    if next_page_token:
        response += f"Next page token: {next_page_token}"

    logger.info(f"Found {len(groups)} groups under {parent} for {user_google_email}")
    return response


@server.tool()
@require_google_service("cloudidentity", "groups_read")
@handle_http_errors("lookup_group", service_type="cloudidentity")
async def lookup_group(
    service: Resource,
    user_google_email: str,
    group_email: str,
) -> str:
    """
    Look up the resource name of a group by its email address.

    Args:
        user_google_email (str): The user's Google email address. Required.
        group_email (str): The group's email address to look up.

    Returns:
        str: The group's resource name and key information.
    """
    logger.info(
        f"[lookup_group] Invoked. Email: '{user_google_email}', "
        f"Group email: '{group_email}'"
    )

    result = await asyncio.to_thread(
        service.groups()
        .lookup(groupKey_id=group_email)
        .execute
    )

    resource_name = result.get("name", "Unknown")
    group_id = resource_name.replace("groups/", "")

    response = f"Group Lookup Result:\n\n"
    response += f"Email: {group_email}\n"
    response += f"Resource Name: {resource_name}\n"
    response += f"Group ID: {group_id}\n"

    logger.info(f"Looked up group {group_email} -> {resource_name}")
    return response
