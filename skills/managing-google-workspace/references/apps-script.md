# Google Apps Script Tools Reference

MCP tools for Google Apps Script via the Google Workspace MCP server. All tools require `user_google_email` (string, required) except `generate_trigger_code`.

## Contents
- Projects: list_script_projects, get_script_project, create_script_project, delete_script_project
- File Content: get_script_content, update_script_content
- Execution: run_script_function, generate_trigger_code
- Deployments: manage_deployment, list_deployments
- Versions: list_versions, create_version, get_version
- Monitoring: list_script_processes, get_script_metrics
- Tips

---

## Projects

### list_script_projects
Lists Apps Script projects accessible to the user (via Drive API).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| page_size | integer | no | 50 | |
| page_token | any | no | | Pagination token |

### get_script_project
Retrieves complete project details including all source files.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |

### create_script_project
Creates a new Apps Script project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| title | string | yes | | Project title |
| parent_id | any | no | | Drive folder ID or bound container ID |

### delete_script_project
Permanently deletes an Apps Script project. Cannot be undone.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |

---

## File Content

### get_script_content
Retrieves content of a specific file within a project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |
| file_name | string | yes | | |

### update_script_content
Updates or creates files in a script project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |
| files | array | yes | | List of objects with `name`, `type`, and `source` |

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

### generate_trigger_code
Generates Apps Script code for creating triggers. The API cannot create triggers directly -- they must be created from within Apps Script.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| trigger_type | string | yes | | One of: `time_minutes`, `time_hours`, `time_daily`, `time_weekly`, `on_open`, `on_edit`, `on_form_submit`, `on_change` |
| function_name | string | yes | | Function to run when trigger fires |
| schedule | string | no | "" | Depends on type: minutes (`1`/`5`/`10`/`15`/`30`), hours (`1`/`2`/`4`/`6`/`8`/`12`), daily (`0`-`23`), weekly (`MONDAY`-`SUNDAY`) |

---

## Deployments

### manage_deployment
Creates, updates, or deletes a deployment.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `update`, or `delete` |
| script_id | string | yes | | |
| deployment_id | any | no | | Required for `update` and `delete` |
| description | any | no | | Required for `create` and `update` |
| version_description | any | no | | For `create` only |

### list_deployments
Lists all deployments for a script project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |

---

## Versions

### list_versions
Lists all immutable version snapshots of a script project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |

### create_version
Creates a new immutable version snapshot of the current script code.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |
| description | any | no | | Version description |

### get_version
Gets details of a specific version.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |
| version_number | integer | yes | | |

---

## Monitoring

### list_script_processes
Lists recent execution processes for the user's scripts.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| page_size | integer | no | 50 | |
| script_id | any | no | | Filter by script ID |

### get_script_metrics
Gets execution metrics (active users, total executions, failures) for a script project.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| script_id | string | yes | | |
| metrics_granularity | string | no | "DAILY" | `DAILY` or `WEEKLY` |

---

## Tips

**Triggers**: The Apps Script API cannot create triggers directly. Use `generate_trigger_code` to produce the code, then paste it into the script editor and run it once manually to install the trigger.

**Development mode**: Set `dev_mode: true` in `run_script_function` to execute the latest saved code instead of the last deployed version. This is useful during development and testing.

**Deploy workflow**: `manage_deployment` (create action) creates a version internally, so a separate `create_version` call is not required. Use `create_version` explicitly if you want a named version snapshot independent of deployment.
