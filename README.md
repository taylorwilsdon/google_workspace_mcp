<div align="center">

# Google Workspace MCP Server <img src="https://github.com/user-attachments/assets/b89524e4-6e6e-49e6-ba77-00d6df0c6e5c" width="80" align="right" />

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/workspace-mcp.svg)](https://pypi.org/project/workspace-mcp/)
[![PyPI Downloads](https://static.pepy.tech/badge/workspace-mcp/month)](https://pepy.tech/projects/workspace-mcp)
[![Website](https://img.shields.io/badge/Website-workspacemcp.com-green.svg)](https://workspacemcp.com)

[![Trust Score](https://archestra.ai/mcp-catalog/api/badge/quality/taylorwilsdon/google_workspace_mcp)](https://archestra.ai/mcp-catalog/taylorwilsdon__google_workspace_mcp)

*Full natural language control over Google Calendar, Drive, Gmail, Docs, Sheets, Slides, Forms, Tasks, and Chat through all MCP clients, AI assistants and developer tools.*

**The most feature-complete Google Workspace MCP server**, now with Remote OAuth2.1 multi-user support and 1-click Claude installation.


###### Support for all free Google accounts (Gmail, Docs, Drive etc) & Google Workspace plans (Starter, Standard, Plus, Enterprise, Non Profit) with expanded app options like Chat & Spaces. <br/> Interested in a private cloud instance? [Get in touch](https://workspacemcp.com/workspace-mcp-cloud)!


</div>

<div align="center">
<a href="https://glama.ai/mcp/servers/@taylorwilsdon/google_workspace_mcp">
  <img width="195" src="https://glama.ai/mcp/servers/@taylorwilsdon/google_workspace_mcp/badge" alt="Google Workspace Server MCP server" align="center"/>
</a>
<a href="https://www.pulsemcp.com/servers/taylorwilsdon-google-workspace">
<img width="456" src="https://github.com/user-attachments/assets/0794ef1a-dc1c-447d-9661-9c704d7acc9d" align="center"/>
</a>
</div>

---


**See it in action:**
<div align="center">
  <video width="832" src="https://github.com/user-attachments/assets/a342ebb4-1319-4060-a974-39d202329710"></video>
</div>

---

### A quick plug for AI-Enhanced Docs
<details>
<summary>But why?</summary>

**This README was written with AI assistance, and here's why that matters**
>
> As a solo dev building open source tools, comprehensive documentation often wouldn't happen without AI help. Using agentic dev tools like **Roo** & **Claude Code** that understand the entire codebase, AI doesn't just regurgitate generic content - it extracts real implementation details and creates accurate, specific documentation.
>
> In this case, Sonnet 4 took a pass & a human (me) verified them 8/9/25.
</details>

## Overview

A production-ready MCP server that integrates all major Google Workspace services with AI assistants. It supports both single-user operation and multi-user authentication via OAuth 2.1, making it a powerful backend for custom applications. Built with FastMCP for optimal performance, featuring advanced authentication handling, service caching, and streamlined development patterns.

**Simplified Setup**: Now uses Google Desktop OAuth clients - no redirect URIs or port configuration needed!

## Features

- **🔐 Advanced OAuth 2.0 & OAuth 2.1**: Secure authentication with automatic token refresh, transport-aware callback handling, session management, centralized scope management, and OAuth 2.1 bearer token support for multi-user environments with innovative CORS proxy architecture
- **📅 Google Calendar**: Full calendar management with event CRUD operations
- **📁 Google Drive**: File operations with native Microsoft Office format support (.docx, .xlsx)
- **📧 Gmail**: Complete email management with search, send, and draft capabilities
- **📄 Google Docs**: Complete document management including content extraction, creation, full editing capabilities, and comment management
- **📊 Google Sheets**: Comprehensive spreadsheet management with flexible cell operations and comment management
- **🖼️ Google Slides**: Presentation management with slide creation, updates, content manipulation, and comment management
- **📝 Google Forms**: Form creation, retrieval, publish settings, and response management
- **✓ Google Tasks**: Complete task and task list management with hierarchy, due dates, and status tracking
- **💬 Google Chat**: Space management and messaging capabilities
- **🔍 Google Custom Search**: Programmable Search Engine (PSE) integration for custom web searches
- **🔄 All Transports**: Stdio, Streamable HTTP & SSE, OpenAPI compatibility via `mcpo`
- **⚡ High Performance**: Service caching, thread-safe sessions, FastMCP integration
- **Developer Friendly**: Minimal boilerplate, automatic service injection, centralized configuration

---

## 🚀 Quick Start

### 1. One-Click Claude Desktop Install (Recommended)

1. **Download:** Grab the latest `google_workspace_mcp.dxt` from the “Releases” page
2. **Install:** Double-click the file – Claude Desktop opens and prompts you to **Install**
3. **Configure:** In Claude Desktop → **Settings → Extensions → Google Workspace MCP**, paste your Google OAuth credentials
4. **Use it:** Start a new Claude chat and call any Google Workspace tool

>
**Why DXT?**
> Desktop Extensions (`.dxt`) bundle the server, dependencies, and manifest so users go from download → working MCP in **one click** – no terminal, no JSON editing, no version conflicts.

#### Required Configuration
<details>
<summary>Environment - you will configure these in Claude itself, see screenshot:</summary>

| Variable | Purpose |
|----------|---------|
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client ID from Google Cloud (used by both legacy auth and OAuth 2.1) |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth client secret (used by both legacy auth and OAuth 2.1) |
| `USER_GOOGLE_EMAIL` *(optional)* | Default email for single-user auth |
| `GOOGLE_PSE_API_KEY` *(optional)* | API key for Google Custom Search - see [Custom Search Setup](#google-custom-search-setup) |
| `GOOGLE_PSE_ENGINE_ID` *(optional)* | Programmable Search Engine ID for Custom Search |
| `MCP_ENABLE_OAUTH21` *(optional)* | Set to `true` to enable OAuth 2.1 support (requires streamable-http transport & will remove user_google_email from tool docstrings) |
| `OAUTHLIB_INSECURE_TRANSPORT=1` | Development only (allows `http://` redirect) |

Claude Desktop stores these securely in the OS keychain; set them once in the extension pane.
</details>

<div align="center">
  <video width="832" src="https://github.com/user-attachments/assets/83cca4b3-5e94-448b-acb3-6e3a27341d3a"></video>
</div>
---

### Prerequisites

- **Python 3.10+**
- **[uvx](https://github.com/astral-sh/uv)** (for instant installation) or [uv](https://github.com/astral-sh/uv) (for development)
- **Google Cloud Project** with OAuth 2.0 credentials

### Configuration

1. **Google Cloud Setup**:
   - Create OAuth 2.0 credentials in [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project (or use an existing one) for your MCP server.
   - Navigate to APIs & Services → Credentials.
   - Click Create Credentials → OAuth Client ID.
   - **Choose Desktop Application as the application type** (simpler setup, no redirect URIs needed!)
   - Download your credentials and note the Client ID and Client Secret

   - **Enable APIs**:
   - In the Google Cloud Console, go to APIs & Services → Library.
   - Search for & enable Calendar, Drive, Gmail, Docs, Sheets, Slides, Forms, Tasks, Chat
   - Expand the section below marked "API Enablement Links" for direct links to each!
<details>
  <summary>API Enablement Links</summary>
  You can enable each one by clicking the links below (make sure you're logged into the Google Cloud Console and have the correct project selected):

* [Enable Google Calendar API](https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com)
* [Enable Google Drive API](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com)
* [Enable Gmail API](https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com)
* [Enable Google Docs API](https://console.cloud.google.com/flows/enableapi?apiid=docs.googleapis.com)
* [Enable Google Sheets API](https://console.cloud.google.com/flows/enableapi?apiid=sheets.googleapis.com)
* [Enable Google Slides API](https://console.cloud.google.com/flows/enableapi?apiid=slides.googleapis.com)
* [Enable Google Forms API](https://console.cloud.google.com/flows/enableapi?apiid=forms.googleapis.com)
* [Enable Google Tasks API](https://console.cloud.google.com/flows/enableapi?apiid=tasks.googleapis.com)
* [Enable Google Chat API](https://console.cloud.google.com/flows/enableapi?apiid=chat.googleapis.com)
* [Enable Google Custom Search API](https://console.cloud.google.com/flows/enableapi?apiid=customsearch.googleapis.com)

</details>

1.1. **Credentials**:
   - Configure credentials using one of these methods:

     **Option A: Environment Variables (Recommended for Production)**
     ```bash
     export GOOGLE_OAUTH_CLIENT_ID="your-client-id.apps.googleusercontent.com"
     export GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret"
     ```

     **Option B: File-based (Traditional)**
     - Download credentials as `client_secret.json` in project root
     - To use a different location, set `GOOGLE_CLIENT_SECRET_PATH` (or legacy `GOOGLE_CLIENT_SECRETS`) environment variable with the file path

     **Option C: .env File (Recommended for Development)**
     - Copy the provided `.env.oauth21` example file to `.env` in the project root:
     ```bash
     cp .env.oauth21 .env
     ```
     - Edit the `.env` file to add your `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`.
     - The server automatically loads variables from this file on startup, simplifying local development.

   **Credential Loading Priority**:
   The server loads credentials in the following order of precedence:
   1. Manually set environment variables (e.g., `export VAR=value`).
   2. Variables defined in a `.env` file in the project root.
   3. `client_secret.json` file specified by `GOOGLE_CLIENT_SECRET_PATH`.
   4. Default `client_secret.json` file in the project root.

   **Why Environment Variables?**
   - ✅ Containerized deployments (Docker, Kubernetes)
   - ✅ Cloud platforms (Heroku, Railway, etc.)
   - ✅ CI/CD pipelines
   - ✅ No secrets in version control
   - ✅ Easy credential rotation

2. **Environment**:
   ```bash
   export OAUTHLIB_INSECURE_TRANSPORT=1  # Development only
   export USER_GOOGLE_EMAIL=your.email@gmail.com  # Optional: Default email for auth - use this for single user setups and you won't need to set your email in system prompt for magic auth
   export GOOGLE_PSE_API_KEY=your-custom-search-api-key  # Optional: Only needed for Google Custom Search tools
   export GOOGLE_PSE_ENGINE_ID=your-search-engine-id  # Optional: Only needed for Google Custom Search tools
   ```

3. **Server Configuration**:
   The server's base URL and port can be customized using environment variables:
   - `WORKSPACE_MCP_BASE_URI`: Sets the base URI for the server (default: http://localhost). Note: do not include a port in `WORKSPACE_MCP_BASE_URI` - set that with the variable below.
   - `WORKSPACE_MCP_PORT`: Sets the port the server listens on (default: 8000).
   - `GOOGLE_OAUTH_REDIRECT_URI`: Override the OAuth redirect URI (useful for reverse proxy setups - see below).
   - `USER_GOOGLE_EMAIL`: Optional default email for authentication flows. If set, the LLM won't need to specify your email when calling `start_google_auth`.

### Google Custom Search Setup

To use the Google Custom Search tools, you need to:

1. **Create a Programmable Search Engine**:
   - Go to [Programmable Search Engine Control Panel](https://programmablesearchengine.google.com/controlpanel/create)
   - Configure sites to search (or search the entire web)
   - Note your Search Engine ID (cx parameter)

2. **Get an API Key**:
   - Visit [Google Developers Console](https://developers.google.com/custom-search/v1/overview)
   - Create or select a project
   - Enable the Custom Search API
   - Create credentials (API Key)
   - Set the `GOOGLE_PSE_API_KEY` environment variable with your API key

3. **Configure Environment Variables**:
   - Set `GOOGLE_PSE_API_KEY` to your Custom Search API key
   - Set `GOOGLE_PSE_ENGINE_ID` to your Search Engine ID (the cx parameter from step 1)

For detailed setup instructions, see the [Custom Search JSON API documentation](https://developers.google.com/custom-search/v1/overview).

### Start the Server

```bash
# Default (stdio mode for MCP clients)
uv run main.py

# HTTP mode (for web interfaces and debugging)
uv run main.py --transport streamable-http

# Single-user mode (simplified authentication)
uv run main.py --single-user

# Selective tool registration (only register specific tools)
uv run main.py --tools gmail drive calendar tasks
uv run main.py --tools sheets docs
uv run main.py --single-user --tools gmail  # Can combine with other flags

# Docker
docker build -t workspace-mcp .
docker run -p 8000:8000 -v $(pwd):/app workspace-mcp --transport streamable-http
```

**Available Tools for `--tools` flag**: `gmail`, `drive`, `calendar`, `docs`, `sheets`, `forms`, `tasks`, `chat`, `search`

### OAuth 2.1 Support (Multi-User Bearer Token Authentication)

The server includes OAuth 2.1 support for bearer token authentication, enabling multi-user session management. **OAuth 2.1 automatically reuses your existing `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` credentials** - no additional configuration needed!

**When to use OAuth 2.1:**
- Multiple users accessing the same MCP server instance
- Need for bearer token authentication instead of passing user emails
- Building web applications or APIs on top of the MCP server
- Production environments requiring secure session management
- Browser-based clients requiring CORS support

**Enabling OAuth 2.1:**
To enable OAuth 2.1, set the `MCP_ENABLE_OAUTH21` environment variable to `true`.

```bash
# OAuth 2.1 requires HTTP transport mode
export MCP_ENABLE_OAUTH21=true
uv run main.py --transport streamable-http
```

If `MCP_ENABLE_OAUTH21` is not set to `true`, the server will use legacy authentication, which is suitable for clients that do not support OAuth 2.1.

<details>
<summary><b>Innovative CORS Proxy Architecture</b></summary>

This implementation solves two critical challenges when using Google OAuth in browser environments:

1.  **Dynamic Client Registration**: Google doesn't support OAuth 2.1 dynamic client registration. Our server provides a clever proxy that accepts any client registration request and returns the pre-configured Google OAuth credentials, allowing standards-compliant clients to work seamlessly.

2.  **CORS Issues**: Google's OAuth endpoints don't include CORS headers, blocking browser-based clients. We implement intelligent proxy endpoints that:
   - Proxy authorization server discovery requests through `/auth/discovery/authorization-server/{server}`
   - Proxy token exchange requests through `/oauth2/token`
   - Add proper CORS headers to all responses
   - Maintain security by only proxying to known Google OAuth endpoints

This architecture enables any OAuth 2.1 compliant client to authenticate users through Google, even from browser environments, without requiring changes to the client implementation.

</details>

**MCP Inspector**: No additional configuration needed with desktop OAuth client.

**Claude Code Inspector**: No additional configuration needed with desktop OAuth client.

### VS Code MCP Client Support
**VS Code mcp.json Configuration Example**:

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

### Connect to Claude Desktop

The server supports two transport modes:

#### Stdio Mode (Default - Recommended for Claude Desktop)

In general, you should use the one-click DXT installer package for Claude Desktop.
If you are unable to for some reason, you can configure it manually via `claude_desktop_config.json`

**Manual Claude Configuration (Alternative)**
1. Open Claude Desktop Settings → Developer → Edit Config
   1. **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   2. **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
2. Add the server configuration:
   ```json
   {
     "mcpServers": {
       "google_workspace": {
         "command": "uvx",
         "args": ["workspace-mcp"],
         "env": {
           "GOOGLE_OAUTH_CLIENT_ID": "your-client-id.apps.googleusercontent.com",
           "GOOGLE_OAUTH_CLIENT_SECRET": "your-client-secret",
           "OAUTHLIB_INSECURE_TRANSPORT": "1"
         }
       }
     }
   }
   ```

### 2. Advanced / Cross-Platform Installation

If you’re developing, deploying to servers, or using another MCP-capable client, keep reading.

#### Instant CLI (uvx)

```bash
# Requires Python 3.10+ and uvx
export GOOGLE_OAUTH_CLIENT_ID="xxx"
export GOOGLE_OAUTH_CLIENT_SECRET="yyy"
uvx workspace-mcp --tools gmail drive calendar
```

> Run instantly without manual installation - you must configure OAuth credentials when using uvx. You can use either environment variables (recommended for production) or set the `GOOGLE_CLIENT_SECRET_PATH` (or legacy `GOOGLE_CLIENT_SECRETS`) environment variable to point to your `client_secret.json` file.

#### Reverse Proxy Setup

If you're running the MCP server behind a reverse proxy (nginx, Apache, Cloudflare, etc.), you'll need to configure `GOOGLE_OAUTH_REDIRECT_URI` to match your external URL:

**Problem**: When behind a reverse proxy, the server constructs redirect URIs using internal ports (e.g., `http://localhost:8000/oauth2callback`) but Google expects the external URL (e.g., `https://your-domain.com/oauth2callback`).

You also have options for:
| `OAUTH_CUSTOM_REDIRECT_URIS` *(optional)* | Comma-separated list of additional redirect URIs |
| `OAUTH_ALLOWED_ORIGINS` *(optional)* | Comma-separated list of additional CORS origins |

**Solution**: Set `GOOGLE_OAUTH_REDIRECT_URI` to your external URL:

```bash
# External URL without port (nginx/Apache handling HTTPS)
export GOOGLE_OAUTH_REDIRECT_URI="https://your-domain.com/oauth2callback"

# Or with custom port if needed
export GOOGLE_OAUTH_REDIRECT_URI="https://your-domain.com:8443/oauth2callback"
```

**Important**:
- The redirect URI must exactly match what's configured in your Google Cloud Console
- The server will use this value for all OAuth flows instead of constructing it from `WORKSPACE_MCP_BASE_URI` and `WORKSPACE_MCP_PORT`
- Your reverse proxy must forward `/oauth2callback` requests to the MCP server

```bash
# Set OAuth credentials via environment variables (recommended)
export GOOGLE_OAUTH_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret"

# Start with specific tools only
uvx workspace-mcp --tools gmail drive calendar tasks

# Start in HTTP mode for debugging
uvx workspace-mcp --transport streamable-http
```

*Requires Python 3.10+ and [uvx](https://github.com/astral-sh/uv). The package is available on [PyPI](https://pypi.org/project/workspace-mcp).*

### Development Installation

For development or customization:

```bash
git clone https://github.com/taylorwilsdon/google_workspace_mcp.git
cd google_workspace_mcp
uv run main.py
```

**Development Installation (For Contributors)**:
```json
{
  "mcpServers": {
    "google_workspace": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/path/to/repo/google_workspace_mcp",
        "main.py"
      ],
      "env": {
        "GOOGLE_OAUTH_CLIENT_ID": "your-client-id.apps.googleusercontent.com",
        "GOOGLE_OAUTH_CLIENT_SECRET": "your-client-secret",
        "OAUTHLIB_INSECURE_TRANSPORT": "1"
      }
    }
  }
}
```

#### HTTP Mode (For debugging or web interfaces)
If you need to use HTTP mode with Claude Desktop:

```json
{
  "mcpServers": {
    "google_workspace": {
      "command": "npx",
      "args": ["mcp-remote", "http://localhost:8000/mcp"]
    }
  }
}
```

*Note: Make sure to start the server with `--transport streamable-http` when using HTTP mode.*

### First-Time Authentication

The server uses **Google Desktop OAuth** for simplified authentication:

- **No redirect URIs needed**: Desktop OAuth clients handle authentication without complex callback URLs
- **Automatic flow**: The server manages the entire OAuth process transparently
- **Transport-agnostic**: Works seamlessly in both stdio and HTTP modes

When calling a tool:
1. Server returns authorization URL
2. Open URL in browser and authorize
3. Google provides an authorization code
4. Paste the code when prompted (or it's handled automatically)
5. Server completes authentication and retries your request

---

## 🧰 Available Tools

> **Note**: All tools support automatic authentication via `@require_google_service()` decorators with 30-minute service caching.

### 📅 Google Calendar ([`calendar_tools.py`](gcalendar/calendar_tools.py))

| Tool | Description |
|------|-------------|
| `list_calendars` | List accessible calendars |
| `get_events` | Retrieve events with time range filtering |
| `get_event` | Fetch detailed information of a single event by ID |
| `create_event` | Create events (all-day or timed) with optional Drive file attachments and custom reminders |
| `modify_event` | Update existing events with intelligent reminder handling |
| `delete_event` | Remove events |

### 📁 Google Drive ([`drive_tools.py`](gdrive/drive_tools.py))

| Tool | Description |
|------|-------------|
| `search_drive_files` | Search files with query syntax |
| `get_drive_file_content` | Read file content (supports Office formats) |
| `list_drive_items` | List folder contents |
| `create_drive_file` | Create new files or fetch content from public URLs |

### 📧 Gmail ([`gmail_tools.py`](gmail/gmail_tools.py))

| Tool | Description |
|------|-------------|
| `search_gmail_messages` | Search with Gmail operators |
| `get_gmail_message_content` | Retrieve message content |
| `send_gmail_message` | Send emails |
| `draft_gmail_message` | Create drafts |

### 📝 Google Docs ([`docs_tools.py`](gdocs/docs_tools.py))

| Tool | Description |
|------|-------------|
| `search_docs` | Find documents by name |
| `get_doc_content` | Extract document text |
| `list_docs_in_folder` | List docs in folder |
| `create_doc` | Create new documents |
| `update_doc_text` | Insert or replace text at specific positions |
| `find_and_replace_doc` | Find and replace text throughout document |
| `format_doc_text` | Apply text formatting (bold, italic, underline, fonts) |
| `insert_doc_elements` | Add tables, lists, or page breaks |
| `insert_doc_image` | Insert images from Drive or URLs |
| `update_doc_headers_footers` | Modify document headers and footers |
| `batch_update_doc` | Execute multiple document operations atomically |
| `read_doc_comments` | Read all comments and replies |
| `create_doc_comment` | Create new comments |
| `reply_to_comment` | Reply to existing comments |
| `resolve_comment` | Resolve comments |

### 📊 Google Sheets ([`sheets_tools.py`](gsheets/sheets_tools.py))

| Tool | Description |
|------|-------------|
| `list_spreadsheets` | List accessible spreadsheets |
| `get_spreadsheet_info` | Get spreadsheet metadata |
| `read_sheet_values` | Read cell ranges |
| `modify_sheet_values` | Write/update/clear cells |
| `create_spreadsheet` | Create new spreadsheets |
| `create_sheet` | Add sheets to existing files |
| `read_sheet_comments` | Read all comments and replies |
| `create_sheet_comment` | Create new comments |
| `reply_to_sheet_comment` | Reply to existing comments |
| `resolve_sheet_comment` | Resolve comments |

### 🖼️ Google Slides ([`slides_tools.py`](gslides/slides_tools.py))

| Tool | Description |
|------|-------------|
| `create_presentation` | Create new presentations |
| `get_presentation` | Retrieve presentation details |
| `batch_update_presentation` | Apply multiple updates at once |
| `get_page` | Get specific slide information |
| `get_page_thumbnail` | Generate slide thumbnails |
| `read_presentation_comments` | Read all comments and replies |
| `create_presentation_comment` | Create new comments |
| `reply_to_presentation_comment` | Reply to existing comments |
| `resolve_presentation_comment` | Resolve comments |

### 📝 Google Forms ([`forms_tools.py`](gforms/forms_tools.py))

| Tool | Description |
|------|-------------|
| `create_form` | Create new forms with title and description |
| `get_form` | Retrieve form details, questions, and URLs |
| `set_publish_settings` | Configure form template and authentication settings |
| `get_form_response` | Get individual form response details |
| `list_form_responses` | List all responses to a form with pagination |

### ✓ Google Tasks ([`tasks_tools.py`](gtasks/tasks_tools.py))

| Tool | Description |
|------|-------------|
| `list_task_lists` | List all task lists with pagination support |
| `get_task_list` | Retrieve details of a specific task list |
| `create_task_list` | Create new task lists with custom titles |
| `update_task_list` | Modify existing task list titles |
| `delete_task_list` | Remove task lists and all contained tasks |
| `list_tasks` | List tasks in a specific list with filtering options |
| `get_task` | Retrieve detailed information about a specific task |
| `create_task` | Create new tasks with title, notes, due dates, and hierarchy |
| `update_task` | Modify task properties including title, notes, status, and due dates |
| `delete_task` | Remove tasks from task lists |
| `move_task` | Reposition tasks within lists or move between lists |
| `clear_completed_tasks` | Hide all completed tasks from a list |

### 💬 Google Chat ([`chat_tools.py`](gchat/chat_tools.py))

| Tool | Description |
|------|-------------|
| `list_spaces` | List chat spaces/rooms |
| `get_messages` | Retrieve space messages |
| `send_message` | Send messages to spaces |
| `search_messages` | Search across chat history |

### 🔍 Google Custom Search ([`search_tools.py`](gsearch/search_tools.py))

| Tool | Description |
|------|-------------|
| `search_custom` | Perform web searches using Programmable Search Engine |
| `get_search_engine_info` | Retrieve search engine metadata and configuration |
| `search_custom_siterestrict` | Search within specific sites/domains |

---

## 🛠️ Development

### Project Structure

```
google_workspace_mcp/
├── auth/              # Authentication system with decorators
├── core/              # MCP server and utilities
├── g{service}/        # Service-specific tools
├── main.py            # Server entry point
├── client_secret.json # OAuth credentials (not committed)
└── pyproject.toml     # Dependencies
```

### Adding New Tools

```python
from auth.service_decorator import require_google_service

@require_google_service("drive", "drive_read")  # Service + scope group
async def your_new_tool(service, param1: str, param2: int = 10):
    """Tool description"""
    # service is automatically injected and cached
    result = service.files().list().execute()
    return result  # Return native Python objects
```

### Architecture Highlights

- **Service Caching**: 30-minute TTL reduces authentication overhead
- **Scope Management**: Centralized in `SCOPE_GROUPS` for easy maintenance
- **Error Handling**: Native exceptions instead of manual error construction
- **Multi-Service Support**: `@require_multiple_services()` for complex tools

---

## 🔒 Security

- **Credentials**: Never commit `.env`, `client_secret.json` or the `.credentials/` directory to source control!
- **OAuth Callback**: Uses `http://localhost:8000/oauth2callback` for development (requires `OAUTHLIB_INSECURE_TRANSPORT=1`)
- **Transport-Aware Callbacks**: Stdio mode starts a minimal HTTP server only for OAuth, ensuring callbacks work in all modes
- **Production**: Use HTTPS & OAuth 2.1 and configure accordingly
- **Network Exposure**: Consider authentication when using `mcpo` over networks
- **Scope Minimization**: Tools request only necessary permissions

---

## 🌐 Integration with Open WebUI

To use this server as a tool provider within Open WebUI:

### Instant Start (No Config Needed)
Just copy and paste the below, set your values and you're off!
```bash
GOOGLE_OAUTH_CLIENT_ID="your_client_id" GOOGLE_OAUTH_CLIENT_SECRET="your_client_secret" uvx mcpo --port 8000 --api-key "top-secret" -- uvx workspace-mcp
```

Otherwise:

### 1. Create MCPO Configuration

Create a file named `config.json` with the following structure to have `mcpo` make the streamable HTTP endpoint available as an OpenAPI spec tool:

```json
{
  "mcpServers": {
    "google_workspace": {
      "type": "streamablehttp",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### 2. Start the MCPO Server

```bash
mcpo --port 8001 --config config.json --api-key "your-optional-secret-key"
```

This command starts the `mcpo` proxy, serving your active (assuming port 8000) Google Workspace MCP on port 8001.

### 3. Configure Open WebUI

1. Navigate to your Open WebUI settings
2. Go to **"Connections"** → **"Tools"**
3. Click **"Add Tool"**
4. Enter the **Server URL**: `http://localhost:8001/google_workspace` (matching the mcpo base URL and server name from config.json)
5. If you used an `--api-key` with mcpo, enter it as the **API Key**
6. Save the configuration

The Google Workspace tools should now be available when interacting with models in Open WebUI.

---

## 📄 License

MIT License - see `LICENSE` file for details.

---

Validations:
[![MCP Badge](https://lobehub.com/badge/mcp/taylorwilsdon-google_workspace_mcp)](https://lobehub.com/mcp/taylorwilsdon-google_workspace_mcp)

[![Verified on MseeP](https://mseep.ai/badge.svg)](https://mseep.ai/app/eebbc4a6-0f8c-41b2-ace8-038e5516dba0)


<div align="center">
<img width="810" alt="Gmail Integration" src="https://github.com/user-attachments/assets/656cea40-1f66-40c1-b94c-5a2c900c969d" />
<img width="810" alt="Calendar Management" src="https://github.com/user-attachments/assets/d3c2a834-fcca-4dc5-8990-6d6dc1d96048" />
<img width="842" alt="Batch Emails" src="https://github.com/user-attachments/assets/0876c789-7bcc-4414-a144-6c3f0aaffc06" />
</div>
