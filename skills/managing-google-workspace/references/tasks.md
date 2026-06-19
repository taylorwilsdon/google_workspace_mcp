# Google Tasks Tools Reference

MCP tools for managing Google Tasks lists and task items. All tools require `user_google_email` (string, required).

---

## Task Lists

### list_task_lists
List all task lists for the user.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| max_results | integer | no | 1000 | Max 1000 |
| page_token | any | no | | Pagination token |

### get_task_list
Get details of a specific task list.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| task_list_id | string | yes | | |
| user_google_email | string | yes | | |

### manage_task_list
Create, update, delete, or clear completed tasks from a task list.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, `delete`, or `clear_completed` |
| user_google_email | string | yes | | |
| task_list_id | string | conditional | | Required for `update`, `delete`, `clear_completed` |
| title | string | conditional | | Required for `create` and `update` |

---

## Tasks

### list_tasks
List tasks in a specific task list.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| task_list_id | string | yes | | |
| user_google_email | string | yes | | |
| max_results | integer | no | 20 | Max 10000 |
| page_token | any | no | | Pagination token |
| show_completed | boolean | no | true | Include completed tasks |
| show_deleted | boolean | no | false | Include deleted tasks |
| show_hidden | boolean | no | false | Include hidden tasks; must be true to see tasks completed in first-party clients |
| show_assigned | boolean | no | false | Include assigned tasks |
| completed_max | string | no | | RFC 3339 timestamp upper bound for completion date |
| completed_min | string | no | | RFC 3339 timestamp lower bound for completion date |
| due_max | string | no | | RFC 3339 timestamp upper bound for due date |
| due_min | string | no | | RFC 3339 timestamp lower bound for due date |
| updated_min | string | no | | RFC 3339 timestamp lower bound for last modification |

### get_task
Get details of a specific task.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| task_list_id | string | yes | | |
| task_id | string | yes | | |
| user_google_email | string | yes | | |

### manage_task
Create, update, delete, or move a task.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, `delete`, or `move` |
| task_list_id | string | yes | | |
| user_google_email | string | yes | | |
| task_id | string | conditional | | Required for `update`, `delete`, `move` |
| title | string | conditional | | Required for `create`; optional for `update` |
| notes | string | no | | Description text (`create`, `update`) |
| status | string | no | | `needsAction` or `completed` (`update`) |
| due | string | no | | RFC 3339 format, e.g. `2026-12-31T23:59:59Z` (`create`, `update`) |
| parent | string | no | | Parent task ID for subtasks (`create`, `move`) |
| previous | string | no | | Previous sibling task ID for positioning (`create`, `move`) |
| destination_task_list | string | no | | Move task to a different list (`move`) |

---

## Tips

**Task list IDs**: Always call `list_task_lists` first to get the `task_list_id` values you need. The default list is usually the first one returned.

**Seeing completed tasks**: Tasks completed in Google's own apps are hidden by default. Set both `show_completed` and `show_hidden` to `true` in `list_tasks` to include them.

**Due dates**: The `due` field uses RFC 3339 format (e.g. `2026-03-20T00:00:00Z`). Google Tasks only stores the date portion, so the time component is ignored.

**Subtasks**: Pass the `parent` parameter when creating or moving a task to nest it under another task as a subtask.
