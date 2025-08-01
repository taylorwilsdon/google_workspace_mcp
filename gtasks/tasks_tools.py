"""
Google Tasks MCP Tools

This module provides MCP tools for interacting with Google Tasks API.
"""

import logging
import asyncio
from typing import Optional

from googleapiclient.errors import HttpError

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors

logger = logging.getLogger(__name__)


@server.tool()
@require_google_service("tasks", "tasks_read")
@handle_http_errors("list_task_lists", service_type="tasks")
async def list_task_lists(
    service,
    user_google_email: str,
    max_results: Optional[int] = None,
    page_token: Optional[str] = None
) -> str:
    """
    List all task lists for the user.

    Args:
        user_google_email (str): The user's Google email address. Required.
        max_results (Optional[int]): Maximum number of task lists to return (default: 1000, max: 1000).
        page_token (Optional[str]): Token for pagination.

    Returns:
        str: List of task lists with their IDs, titles, and details.
    """
    logger.info(f"[list_task_lists] Invoked. Email: '{user_google_email}'")

    try:
        params = {}
        if max_results is not None:
            params["maxResults"] = max_results
        if page_token:
            params["pageToken"] = page_token

        result = await asyncio.to_thread(
            service.tasklists().list(**params).execute
        )

        task_lists = result.get("items", [])
        next_page_token = result.get("nextPageToken")

        if not task_lists:
            return f"No task lists found for {user_google_email}."

        response = f"Task Lists for {user_google_email}:\n"
        for task_list in task_lists:
            response += f"- {task_list['title']} (ID: {task_list['id']})\n"
            response += f"  Updated: {task_list.get('updated', 'N/A')}\n"

        if next_page_token:
            response += f"\nNext page token: {next_page_token}"

        logger.info(f"Found {len(task_lists)} task lists for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks_read")
@handle_http_errors("get_task_list", service_type="tasks")
async def get_task_list(
    service,
    user_google_email: str,
    task_list_id: str
) -> str:
    """
    Get details of a specific task list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list to retrieve.

    Returns:
        str: Task list details including title, ID, and last updated time.
    """
    logger.info(f"[get_task_list] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}")

    try:
        task_list = await asyncio.to_thread(
            service.tasklists().get(tasklist=task_list_id).execute
        )

        response = f"""Task List Details for {user_google_email}:
- Title: {task_list['title']}
- ID: {task_list['id']}
- Updated: {task_list.get('updated', 'N/A')}
- Self Link: {task_list.get('selfLink', 'N/A')}"""

        logger.info(f"Retrieved task list '{task_list['title']}' for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("create_task_list", service_type="tasks")
async def create_task_list(
    service,
    user_google_email: str,
    title: str
) -> str:
    """
    Create a new task list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        title (str): The title of the new task list.

    Returns:
        str: Confirmation message with the new task list ID and details.
    """
    logger.info(f"[create_task_list] Invoked. Email: '{user_google_email}', Title: '{title}'")

    try:
        body = {
            "title": title
        }

        result = await asyncio.to_thread(
            service.tasklists().insert(body=body).execute
        )

        response = f"""Task List Created for {user_google_email}:
- Title: {result['title']}
- ID: {result['id']}
- Created: {result.get('updated', 'N/A')}
- Self Link: {result.get('selfLink', 'N/A')}"""

        logger.info(f"Created task list '{title}' with ID {result['id']} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("update_task_list", service_type="tasks")
async def update_task_list(
    service,
    user_google_email: str,
    task_list_id: str,
    title: str
) -> str:
    """
    Update an existing task list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list to update.
        title (str): The new title for the task list.

    Returns:
        str: Confirmation message with updated task list details.
    """
    logger.info(f"[update_task_list] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}, New Title: '{title}'")

    try:
        body = {
            "id": task_list_id,
            "title": title
        }

        result = await asyncio.to_thread(
            service.tasklists().update(tasklist=task_list_id, body=body).execute
        )

        response = f"""Task List Updated for {user_google_email}:
- Title: {result['title']}
- ID: {result['id']}
- Updated: {result.get('updated', 'N/A')}"""

        logger.info(f"Updated task list {task_list_id} with new title '{title}' for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("delete_task_list", service_type="tasks")
async def delete_task_list(
    service,
    user_google_email: str,
    task_list_id: str
) -> str:
    """
    Delete a task list. Note: This will also delete all tasks in the list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list to delete.

    Returns:
        str: Confirmation message.
    """
    logger.info(f"[delete_task_list] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}")

    try:
        await asyncio.to_thread(
            service.tasklists().delete(tasklist=task_list_id).execute
        )

        response = f"Task list {task_list_id} has been deleted for {user_google_email}. All tasks in this list have also been deleted."

        logger.info(f"Deleted task list {task_list_id} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks_read")
@handle_http_errors("list_tasks", service_type="tasks")
async def list_tasks(
    service,
    user_google_email: str,
    task_list_id: str,
    max_results: Optional[int] = None,
    page_token: Optional[str] = None,
    show_completed: Optional[bool] = None,
    show_deleted: Optional[bool] = None,
    show_hidden: Optional[bool] = None,
    show_assigned: Optional[bool] = None,
    completed_max: Optional[str] = None,
    completed_min: Optional[str] = None,
    due_max: Optional[str] = None,
    due_min: Optional[str] = None,
    updated_min: Optional[str] = None
) -> str:
    """
    List all tasks in a specific task list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list to retrieve tasks from.
        max_results (Optional[int]): Maximum number of tasks to return (default: 20, max: 100).
        page_token (Optional[str]): Token for pagination.
        show_completed (Optional[bool]): Whether to include completed tasks (default: True).
        show_deleted (Optional[bool]): Whether to include deleted tasks (default: False).
        show_hidden (Optional[bool]): Whether to include hidden tasks (default: False).
        show_assigned (Optional[bool]): Whether to include assigned tasks (default: False).
        completed_max (Optional[str]): Upper bound for completion date (RFC 3339 timestamp).
        completed_min (Optional[str]): Lower bound for completion date (RFC 3339 timestamp).
        due_max (Optional[str]): Upper bound for due date (RFC 3339 timestamp).
        due_min (Optional[str]): Lower bound for due date (RFC 3339 timestamp).
        updated_min (Optional[str]): Lower bound for last modification time (RFC 3339 timestamp).

    Returns:
        str: List of tasks with their details.
    """
    logger.info(f"[list_tasks] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}")

    try:
        params = {"tasklist": task_list_id}
        if max_results is not None:
            params["maxResults"] = max_results
        if page_token:
            params["pageToken"] = page_token
        if show_completed is not None:
            params["showCompleted"] = show_completed
        if show_deleted is not None:
            params["showDeleted"] = show_deleted
        if show_hidden is not None:
            params["showHidden"] = show_hidden
        if show_assigned is not None:
            params["showAssigned"] = show_assigned
        if completed_max:
            params["completedMax"] = completed_max
        if completed_min:
            params["completedMin"] = completed_min
        if due_max:
            params["dueMax"] = due_max
        if due_min:
            params["dueMin"] = due_min
        if updated_min:
            params["updatedMin"] = updated_min

        result = await asyncio.to_thread(
            service.tasks().list(**params).execute
        )

        tasks = result.get("items", [])
        next_page_token = result.get("nextPageToken")

        if not tasks:
            return f"No tasks found in task list {task_list_id} for {user_google_email}."

        response = f"Tasks in list {task_list_id} for {user_google_email}:\n"
        for task in tasks:
            response += f"- {task.get('title', 'Untitled')} (ID: {task['id']})\n"
            response += f"  Status: {task.get('status', 'N/A')}\n"
            if task.get('due'):
                response += f"  Due: {task['due']}\n"
            if task.get('notes'):
                response += f"  Notes: {task['notes'][:100]}{'...' if len(task['notes']) > 100 else ''}\n"
            if task.get('completed'):
                response += f"  Completed: {task['completed']}\n"
            response += f"  Updated: {task.get('updated', 'N/A')}\n"
            response += "\n"

        if next_page_token:
            response += f"Next page token: {next_page_token}"

        logger.info(f"Found {len(tasks)} tasks in list {task_list_id} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks_read")
@handle_http_errors("get_task", service_type="tasks")
async def get_task(
    service,
    user_google_email: str,
    task_list_id: str,
    task_id: str
) -> str:
    """
    Get details of a specific task.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list containing the task.
        task_id (str): The ID of the task to retrieve.

    Returns:
        str: Task details including title, notes, status, due date, etc.
    """
    logger.info(f"[get_task] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}, Task ID: {task_id}")

    try:
        task = await asyncio.to_thread(
            service.tasks().get(tasklist=task_list_id, task=task_id).execute
        )

        response = f"""Task Details for {user_google_email}:
- Title: {task.get('title', 'Untitled')}
- ID: {task['id']}
- Status: {task.get('status', 'N/A')}
- Updated: {task.get('updated', 'N/A')}"""

        if task.get('due'):
            response += f"\n- Due Date: {task['due']}"
        if task.get('completed'):
            response += f"\n- Completed: {task['completed']}"
        if task.get('notes'):
            response += f"\n- Notes: {task['notes']}"
        if task.get('parent'):
            response += f"\n- Parent Task ID: {task['parent']}"
        if task.get('position'):
            response += f"\n- Position: {task['position']}"
        if task.get('selfLink'):
            response += f"\n- Self Link: {task['selfLink']}"
        if task.get('webViewLink'):
            response += f"\n- Web View Link: {task['webViewLink']}"

        logger.info(f"Retrieved task '{task.get('title', 'Untitled')}' for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("create_task", service_type="tasks")
async def create_task(
    service,
    user_google_email: str,
    task_list_id: str,
    title: str,
    notes: Optional[str] = None,
    due: Optional[str] = None,
    parent: Optional[str] = None,
    previous: Optional[str] = None
) -> str:
    """
    Create a new task in a task list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list to create the task in.
        title (str): The title of the task.
        notes (Optional[str]): Notes/description for the task.
        due (Optional[str]): Due date in RFC 3339 format (e.g., "2024-12-31T23:59:59Z").
        parent (Optional[str]): Parent task ID (for subtasks).
        previous (Optional[str]): Previous sibling task ID (for positioning).

    Returns:
        str: Confirmation message with the new task ID and details.
    """
    logger.info(f"[create_task] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}, Title: '{title}'")

    try:
        body = {
            "title": title
        }
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = due

        params = {"tasklist": task_list_id, "body": body}
        if parent:
            params["parent"] = parent
        if previous:
            params["previous"] = previous

        result = await asyncio.to_thread(
            service.tasks().insert(**params).execute
        )

        response = f"""Task Created for {user_google_email}:
- Title: {result['title']}
- ID: {result['id']}
- Status: {result.get('status', 'N/A')}
- Updated: {result.get('updated', 'N/A')}"""

        if result.get('due'):
            response += f"\n- Due Date: {result['due']}"
        if result.get('notes'):
            response += f"\n- Notes: {result['notes']}"
        if result.get('webViewLink'):
            response += f"\n- Web View Link: {result['webViewLink']}"

        logger.info(f"Created task '{title}' with ID {result['id']} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("update_task", service_type="tasks")
async def update_task(
    service,
    user_google_email: str,
    task_list_id: str,
    task_id: str,
    title: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    due: Optional[str] = None
) -> str:
    """
    Update an existing task.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list containing the task.
        task_id (str): The ID of the task to update.
        title (Optional[str]): New title for the task.
        notes (Optional[str]): New notes/description for the task.
        status (Optional[str]): New status ("needsAction" or "completed").
        due (Optional[str]): New due date in RFC 3339 format.

    Returns:
        str: Confirmation message with updated task details.
    """
    logger.info(f"[update_task] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}, Task ID: {task_id}")

    try:
        # First get the current task to build the update body
        current_task = await asyncio.to_thread(
            service.tasks().get(tasklist=task_list_id, task=task_id).execute
        )

        body = {
            "id": task_id,
            "title": title if title is not None else current_task.get("title", ""),
            "status": status if status is not None else current_task.get("status", "needsAction")
        }

        if notes is not None:
            body["notes"] = notes
        elif current_task.get("notes"):
            body["notes"] = current_task["notes"]

        if due is not None:
            body["due"] = due
        elif current_task.get("due"):
            body["due"] = current_task["due"]

        result = await asyncio.to_thread(
            service.tasks().update(tasklist=task_list_id, task=task_id, body=body).execute
        )

        response = f"""Task Updated for {user_google_email}:
- Title: {result['title']}
- ID: {result['id']}
- Status: {result.get('status', 'N/A')}
- Updated: {result.get('updated', 'N/A')}"""

        if result.get('due'):
            response += f"\n- Due Date: {result['due']}"
        if result.get('notes'):
            response += f"\n- Notes: {result['notes']}"
        if result.get('completed'):
            response += f"\n- Completed: {result['completed']}"

        logger.info(f"Updated task {task_id} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("delete_task", service_type="tasks")
async def delete_task(
    service,
    user_google_email: str,
    task_list_id: str,
    task_id: str
) -> str:
    """
    Delete a task from a task list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list containing the task.
        task_id (str): The ID of the task to delete.

    Returns:
        str: Confirmation message.
    """
    logger.info(f"[delete_task] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}, Task ID: {task_id}")

    try:
        await asyncio.to_thread(
            service.tasks().delete(tasklist=task_list_id, task=task_id).execute
        )

        response = f"Task {task_id} has been deleted from task list {task_list_id} for {user_google_email}."

        logger.info(f"Deleted task {task_id} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("move_task", service_type="tasks")
async def move_task(
    service,
    user_google_email: str,
    task_list_id: str,
    task_id: str,
    parent: Optional[str] = None,
    previous: Optional[str] = None,
    destination_task_list: Optional[str] = None
) -> str:
    """
    Move a task to a different position or parent within the same list, or to a different list.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the current task list containing the task.
        task_id (str): The ID of the task to move.
        parent (Optional[str]): New parent task ID (for making it a subtask).
        previous (Optional[str]): Previous sibling task ID (for positioning).
        destination_task_list (Optional[str]): Destination task list ID (for moving between lists).

    Returns:
        str: Confirmation message with updated task details.
    """
    logger.info(f"[move_task] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}, Task ID: {task_id}")

    try:
        params = {
            "tasklist": task_list_id,
            "task": task_id
        }
        if parent:
            params["parent"] = parent
        if previous:
            params["previous"] = previous
        if destination_task_list:
            params["destinationTasklist"] = destination_task_list

        result = await asyncio.to_thread(
            service.tasks().move(**params).execute
        )

        response = f"""Task Moved for {user_google_email}:
- Title: {result['title']}
- ID: {result['id']}
- Status: {result.get('status', 'N/A')}
- Updated: {result.get('updated', 'N/A')}"""

        if result.get('parent'):
            response += f"\n- Parent Task ID: {result['parent']}"
        if result.get('position'):
            response += f"\n- Position: {result['position']}"

        move_details = []
        if destination_task_list:
            move_details.append(f"moved to task list {destination_task_list}")
        if parent:
            move_details.append(f"made a subtask of {parent}")
        if previous:
            move_details.append(f"positioned after {previous}")

        if move_details:
            response += f"\n- Move Details: {', '.join(move_details)}"

        logger.info(f"Moved task {task_id} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)


@server.tool()
@require_google_service("tasks", "tasks")
@handle_http_errors("clear_completed_tasks", service_type="tasks")
async def clear_completed_tasks(
    service,
    user_google_email: str,
    task_list_id: str
) -> str:
    """
    Clear all completed tasks from a task list. The tasks will be marked as hidden.

    Args:
        user_google_email (str): The user's Google email address. Required.
        task_list_id (str): The ID of the task list to clear completed tasks from.

    Returns:
        str: Confirmation message.
    """
    logger.info(f"[clear_completed_tasks] Invoked. Email: '{user_google_email}', Task List ID: {task_list_id}")

    try:
        await asyncio.to_thread(
            service.tasks().clear(tasklist=task_list_id).execute
        )

        response = f"All completed tasks have been cleared from task list {task_list_id} for {user_google_email}. The tasks are now hidden and won't appear in default task list views."

        logger.info(f"Cleared completed tasks from list {task_list_id} for {user_google_email}")
        return response

    except HttpError as error:
        message = f"API error: {error}. You might need to re-authenticate. LLM: Try 'start_google_auth' with the user's email ({user_google_email}) and service_name='Google Tasks'."
        logger.error(message, exc_info=True)
        raise Exception(message)
    except Exception as e:
        message = f"Unexpected error: {e}."
        logger.exception(message)
        raise Exception(message)