# Google Drive Tools Reference

MCP tools for Google Drive file management, search, content retrieval, and permission control. All tools require `user_google_email` (string, required).

## Contents
- Search & Browse: search_drive_files, list_drive_items
- Content & Download: get_drive_file_content, get_drive_file_download_url
- Create & Modify: create_drive_file, create_drive_folder, copy_drive_file, update_drive_file
- Permissions & Sharing: set_drive_file_permissions, manage_drive_access, get_drive_file_permissions, get_drive_shareable_link, check_drive_file_public_access
- Import: import_to_google_doc, import_to_google_sheets, import_to_google_slides
- Tips

---

## Search & Browse

### search_drive_files
Search for files and folders across My Drive and shared drives.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| query | string | yes | | Google Drive search query (see operators below). **WARNING:** Owner-based queries (`'user@example.com' in owners`) do NOT work in Shared Drives - see Shared Drives Limitations below |
| page_size | integer | no | 10 | Max results to return |
| page_token | any | no | | Pagination token |
| drive_id | string | no | | Shared drive ID to scope search |
| include_items_from_all_drives | boolean | no | true | Include shared drive items when no drive_id set |
| corpora | string | no | | `user`, `domain`, `drive`, or `allDrives`. Defaults to `drive` when drive_id is set. Prefer `user` or `drive` over `allDrives` |
| file_type | string | no | | Friendly name (`folder`, `document`/`doc`, `spreadsheet`/`sheet`, `presentation`/`slides`, `form`, `drawing`, `pdf`, `shortcut`, `script`, `site`, `jam`/`jamboard`) or raw MIME type |
| detailed | boolean | no | true | Include size, modified time, and link |
| order_by | string | no | | Sort order (see Sort Order below) |
| include_trashed | boolean | no | false | Include files in the trash. A `trashed` clause (`=` or `!=`) written into `query` always wins over this flag |

### list_drive_items
List files and folders in a specific folder, shared drive containers with `resource_type="shared_drives"`, or a file's revision history with `resource_type="revisions"` and `file_id`.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| folder_id | string | no | root | Folder ID. Use shared drive ID for its root |
| page_size | integer | no | 100 | Max items to return; 1-1000 for revisions |
| page_token | any | no | | Pagination token; use `next_page_token` from revision responses |
| drive_id | string | no | | Shared drive ID to scope listing |
| include_items_from_all_drives | boolean | no | true | Include shared drive items when no drive_id set |
| corpora | string | no | | `user`, `drive`, `allDrives` |
| file_type | string | no | | Same friendly names as search_drive_files |
| detailed | boolean | no | true | Include size, modified time, and link |
| order_by | string | no | | Sort order (see Sort Order below) |
| resource_type | string | no | items | `items` (folder contents), `shared_drives` (shared drive containers), or `revisions` (a file's history) |
| query | string | no | | Shared drive query, e.g. `name contains 'Engineering'`. Only with `resource_type="shared_drives"` |
| include_organizers | boolean | no | false | Include organizer principals per shared drive (one extra API call each). Only with `resource_type="shared_drives"` |
| file_id | string | with revisions | | Drive file ID or shortcut; only with `resource_type="revisions"` |

#### Revision history

Use `list_drive_items(resource_type="revisions", file_id=...)` to retrieve one page of Drive-visible history. This mode returns a JSON string with `file`, `revisions`, `next_page_token`, and `history_scope`. Folder, search, sorting, shared-drive and `detailed` options do not apply. Drive can compact or omit older revisions, especially for frequently edited Google Workspace files, so this is not guaranteed to be a complete audit log.

Each revision includes its ID, modified time, last modifying user, MIME/size metadata, keep-forever status where applicable, and a `download_available` flag. Pass its `id` to `get_drive_file_content(revision_id=...)` to read the historical text, or to `get_drive_file_download_url(revision_id=...)` to download that version. No new MCP tool is required.

---

## Content & Download

### get_drive_file_content
Retrieve file content as text.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | Drive file ID |
| offset | integer | no | 0 | Character offset to start from, to read on after a cut-off result |
| full_content | boolean | no | false | Return everything from `offset` onward in one call instead of a page |
| revision_id | string | no | | Nonblank historical revision ID returned by `list_drive_items(resource_type="revisions", file_id=...)`; supports the same paging options |

Content handling:
- Google Docs/Sheets/Slides: exported as text/CSV
- Office files (.docx/.xlsx/.pptx): parsed to extract readable text
- PDFs: text extracted when possible; scanned PDFs return a download hint
- Images (PNG, JPEG, GIF, WebP): metadata header as text, plus the image itself as MCP image content
- SVG: returned as its markup (text). Each PNG, JPEG, GIF or WebP image embedded as a base64 data URI is attached as image content, and its place in the markup reads `[embedded image N, … attached as image content]`. Other embedded payloads, such as fonts, are omitted, with a note giving their size
- BMP, TIFF: returned as base64 text after the header, or a pointer to `get_drive_file_download_url` when that text would be over the default page size below and `full_content` is not set
- Other files: downloaded, UTF-8 decoded if possible

By default, long content comes back a page at a time: at most 40,000 characters per call (the server sets this with `WORKSPACE_MCP_MAX_CONTENT_CHARS`; `0` returns whole content by default). A page ends at a line break where possible, with a note giving the range shown, how much follows, and the `offset` to call again with. To get the whole file in one call instead, pass `full_content=true`. Current-file headers name the file's version; if it changes between pages, start again from `offset=0`. When `revision_id` is supplied, keep that ID on subsequent page requests. Historical headers identify the selected revision and its modification time rather than the current file's version.

### get_drive_file_download_url
Download a file to local disk (stdio mode) or get a temporary URL (HTTP mode, valid 1 hour).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_id | string | yes | | Drive file ID |
| export_format | string | no | | `pdf`, `docx`, `xlsx`, `csv`, `pptx`, `png`, `jpg`, `svg` |
| revision_id | string | no | | Nonblank historical revision ID returned by `list_drive_items(resource_type="revisions", file_id=...)` |

Default export formats for Google native files:
- Docs: PDF (or `docx`)
- Sheets: XLSX (or `pdf`, `csv`)
- Slides: PDF (or `pptx`)
- Drawings: PNG (or `pdf`, `jpg`, `svg`)

When `revision_id` is set, the tool retrieves that historical Drive revision. Export selection follows the selected revision's type even if the current file has changed format. Native Docs, Sheets, and Slides revisions are exported in the requested format; historical Drawing exports are not supported. For uploaded/blob files, older content is only downloadable when Drive still retains it, normally because the revision is the current head or has `keepForever=true`.

---

## Create & Modify

### create_drive_file
Create a new file in Drive without converting it to a native Google format. For
Office-to-Google conversion, use `import_to_google_doc`,
`import_to_google_sheets`, or `import_to_google_slides`.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_name | string | yes | | Name for the new file |
| content | string | no | | File content |
| folder_id | string | no | root | Parent folder ID |
| mime_type | string | no | text/plain | MIME type of the file |
| fileUrl | string | no | | Fetch content from this URL (file://, http://, https://) |
| base64_content | string | no | | Standard base64-encoded binary content |
| content_mime_type | string | with base64_content | | Source MIME type; Google-native MIME types are rejected |
| base64_sha256 | string | no | | Optional SHA-256 integrity check for decoded binary content |
| return_upload_url | boolean | no | false | Remote mode only (local file access off): return a resumable-upload URL to PUT the bytes to instead of passing a source |

The import tools also accept `base64_content` and optional `base64_sha256` for
binary Office/OpenDocument sources. ZIP-based formats are checked for required
members and CRC/decompression errors before Drive is called.

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
| copy_comments | boolean | no | false | Copy open comments and suggestions (Docs/Sheets/Slides only) |

### update_drive_file
Update file metadata and properties, and/or replace or extend file content in place (keeps the file ID, sharing, comments, and links).

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
| content | string | no | | New text content (markdown, TXT, HTML). Required for `append`/`prepend` |
| file_path | string | no | | Local file path or `file://` URL for new content (DOCX, ODT, etc.). Local file access only |
| return_upload_url | boolean | no | false | Remote mode only (local file access off): return a resumable-upload URL to PUT the bytes to instead of passing a source |
| file_url | string | no | | Remote http(s) URL to fetch new content from |
| source_format | string | no | (auto-detect) | Conversion hint for native Google files, from the matching import tool's formats. Ignored for non-Google files |
| mode | string | no | replace | `replace`, `append`, or `prepend`. Append/prepend need `content` and a UTF-8 text file (.md, .txt) |

Provide at most one content source (`content`, `file_path`, `file_url`). Native Docs/Sheets/Slides are re-converted from the source format; other files are written back as-is. For native Google Docs prefer `modify_doc_text`, `insert_doc_elements`, or `find_and_replace_doc` over a full rewrite. On a shortcut, metadata changes apply to the shortcut and content follows to the target, so do not combine both in one call. Where local file access is disabled, `file_path` is replaced by `return_upload_url` (returns a resumable-upload URL to PUT bytes to; `replace` mode only).

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
| drive_id | string | no | | Shared drive ID to scope the search. Needed to reliably find files that live only in that shared drive |

---

## Drive Search Query Operators

The `query` parameter of `search_drive_files` uses Google Drive query syntax (e.g. `name contains`, `mimeType =`, `'id' in parents`, `modifiedTime >`, `trashed =`, `sharedWithMe`). Combine with `and`/`or`/`not`.

**Trash:** both `search_drive_files` and `list_drive_items` exclude trashed files by default. To see trashed files, either pass `include_trashed=true` to `search_drive_files` or write an explicit `trashed = true` clause into `query`. An explicit clause always wins over the flag.

---

## Sort Order

The `order_by` parameter controls result ordering for `search_drive_files` and `list_drive_items`.

**Valid sort keys:**
- `createdTime` - When the file was created
- `folder` - Folders first, then files
- `modifiedByMeTime` - Last time the requesting user modified the file
- `modifiedTime` - Last time anyone modified the file
- `name` - File name (case-sensitive)
- `name_natural` - File name (natural sort order)
- `quotaBytesUsed` - Storage space used
- `recency` - Recently used by the user
- `sharedWithMeTime` - When the file was shared with the user
- `starred` - Starred files first
- `viewedByMeTime` - Last time the user viewed the file

**Modifiers:**
- Add `desc` after a key to sort in descending order (default is ascending)
- Combine multiple keys with commas: `folder,modifiedTime desc,name`

**Examples:**
- `modifiedTime desc` - Most recently modified files first
- `folder,name` - Folders first, then by name within each group
- `starred desc,modifiedTime desc` - Starred files first, then by modified time

**Limitation:** For users with approximately one million files, the requested sort order may be ignored.

---

## Shared Drives Limitations

**IMPORTANT:** Files in Shared Drives have different ownership models than My Drive files:

**Ownership:**
- Files in Shared Drives are owned by the **shared drive itself**, not individual users
- The `owners` and `ownerNames` fields are **NOT populated** for Shared Drive files
- The `ownedByMe` field is always false

**Query Impact:**
- Owner-based queries **DO NOT WORK** in Shared Drives:
  - ❌ `'user@example.com' in owners` - Will not return expected results
  - ❌ `ownedByMe=true` - Will not find files in Shared Drives
- To find recent files in Shared Drives, use time-based queries instead:
  - ✅ `modifiedTime > '2026-01-01T00:00:00'` with `order_by='modifiedTime desc'`
  - ✅ Search by name, type, or content with `order_by='modifiedTime desc'`

**Other field limitations in Shared Drives:**
- `permissions` - Not returned directly; use `get_drive_file_permissions` instead
- `shared` - All items are automatically shared (always true)
- `folderColorRgb` - Individual folder coloring not supported
- `writersCanShare` - Cannot restrict sharing by role

**Workaround for finding a user's recent activity:**
Instead of searching by owner, use:
1. `order_by='modifiedTime desc'` to get most recent files first
2. Filter by `modifiedTime > 'YYYY-MM-DDTHH:MM:SS'` to limit to recent files
3. Manually filter results by checking file activity (if needed)

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
| file_path | any | no | | Local file path for DOCX, ODT, etc. Supports `file://` URLs. Local file access only |
| return_upload_url | boolean | no | false | Remote mode only (local file access off): return a resumable-upload URL to PUT the bytes to instead of passing a source |
| file_url | any | no | | Remote URL to fetch (http/https) |
| source_format | any | no | (auto-detect) | `md`, `markdown`, `docx`, `txt`, `html`, `rtf`, `odt` |
| folder_id | string | no | root | Parent folder ID |
| base64_content | string | no | | Standard base64-encoded bytes for a binary source (DOCX, ODT) |
| base64_sha256 | string | no | | Expected SHA-256 of decoded `base64_content` for integrity checks |

Either provide exactly one source (`content`, `file_path`, `file_url`, or `base64_content`), or set `return_upload_url=true` with `source_format` and no source. Without `source_format`, the `file_name` extension sets the format (e.g. `Notes.md` so Markdown tables convert); the extension is dropped from the title. `return_upload_url` is offered only where local file access is disabled (it replaces `file_path`) and returns a resumable-upload URL to PUT the bytes to.

### import_to_google_sheets
Imports a spreadsheet (XLSX, XLS, ODS, CSV, TSV) into Google Sheets format with automatic conversion.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_name | string | yes | | Name for the new spreadsheet; extension sets the format when `source_format` is omitted |
| content | string | no | | Text content for CSV or TSV |
| file_path | string | no | | Local file path for any supported format. Supports `file://` URLs. Local file access only |
| return_upload_url | boolean | no | false | Remote mode only (local file access off): return a resumable-upload URL to PUT the bytes to instead of passing a source |
| file_url | string | no | | Remote URL to fetch (http/https) |
| source_format | string | no | (auto-detect) | `xlsx`, `xls`, `ods`, `csv`, `tsv` |
| folder_id | string | no | root | Parent folder ID |
| base64_content | string | no | | Standard base64-encoded bytes for XLSX, XLS, or ODS |
| base64_sha256 | string | no | | Expected SHA-256 of decoded `base64_content` for integrity checks |

Either provide exactly one source (`content`, `file_path`, `file_url`, or `base64_content`), or set `return_upload_url=true` with `source_format` and no source. Other rules match `import_to_google_doc`.

### import_to_google_slides
Imports a presentation (PPTX, PPT, ODP) into Google Slides format with automatic conversion.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| file_name | string | yes | | Name for the new presentation; extension sets the format when `source_format` is omitted |
| file_path | string | no | | Local file path for any supported format. Supports `file://` URLs. Local file access only |
| return_upload_url | boolean | no | false | Remote mode only (local file access off): return a resumable-upload URL to PUT the bytes to instead of passing a source |
| file_url | string | no | | Remote URL to fetch (http/https) |
| source_format | string | no | (auto-detect) | `pptx`, `ppt`, `odp` |
| folder_id | string | no | root | Parent folder ID |
| base64_content | string | no | | Standard base64-encoded bytes for PPTX or ODP |
| base64_sha256 | string | no | | Expected SHA-256 of decoded `base64_content` for integrity checks |

No inline text `content`: presentations are binary. Either provide exactly one source (`file_path`, `file_url`, or `base64_content`), or set `return_upload_url=true` with `source_format` and no source. Other rules match `import_to_google_doc`.

---

## Tips

**Shared drives**: Set `drive_id` to scope operations. When `drive_id` is set, `corpora` defaults to `drive`. For folder operations in shared drives, use a folder ID within that drive (or the drive ID itself for root).

**Pagination**: Both `search_drive_files` and `list_drive_items` return a `next_page_token` when more results exist. Pass it back as `page_token` to get the next page. Search results are incomplete without paginating.

**Moving files**: Use `update_drive_file` with `add_parents` (destination) and `remove_parents` (source) set together.

**Snapshot before destructive Doc edits**: Native Google Docs revisions cannot be pinned or named through the API (`keepForever` only applies to binary files), so take a sibling copy before a large rewrite: `copy_drive_file(file_id=<doc id>, new_name="<title>.snapshot.<YYYYMMDDTHHMMSSZ>", parent_folder_id=<doc's parent>)`, with the parent ID from `get_drive_file_permissions` (the default is My Drive root) and a compact UTC timestamp (no `:`) so the copy sorts next to the source. Drive allows duplicate names, so before retrying a failed snapshot, search for it (`search_drive_files` with `name = '<snapshot name>' and '<parent id>' in parents`, escaping `\` as `\\` and `'` as `\'` in the name) instead of copying again. The copy keeps content and formatting, plus open comments with `copy_comments=True`, but not resolved comments, version history or the original's direct sharing. Roll back by restoring the snapshot's content into the original, which keeps its ID, sharing and links; switching to the snapshot instead means re-sharing it and updating every link to the old ID.

**Permission workflow**: Use `get_drive_file_permissions` to inspect current permissions (and get permission IDs), then `manage_drive_access` with `action: "update"` or `action: "revoke"` using those IDs.

**Batch sharing**: Use `manage_drive_access` with `action: "grant_batch"` and a `recipients` list to share with multiple people in one call.

**Link sharing shortcut**: Use `set_drive_file_permissions` with `link_sharing` to quickly toggle "anyone with the link" access without dealing with permission IDs.
