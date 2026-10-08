# Google Apps Script Tools Reference

MCP tools for Google Apps Script via the Google Workspace MCP server. All tools require `user_google_email` (string, required) except `generate_trigger_code`. Most tools take an `action` argument that selects the operation.

## Contents
- Project/file reads: `get_script_project` (list, get)
- Project mutations: `manage_script_project` (create, delete)
- File updates: `manage_script_content` (update)
- Execution: `run_script_function`, `generate_trigger_code`
- Deployments: `list_script_deployments`; `manage_deployment` (create, update, delete)
- Versions: `get_script_version` (list, get); `manage_script_version` (create)
- Activity: `get_script_activity` (processes, metrics)
- Triggers: `manage_script_trigger` (list, delete)
- Tips

---

## Projects

### get_script_project
List projects, get a project's metadata and file overview, or retrieve one complete source file.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `list` or `get` |
| script_id | string | for get | | |
| file_name | string | no | | Complete source file to return (get only) |
| page_size | integer | no | 50 | list only |
| page_token | any | no | | Pagination token (list only) |

### manage_script_project
Create or permanently delete a project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create` or `delete` |
| script_id | string | for delete | | |
| title | string | for create | | Project title |
| parent_id | any | no | | Drive folder ID or bound container ID (create only) |

## File Content

### manage_script_content
Update source files. The update defaults to merging supplied files by `(name, type)`; set `merge=false` to replace the full project (omitted files are deleted).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `update` |
| script_id | string | yes | | |
| files | array | yes | | Objects with `name`, `type`, and `source` |
| merge | boolean | no | true | `true` overlays updates; `false` replaces the entire project |

---

## Execution

### run_script_function
Executes a function in a deployed script.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |
| function_name | string | yes | | |
| parameters | any | no | | List of parameters to pass |
| dev_mode | boolean | no | false | true = run latest code; false = run deployed version |
| deployment_id | any | no | | API Executable deployment to use; omit to auto-select the highest-versioned one |

### generate_trigger_code
Generates Apps Script code for creating triggers. The API cannot create triggers directly -- the generated setup code must be added to the project and run once.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| trigger_type | string | yes | | One of: `time_minutes`, `time_hours`, `time_daily`, `time_weekly`, `on_open`, `on_edit`, `on_form_submit`, `on_change` |
| function_name | string | yes | | Function to run when trigger fires |
| schedule | string | no | "" | Depends on type: minutes (`1`/`5`/`10`/`15`/`30`), hours (`1`/`2`/`4`/`6`/`8`/`12`), daily (`0`-`23`), weekly (`MONDAY`-`SUNDAY`) |

---

## Deployments

### list_script_deployments
List deployments and their bound version numbers.

Required parameters: `user_google_email`, `script_id`.

### manage_deployment
Create, update, or delete deployments.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `update`, or `delete` |
| script_id | string | yes | | |
| deployment_id | any | no | | Required for `update` and `delete` |
| description | any | no | | Required for `create`; optional for `update` when `version_number` is set |
| version_description | any | no | | For `create` only |
| version_number | integer | no | | Repoint a deployment at a version (`update` only) |

---

## Versions

### get_script_version
List or retrieve immutable version snapshots.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `list` or `get` |
| script_id | string | yes | | |
| version_number | integer | for get | | Version to retrieve |

### manage_script_version
Create an immutable version snapshot.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create` |
| script_id | string | yes | | |
| description | any | no | | Version description (create only) |

---

## Activity

### get_script_activity
Read execution activity: recent processes or aggregate metrics.

- `action="processes"`: without `script_id`, lists the user's own recent runs; with `script_id`, lists all processes for that script visible to the user.
- `action="metrics"`: aggregate execution metrics (active users, total and failed executions) for one script.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `processes` or `metrics` |
| script_id | string | for metrics | | Optional filter for `processes`; required for `metrics` |
| page_size | integer | no | 50 | processes only |
| metrics_granularity | string | no | "DAILY" | `DAILY` or `WEEKLY` (metrics only) |

---

## Triggers

### manage_script_trigger
List or delete the current user's installable triggers on a project. The Apps Script REST API has no triggers resource, so both actions provision a collision-protected helper file and run it through an API Executable deployment; neither action is read-only. Requires the `script.scriptapp` scope.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `list` or `delete` |
| script_id | string | yes | | |
| trigger_id | any | no | | Delete a specific trigger by unique ID (delete only) |
| handler_function | any | no | | Delete every trigger calling this function (delete only) |
| dev_mode | boolean | no | true | Latest saved code; Google restricts development mode to the project owner |
| deployment_id | any | no | | API Executable deployment; omit to auto-select the highest version |

For `delete`, provide `trigger_id` and/or `handler_function` (at least one); when both are given, a trigger must match both to be deleted. Run `action="list"` first to find a trigger's unique ID.

---

## Tips

**Triggers**: The Apps Script API cannot create triggers directly. To create one: generate the setup code with `generate_trigger_code`, add it with `manage_script_content(action="update")`, then run it once with `run_script_function`. To inspect or remove existing triggers, use `manage_script_trigger`.

**Development mode**: Set `dev_mode: true` in `run_script_function` to execute the latest saved code instead of the last deployed version. This is useful during development and testing.

**Deploy workflow**: `manage_deployment(action="create")` creates a version internally, so a separate `manage_script_version(action="create")` call is not required. Create a version explicitly if you want a named snapshot independent of deployment.
