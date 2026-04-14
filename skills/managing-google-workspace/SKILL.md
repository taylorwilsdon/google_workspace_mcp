---
name: managing-google-workspace
description: >
  Manages Google Workspace operations across 12 services (Gmail, Drive, Calendar, Docs, Sheets, Slides, Forms, Tasks, Contacts, Chat, Apps Script, Custom Search).
  Supports MCP tools or CLI via uvx workspace-mcp --cli. Provides tool routing, workflows, and parameter guidance for 114 tools.
  Triggers for "check my email", "find a file", "schedule a meeting", "update the spreadsheet", "share a doc",
  "create a presentation", "add a task", "look up a contact", or any mention of Google Workspace services.
allowed-tools: Bash(uvx workspace-mcp *)
user-invocable: false
---

# Google Workspace -- Tool Router

## Execution Mode

Detect which mode is available and use it.

### MCP (preferred)
If MCP tools from the `google-workspace` server are available, call them directly. The tool names in the tables below are the base names -- prefix with the server name as needed (e.g. `google-workspace:search_gmail_messages`).

### CLI (no MCP)
If no MCP tools are available, execute via bash:

```bash
uvx workspace-mcp --cli <tool_name> --args '{"user_google_email": "USER_EMAIL", ...}'
```

Before calling a tool, read the relevant reference file for exact parameter names and types. Only use parameters documented there -- do not invent parameters.

## First-Time Setup

If a tool call fails with a credential error, walk the user through this setup.

### 1. Create OAuth credentials
Direct the user to [Google Cloud Console](https://console.cloud.google.com/apis/credentials):
- Create OAuth 2.0 Client ID (Desktop application type)
- Enable the Google APIs they need (Gmail, Drive, Calendar, etc.)
- Copy the Client ID and Client Secret

### 2. Store credentials
Direct the user to edit `~/.claude/settings.local.json` themselves (this file is gitignored and never shared). Do not ask the user to paste secrets into the conversation. Tell them to add the following to the `env` block:

```json
{
  "env": {
    "GOOGLE_OAUTH_CLIENT_ID": "their-client-id",
    "GOOGLE_OAUTH_CLIENT_SECRET": "their-secret"
  }
}
```

If `settings.local.json` already exists, they should merge into the existing `env` object, not replace it. These env vars are inherited by all MCP servers and CLI processes that Claude Code spawns.

### 3. Authenticate
- **MCP mode**: call `start_google_auth` to open the browser OAuth flow
- **CLI mode**: run any tool -- the first invocation opens the OAuth flow
- Credentials are cached in `~/.google_workspace_mcp/credentials` (or as configured by `WORKSPACE_MCP_CREDENTIALS_DIR`) for future sessions

For server options, transport, auth modes, tool filtering, and deployment: [references/server-options.md](references/server-options.md)

## Universal Patterns

- Consolidated "manage" tools use an `action` parameter for create/update/delete.

## Tool Reference

### Gmail

| Task | Tool |
|------|------|
| Search/find emails | `search_gmail_messages` |
| Read one email | `get_gmail_message_content` |
| Read multiple emails | `get_gmail_messages_content_batch` |
| Read a thread | `get_gmail_thread_content` |
| Read multiple threads | `get_gmail_threads_content_batch` |
| Send email (new or reply) | `send_gmail_message` |
| Create draft | `draft_gmail_message` |
| Download attachment | `get_gmail_attachment_content` |
| Add/remove labels (one) | `modify_gmail_message_labels` |
| Add/remove labels (batch) | `batch_modify_gmail_message_labels` |
| Manage labels | `manage_gmail_label` |
| List labels | `list_gmail_labels` |
| Manage filters | `manage_gmail_filter` |
| List filters | `list_gmail_filters` |

For parameters: [references/gmail.md](references/gmail.md)

### Google Drive

| Task | Tool |
|------|------|
| Search files/folders | `search_drive_files` |
| List items in folder | `list_drive_items` |
| Read file content | `get_drive_file_content` |
| Download file | `get_drive_file_download_url` |
| Create file | `create_drive_file` |
| Create folder | `create_drive_folder` |
| Copy file | `copy_drive_file` |
| Update file metadata | `update_drive_file` |
| Share / set permissions | `set_drive_file_permissions` |
| Manage access (add/remove) | `manage_drive_access` |
| Check permissions | `get_drive_file_permissions` |
| Get shareable link | `get_drive_shareable_link` |
| Check public access | `check_drive_file_public_access` |
| Import file to Google Doc | `import_to_google_doc` |

For parameters: [references/drive.md](references/drive.md)

### Google Calendar

| Task | Tool |
|------|------|
| List calendars | `list_calendars` |
| Get events | `get_events` |
| Create/update/delete event | `manage_event` |
| Check availability | `query_freebusy` |

For parameters: [references/calendar.md](references/calendar.md)

### Google Docs

| Task | Tool |
|------|------|
| Read doc as Markdown | `get_doc_as_markdown` |
| Read doc content (raw) | `get_doc_content` |
| Create new doc | `create_doc` |
| Modify text / apply styles | `modify_doc_text` |
| Insert elements (tables, lists, breaks) | `insert_doc_elements` |
| Insert image | `insert_doc_image` |
| Create table with data | `create_table_with_data` |
| Update paragraph styles | `update_paragraph_style` |
| Find and replace | `find_and_replace_doc` |
| Inspect structure | `inspect_doc_structure` |
| Batch update (multiple ops) | `batch_update_doc` |
| Headers/footers | `update_doc_headers_footers` |
| Manage tabs | `insert_doc_tab` / `update_doc_tab` / `delete_doc_tab` |
| Export to PDF | `export_doc_to_pdf` |
| List docs in folder | `list_docs_in_folder` |
| Search docs | `search_docs` |
| Comments | `manage_document_comment` / `list_document_comments` |
| Debug table structure | `debug_table_structure` |

For parameters: [references/docs.md](references/docs.md)

### Google Sheets

| Task | Tool |
|------|------|
| Read cell values | `read_sheet_values` |
| Write/append/clear values | `modify_sheet_values` |
| Format cells | `format_sheet_range` |
| Conditional formatting | `manage_conditional_formatting` |
| Get spreadsheet info | `get_spreadsheet_info` |
| Create spreadsheet | `create_spreadsheet` |
| Create sheet (tab) | `create_sheet` |
| List spreadsheets | `list_spreadsheets` |
| Comments | `manage_spreadsheet_comment` / `list_spreadsheet_comments` |

For parameters: [references/sheets.md](references/sheets.md)

### Google Slides

| Task | Tool |
|------|------|
| Get presentation | `get_presentation` |
| Get specific slide | `get_page` |
| Get slide thumbnail | `get_page_thumbnail` |
| Create presentation | `create_presentation` |
| Batch update | `batch_update_presentation` |
| Comments | `manage_presentation_comment` / `list_presentation_comments` |

For parameters: [references/slides.md](references/slides.md)

### Google Forms

| Task | Tool |
|------|------|
| Get form | `get_form` |
| Create form | `create_form` |
| Batch update form | `batch_update_form` |
| List responses | `list_form_responses` |
| Get one response | `get_form_response` |
| Publish settings | `set_publish_settings` |

For parameters: [references/forms.md](references/forms.md)

### Google Tasks

| Task | Tool |
|------|------|
| List task lists | `list_task_lists` |
| Get task list | `get_task_list` |
| Manage task list (CRUD) | `manage_task_list` |
| List tasks | `list_tasks` |
| Get task | `get_task` |
| Manage task (CRUD/move) | `manage_task` |

For parameters: [references/tasks.md](references/tasks.md)

### Google Contacts

| Task | Tool |
|------|------|
| Search contacts | `search_contacts` |
| Get contact | `get_contact` |
| Manage contact (CRUD) | `manage_contact` |
| Batch manage contacts | `manage_contacts_batch` |
| List contact groups | `list_contact_groups` |
| Get contact group | `get_contact_group` |
| Manage contact group | `manage_contact_group` |
| List all contacts | `list_contacts` |

For parameters: [references/contacts.md](references/contacts.md)

### Google Chat

| Task | Tool |
|------|------|
| List spaces | `list_spaces` |
| Get messages | `get_messages` |
| Search messages | `search_messages` |
| Send message | `send_message` |
| React to message | `create_reaction` |
| Download attachment | `download_chat_attachment` |

For parameters: [references/chat.md](references/chat.md)

### Google Apps Script

| Task | Tool |
|------|------|
| List projects | `list_script_projects` |
| Get project | `get_script_project` |
| Create project | `create_script_project` |
| Delete project | `delete_script_project` |
| Get file content | `get_script_content` |
| Update file content | `update_script_content` |
| Run function | `run_script_function` |
| Generate trigger code | `generate_trigger_code` |
| Manage deployments | `manage_deployment` / `list_deployments` |
| Versions | `create_version` / `get_version` / `list_versions` |
| Execution metrics | `get_script_metrics` |
| Process history | `list_script_processes` |

For parameters: [references/apps-script.md](references/apps-script.md)

### Google Custom Search

| Task | Tool |
|------|------|
| Web search | `search_custom` |
| Get search engine info | `get_search_engine_info` |

For parameters: [references/search.md](references/search.md)

### Auth

| Task | Tool |
|------|------|
| Start OAuth flow | `start_google_auth` |

Parameters: `user_google_email` (string, optional), `service_name` (string, required -- e.g. `"gmail"`, `"drive"`). Legacy OAuth 2.0 only -- disabled when OAuth 2.1 is enabled. In most cases, just call the tool you need and auth happens automatically.

## Common Workflows

### Reply to an email
1. `search_gmail_messages` -- find the email
2. `get_gmail_message_content` -- read it (get `message_id` and `thread_id`)
3. `send_gmail_message` -- reply using `in_reply_to` and `thread_id`

### Find and share a file
1. `search_drive_files` -- find the file
2. `manage_drive_access` -- share it
3. `get_drive_shareable_link` -- get the link

### Read and update a spreadsheet
1. `get_spreadsheet_info` -- get sheet names
2. `read_sheet_values` -- read current data
3. `modify_sheet_values` -- write updated data
4. `read_sheet_values` -- verify the update

### Create a formatted document
1. `create_doc` -- create the doc
2. `modify_doc_text` -- add text with formatting
3. `insert_doc_elements` -- add tables, lists, page breaks
4. `update_paragraph_style` -- apply heading styles
5. `get_doc_as_markdown` -- verify the result

### Process email attachments
1. `search_gmail_messages` -- find the email
2. `get_gmail_message_content` -- get attachment metadata
3. `get_gmail_attachment_content` -- download the attachment

### Edit a Google Doc
1. `get_doc_as_markdown` -- read current content
2. `inspect_doc_structure` -- find insertion points and indices
3. `modify_doc_text` / `insert_doc_elements` -- make changes
4. `get_doc_as_markdown` -- verify the result

## Tips

- **Check parameters**: In CLI mode, run `uvx workspace-mcp --cli <tool> --help` for any unfamiliar tool.
