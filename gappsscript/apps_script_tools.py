"""
Google Apps Script MCP Tools

This module provides MCP tools for interacting with Google Apps Script API.
"""

import asyncio
import inspect
import json
import logging
import weakref
from functools import wraps
from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import ObjectList, UserInputError, handle_http_errors

logger = logging.getLogger(__name__)

_VALID_SCRIPT_FILE_TYPES = frozenset({"SERVER_JS", "HTML", "JSON"})

# These locks serialize updates only within this process. Other worker
# processes or service instances can still race while merging the same script_id.
_SCRIPT_UPDATE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


def _get_script_update_lock(script_id: str) -> asyncio.Lock:
    """Return the in-process lock that orders updates for one script."""
    return _SCRIPT_UPDATE_LOCKS.setdefault(script_id, asyncio.Lock())


def _normalize_script_file(file: Dict[str, Any]) -> Dict[str, str]:
    """Return the Script API file fields used for updateContent requests.

    Output-only fields returned by getContent (createTime, functionSet, ...)
    are dropped. Fields the caller omitted stay omitted so a merge can fall
    back to the existing value instead of blanking it.
    """
    return {
        key: file[key]
        for key in ("name", "type", "source")
        if key in file and file[key] is not None
    }


def _merge_script_files(
    existing_files: List[Dict[str, Any]],
    updated_files: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Overlay updated files onto the current project.

    Files are keyed by (name, type) because Script API names exclude the
    extension, so one project may hold both Code.gs and Code.html as "Code".
    An update that omits `type` falls back to matching by name, but only when
    exactly one existing file carries that name; otherwise the type cannot be
    inferred and a UserInputError is raised rather than pushing an untyped file.
    """
    merged = {
        (file["name"], file.get("type")): _normalize_script_file(file)
        for file in existing_files
        if file.get("name")
    }

    for index, file in enumerate(updated_files):
        name = file.get("name")
        if not name:
            raise UserInputError(
                f"File at index {index} is missing a non-empty 'name'."
            )
        file_type = file.get("type")
        if file_type is not None:
            if file_type not in _VALID_SCRIPT_FILE_TYPES:
                raise UserInputError(
                    f"File '{name}' has unsupported type '{file_type}'; it must "
                    "be one of SERVER_JS, HTML, or JSON."
                )
            if file_type == "JSON" and name != "appsscript":
                raise UserInputError(
                    f"JSON file '{name}' must use the manifest name 'appsscript'."
                )

        key = (name, file_type)
        if key not in merged and file_type is None:
            same_name = [existing for existing in merged if existing[0] == name]
            if len(same_name) != 1:
                raise UserInputError(
                    f"File '{name}' is missing 'type'; it must be one of "
                    "SERVER_JS, HTML, or JSON because the existing project "
                    "does not identify a single file with that name."
                )
            key = same_name[0]
        merged[key] = {**merged.get(key, {}), **_normalize_script_file(file)}

    return list(merged.values())


# Internal implementation functions for testing
async def _list_script_projects_impl(
    service: Any,
    user_google_email: str,
    page_size: int = 50,
    page_token: Optional[str] = None,
) -> str:
    """Internal implementation for list_script_projects.

    Uses Drive API to find Apps Script files since the Script API
    does not have a projects.list method.
    """
    logger.info(
        f"[list_script_projects] Email: {user_google_email}, PageSize: {page_size}"
    )

    # Search for Apps Script files using Drive API
    query = "mimeType='application/vnd.google-apps.script' and trashed=false"
    request_params = {
        "q": query,
        "pageSize": page_size,
        "fields": "nextPageToken, files(id, name, createdTime, modifiedTime)",
        "orderBy": "modifiedTime desc",
    }
    if page_token:
        request_params["pageToken"] = page_token

    response = await asyncio.to_thread(service.files().list(**request_params).execute)

    files = response.get("files", [])

    if not files:
        return "No Apps Script projects found."

    output = [f"Found {len(files)} Apps Script projects:"]
    for file in files:
        title = file.get("name", "Untitled")
        script_id = file.get("id", "Unknown ID")
        create_time = file.get("createdTime", "Unknown")
        update_time = file.get("modifiedTime", "Unknown")

        output.append(
            f"- {title} (ID: {script_id}) Created: {create_time} Modified: {update_time}"
        )

    if "nextPageToken" in response:
        output.append(f"\nNext page token: {response['nextPageToken']}")

    logger.info(
        f"[list_script_projects] Found {len(files)} projects for {user_google_email}"
    )
    return "\n".join(output)


def _require_project_action_service(action_services):
    """Select authentication by action while retaining the named service slots."""

    def decorator(func):
        original_sig = inspect.signature(func)
        public_params = list(original_sig.parameters.values())[2:]
        dispatch_sig = original_sig.replace(parameters=public_params)

        @wraps(func)
        async def invoke(service, *args, **kwargs):
            action = (
                dispatch_sig.bind(*args, **kwargs).arguments["action"].lower().strip()
            )
            service_type, _ = action_services[action]
            return await func(
                service if service_type == "drive" else None,
                service if service_type == "script" else None,
                *args,
                **kwargs,
            )

        invoke.__signature__ = original_sig.replace(
            parameters=[
                inspect.Parameter("service", inspect.Parameter.POSITIONAL_OR_KEYWORD),
                *public_params,
            ]
        )
        handlers = {
            action: require_google_service(service_type, scopes)(invoke)
            for action, (service_type, scopes) in action_services.items()
        }
        # Let the existing auth decorator handle the managed-email signature.
        public_sig = inspect.signature(next(iter(handlers.values())))

        @wraps(func)
        async def wrapper(*args, **kwargs):
            arguments = public_sig.bind(*args, **kwargs).arguments
            action = arguments["action"].lower().strip()
            if action not in handlers:
                choices = " or ".join(repr(value) for value in handlers)
                raise UserInputError(f"Invalid action '{action}'. Must be {choices}.")
            return await handlers[action](**arguments)

        wrapper.__signature__ = public_sig
        wrapper._required_google_scopes = list(
            dict.fromkeys(
                scope
                for handler in handlers.values()
                for scope in handler._required_google_scopes
            )
        )
        return wrapper

    return decorator


@server.tool(
    title="Get Script Project",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@_require_project_action_service(
    {"list": ("drive", "drive_read"), "get": ("script", "script_readonly")}
)
@handle_http_errors("get_script_project", is_read_only=True, service_type="script")
async def get_script_project(
    drive_service: Any,
    script_service: Any,
    user_google_email: str,
    action: str,
    script_id: Optional[str] = None,
    file_name: Optional[str] = None,
    page_size: int = 50,
    page_token: Optional[str] = None,
) -> str:
    """
    Read Apps Script projects and their source files.

    Actions:
        - "list": List Apps Script projects accessible to the user (Drive-backed).
          Optional: page_size, page_token.
        - "get": Retrieve one project's metadata and file overview, or one
          complete source file when file_name is provided. Requires script_id.

    Args:
        drive_service: Injected Drive client (used for list).
        script_service: Injected Script client (used for get).
        user_google_email: User's email address
        action: One of "list", "get".
        script_id: The script project ID (required for get).
        file_name: Optional source file name for get.
        page_size: Number of results per page for list (default: 50).
        page_token: Pagination token for list (optional).

    Returns:
        str: Formatted result for the requested action.
    """
    action = action.lower().strip()
    if action == "list":
        return await _list_script_projects_impl(
            drive_service, user_google_email, page_size, page_token
        )
    elif action == "get":
        if not script_id:
            raise UserInputError("script_id is required for get action")
        if file_name:
            return await _get_script_content_impl(
                script_service, user_google_email, script_id, file_name
            )
        return await _get_script_project_impl(
            script_service, user_google_email, script_id
        )
    else:
        raise UserInputError(f"Invalid action '{action}'. Must be 'list' or 'get'.")


async def _get_script_project_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
) -> str:
    """Internal implementation for get_script_project."""
    logger.info(f"[get_script_project] Email: {user_google_email}, ID: {script_id}")

    # Get project metadata and content concurrently (independent requests)
    project, content = await asyncio.gather(
        asyncio.to_thread(service.projects().get(scriptId=script_id).execute),
        asyncio.to_thread(service.projects().getContent(scriptId=script_id).execute),
    )

    title = project.get("title", "Untitled")
    project_script_id = project.get("scriptId", "Unknown")
    creator = project.get("creator", {}).get("email", "Unknown")
    create_time = project.get("createTime", "Unknown")
    update_time = project.get("updateTime", "Unknown")

    output = [
        f"Project: {title} (ID: {project_script_id})",
        f"Creator: {creator}",
        f"Created: {create_time}",
        f"Modified: {update_time}",
        "",
        "Files:",
    ]

    files = content.get("files", [])
    for i, file in enumerate(files, 1):
        file_name = file.get("name", "Untitled")
        file_type = file.get("type", "Unknown")
        source = file.get("source", "")

        output.append(f"{i}. {file_name} ({file_type})")
        if source:
            output.append(f"   {source[:200]}{'...' if len(source) > 200 else ''}")
            output.append("")

    logger.info(f"[get_script_project] Retrieved project {script_id}")
    return "\n".join(output)


async def _get_script_content_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    file_name: str,
) -> str:
    """Internal implementation for get_script_content."""
    logger.info(
        f"[get_script_content] Email: {user_google_email}, ID: {script_id}, File: {file_name}"
    )

    # Must use getContent() to retrieve files, not get() which only returns metadata
    content = await asyncio.to_thread(
        service.projects().getContent(scriptId=script_id).execute
    )

    files = content.get("files", [])
    target_file = None

    for file in files:
        if file.get("name") == file_name:
            target_file = file
            break

    if not target_file:
        return f"File '{file_name}' not found in project {script_id}"

    source = target_file.get("source", "")
    file_type = target_file.get("type", "Unknown")

    output = [f"File: {file_name} ({file_type})", "", source]

    logger.info(f"[get_script_content] Retrieved file {file_name} from {script_id}")
    return "\n".join(output)


@server.tool(
    title="Manage Script Content",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_script_content", service_type="script")
@require_google_service("script", "script_projects")
async def manage_script_content(
    service: Any,
    user_google_email: str,
    action: str,
    script_id: str,
    files: Optional[List[Dict[str, str]]] = None,
    merge: bool = True,
) -> str:
    """
    Update the source files of an Apps Script project.

    Actions:
        - "update": Create or update files. By default (merge=True) the supplied
          files are overlaid onto the project by (name, type), leaving other
          files untouched. Set merge=False to replace the full project file set;
          any existing file omitted from `files` is permanently deleted.

    Args:
        service: Injected Google API service client
        user_google_email: User's email address
        action: "update".
        script_id: The script project ID.
        files: File objects with name, type, and source to create or update
            (required for update).
        merge: When True (default), overlay `files` onto the current project.
            When False, replace the full project file set (update only).

    Returns:
        str: Confirmation with the updated file list.
    """
    action = action.lower().strip()
    if action == "update":
        if not files:
            raise UserInputError("files is required for update action")
        return await _update_script_content_impl(
            service, user_google_email, script_id, files, merge
        )
    else:
        raise UserInputError(f"Invalid action '{action}'. Must be 'update'.")


async def _create_script_project_impl(
    service: Any,
    user_google_email: str,
    title: str,
    parent_id: Optional[str] = None,
) -> str:
    """Internal implementation for create_script_project."""
    logger.info(
        f"[create_script_project] Email: {user_google_email}, title_len={len(title)}"
    )

    request_body = {"title": title}

    if parent_id:
        request_body["parentId"] = parent_id

    project = await asyncio.to_thread(
        service.projects().create(body=request_body).execute
    )

    script_id = project.get("scriptId", "Unknown")
    edit_url = f"https://script.google.com/d/{script_id}/edit"

    output = [
        f"Created Apps Script project: {title}",
        f"Script ID: {script_id}",
        f"Edit URL: {edit_url}",
    ]

    logger.info(f"[create_script_project] Created project {script_id}")
    return "\n".join(output)


async def _update_script_content_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    files: List[Dict[str, str]],
    merge: bool = True,
) -> str:
    """Internal implementation for update_script_content."""
    logger.info(
        f"[update_script_content] Email: {user_google_email}, ID: {script_id}, "
        f"Files: {len(files)}, merge: {merge}"
    )

    files_to_push = [_normalize_script_file(file) for file in files]

    async with _get_script_update_lock(script_id):
        if merge:
            current_content = await asyncio.to_thread(
                service.projects().getContent(scriptId=script_id).execute
            )
            files_to_push = _merge_script_files(
                current_content.get("files", []), files_to_push
            )

        request_body = {"files": files_to_push}

        updated_content = await asyncio.to_thread(
            service.projects()
            .updateContent(scriptId=script_id, body=request_body)
            .execute
        )

    mode = "merged into project" if merge else "replaced entire project"
    output = [
        f"Updated script project: {script_id} ({mode})",
        "",
        "Files in project after update:",
    ]

    for file in updated_content.get("files", []):
        file_name = file.get("name", "Untitled")
        file_type = file.get("type", "Unknown")
        output.append(f"- {file_name} ({file_type})")

    logger.info(
        f"[update_script_content] Pushed {len(files_to_push)} files to {script_id}"
    )
    return "\n".join(output)


async def _run_script_function_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    function_name: str,
    parameters: Optional[list[object]] = None,
    dev_mode: bool = False,
    deployment_id: Optional[str] = None,
) -> str:
    """Internal implementation for run_script_function."""
    logger.info(
        f"[run_script_function] Email: {user_google_email}, ID: {script_id}, Function: {function_name}"
    )

    request_body = {"function": function_name, "devMode": dev_mode}

    if parameters:
        request_body["parameters"] = parameters

    try:
        if not deployment_id:
            deployment_id = await _resolve_execution_deployment_id(service, script_id)
            if not deployment_id:
                return (
                    "Execution failed\n"
                    f"Function: {function_name}\n"
                    "Error: No versioned API Executable deployment was found. In the "
                    "Apps Script editor, use Deploy > New deployment > API Executable. "
                    "The script and caller must share a standard Google Cloud project. "
                    "manage_deployment(action='create') is sufficient only when the "
                    "script manifest already defines executionApi."
                )

        response = await asyncio.to_thread(
            service.scripts().run(scriptId=deployment_id, body=request_body).execute
        )

        if "error" in response:
            error_details = response["error"]
            error_message = error_details.get("message", "Unknown error")
            return (
                f"Execution failed\nFunction: {function_name}\nError: {error_message}"
            )

        result = response.get("response", {}).get("result")
        output = [
            "Execution successful",
            f"Function: {function_name}",
            f"Result: {result}",
        ]

        logger.info(f"[run_script_function] Successfully executed {function_name}")
        return "\n".join(output)

    except Exception as e:
        logger.error(f"[run_script_function] Execution error: {str(e)}")
        return f"Execution failed\nFunction: {function_name}\nError: {str(e)}"


async def _resolve_execution_deployment_id(
    service: Any,
    script_id: str,
) -> Optional[str]:
    """Return the newest versioned API Executable deployment for a project."""
    all_deployments = []
    page_token = None
    while True:
        list_params = {"scriptId": script_id}
        if page_token:
            list_params["pageToken"] = page_token

        response = await asyncio.to_thread(
            service.projects().deployments().list(**list_params).execute
        )
        all_deployments.extend(response.get("deployments", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    candidates = [
        deployment
        for deployment in all_deployments
        if deployment.get("deploymentId")
        and deployment.get("deploymentConfig", {}).get("versionNumber") is not None
        and any(
            entry_point.get("entryPointType") == "EXECUTION_API"
            for entry_point in deployment.get("entryPoints", [])
        )
    ]
    if not candidates:
        return None

    latest = max(
        candidates,
        key=lambda deployment: deployment["deploymentConfig"]["versionNumber"],
    )
    return latest["deploymentId"]


@server.tool(
    title="Run Script Function",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("run_script_function", service_type="script")
@require_google_service("script", ["script_run", "script_deployments_readonly"])
async def run_script_function(
    service: Any,
    user_google_email: str,
    script_id: str,
    function_name: str,
    parameters: Optional[ObjectList] = None,
    dev_mode: bool = False,
    deployment_id: Optional[str] = None,
) -> str:
    """
    Executes a function in a deployed script.

    Args:
        service: Injected Google API service client
        user_google_email: User's email address
        script_id: The script project ID
        function_name: Name of function to execute
        parameters: Optional list of parameters to pass
        dev_mode: Whether to run latest code vs deployed version
        deployment_id: Optional API Executable deployment ID. When supplied,
            skips the automatic deployment lookup. When omitted, the versioned
            API Executable deployment with the highest version number is used.

    Returns:
        str: Formatted string with execution result or error
    """
    return await _run_script_function_impl(
        service,
        user_google_email,
        script_id,
        function_name,
        parameters,
        dev_mode,
        deployment_id,
    )


async def _create_deployment_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    description: str,
    version_description: Optional[str] = None,
) -> str:
    """Internal implementation for create_deployment.

    Creates a new version first, then creates a deployment using that version.
    """
    logger.info(
        f"[create_deployment] Email: {user_google_email}, ID: {script_id}, desc_len={len(description) if description else 0}"
    )

    # First, create a new version
    version_body = {"description": version_description or description}
    version = await asyncio.to_thread(
        service.projects()
        .versions()
        .create(scriptId=script_id, body=version_body)
        .execute
    )
    version_number = version.get("versionNumber")
    logger.info(f"[create_deployment] Created version {version_number}")

    # Now create the deployment with the version number
    deployment_body = {
        "versionNumber": version_number,
        "description": description,
    }

    deployment = await asyncio.to_thread(
        service.projects()
        .deployments()
        .create(scriptId=script_id, body=deployment_body)
        .execute
    )

    deployment_id = deployment.get("deploymentId", "Unknown")

    output = [
        f"Created deployment for script: {script_id}",
        f"Deployment ID: {deployment_id}",
        f"Version: {version_number}",
        f"Description: {description}",
    ]

    logger.info(f"[create_deployment] Created deployment {deployment_id}")
    return "\n".join(output)


@server.tool(
    title="Manage Deployment",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_deployment", service_type="script")
@require_google_service("script", "script_deployments")
async def manage_deployment(
    service: Any,
    user_google_email: str,
    action: str,
    script_id: str,
    deployment_id: Optional[str] = None,
    description: Optional[str] = None,
    version_description: Optional[str] = None,
    version_number: Optional[int] = None,
) -> str:
    """
    Create, update, or delete Apps Script deployments.

    Args:
        service: Injected Google API service client
        user_google_email: User's email address
        action: Action to perform - "create", "update", or "delete"
        script_id: The script project ID
        deployment_id: The deployment ID (required for update and delete)
        description: Deployment description (required for create; optional for update
            when version_number is supplied)
        version_description: Optional version description (for create only)
        version_number: Version number to point the deployment at (for update only).
            Required to roll a deployment forward to a newly created script version.

    Returns:
        str: Formatted string with deployment details or confirmation
    """
    action = action.lower().strip()
    if action == "create":
        if description is None or description.strip() == "":
            raise ValueError("description is required for create action")
        return await _create_deployment_impl(
            service, user_google_email, script_id, description, version_description
        )
    elif action == "update":
        if not deployment_id:
            raise ValueError("deployment_id is required for update action")
        has_description = description is not None and description.strip() != ""
        if not has_description and version_number is None:
            raise ValueError(
                "description or version_number is required for update action"
            )
        return await _update_deployment_impl(
            service,
            user_google_email,
            script_id,
            deployment_id,
            description,
            version_number,
        )
    elif action == "delete":
        if not deployment_id:
            raise ValueError("deployment_id is required for delete action")
        return await _delete_deployment_impl(
            service, user_google_email, script_id, deployment_id
        )
    else:
        raise ValueError(
            f"Invalid action '{action}'. Must be 'create', 'update', or 'delete'."
        )


async def _list_deployments_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
) -> str:
    """Internal implementation for list_deployments."""
    logger.info(f"[list_deployments] Email: {user_google_email}, ID: {script_id}")

    response = await asyncio.to_thread(
        service.projects().deployments().list(scriptId=script_id).execute
    )

    deployments = response.get("deployments", [])

    if not deployments:
        return f"No deployments found for script: {script_id}"

    output = [f"Deployments for script: {script_id}", ""]

    for i, deployment in enumerate(deployments, 1):
        deployment_id = deployment.get("deploymentId", "Unknown")
        # description and versionNumber live under deploymentConfig; fall back to
        # any top-level description for forward/backward compatibility.
        config = deployment.get("deploymentConfig", {})
        description = (
            config.get("description")
            or deployment.get("description")
            or "No description"
        )
        update_time = deployment.get("updateTime", "Unknown")
        # A HEAD deployment has no versionNumber — it always serves the latest
        # saved content rather than a pinned version.
        version_number = config.get("versionNumber")
        version_label = (
            str(version_number) if version_number is not None else "HEAD (latest)"
        )

        output.append(f"{i}. {description} ({deployment_id})")
        output.append(f"   Version: {version_label}")
        output.append(f"   Updated: {update_time}")
        output.append("")

    logger.info(f"[list_deployments] Found {len(deployments)} deployments")
    return "\n".join(output)


@server.tool(
    title="List Script Deployments",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("list_script_deployments", is_read_only=True, service_type="script")
@require_google_service("script", "script_deployments_readonly")
async def list_script_deployments(
    service: Any,
    user_google_email: str,
    script_id: str,
) -> str:
    """List deployments for an Apps Script project."""
    return await _list_deployments_impl(service, user_google_email, script_id)


async def _update_deployment_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    deployment_id: str,
    description: Optional[str] = None,
    version_number: Optional[int] = None,
) -> str:
    """Internal implementation for update_deployment.

    The Apps Script ``projects.deployments.update`` endpoint expects every
    field nested inside a ``deploymentConfig`` object; sending them at the top
    level fails with ``400 Invalid JSON payload``. ``scriptId`` is always part
    of the config, and ``versionNumber`` is required to repoint a deployment at
    a newer script version.
    """
    logger.info(
        f"[update_deployment] Email: {user_google_email}, Script: {script_id}, Deployment: {deployment_id}"
    )

    deployment_config: Dict[str, Any] = {"scriptId": script_id}
    if version_number is not None:
        deployment_config["versionNumber"] = version_number
    if description:
        deployment_config["description"] = description

    request_body = {"deploymentConfig": deployment_config}

    deployment = await asyncio.to_thread(
        service.projects()
        .deployments()
        .update(scriptId=script_id, deploymentId=deployment_id, body=request_body)
        .execute
    )

    deployment_config_resp = deployment.get("deploymentConfig", {})
    resolved_version = deployment_config_resp.get("versionNumber", version_number)
    resolved_description = deployment_config_resp.get(
        "description", deployment.get("description", "No description")
    )

    output = [
        f"Updated deployment: {deployment_id}",
        f"Script: {script_id}",
        f"Version: {resolved_version if resolved_version is not None else 'unchanged'}",
        f"Description: {resolved_description}",
    ]

    logger.info(f"[update_deployment] Updated deployment {deployment_id}")
    return "\n".join(output)


async def _delete_deployment_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    deployment_id: str,
) -> str:
    """Internal implementation for delete_deployment."""
    logger.info(
        f"[delete_deployment] Email: {user_google_email}, Script: {script_id}, Deployment: {deployment_id}"
    )

    await asyncio.to_thread(
        service.projects()
        .deployments()
        .delete(scriptId=script_id, deploymentId=deployment_id)
        .execute
    )

    output = f"Deleted deployment: {deployment_id} from script: {script_id}"

    logger.info(f"[delete_deployment] Deleted deployment {deployment_id}")
    return output


async def _list_script_processes_impl(
    service: Any,
    user_google_email: str,
    page_size: int = 50,
    script_id: Optional[str] = None,
) -> str:
    """Internal implementation for list_script_processes."""
    logger.info(
        f"[list_script_processes] Email: {user_google_email}, PageSize: {page_size}"
    )

    if script_id:
        # processes.list() has no top-level scriptId parameter; the
        # script-scoped endpoint takes it directly.
        response = await asyncio.to_thread(
            service.processes()
            .listScriptProcesses(scriptId=script_id, pageSize=page_size)
            .execute
        )
    else:
        response = await asyncio.to_thread(
            service.processes().list(pageSize=page_size).execute
        )

    processes = response.get("processes", [])

    if not processes:
        return "No recent script executions found."

    output = ["Recent script executions:", ""]

    for i, process in enumerate(processes, 1):
        function_name = process.get("functionName", "Unknown")
        process_status = process.get("processStatus", "Unknown")
        start_time = process.get("startTime", "Unknown")
        duration = process.get("duration", "Unknown")

        output.append(f"{i}. {function_name}")
        output.append(f"   Status: {process_status}")
        output.append(f"   Started: {start_time}")
        output.append(f"   Duration: {duration}")
        output.append("")

    logger.info(f"[list_script_processes] Found {len(processes)} processes")
    return "\n".join(output)


# ============================================================================
# Delete Script Project
# ============================================================================


async def _delete_script_project_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
) -> str:
    """Internal implementation for delete_script_project."""
    logger.info(
        f"[delete_script_project] Email: {user_google_email}, ScriptID: {script_id}"
    )

    # Apps Script projects are stored as Drive files
    await asyncio.to_thread(service.files().delete(fileId=script_id).execute)

    logger.info(f"[delete_script_project] Deleted script {script_id}")
    return f"Deleted Apps Script project: {script_id}"


@server.tool(
    title="Manage Script Project",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@_require_project_action_service(
    {"create": ("script", "script_projects"), "delete": ("drive", "drive_full")}
)
@handle_http_errors("manage_script_project", service_type="script")
async def manage_script_project(
    drive_service: Any,
    script_service: Any,
    user_google_email: str,
    action: str,
    script_id: Optional[str] = None,
    title: Optional[str] = None,
    parent_id: Optional[str] = None,
) -> str:
    """
    Create or delete an Apps Script project.

    Actions:
        - "create": Create a project. Requires title; parent_id is optional.
        - "delete": Permanently delete a project. Requires script_id.
    """
    action = action.lower().strip()
    if action == "create":
        if not title or not title.strip():
            raise UserInputError("title is required for create action")
        return await _create_script_project_impl(
            script_service, user_google_email, title, parent_id
        )
    elif action == "delete":
        if not script_id:
            raise UserInputError("script_id is required for delete action")
        return await _delete_script_project_impl(
            drive_service, user_google_email, script_id
        )
    else:
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'create' or 'delete'."
        )


# ============================================================================
# Version Management
# ============================================================================


async def _list_versions_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
) -> str:
    """Internal implementation for list_versions."""
    logger.info(f"[list_versions] Email: {user_google_email}, ScriptID: {script_id}")

    response = await asyncio.to_thread(
        service.projects().versions().list(scriptId=script_id).execute
    )

    versions = response.get("versions", [])

    if not versions:
        return f"No versions found for script: {script_id}"

    output = [f"Versions for script: {script_id}", ""]

    for version in versions:
        version_number = version.get("versionNumber", "Unknown")
        description = version.get("description", "No description")
        create_time = version.get("createTime", "Unknown")

        output.append(f"Version {version_number}: {description}")
        output.append(f"   Created: {create_time}")
        output.append("")

    logger.info(f"[list_versions] Found {len(versions)} versions")
    return "\n".join(output)


@server.tool(
    title="Manage Script Version",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_script_version", service_type="script")
@require_google_service("script", "script_full")
async def manage_script_version(
    service: Any,
    user_google_email: str,
    action: str,
    script_id: str,
    description: Optional[str] = None,
) -> str:
    """
    Create immutable version snapshots of a script project.

    Versions capture a snapshot of the current script code; once created they
    cannot be modified.

    Actions:
        - "create": Create a new version from the current code. Optional
          description.

    Args:
        service: Injected Google API service client
        user_google_email: User's email address
        action: "create".
        script_id: The script project ID.
        description: Optional description for the new version (create only).

    Returns:
        str: Formatted result for the requested action.
    """
    action = action.lower().strip()
    if action == "create":
        return await _create_version_impl(
            service, user_google_email, script_id, description
        )
    else:
        raise UserInputError(f"Invalid action '{action}'. Must be 'create'.")


async def _create_version_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    description: Optional[str] = None,
) -> str:
    """Internal implementation for create_version."""
    logger.info(f"[create_version] Email: {user_google_email}, ScriptID: {script_id}")

    request_body = {}
    if description:
        request_body["description"] = description

    version = await asyncio.to_thread(
        service.projects()
        .versions()
        .create(scriptId=script_id, body=request_body)
        .execute
    )

    version_number = version.get("versionNumber", "Unknown")
    create_time = version.get("createTime", "Unknown")

    output = [
        f"Created version {version_number} for script: {script_id}",
        f"Description: {description or 'No description'}",
        f"Created: {create_time}",
    ]

    logger.info(f"[create_version] Created version {version_number}")
    return "\n".join(output)


async def _get_version_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    version_number: int,
) -> str:
    """Internal implementation for get_version."""
    logger.info(
        f"[get_version] Email: {user_google_email}, ScriptID: {script_id}, Version: {version_number}"
    )

    version = await asyncio.to_thread(
        service.projects()
        .versions()
        .get(scriptId=script_id, versionNumber=version_number)
        .execute
    )

    ver_num = version.get("versionNumber", "Unknown")
    description = version.get("description", "No description")
    create_time = version.get("createTime", "Unknown")

    output = [
        f"Version {ver_num} of script: {script_id}",
        f"Description: {description}",
        f"Created: {create_time}",
    ]

    logger.info(f"[get_version] Retrieved version {ver_num}")
    return "\n".join(output)


@server.tool(
    title="Get Script Version",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_script_version", is_read_only=True, service_type="script")
@require_google_service("script", "script_readonly")
async def get_script_version(
    service: Any,
    user_google_email: str,
    action: str,
    script_id: str,
    version_number: Optional[int] = None,
) -> str:
    """
    List or retrieve immutable version snapshots of a script project.

    Actions:
        - "list": List every version of the project.
        - "get": Retrieve one version. Requires version_number.
    """
    action = action.lower().strip()
    if action == "list":
        return await _list_versions_impl(service, user_google_email, script_id)
    elif action == "get":
        if version_number is None:
            raise UserInputError("version_number is required for get action")
        return await _get_version_impl(
            service, user_google_email, script_id, version_number
        )
    else:
        raise UserInputError(f"Invalid action '{action}'. Must be 'list' or 'get'.")


# ============================================================================
# Activity: processes and metrics
# ============================================================================


async def _get_script_metrics_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    metrics_granularity: str = "DAILY",
) -> str:
    """Internal implementation for get_script_metrics."""
    logger.info(
        f"[get_script_metrics] Email: {user_google_email}, ScriptID: {script_id}, Granularity: {metrics_granularity}"
    )

    request_params = {
        "scriptId": script_id,
        "metricsGranularity": metrics_granularity,
    }

    response = await asyncio.to_thread(
        service.projects().getMetrics(**request_params).execute
    )

    output = [
        f"Metrics for script: {script_id}",
        f"Granularity: {metrics_granularity}",
        "",
    ]

    # Active users
    active_users = response.get("activeUsers", [])
    if active_users:
        output.append("Active Users:")
        for metric in active_users:
            start_time = metric.get("startTime", "Unknown")
            end_time = metric.get("endTime", "Unknown")
            value = metric.get("value", "0")
            output.append(f"  {start_time} to {end_time}: {value} users")
        output.append("")

    # Total executions
    total_executions = response.get("totalExecutions", [])
    if total_executions:
        output.append("Total Executions:")
        for metric in total_executions:
            start_time = metric.get("startTime", "Unknown")
            end_time = metric.get("endTime", "Unknown")
            value = metric.get("value", "0")
            output.append(f"  {start_time} to {end_time}: {value} executions")
        output.append("")

    # Failed executions
    failed_executions = response.get("failedExecutions", [])
    if failed_executions:
        output.append("Failed Executions:")
        for metric in failed_executions:
            start_time = metric.get("startTime", "Unknown")
            end_time = metric.get("endTime", "Unknown")
            value = metric.get("value", "0")
            output.append(f"  {start_time} to {end_time}: {value} failures")
        output.append("")

    if not active_users and not total_executions and not failed_executions:
        output.append("No metrics data available for this script.")

    logger.info(f"[get_script_metrics] Retrieved metrics for {script_id}")
    return "\n".join(output)


@server.tool(
    title="Get Script Activity",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_script_activity", is_read_only=True, service_type="script")
@require_google_service("script", "script_readonly")
async def get_script_activity(
    service: Any,
    user_google_email: str,
    action: str,
    script_id: Optional[str] = None,
    page_size: int = 50,
    metrics_granularity: str = "DAILY",
) -> str:
    """
    Read execution activity for scripts: recent processes or aggregate metrics.

    Actions:
        - "processes": List recent execution processes. Without script_id, lists
          the user's own recent runs; with script_id, lists all processes for
          that script visible to the user (including runs by others). Optional
          page_size.
        - "metrics": Get aggregate execution metrics (active users, total and
          failed executions) for one script. Requires script_id; optional
          metrics_granularity ("DAILY" or "WEEKLY").

    Args:
        service: Injected Google API service client
        user_google_email: User's email address
        action: One of "processes", "metrics".
        script_id: The script project ID (required for metrics; optional for
            processes).
        page_size: Number of results for processes (default: 50).
        metrics_granularity: Granularity for metrics - "DAILY" or "WEEKLY".

    Returns:
        str: Formatted process list or metrics data.
    """
    action = action.lower().strip()
    if action == "processes":
        return await _list_script_processes_impl(
            service, user_google_email, page_size, script_id
        )
    elif action == "metrics":
        if not script_id:
            raise UserInputError("script_id is required for metrics action")
        return await _get_script_metrics_impl(
            service, user_google_email, script_id, metrics_granularity
        )
    else:
        raise UserInputError(
            f"Invalid action '{action}'. Must be 'processes' or 'metrics'."
        )


# ============================================================================
# Trigger Code Generation
# ============================================================================


def _generate_trigger_code_impl(
    trigger_type: str,
    function_name: str,
    schedule: str = "",
) -> str:
    """Internal implementation for generate_trigger_code."""
    code_lines = []

    if trigger_type == "on_open":
        code_lines = [
            "// Simple trigger - just rename your function to 'onOpen'",
            "// This runs automatically when the document is opened",
            "function onOpen(e) {",
            f"  {function_name}();",
            "}",
        ]
    elif trigger_type == "on_edit":
        code_lines = [
            "// Simple trigger - just rename your function to 'onEdit'",
            "// This runs automatically when a user edits the spreadsheet",
            "function onEdit(e) {",
            f"  {function_name}();",
            "}",
        ]
    elif trigger_type == "time_minutes":
        interval = schedule or "5"
        code_lines = [
            "// Run this function ONCE to install the trigger",
            f"function createTimeTrigger_{function_name}() {{",
            "  // Delete existing triggers for this function first",
            "  const triggers = ScriptApp.getProjectTriggers();",
            "  triggers.forEach(trigger => {",
            f"    if (trigger.getHandlerFunction() === '{function_name}') {{",
            "      ScriptApp.deleteTrigger(trigger);",
            "    }",
            "  });",
            "",
            f"  // Create new trigger - runs every {interval} minutes",
            f"  ScriptApp.newTrigger('{function_name}')",
            "    .timeBased()",
            f"    .everyMinutes({interval})",
            "    .create();",
            "",
            f"  Logger.log('Trigger created: {function_name} will run every {interval} minutes');",
            "}",
        ]
    elif trigger_type == "time_hours":
        interval = schedule or "1"
        code_lines = [
            "// Run this function ONCE to install the trigger",
            f"function createTimeTrigger_{function_name}() {{",
            "  // Delete existing triggers for this function first",
            "  const triggers = ScriptApp.getProjectTriggers();",
            "  triggers.forEach(trigger => {",
            f"    if (trigger.getHandlerFunction() === '{function_name}') {{",
            "      ScriptApp.deleteTrigger(trigger);",
            "    }",
            "  });",
            "",
            f"  // Create new trigger - runs every {interval} hour(s)",
            f"  ScriptApp.newTrigger('{function_name}')",
            "    .timeBased()",
            f"    .everyHours({interval})",
            "    .create();",
            "",
            f"  Logger.log('Trigger created: {function_name} will run every {interval} hour(s)');",
            "}",
        ]
    elif trigger_type == "time_daily":
        hour = schedule or "9"
        code_lines = [
            "// Run this function ONCE to install the trigger",
            f"function createDailyTrigger_{function_name}() {{",
            "  // Delete existing triggers for this function first",
            "  const triggers = ScriptApp.getProjectTriggers();",
            "  triggers.forEach(trigger => {",
            f"    if (trigger.getHandlerFunction() === '{function_name}') {{",
            "      ScriptApp.deleteTrigger(trigger);",
            "    }",
            "  });",
            "",
            f"  // Create new trigger - runs daily at {hour}:00",
            f"  ScriptApp.newTrigger('{function_name}')",
            "    .timeBased()",
            f"    .atHour({hour})",
            "    .everyDays(1)",
            "    .create();",
            "",
            f"  Logger.log('Trigger created: {function_name} will run daily at {hour}:00');",
            "}",
        ]
    elif trigger_type == "time_weekly":
        day = schedule.upper() if schedule else "MONDAY"
        code_lines = [
            "// Run this function ONCE to install the trigger",
            f"function createWeeklyTrigger_{function_name}() {{",
            "  // Delete existing triggers for this function first",
            "  const triggers = ScriptApp.getProjectTriggers();",
            "  triggers.forEach(trigger => {",
            f"    if (trigger.getHandlerFunction() === '{function_name}') {{",
            "      ScriptApp.deleteTrigger(trigger);",
            "    }",
            "  });",
            "",
            f"  // Create new trigger - runs weekly on {day}",
            f"  ScriptApp.newTrigger('{function_name}')",
            "    .timeBased()",
            f"    .onWeekDay(ScriptApp.WeekDay.{day})",
            "    .atHour(9)",
            "    .create();",
            "",
            f"  Logger.log('Trigger created: {function_name} will run every {day} at 9:00');",
            "}",
        ]
    elif trigger_type == "on_form_submit":
        code_lines = [
            "// Run this function ONCE to install the trigger",
            "// This must be run from a script BOUND to the Google Form",
            f"function createFormSubmitTrigger_{function_name}() {{",
            "  // Delete existing triggers for this function first",
            "  const triggers = ScriptApp.getProjectTriggers();",
            "  triggers.forEach(trigger => {",
            f"    if (trigger.getHandlerFunction() === '{function_name}') {{",
            "      ScriptApp.deleteTrigger(trigger);",
            "    }",
            "  });",
            "",
            "  // Create new trigger - runs when form is submitted",
            f"  ScriptApp.newTrigger('{function_name}')",
            "    .forForm(FormApp.getActiveForm())",
            "    .onFormSubmit()",
            "    .create();",
            "",
            f"  Logger.log('Trigger created: {function_name} will run on form submit');",
            "}",
        ]
    elif trigger_type == "on_change":
        code_lines = [
            "// Run this function ONCE to install the trigger",
            "// This must be run from a script BOUND to a Google Sheet",
            f"function createChangeTrigger_{function_name}() {{",
            "  // Delete existing triggers for this function first",
            "  const triggers = ScriptApp.getProjectTriggers();",
            "  triggers.forEach(trigger => {",
            f"    if (trigger.getHandlerFunction() === '{function_name}') {{",
            "      ScriptApp.deleteTrigger(trigger);",
            "    }",
            "  });",
            "",
            "  // Create new trigger - runs when spreadsheet changes",
            f"  ScriptApp.newTrigger('{function_name}')",
            "    .forSpreadsheet(SpreadsheetApp.getActive())",
            "    .onChange()",
            "    .create();",
            "",
            f"  Logger.log('Trigger created: {function_name} will run on spreadsheet change');",
            "}",
        ]
    else:
        return (
            f"Unknown trigger type: {trigger_type}\n\n"
            "Valid types: time_minutes, time_hours, time_daily, time_weekly, "
            "on_open, on_edit, on_form_submit, on_change"
        )

    code = "\n".join(code_lines)

    instructions = []
    if trigger_type.startswith("on_"):
        if trigger_type in ("on_open", "on_edit"):
            instructions = [
                "SIMPLE TRIGGER",
                "=" * 50,
                "",
                "Add this code to your script. Simple triggers run automatically",
                "when the event occurs - no setup function needed.",
                "",
                "Note: Simple triggers have limitations:",
                "- Cannot access services that require authorization",
                "- Cannot run longer than 30 seconds",
                "- Cannot make external HTTP requests",
                "",
                "For more capabilities, use an installable trigger instead.",
                "",
                "CODE TO ADD:",
                "-" * 50,
            ]
        else:
            instructions = [
                "INSTALLABLE TRIGGER",
                "=" * 50,
                "",
                "1. Add this code to your script",
                f"2. Run the setup function once: createFormSubmitTrigger_{function_name}() or similar",
                "3. The trigger will then run automatically",
                "",
                "CODE TO ADD:",
                "-" * 50,
            ]
    else:
        instructions = [
            "INSTALLABLE TRIGGER",
            "=" * 50,
            "",
            "1. Add this code to your script using manage_script_content(action='update')",
            "2. Run the setup function ONCE (manually in Apps Script editor or via run_script_function)",
            "3. The trigger will then run automatically on schedule",
            "",
            "To check installed triggers: Apps Script editor > Triggers (clock icon)",
            "",
            "CODE TO ADD:",
            "-" * 50,
        ]

    return "\n".join(instructions) + "\n\n" + code


@server.tool(
    title="Generate Trigger Code",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def generate_trigger_code(
    trigger_type: str,
    function_name: str,
    schedule: str = "",
) -> str:
    """
    Generates Apps Script code for creating triggers.

    The Apps Script API cannot create triggers directly - they must be created
    from within Apps Script itself. This tool generates the code you need.
    To list or remove existing triggers without opening the editor, use
    `manage_script_trigger` (action="list" / action="delete") instead.

    Args:
        trigger_type: Type of trigger. One of:
                      - "time_minutes" (run every N minutes: 1, 5, 10, 15, 30)
                      - "time_hours" (run every N hours: 1, 2, 4, 6, 8, 12)
                      - "time_daily" (run daily at a specific hour: 0-23)
                      - "time_weekly" (run weekly on a specific day)
                      - "on_open" (simple trigger - runs when document opens)
                      - "on_edit" (simple trigger - runs when user edits)
                      - "on_form_submit" (runs when form is submitted)
                      - "on_change" (runs when content changes)

        function_name: The function to run when trigger fires (e.g., "sendDailyReport")

        schedule: Schedule details (depends on trigger_type):
                  - For time_minutes: "1", "5", "10", "15", or "30"
                  - For time_hours: "1", "2", "4", "6", "8", or "12"
                  - For time_daily: hour as "0"-"23" (e.g., "9" for 9am)
                  - For time_weekly: "MONDAY", "TUESDAY", etc.
                  - For simple triggers (on_open, on_edit): not needed

    Returns:
        str: Apps Script code to create the trigger
    """
    return _generate_trigger_code_impl(trigger_type, function_name, schedule)


# ---------------------------------------------------------------------------
# Trigger management (list / delete for the current user)
#
# The Apps Script REST API has no triggers resource; trigger state only exists
# inside the Apps Script runtime. A small helper file is merged into the
# project and invoked through the Execution API (scripts.run), which requires
# the script.scriptapp scope.
# ---------------------------------------------------------------------------

_TRIGGER_ADMIN_FILE_NAME = "McpTriggerAdmin"
_TRIGGER_ADMIN_MARKER = (
    "// Auto-provisioned by the Google Workspace MCP server's "
    "manage_script_trigger tool."
)
_TRIGGER_ADMIN_SOURCE = (
    _TRIGGER_ADMIN_MARKER
    + """
// Overwritten when the helper changes; safe to delete.

function __mcpListTriggers() {
  var triggers = ScriptApp.getProjectTriggers();
  var out = [];
  for (var i = 0; i < triggers.length; i++) {
    var t = triggers[i];
    out.push({
      uniqueId: t.getUniqueId(),
      handlerFunction: t.getHandlerFunction(),
      eventType: t.getEventType().toString(),
      triggerSource: t.getTriggerSource().toString()
    });
  }
  return JSON.stringify(out);
}

function __mcpDeleteTrigger(uniqueId, handlerFunction) {
  if (!uniqueId && !handlerFunction) return "[]";
  var triggers = ScriptApp.getProjectTriggers();
  var deleted = [];
  for (var i = 0; i < triggers.length; i++) {
    var t = triggers[i];
    var tId = t.getUniqueId();
    var tHandler = t.getHandlerFunction();
    if ((!uniqueId || tId === uniqueId) &&
        (!handlerFunction || tHandler === handlerFunction)) {
      ScriptApp.deleteTrigger(t);
      deleted.push({uniqueId: tId, handlerFunction: tHandler});
    }
  }
  return JSON.stringify(deleted);
}
"""
)


async def _ensure_trigger_admin_file(service: Any, script_id: str) -> None:
    """Install or refresh the trigger helper file without touching other files.

    Holds the per-script update lock so it cannot race manage_script_content.
    A same-named file is only overwritten when it carries the helper marker.
    """
    admin_file = {
        "name": _TRIGGER_ADMIN_FILE_NAME,
        "type": "SERVER_JS",
        "source": _TRIGGER_ADMIN_SOURCE,
    }
    async with _get_script_update_lock(script_id):
        current_content = await asyncio.to_thread(
            service.projects().getContent(scriptId=script_id).execute
        )
        existing_files = current_content.get("files", [])
        current_source = next(
            (
                f.get("source", "")
                for f in existing_files
                if f.get("name") == _TRIGGER_ADMIN_FILE_NAME
                and f.get("type") == "SERVER_JS"
            ),
            None,
        )
        if current_source == _TRIGGER_ADMIN_SOURCE:
            return
        if current_source is not None and not current_source.startswith(
            _TRIGGER_ADMIN_MARKER
        ):
            raise UserInputError(
                f"Cannot install the trigger helper because the project already "
                f"contains a user-managed {_TRIGGER_ADMIN_FILE_NAME}.gs file. "
                "Rename that file before managing triggers."
            )

        merged_files = _merge_script_files(existing_files, [admin_file])
        await asyncio.to_thread(
            service.projects()
            .updateContent(scriptId=script_id, body={"files": merged_files})
            .execute
        )


async def _run_trigger_admin(
    service: Any,
    script_id: str,
    function_name: str,
    parameters: List[Optional[str]],
    dev_mode: bool,
    deployment_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Ensure the executed code has the helper, run it, and parse its JSON result."""
    if not deployment_id:
        deployment_id = await _resolve_execution_deployment_id(service, script_id)
    if not deployment_id:
        raise UserInputError(
            "No versioned API Executable deployment was found. In the Apps Script "
            "editor, use Deploy > New deployment > API Executable."
        )

    if dev_mode:
        await _ensure_trigger_admin_file(service, script_id)
    else:
        deployment = await asyncio.to_thread(
            service.projects()
            .deployments()
            .get(scriptId=script_id, deploymentId=deployment_id)
            .execute
        )
        version_number = deployment.get("deploymentConfig", {}).get("versionNumber")
        if version_number is None:
            raise UserInputError(
                "The selected deployment must reference a script version."
            )
        content = await asyncio.to_thread(
            service.projects()
            .getContent(scriptId=script_id, versionNumber=version_number)
            .execute
        )
        if not any(
            function.get("name") == function_name
            for file in content.get("files", [])
            if file.get("type") == "SERVER_JS"
            for function in file.get("functionSet", {}).get("values", [])
        ):
            raise UserInputError(
                f"Deployment '{deployment_id}' (version {version_number}) does not "
                f"contain {function_name}. Run with dev_mode=True as the project "
                "owner to provision the helper, then create a new version and "
                "update the deployment before using dev_mode=False."
            )

    body: Dict[str, Any] = {"function": function_name, "devMode": dev_mode}
    if parameters:
        body["parameters"] = parameters
    response = await asyncio.to_thread(
        service.scripts().run(scriptId=deployment_id, body=body).execute
    )

    if "error" in response:
        message = response["error"].get("message", "Unknown error")
        raise RuntimeError(f"{function_name} execution failed: {message}")

    result = response.get("response", {}).get("result")
    return json.loads(result) if result else []


async def _list_script_triggers_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    dev_mode: bool = True,
    deployment_id: Optional[str] = None,
) -> str:
    """Internal implementation for manage_script_trigger list."""
    logger.info(f"[list_script_triggers] Email: {user_google_email}, ID: {script_id}")

    triggers = await _run_trigger_admin(
        service, script_id, "__mcpListTriggers", [], dev_mode, deployment_id
    )

    if not triggers:
        return f"No triggers found for the current user on script: {script_id}"

    output = [f"Triggers owned by the current user for script {script_id}:", ""]
    for i, trig in enumerate(triggers, 1):
        output.append(f"{i}. {trig.get('handlerFunction', 'Unknown')}")
        output.append(f"   Unique ID: {trig.get('uniqueId', 'Unknown')}")
        output.append(f"   Event type: {trig.get('eventType', 'Unknown')}")
        output.append(f"   Source: {trig.get('triggerSource', 'Unknown')}")
        output.append("")

    logger.info(f"[list_script_triggers] Found {len(triggers)} triggers")
    return "\n".join(output)


@server.tool(
    title="Manage Script Trigger",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("manage_script_trigger", service_type="script")
@require_google_service(
    "script",
    ["script_projects", "script_scriptapp", "script_deployments_readonly"],
)
async def manage_script_trigger(
    service: Any,
    user_google_email: str,
    action: str,
    script_id: str,
    trigger_id: Optional[str] = None,
    handler_function: Optional[str] = None,
    dev_mode: bool = True,
    deployment_id: Optional[str] = None,
) -> str:
    """
    List or delete the current user's installable triggers on a script project.

    The Apps Script REST API has no triggers resource, so in dev_mode both actions
    provision (or refresh) a small helper file and run it via the Execution
    API - the only way to inspect or change trigger state without opening the
    editor. Neither action is read-only: the helper file may be written into the
    project on first use. To create a trigger, use `generate_trigger_code` to
    produce the setup code, add it with `manage_script_content`, then run it with
    `run_script_function`.

    Actions:
        - "list": List the current user's triggers configured on the project.
        - "delete": Delete triggers matching every supplied selector. trigger_id
          removes one trigger; handler_function alone removes EVERY trigger
          calling that function; both together remove the trigger only if its
          handler also matches. At least one is required; run "list" first to
          find a trigger's unique ID.

    Args:
        service: Injected Google API service client
        user_google_email: User's email address
        action: One of "list", "delete".
        script_id: The script project ID.
        trigger_id: Unique ID of a specific trigger to delete (delete only).
        handler_function: Delete all triggers calling this function name
            (delete only).
        dev_mode: Run against the latest saved code (default; project owner
            only) vs. the deployed version. False requires the helper to
            already exist in the deployed version.
        deployment_id: Optional API Executable deployment ID. When omitted, the
            highest versioned API Executable deployment is used.

    Returns:
        str: Formatted list of triggers, or a summary of the trigger(s) deleted.
    """
    action = action.lower().strip()
    if action == "list":
        return await _list_script_triggers_impl(
            service, user_google_email, script_id, dev_mode, deployment_id
        )
    elif action == "delete":
        return await _delete_script_trigger_impl(
            service,
            user_google_email,
            script_id,
            trigger_id,
            handler_function,
            dev_mode,
            deployment_id,
        )
    else:
        raise UserInputError(f"Invalid action '{action}'. Must be 'list' or 'delete'.")


async def _delete_script_trigger_impl(
    service: Any,
    user_google_email: str,
    script_id: str,
    trigger_id: Optional[str] = None,
    handler_function: Optional[str] = None,
    dev_mode: bool = True,
    deployment_id: Optional[str] = None,
) -> str:
    """Internal implementation for manage_script_trigger delete."""
    if not trigger_id and not handler_function:
        raise UserInputError("Provide trigger_id or handler_function (or both).")

    logger.info(
        f"[delete_script_trigger] Email: {user_google_email}, ID: {script_id}, "
        f"trigger_id: {trigger_id}, handler_function: {handler_function}"
    )

    deleted = await _run_trigger_admin(
        service,
        script_id,
        "__mcpDeleteTrigger",
        [trigger_id, handler_function],
        dev_mode,
        deployment_id,
    )

    if not deleted:
        return (
            f"No matching trigger found for script {script_id} "
            f"(trigger_id={trigger_id}, handler_function={handler_function})."
        )

    output = [f"Deleted {len(deleted)} trigger(s) from script {script_id}:"]
    for trig in deleted:
        output.append(
            f"- {trig.get('handlerFunction', 'Unknown')} "
            f"(unique ID: {trig.get('uniqueId', 'Unknown')})"
        )

    logger.info(f"[delete_script_trigger] Deleted {len(deleted)} trigger(s)")
    return "\n".join(output)
