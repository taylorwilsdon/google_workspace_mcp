<div align="center">

# Google Workspace MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/workspace-mcp.svg)](https://pypi.org/project/workspace-mcp/)

**Complete Google Workspace control through natural language.** Gmail, Calendar, Drive, Docs, Sheets, Slides, Forms, Tasks, Chat, Apps Script, and Custom Search—all via MCP.

[Quick Start](#-quick-start) • [Tools Reference](#-tools-reference) • [Configuration](#-configuration) • [OAuth Setup](#-oauth-setup)

</div>

---

## ⚡ Quick Start

### One-Click Install (Claude Desktop)

1. Download `google_workspace_mcp.dxt` from [Releases](https://github.com/taylorwilsdon/google_workspace_mcp/releases)
2. Double-click → Claude Desktop installs automatically
3. Add your Google OAuth credentials in Settings → Extensions

### CLI Install

```bash
# Instant run (no install)
uvx workspace-mcp

# With specific tools only
uvx workspace-mcp --tools gmail drive calendar

# With tool tier
uvx workspace-mcp --tool-tier core
```

### Environment Variables

```bash
export GOOGLE_OAUTH_CLIENT_ID="your-client-id"
export GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret"
export OAUTHLIB_INSECURE_TRANSPORT=1  # Development only
```

---

## 🛠 Tools Reference

### Gmail (10 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `search_gmail_messages` | Core | Search with Gmail operators, returns message/thread IDs with web links |
| `get_gmail_message_content` | Core | Get full message: subject, sender, body, attachments |
| `get_gmail_messages_content_batch` | Core | Batch retrieve up to 25 messages |
| `send_gmail_message` | Core | Send emails with HTML support, CC/BCC, threading |
| `get_gmail_thread_content` | Extended | Get complete conversation thread |
| `draft_gmail_message` | Extended | Create drafts with threading support |
| `list_gmail_labels` | Extended | List all system and user labels |
| `manage_gmail_label` | Extended | Create, update, delete labels |
| `modify_gmail_message_labels` | Extended | Add/remove labels (archive, trash, etc.) |
| `manage_gmail_filter` | Extended | Create or delete Gmail filters |
| `get_gmail_threads_content_batch` | Complete | Batch retrieve threads |
| `batch_modify_gmail_message_labels` | Complete | Bulk label operations |

**Also includes:** `get_gmail_attachment_content`, `list_gmail_filters`

### Google Drive (10 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `search_drive_files` | Core | Search files with Drive query syntax or free text |
| `get_drive_file_content` | Core | Read content from Docs, Sheets, Office files (.docx, .xlsx, .pptx) |
| `get_drive_file_download_url` | Core | Download Drive files to local disk |
| `create_drive_file` | Core | Create files from content or URL (supports file://, http://, https://) |
| `create_drive_folder` | Core | Create empty folders in Drive or shared drives |
| `import_to_google_doc` | Core | Import files (MD, DOCX, HTML, etc.) as Google Docs |
| `get_drive_shareable_link` | Core | Get shareable links for a file |
| `list_drive_items` | Extended | List folder contents with shared drive support |
| `copy_drive_file` | Extended | Copy existing files (templates) with optional renaming |
| `update_drive_file` | Extended | Update metadata, move between folders, star, trash |
| `manage_drive_access` | Extended | Grant, update, revoke permissions, and transfer ownership |
| `set_drive_file_permissions` | Extended | Set link sharing and file-level sharing settings |
| `get_drive_file_permissions` | Complete | Get detailed file permissions |
| `check_drive_file_public_access` | Complete | Verify public link sharing for Docs image insertion |

### Google Calendar (3 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `list_calendars` | Core | List all accessible calendars |
| `get_events` | Core | Query events by time range, search, or specific ID |
| `manage_event` | Core | Create, update, or delete calendar events |

**Event features:** Timezone support, transparency (busy/free), visibility settings, up to 5 custom reminders, Google Meet integration, attendees, attachments

### Google Docs (14 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `get_doc_content` | Core | Extract text from Docs or .docx files (supports tabs) |
| `create_doc` | Core | Create new documents with optional initial content |
| `modify_doc_text` | Core | Insert, replace, format text (bold, italic, colors, fonts, links) |
| `search_docs` | Extended | Find documents by name |
| `find_and_replace_doc` | Extended | Global find/replace with case matching |
| `list_docs_in_folder` | Extended | List Docs in a specific folder |
| `insert_doc_elements` | Extended | Add tables, lists, page breaks |
| `update_paragraph_style` | Extended | Apply heading styles, lists (bulleted/numbered with nesting), and paragraph formatting |
| `get_doc_as_markdown` | Extended | Export document as formatted Markdown with optional comments |
| `export_doc_to_pdf` | Extended | Export to PDF and save to Drive |
| `insert_doc_image` | Complete | Insert images from Drive or URLs |
| `update_doc_headers_footers` | Complete | Modify headers/footers |
| `batch_update_doc` | Complete | Execute multiple operations atomically |
| `inspect_doc_structure` | Complete | Analyze document structure for safe insertion points |
| `create_table_with_data` | Complete | Create and populate tables in one operation |
| `debug_table_structure` | Complete | Debug table cell positions and content |
| `list_document_comments` | Complete | List all document comments |
| `manage_document_comment` | Complete | Create, reply to, or resolve comments |

### Google Sheets (9 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `read_sheet_values` | Core | Read cell ranges with formatted output |
| `modify_sheet_values` | Core | Write, update, or clear cell values |
| `create_spreadsheet` | Core | Create new spreadsheets with multiple sheets |
| `list_spreadsheets` | Extended | List accessible spreadsheets |
| `get_spreadsheet_info` | Extended | Get metadata, sheets, conditional formats |
| `format_sheet_range` | Extended | Apply colors, number formats, text wrapping, alignment, bold/italic, font size |
| `create_sheet` | Complete | Add sheets to existing spreadsheets |
| `list_spreadsheet_comments` | Complete | List all spreadsheet comments |
| `manage_spreadsheet_comment` | Complete | Create, reply to, or resolve comments |
| `manage_conditional_formatting` | Complete | Add, update, or delete conditional formatting rules |

### Google Slides (7 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `create_presentation` | Core | Create new presentations |
| `get_presentation` | Core | Get presentation details with slide text extraction |
| `batch_update_presentation` | Extended | Apply multiple updates (create slides, shapes, etc.) |
| `get_page` | Extended | Get specific slide details and elements |
| `get_page_thumbnail` | Extended | Generate PNG thumbnails |
| `list_presentation_comments` | Complete | List all presentation comments |
| `manage_presentation_comment` | Complete | Create, reply to, or resolve comments |

### Google Forms (6 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `create_form` | Core | Create forms with title and description |
| `get_form` | Core | Get form details, questions, and URLs |
| `list_form_responses` | Extended | List responses with pagination |
| `set_publish_settings` | Complete | Configure template and authentication settings |
| `get_form_response` | Complete | Get individual response details |
| `batch_update_form` | Complete | Execute batch updates to forms (questions, items, settings) |

### Google Tasks (5 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `list_tasks` | Core | List tasks with filtering, subtask hierarchy preserved |
| `get_task` | Core | Get task details |
| `manage_task` | Core | Create, update, delete, or move tasks |
| `list_task_lists` | Complete | List all task lists |
| `get_task_list` | Complete | Get task list details |
| `manage_task_list` | Complete | Create, update, delete task lists, or clear completed tasks |

### Google Apps Script (9 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `list_script_projects` | Core | List accessible Apps Script projects |
| `get_script_project` | Core | Get complete project with all files |
| `get_script_content` | Core | Retrieve specific file content |
| `create_script_project` | Core | Create new standalone or bound project |
| `update_script_content` | Core | Update or create script files |
| `run_script_function` | Core | Execute function with parameters |
| `list_deployments` | Extended | List all project deployments |
| `manage_deployment` | Extended | Create, update, or delete script deployments |
| `list_script_processes` | Extended | View recent executions and status |

**Enables:** Cross-app automation, persistent workflows, custom business logic execution, script development and debugging

**Note:** Trigger management is not currently supported via MCP tools.

### Google Contacts (7 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `search_contacts` | Core | Search contacts by name, email, phone |
| `get_contact` | Core | Retrieve detailed contact info |
| `list_contacts` | Core | List contacts with pagination |
| `manage_contact` | Core | Create, update, or delete contacts |
| `list_contact_groups` | Extended | List contact groups/labels |
| `get_contact_group` | Extended | Get group details with members |
| `manage_contacts_batch` | Complete | Batch create, update, or delete contacts |
| `manage_contact_group` | Complete | Create, update, delete groups, or modify membership |

### Google Chat (4 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `get_messages` | Core | Retrieve messages from a space |
| `send_message` | Core | Send messages with optional threading |
| `search_messages` | Core | Search across chat history |
| `list_spaces` | Extended | List rooms and DMs |

### Google Custom Search (2 tools)

| Tool | Tier | Description |
|------|------|-------------|
| `search_custom` | Core | Web search with filters (date, file type, language, safe search, site restrictions via sites parameter) |
| `get_search_engine_info` | Complete | Get search engine metadata |

**Requires:** `GOOGLE_PSE_API_KEY` and `GOOGLE_PSE_ENGINE_ID` environment variables

---

## 📊 Tool Tiers

Choose a tier based on your needs:

| Tier | Tools | Use Case |
|------|-------|----------|
| **Core** | ~30 | Essential operations: search, read, create, send |
| **Extended** | ~50 | Core + management: labels, folders, batch ops |
| **Complete** | 111 | Full API: comments, headers, admin functions |

```bash
uvx workspace-mcp --tool-tier core      # Start minimal
uvx workspace-mcp --tool-tier extended  # Add management
uvx workspace-mcp --tool-tier complete  # Everything
```

Mix tiers with specific services:
```bash
uvx workspace-mcp --tools gmail drive --tool-tier extended
```

---

## ⚙ Configuration

### Required

| Variable | Description |
|----------|-------------|
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client ID from Google Cloud |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth client secret |

### Optional

| Variable | Description |
|----------|-------------|
| `USER_GOOGLE_EMAIL` | Default email for single-user mode |
| `GOOGLE_PSE_API_KEY` | Custom Search API key |
| `GOOGLE_PSE_ENGINE_ID` | Programmable Search Engine ID |
| `MCP_ENABLE_OAUTH21` | Enable OAuth 2.1 multi-user support |
| `WORKSPACE_MCP_STATELESS_MODE` | No file writes (container-friendly) |
| `EXTERNAL_OAUTH21_PROVIDER` | External OAuth flow with bearer tokens |
| `WORKSPACE_MCP_BASE_URI` | Server base URL (default: `http://localhost`) |
| `WORKSPACE_MCP_PORT` | Server port (default: `8000`) |
| `WORKSPACE_EXTERNAL_URL` | External URL for reverse proxy setups |
| `GOOGLE_MCP_CREDENTIALS_DIR` | Custom credentials storage path |

---

## 🔐 OAuth Setup

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project
3. Navigate to **APIs & Services → Credentials**
4. Click **Create Credentials → OAuth Client ID**
5. Select **Desktop Application**
6. Download credentials

### 2. Enable APIs

Click to enable each API:

- [Calendar](https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com)
- [Drive](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com)
- [Gmail](https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com)
- [Docs](https://console.cloud.google.com/flows/enableapi?apiid=docs.googleapis.com)
- [Sheets](https://console.cloud.google.com/flows/enableapi?apiid=sheets.googleapis.com)
- [Slides](https://console.cloud.google.com/flows/enableapi?apiid=slides.googleapis.com)
- [Forms](https://console.cloud.google.com/flows/enableapi?apiid=forms.googleapis.com)
- [Tasks](https://console.cloud.google.com/flows/enableapi?apiid=tasks.googleapis.com)
- [Chat](https://console.cloud.google.com/flows/enableapi?apiid=chat.googleapis.com)
- [Custom Search](https://console.cloud.google.com/flows/enableapi?apiid=customsearch.googleapis.com)

### 3. First Authentication

When you first call a tool:
1. Server returns an authorization URL
2. Open URL in browser, authorize access
3. Paste the authorization code when prompted
4. Credentials are cached for future use

---

## 🚀 Transport Modes

### Stdio (Default)

Best for Claude Desktop and local MCP clients:

```bash
uvx workspace-mcp
```

### HTTP (Streamable)

For web interfaces, debugging, or multi-client setups:

```bash
uvx workspace-mcp --transport streamable-http
```

Access at `http://localhost:8000/mcp/`

### Docker

```bash
docker build -t workspace-mcp .
docker run -p 8000:8000 \
  -e GOOGLE_OAUTH_CLIENT_ID="..." \
  -e GOOGLE_OAUTH_CLIENT_SECRET="..." \
  workspace-mcp --transport streamable-http
```

---

## 🔧 Client Configuration

### Claude Desktop

```json
{
  "mcpServers": {
    "google_workspace": {
      "command": "uvx",
      "args": ["workspace-mcp", "--tool-tier", "core"],
      "env": {
        "GOOGLE_OAUTH_CLIENT_ID": "your-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "your-secret",
        "OAUTHLIB_INSECURE_TRANSPORT": "1"
      }
    }
  }
}
```

### LM Studio

```json
{
  "mcpServers": {
    "google_workspace": {
      "command": "uvx",
      "args": ["workspace-mcp"],
      "env": {
        "GOOGLE_OAUTH_CLIENT_ID": "your-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "your-secret",
        "OAUTHLIB_INSECURE_TRANSPORT": "1",
        "USER_GOOGLE_EMAIL": "you@example.com"
      }
    }
  }
}
```

### VS Code

```json
{
  "servers": {
    "google-workspace": {
      "url": "http://localhost:8000/mcp/",
      "type": "http"
    }
  }
}
```

### Claude Code

```bash
claude mcp add --transport http workspace-mcp http://localhost:8000/mcp
```

---

## 🏗 Architecture

```
google_workspace_mcp/
├── auth/                 # OAuth 2.0/2.1, credential storage, decorators
├── core/                 # MCP server, tool registry, utilities
├── gcalendar/           # Calendar tools
├── gchat/               # Chat tools
├── gdocs/               # Docs tools + managers (tables, headers, batch)
├── gdrive/              # Drive tools + helpers
├── gforms/              # Forms tools
├── gmail/               # Gmail tools
├── gsearch/             # Custom Search tools
├── gsheets/             # Sheets tools + helpers
├── gslides/             # Slides tools
├── gtasks/              # Tasks tools
└── main.py              # Entry point
```

### Key Patterns

**Service Decorator:** All tools use `@require_google_service()` for automatic authentication with 30-minute service caching.

```python
@server.tool()
@require_google_service("gmail", "gmail_read")
async def search_gmail_messages(service, user_google_email: str, query: str):
    # service is injected automatically
    ...
```

**Multi-Service Tools:** Some tools need multiple APIs:

```python
@require_multiple_services([
    {"service_type": "drive", "scopes": "drive_read", "param_name": "drive_service"},
    {"service_type": "docs", "scopes": "docs_read", "param_name": "docs_service"},
])
async def get_doc_content(drive_service, docs_service, ...):
    ...
```

---

## 🧪 Development

```bash
git clone https://github.com/taylorwilsdon/google_workspace_mcp.git
cd google_workspace_mcp

# Install with dev dependencies
uv sync --group dev

# Run locally
uv run main.py

# Run tests
uv run pytest

# Lint
uv run ruff check .
```

---

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

<div align="center">

**[Documentation](https://workspacemcp.com)** • **[Issues](https://github.com/taylorwilsdon/google_workspace_mcp/issues)** • **[PyPI](https://pypi.org/project/workspace-mcp/)**

</div>
