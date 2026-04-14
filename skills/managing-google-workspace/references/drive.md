# Google Drive Tools Reference

MCP tools for Google Drive file management, search, content retrieval, and permission control. All tools require `user_google_email` (string, required).

## Contents
- Search & Browse: search_drive_files, list_drive_items
- Content & Download: get_drive_file_content, get_drive_file_download_url
- Create & Modify: create_drive_file, create_drive_folder, copy_drive_file, update_drive_file
- Permissions & Sharing: set_drive_file_permissions, manage_drive_access, get_drive_file_permissions, get_drive_shareable_link, check_drive_file_public_access
- Import: import_to_google_doc
- Tips

---

## Search & Browse

### search_drive_files
Search for files and folders across My Drive and shared drives.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| query | string | yes | | Google Drive search query (see operators below) |
| page_size | integer | no | 10 | Max results to return |
| page_token | any | no | | Pagination token |
| drive_id | string | no | | Shared drive ID to scope search |
| include_items_from_all_drives | boolean | no | true | Include shared drive items when no drive_id set |
| corpora | string | no | | `user`, `domain`, `drive`, or `allDrives`. Defaults to `drive` when drive_id is set. Prefer `user` or `drive` over `allDrives` |
| file_type | string | no | | Friendly name (`folder`, `document`/`doc`, `spreadsheet`/`sheet`, `presentation`/`slides`, `form`, `drawing`, `pdf`, `shortcut`, `script`, `site`, `jam`/`jamboard`) or raw MIME type |
| detailed | boolean | no | true | Include size, modified time, and link |

### list_drive_items
List files and folders in a specific folder.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| folder_id | string | no | root | Folder ID. Use shared drive ID for its root |
| page_size | integer | no | 100 | Max items to return |
| page_token | any | no | | Pagination token |
| drive_id | string | no | | Shared drive ID to scope listing |
| include_items_from_all_drives | boolean | no | true | Include shared drive items when no drive_id set |
| corpora | string | no | | `user`, `drive`, `allDrives` |
| file_type | string | no | | Same friendly names as search_drive_files |
| detailed | boolean | no | true | Include size, modified time, and link |

---

## Content & Download

### get_drive_file_content
Retrieve file content as text.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | Drive file ID |

Content handling:
- Google Docs/Sheets/Slides: exported as text/CSV
- Office files (.docx/.xlsx/.pptx): parsed to extract readable text
- Other files: downloaded, UTF-8 decoded if possible

### get_drive_file_download_url
Download a file to local disk (stdio mode) or get a temporary URL (HTTP mode, valid 1 hour).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | Drive file ID |
| export_format | string | no | | `pdf`, `docx`, `xlsx`, `csv`, `pptx` |

Default export formats for Google native files:
- Docs: PDF (or `docx`)
- Sheets: XLSX (or `pdf`, `csv`)
- Slides: PDF (or `pptx`)

---

## Create & Modify

### create_drive_file
Create a new file in Drive.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_name | string | yes | | Name for the new file |
| content | string | no | | File content |
| folder_id | string | no | root | Parent folder ID |
| mime_type | string | no | text/plain | MIME type of the file |
| fileUrl | string | no | | Fetch content from this URL (file://, http://, https://) |

### create_drive_folder
Create a new folder.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| folder_name | string | yes | | Name for the new folder |
| parent_folder_id | string | no | root | Parent folder ID |

### copy_drive_file
Copy an existing file.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | ID of the file to copy |
| new_name | string | no | | New name (defaults to "Copy of [original]") |
| parent_folder_id | string | no | root | Destination folder ID |

### update_drive_file
Update file metadata and properties.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | File ID to update |
| name | string | no | | New file name |
| description | string | no | | New description |
| mime_type | string | no | | New MIME type (may require content upload) |
| add_parents | string | no | | Comma-separated folder IDs to add as parents |
| remove_parents | string | no | | Comma-separated folder IDs to remove from parents |
| starred | boolean | no | | Star or unstar |
| trashed | boolean | no | | Move to or restore from trash |
| writers_can_share | boolean | no | | Whether editors can share |
| copy_requires_writer_permission | boolean | no | | Prevent viewers from copying/printing/downloading |
| properties | object | no | | Custom key-value properties |

Move a file between folders by setting both `add_parents` and `remove_parents`.

---

## Permissions & Sharing

### set_drive_file_permissions
High-level tool for link sharing and file-level sharing settings.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | File or folder ID |
| link_sharing | string | no | | `off`, `reader`, `commenter`, or `writer` |
| writers_can_share | boolean | no | | Whether editors can change permissions |
| copy_requires_writer_permission | boolean | no | | Prevent viewers from copying/printing/downloading |

### manage_drive_access
Consolidated tool for all permission operations: grant, batch grant, update, revoke, transfer ownership.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | File or folder ID |
| action | string | yes | | `grant`, `grant_batch`, `update`, `revoke`, or `transfer_owner` |
| share_with | string | no | | Email, domain name, or omit for "anyone". Used by `grant` |
| role | string | no | reader (for grant) | `reader`, `commenter`, or `writer` |
| share_type | string | no | user | `user`, `group`, `domain`, or `anyone` |
| permission_id | string | no | | Required for `update` and `revoke` |
| recipients | array | no | | For `grant_batch`: array of `{email, role?, share_type?, expiration_time?}` objects. Use `domain` field instead of `email` for domain shares |
| send_notification | boolean | no | true | Send notification emails |
| email_message | string | no | | Custom notification message |
| expiration_time | string | no | | RFC 3339 format, e.g. `2026-12-31T00:00:00Z` |
| allow_file_discovery | boolean | no | | For domain/anyone shares, whether file appears in search |
| new_owner_email | string | no | | Required for `transfer_owner` |
| move_to_new_owners_root | boolean | no | false | Move file to new owner's My Drive root |

### get_drive_file_permissions
Get detailed file metadata including all sharing permissions.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | File ID |

### get_drive_shareable_link
Get the shareable link and current sharing status.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | File or folder ID |

### check_drive_file_public_access
Search for a file by name and check if it has public link sharing enabled.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_name | string | yes | | File name to search for |

---

## Drive Search Query Operators

The `query` parameter of `search_drive_files` uses Google Drive query syntax (e.g. `name contains`, `mimeType =`, `'id' in parents`, `modifiedTime >`, `trashed =`, `sharedWithMe`). Combine with `and`/`or`/`not`.

---

## File Types

The `file_type` parameter on search/list tools accepts friendly names directly (e.g. `folder`, `document`, `spreadsheet`, `pdf`, `csv`) -- no need to use full MIME type strings.

---

## Import

### import_to_google_doc
Imports a file (Markdown, DOCX, TXT, HTML, RTF, ODT) into Google Docs format with automatic conversion.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_name | string | yes | | Name for the new Google Doc |
| content | any | no | | Text content for MD, TXT, HTML |
| file_path | any | no | | Local file path for DOCX, ODT, etc. Supports `file://` URLs |
| file_url | any | no | | Remote URL to fetch (http/https) |
| source_format | any | no | (auto-detect) | `md`, `markdown`, `docx`, `txt`, `html`, `rtf`, `odt` |
| folder_id | string | no | root | Parent folder ID |

---

## Tips

**Shared drives**: Set `drive_id` to scope operations. When `drive_id` is set, `corpora` defaults to `drive`. For folder operations in shared drives, use a folder ID within that drive (or the drive ID itself for root).

**Pagination**: Both `search_drive_files` and `list_drive_items` return a `next_page_token` when more results exist. Pass it back as `page_token` to get the next page. Search results are incomplete without paginating.

**Moving files**: Use `update_drive_file` with `add_parents` (destination) and `remove_parents` (source) set together.

**Permission workflow**: Use `get_drive_file_permissions` to inspect current permissions (and get permission IDs), then `manage_drive_access` with `action: "update"` or `action: "revoke"` using those IDs.

**Batch sharing**: Use `manage_drive_access` with `action: "grant_batch"` and a `recipients` list to share with multiple people in one call.

**Link sharing shortcut**: Use `set_drive_file_permissions` with `link_sharing` to quickly toggle "anyone with the link" access without dealing with permission IDs.
