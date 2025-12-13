<div align="center">

# <span style="color:#cad8d9">Google Workspace MCP Server</span> <img src="https://github.com/user-attachments/assets/b89524e4-6e6e-49e6-ba77-00d6df0c6e5c" width="80" align="right" />

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/workspace-mcp.svg)](https://pypi.org/project/workspace-mcp/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/workspace-mcp?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=BLUE&left_text=pip%20downloads)](https://pepy.tech/projects/workspace-mcp)
[![Website](https://img.shields.io/badge/Website-workspacemcp.com-green.svg)](https://workspacemcp.com)

*Full natural language control over Google Calendar, Drive, Gmail, Docs, Sheets, Slides, Forms, Tasks, and Chat through all MCP clients, AI assistants and developer tools.*

**The most feature-complete Google Workspace MCP server**, now with Remote OAuth2.1 multi-user support and 1-click Claude installation.


###### Support for all free Google accounts (Gmail, Docs, Drive etc) & Google Workspace plans (Starter, Standard, Plus, Enterprise, Non Profit) with expanded app options like Chat & Spaces. <br/> Interested in a private cloud instance? [That can be arranged.](https://workspacemcp.com/workspace-mcp-cloud)


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
  <video width="400" src="https://github.com/user-attachments/assets/a342ebb4-1319-4060-a974-39d202329710"></video>
</div>

---

### A quick plug for AI-Enhanced Docs
<details>
<summary>◆ <b>But why?</b></summary>

**This README was written with AI assistance, and here's why that matters**
>
> As a solo dev building open source tools, comprehensive documentation often wouldn't happen without AI help. Using agentic dev tools like **Roo** & **Claude Code** that understand the entire codebase, AI doesn't just regurgitate generic content - it extracts real implementation details and creates accurate, specific documentation.
>
> In this case, Sonnet 4 took a pass & a human (me) verified them 8/16/25.
</details>

## <span style="color:#adbcbc">Overview</span>

A production-ready MCP server that integrates all major Google Workspace services with AI assistants. It supports both single-user operation and multi-user authentication via OAuth 2.1, making it a powerful backend for custom applications. Built with FastMCP for optimal performance, featuring advanced authentication handling, service caching, and streamlined development patterns.

**Simplified Setup**: Now uses Google Desktop OAuth clients - no redirect URIs or port configuration needed!

## <span style="color:#adbcbc">Features</span>

<table align="center" style="width: 100%; max-width: 100%;">
<tr>
<td width="50%" valign="top">

**<span style="color:#72898f">@</span> Gmail** • **<span style="color:#72898f">≡</span> Drive** • **<span style="color:#72898f">⧖</span> Calendar** **<span style="color:#72898f">≡</span> Docs**
- Complete Gmail management, end to end coverage
- Full calendar management with advanced features
- File operations with Office format support
- Document creation, editing & comments
- Deep, exhaustive support for fine grained editing

---

**<span style="color:#72898f">≡</span> Forms** • **<span style="color:#72898f">@</span> Chat** • **<span style="color:#72898f">≡</span> Sheets** • **<span style="color:#72898f">≡</span> Slides**
- Form creation, publish settings & response management
- Space management & messaging capabilities
- Spreadsheet operations with flexible cell management
- Presentation creation, updates & content manipulation

</td>
<td width="50%" valign="top">

**<span style="color:#72898f">⊠</span> Authentication & Security**
- Advanced OAuth 2.0 & OAuth 2.1 support
- Automatic token refresh & session management
- Transport-aware callback handling
- Multi-user bearer token authentication
- Innovative CORS proxy architecture

---

**<span style="color:#72898f">✓</span> Tasks** • **<span style="color:#72898f">◆</span> Custom Search** • **<span style="color:#72898f">↻</span> Transport Support**
- Full support for all MCP Transports
- Task & task list management with hierarchy
- Programmable Search Engine (PSE) integration

</td>
</tr>
</table>

---

## ▶ Quick Start

<details>
<summary>≡ <b>Quick Reference Card</b>← Essential commands & configs at a glance</summary>

<table>
<tr><td width="33%" valign="top">

**⊠ Credentials**
```bash
export GOOGLE_OAUTH_CLIENT_ID="..."
export GOOGLE_OAUTH_CLIENT_SECRET="..."
```
[Full setup →](#-credential-configuration)

</td><td width="33%" valign="top">

**▶ Launch Commands**
```bash
uvx workspace-mcp --tool-tier core
uv run main.py --tools gmail drive
```
[More options →](#start-the-server)

</td><td width="34%" valign="top">

**★ Tool Tiers**
- ● `core` - Essential tools
- ◐ `extended` - Core + extras
- ○ `complete` - Everything
[Details →](#tool-tiers)

</td></tr>
</table>

</details>

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
<summary>◆ <b>Environment Variables</b> <sub><sup>← Click to configure in Claude Desktop</sup></sub></summary>

<table>
<tr><td width="50%" valign="top">

**Required**
| Variable | Purpose |
|----------|---------|
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth client ID from Google Cloud |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth client secret |
| `OAUTHLIB_INSECURE_TRANSPORT=1` | Development only (allows `http://` redirect) |

</td><td width="50%" valign="top">

**Optional**
| Variable | Purpose |
|----------|---------|
| `USER_GOOGLE_EMAIL` | Default email for single-user auth |
| `GOOGLE_PSE_API_KEY` | API key for Custom Search |
| `GOOGLE_PSE_ENGINE_ID` | Search Engine ID for Custom Search |
| `MCP_ENABLE_OAUTH21` | Set to `true` for OAuth 2.1 support |
| `EXTERNAL_OAUTH21_PROVIDER` | Set to `true` for external OAuth flow with bearer tokens (requires OAuth 2.1) |
| `WORKSPACE_MCP_STATELESS_MODE` | Set to `true` for stateless operation (requires OAuth 2.1) |

</td></tr>
</table>

Claude Desktop stores these securely in the OS keychain; set them once in the extension pane.
</details>

---

<div align="center">
  <video width="832" src="https://github.com/user-attachments/assets/83cca4b3-5e94-448b-acb3-6e3a27341d3a"></video>
</div>

---

### Prerequisites

- **Python 3.10+**
- **[uvx](https://github.com/astral-sh/uv)** (for instant installation) or [uv](https://github.com/astral-sh/uv) (for development)
- **Google Cloud Project** with OAuth 2.0 credentials

### Configuration

<details open>
<summary>◆ <b>Google Cloud Setup</b> <sub><sup>← OAuth 2.0 credentials & API enablement</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**1. Create Project**
```text
console.cloud.google.com

→ Create new project
→ Note project name
```
<sub>[Open Console →](https://console.cloud.google.com/)</sub>

</td>
<td width="33%" align="center">

**2. OAuth Credentials**
```text
APIs & Services → Credentials
→ Create Credentials
→ OAuth Client ID
→ Desktop Application
```
<sub>Download & save credentials</sub>

</td>
<td width="34%" align="center">

**3. Enable APIs**
```text
APIs & Services → Library

Search & enable:
Calendar, Drive, Gmail,
Docs, Sheets, Slides,
Forms, Tasks, Chat, Search
```
<sub>See quick links below</sub>

</td>
</tr>
<tr>
<td colspan="3">

<details>
<summary>≡ <b>OAuth Credential Setup Guide</b> <sub><sup>← Step-by-step instructions</sup></sub></summary>

**Complete Setup Process:**

1. **Create OAuth 2.0 Credentials** - Visit [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project (or use existing)
   - Navigate to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth Client ID**
   - Choose **Desktop Application** as the application type (no redirect URIs needed!)
   - Download credentials and note the Client ID and Client Secret

2. **Enable Required APIs** - In **APIs & Services → Library**
   - Search for and enable each required API
   - Or use the quick links below for one-click enabling

3. **Configure Environment** - Set your credentials:
   ```bash
   export GOOGLE_OAUTH_CLIENT_ID="your-client-id"
   export GOOGLE_OAUTH_CLIENT_SECRET="your-secret"
   ```

≡ [Full Documentation →](https://developers.google.com/workspace/guides/auth-overview)

</details>

</td>
</tr>
</table>

<details>
  <summary>⊥ <b>Quick API Enable Links</b> <sub><sup>← One-click enable each Google API</sup></sub></summary>
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

</details>

1.1. **Credentials**: See [Credential Configuration](#credential-configuration) for detailed setup options

2. **Environment Configuration**:

<details open>
<summary>◆ <b>Environment Variables</b> <sub><sup>← Configure your runtime environment</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**◆ Development Mode**
```bash
export OAUTHLIB_INSECURE_TRANSPORT=1
```
<sub>Allows HTTP redirect URIs</sub>

</td>
<td width="33%" align="center">

**@ Default User**
```bash
export USER_GOOGLE_EMAIL=\
  your.email@gmail.com
```
<sub>Single-user authentication</sub>

</td>
<td width="34%" align="center">

**◆ Custom Search**
```bash
export GOOGLE_PSE_API_KEY=xxx
export GOOGLE_PSE_ENGINE_ID=yyy
```
<sub>Optional: Search API setup</sub>

</td>
</tr>
</table>

</details>

3. **Server Configuration**:

<details open>
<summary>◆ <b>Server Settings</b> <sub><sup>← Customize ports, URIs & proxies</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**◆ Base Configuration**
```bash
export WORKSPACE_MCP_BASE_URI=
  http://localhost
export WORKSPACE_MCP_PORT=8000
```
<sub>Server URL & port settings</sub>

</td>
<td width="33%" align="center">

**↻ Proxy Support**
```bash
export MCP_ENABLE_OAUTH21=
  true
```
<sub>Leverage multi-user OAuth2.1 clients</sub>

</td>
<td width="34%" align="center">

**@ Default Email**
```bash
export USER_GOOGLE_EMAIL=\
  your.email@gmail.com
```
<sub>Skip email in auth flows in single user mode</sub>

</td>
</tr>
</table>

<details>
<summary>≡ <b>Configuration Details</b> <sub><sup>← Learn more about each setting</sup></sub></summary>

| Variable | Description | Default |
|----------|-------------|---------|
| `WORKSPACE_MCP_BASE_URI` | Base server URI (no port) | `http://localhost` |
| `WORKSPACE_MCP_PORT` | Server listening port | `8000` |
| `WORKSPACE_EXTERNAL_URL` | External URL for reverse proxy setups | None |
| `GOOGLE_OAUTH_REDIRECT_URI` | Override OAuth callback URL | Auto-constructed |
| `USER_GOOGLE_EMAIL` | Default auth email | None |

</details>

</details>

### Google Custom Search Setup

<details>
<summary>◆ <b>Custom Search Configuration</b> <sub><sup>← Enable web search capabilities</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**1. Create Search Engine**
```text
programmablesearchengine.google.com
/controlpanel/create

→ Configure sites or entire web
→ Note your Engine ID (cx)
```
<sub>[Open Control Panel →](https://programmablesearchengine.google.com/controlpanel/create)</sub>

</td>
<td width="33%" align="center">

**2. Get API Key**
```text
developers.google.com
/custom-search/v1/overview

→ Create/select project
→ Enable Custom Search API
→ Create credentials (API Key)
```
<sub>[Get API Key →](https://developers.google.com/custom-search/v1/overview)</sub>

</td>
<td width="34%" align="center">

**3. Set Variables**
```bash
export GOOGLE_PSE_API_KEY=\
  "your-api-key"
export GOOGLE_PSE_ENGINE_ID=\
  "your-engine-id"
```
<sub>Configure in environment</sub>

</td>
</tr>
<tr>
<td colspan="3">

<details>
<summary>≡ <b>Quick Setup Guide</b> <sub><sup>← Step-by-step instructions</sup></sub></summary>

**Complete Setup Process:**

1. **Create Search Engine** - Visit the [Control Panel](https://programmablesearchengine.google.com/controlpanel/create)
   - Choose "Search the entire web" or specify sites
   - Copy the Search Engine ID (looks like: `017643444788157684527:6ivsjbpxpqw`)

2. **Enable API & Get Key** - Visit [Google Developers Console](https://console.cloud.google.com/)
   - Enable "Custom Search API" in your project
   - Create credentials → API Key
   - Restrict key to Custom Search API (recommended)

3. **Configure Environment** - Add to your shell or `.env`:
   ```bash
   export GOOGLE_PSE_API_KEY="AIzaSy..."
   export GOOGLE_PSE_ENGINE_ID="01764344478..."
   ```

≡ [Full Documentation →](https://developers.google.com/custom-search/v1/overview)

</details>

</td>
</tr>
</table>

</details>

### Start the Server

<details open>
<summary>▶ <b>Launch Commands</b> <sub><sup>← Choose your startup mode</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**▶ Quick Start**
```bash
uv run main.py
```
<sub>Default stdio mode</sub>

</td>
<td width="33%" align="center">

**◆ HTTP Mode**
```bash
uv run main.py \
  --transport streamable-http
```
<sub>Web interfaces & debugging</sub>

</td>
<td width="34%" align="center">

**@ Single User**
```bash
uv run main.py \
  --single-user
```
<sub>Simplified authentication</sub>

</td>
</tr>
<tr>
<td colspan="3">

<details>
<summary>◆ <b>Advanced Options</b> <sub><sup>← Tool selection, tiers & Docker</sup></sub></summary>

**▶ Selective Tool Loading**
```bash
# Load specific services only
uv run main.py --tools gmail drive calendar
uv run main.py --tools sheets docs

# Combine with other flags
uv run main.py --single-user --tools gmail
```

**★ Tool Tiers**
```bash
uv run main.py --tool-tier core      # ● Essential tools only
uv run main.py --tool-tier extended  # ◐ Core + additional
uv run main.py --tool-tier complete  # ○ All available tools
```

**◆ Docker Deployment**
```bash
docker build -t workspace-mcp .
docker run -p 8000:8000 -v $(pwd):/app \
  workspace-mcp --transport streamable-http

# With tool selection via environment variables
docker run -e TOOL_TIER=core workspace-mcp
docker run -e TOOLS="gmail drive calendar" workspace-mcp
```

**Available Services**: `gmail` • `drive` • `calendar` • `docs` • `sheets` • `forms` • `tasks` • `chat` • `search`

</details>

</td>
</tr>
</table>

</details>

### Tool Tiers

The server organizes tools into **three progressive tiers** for simplified deployment. Choose a tier that matches your usage needs and API quota requirements.

<table>
<tr>
<td width="65%" valign="top">

#### <span style="color:#72898f">Available Tiers</span>

**<span style="color:#2d5b69">●</span> Core** (`--tool-tier core`)
Essential tools for everyday tasks. Perfect for light usage with minimal API quotas. Includes search, read, create, and basic modify operations across all services.

**<span style="color:#72898f">●</span> Extended** (`--tool-tier extended`)
Core functionality plus management tools. Adds labels, folders, batch operations, and advanced search. Ideal for regular usage with moderate API needs.

**<span style="color:#adbcbc">●</span> Complete** (`--tool-tier complete`)
Full API access including comments, headers/footers, publishing settings, and administrative functions. For power users needing maximum functionality.

</td>
<td width="35%" valign="top">

#### <span style="color:#72898f">Important Notes</span>

<span style="color:#72898f">▶</span> **Start with `core`** and upgrade as needed
<span style="color:#72898f">▶</span> **Tiers are cumulative** – each includes all previous
<span style="color:#72898f">▶</span> **Mix and match** with `--tools` for specific services
<span style="color:#72898f">▶</span> **Configuration** in `core/tool_tiers.yaml`
<span style="color:#72898f">▶</span> **Authentication** included in all tiers

</td>
</tr>
</table>

#### <span style="color:#72898f">Usage Examples</span>

```bash
# Basic tier selection
uv run main.py --tool-tier core                            # Start with essential tools only
uv run main.py --tool-tier extended                        # Expand to include management features
uv run main.py --tool-tier complete                        # Enable all available functionality

# Selective service loading with tiers
uv run main.py --tools gmail drive --tool-tier core        # Core tools for specific services
uv run main.py --tools gmail --tool-tier extended          # Extended Gmail functionality only
uv run main.py --tools docs sheets --tool-tier complete    # Full access to Docs and Sheets
```

## 📋 Credential Configuration

<details open>
<summary>🔑 <b>OAuth Credentials Setup</b> <sub><sup>← Essential for all installations</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**🚀 Environment Variables**
```bash
export GOOGLE_OAUTH_CLIENT_ID=\
  "your-client-id"
export GOOGLE_OAUTH_CLIENT_SECRET=\
  "your-secret"
```
<sub>Best for production</sub>

</td>
<td width="33%" align="center">

**📁 File-based**
```bash
# Download & place in project root
client_secret.json

# Or specify custom path
export GOOGLE_CLIENT_SECRET_PATH=\
  /path/to/secret.json
```
<sub>Traditional method</sub>

</td>
<td width="34%" align="center">

**⚡ .env File**
```bash
cp .env.oauth21 .env
# Edit .env with credentials
```
<sub>Best for development</sub>

</td>
</tr>
<tr>
<td colspan="3">

<details>
<summary>📖 <b>Credential Loading Details</b> <sub><sup>← Understanding priority & best practices</sup></sub></summary>

**Loading Priority**
1. Environment variables (`export VAR=value`)
2. `.env` file in project root (warning - if you run via `uvx` rather than `uv run` from the repo directory, you are spawning a standalone process not associated with your clone of the repo and it will not find your .env file without specifying it directly) 
3. `client_secret.json` via `GOOGLE_CLIENT_SECRET_PATH`
4. Default `client_secret.json` in project root

**Why Environment Variables?**
- ✅ **Docker/K8s ready** - Native container support
- ✅ **Cloud platforms** - Heroku, Railway, Vercel
- ✅ **CI/CD pipelines** - GitHub Actions, Jenkins
- ✅ **No secrets in git** - Keep credentials secure
- ✅ **Easy rotation** - Update without code changes

</details>

</td>
</tr>
</table>

</details>

---

## 🧰 Available Tools

> **Note**: All tools support automatic authentication via `@require_google_service()` decorators with 30-minute service caching.

<table width="100%">
<tr>
<td width="50%" valign="top">

### 📅 **Google Calendar** <sub>[`calendar_tools.py`](gcalendar/calendar_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `list_calendars` | **Core** | List accessible calendars |
| `get_events` | **Core** | Retrieve events with time range filtering |
| `create_event` | **Core** | Create events with attachments & reminders |
| `modify_event` | **Core** | Update existing events |
| `delete_event` | Extended | Remove events |

</td>
<td width="50%" valign="top">

### 📁 **Google Drive** <sub>[`drive_tools.py`](gdrive/drive_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `search_drive_files` | **Core** | Search files with query syntax |
| `get_drive_file_content` | **Core** | Read file content (Office formats) |
| `create_drive_file` | **Core** | Create files or fetch from URLs |
| `list_drive_items` | Extended | List folder contents |
| `update_drive_file` | Extended | Update file metadata, move between folders |

</td>
</tr>
<tr>

<tr>
<td width="50%" valign="top">

### 📧 **Gmail** <sub>[`gmail_tools.py`](gmail/gmail_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `search_gmail_messages` | **Core** | Search with Gmail operators |
| `get_gmail_message_content` | **Core** | Retrieve message content |
| `get_gmail_messages_content_batch` | **Core** | Batch retrieve message content |
| `send_gmail_message` | **Core** | Send emails |
| `get_gmail_thread_content` | Extended | Get full thread content |
| `modify_gmail_message_labels` | Extended | Modify message labels |
| `list_gmail_labels` | Extended | List available labels |
| `manage_gmail_label` | Extended | Create/update/delete labels |
| `draft_gmail_message` | Extended | Create drafts |
| `get_gmail_threads_content_batch` | Complete | Batch retrieve thread content |
| `batch_modify_gmail_message_labels` | Complete | Batch modify labels |
| `start_google_auth` | Complete | Initialize authentication |

</td>
<td width="50%" valign="top">

### 📝 **Google Docs** <sub>[`docs_tools.py`](gdocs/docs_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `get_doc_content` | **Core** | Extract document text |
| `create_doc` | **Core** | Create new documents |
| `modify_doc_text` | **Core** | Modify document text |
| `search_docs` | Extended | Find documents by name |
| `find_and_replace_doc` | Extended | Find and replace text |
| `list_docs_in_folder` | Extended | List docs in folder |
| `insert_doc_elements` | Extended | Add tables, lists, page breaks |
| `insert_doc_image` | Complete | Insert images from Drive/URLs |
| `update_doc_headers_footers` | Complete | Modify headers and footers |
| `batch_update_doc` | Complete | Execute multiple operations |
| `inspect_doc_structure` | Complete | Analyze document structure |
| `export_doc_to_pdf` | Extended | Export document to PDF |
| `create_table_with_data` | Complete | Create data tables |
| `debug_table_structure` | Complete | Debug table issues |
| `*_document_comments` | Complete | Read, Reply, Create, Resolve |

</td>
</tr>

<tr>
<td width="50%" valign="top">

### 📊 **Google Sheets** <sub>[`sheets_tools.py`](gsheets/sheets_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `read_sheet_values` | **Core** | Read cell ranges |
| `modify_sheet_values` | **Core** | Write/update/clear cells |
| `create_spreadsheet` | **Core** | Create new spreadsheets |
| `list_spreadsheets` | Extended | List accessible spreadsheets |
| `get_spreadsheet_info` | Extended | Get spreadsheet metadata |
| `create_sheet` | Complete | Add sheets to existing files |
| `*_sheet_comment` | Complete | Read/create/reply/resolve comments |

</td>
<td width="50%" valign="top">

### 🖼️ **Google Slides** <sub>[`slides_tools.py`](gslides/slides_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `create_presentation` | **Core** | Create new presentations |
| `get_presentation` | **Core** | Retrieve presentation details |
| `batch_update_presentation` | Extended | Apply multiple updates |
| `get_page` | Extended | Get specific slide information |
| `get_page_thumbnail` | Extended | Generate slide thumbnails |
| `*_presentation_comment` | Complete | Read/create/reply/resolve comments |

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 📝 **Google Forms** <sub>[`forms_tools.py`](gforms/forms_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `create_form` | **Core** | Create new forms |
| `get_form` | **Core** | Retrieve form details & URLs |
| `set_publish_settings` | Complete | Configure form settings |
| `get_form_response` | Complete | Get individual responses |
| `list_form_responses` | Extended | List all responses with pagination |

</td>
<td width="50%" valign="top">

### ✓ **Google Tasks** <sub>[`tasks_tools.py`](gtasks/tasks_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `list_tasks` | **Core** | List tasks with filtering |
| `get_task` | **Core** | Retrieve task details |
| `create_task` | **Core** | Create tasks with hierarchy |
| `update_task` | **Core** | Modify task properties |
| `delete_task` | Extended | Remove tasks |
| `move_task` | Complete | Reposition tasks |
| `clear_completed_tasks` | Complete | Hide completed tasks |
| `*_task_list` | Complete | List/get/create/update/delete task lists |

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 💬 **Google Chat** <sub>[`chat_tools.py`](gchat/chat_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `list_spaces` | Extended | List chat spaces/rooms |
| `get_messages` | **Core** | Retrieve space messages |
| `send_message` | **Core** | Send messages to spaces |
| `search_messages` | **Core** | Search across chat history |

</td>
<td width="50%" valign="top">

### 🔍 **Google Custom Search** <sub>[`search_tools.py`](gsearch/search_tools.py)</sub>

| Tool | Tier | Description |
|------|------|-------------|
| `search_custom` | **Core** | Perform web searches |
| `get_search_engine_info` | Complete | Retrieve search engine metadata |
| `search_custom_siterestrict` | Extended | Search within specific domains |

</td>
</tr>
</table>


**Tool Tier Legend:**
- <span style="color:#2d5b69">•</span> **Core**: Essential tools for basic functionality • Minimal API usage • Getting started
- <span style="color:#72898f">•</span> **Extended**: Core tools + additional features • Regular usage • Expanded capabilities
- <span style="color:#adbcbc">•</span> **Complete**: All available tools including advanced features • Power users • Full API access

---

### Connect to Claude Desktop

The server supports two transport modes:

#### Stdio Mode (Default - Recommended for Claude Desktop)

In general, you should use the one-click DXT installer package for Claude Desktop.
If you are unable to for some reason, you can configure it manually via `claude_desktop_config.json`

**Manual Claude Configuration (Alternative)**

<details>
<summary>📝 <b>Claude Desktop JSON Config</b> <sub><sup>← Click for manual setup instructions</sup></sub></summary>

1. Open Claude Desktop Settings → Developer → Edit Config
   - **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

2. Add the server configuration:
```json
{
  "mcpServers": {
    "google_workspace": {
      "command": "uvx",
      "args": ["workspace-mcp"],
      "env": {
        "GOOGLE_OAUTH_CLIENT_ID": "your-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "your-secret",
        "OAUTHLIB_INSECURE_TRANSPORT": "1"
      }
    }
  }
}
```
</details>

### 2. Advanced / Cross-Platform Installation

If you’re developing, deploying to servers, or using another MCP-capable client, keep reading.

#### Instant CLI (uvx)

<details open>
<summary>⚡ <b>Quick Start with uvx</b> <sub><sup>← No installation required!</sup></sub></summary>

```bash
# Requires Python 3.10+ and uvx
# First, set credentials (see Credential Configuration above)
uvx workspace-mcp --tool-tier core  # or --tools gmail drive calendar
```

> **Note**: Configure [OAuth credentials](#credential-configuration) before running. Supports environment variables, `.env` file, or `client_secret.json`.

</details>

### Local Development Setup

<details open>
<summary>🛠️ <b>Developer Workflow</b> <sub><sup>← Install deps, lint, and test</sup></sub></summary>

```bash
# Install everything needed for linting, tests, and release tooling
uv sync --group dev

# Run the same linter that git hooks invoke automatically
uv run ruff check .

# Execute the full test suite (async fixtures require pytest-asyncio)
uv run pytest
```

- `uv sync --group test` installs only the testing stack if you need a slimmer environment.
- `uv run main.py --transport streamable-http` launches the server with your checked-out code for manual verification.
- Ruff is part of the `dev` group because pre-push hooks call `ruff check` automatically—run it locally before committing to avoid hook failures.

</details>

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
<summary>🔐 <b>How the FastMCP GoogleProvider handles OAuth</b> <sub><sup>← Advanced OAuth 2.1 details</sup></sub></summary>

FastMCP ships a native `GoogleProvider` that we now rely on directly. It solves the two tricky parts of using Google OAuth with MCP clients:

1.  **Dynamic Client Registration**: Google still doesn't support OAuth 2.1 DCR, but the FastMCP provider exposes the full DCR surface and forwards registrations to Google using your fixed credentials. MCP clients register as usual and the provider hands them your Google client ID/secret under the hood.

2.  **CORS & Browser Compatibility**: The provider includes an OAuth proxy that serves all discovery, authorization, and token endpoints with proper CORS headers. We no longer maintain custom `/oauth2/*` routes—the provider handles the upstream exchanges securely and advertises the correct metadata to clients.

The result is a leaner server that still enables any OAuth 2.1 compliant client (including browser-based ones) to authenticate through Google without bespoke code.

</details>

### Stateless Mode (Container-Friendly)

The server supports a stateless mode designed for containerized environments where file system writes should be avoided:

**Enabling Stateless Mode:**
```bash
# Stateless mode requires OAuth 2.1 to be enabled
export MCP_ENABLE_OAUTH21=true
export WORKSPACE_MCP_STATELESS_MODE=true
uv run main.py --transport streamable-http
```

**Key Features:**
- **No file system writes**: Credentials are never written to disk
- **No debug logs**: File-based logging is completely disabled
- **Memory-only sessions**: All tokens stored in memory via OAuth 2.1 session store
- **Container-ready**: Perfect for Docker, Kubernetes, and serverless deployments
- **Token per request**: Each request must include a valid Bearer token

**Requirements:**
- Must be used with `MCP_ENABLE_OAUTH21=true`
- Incompatible with single-user mode
- Clients must handle OAuth flow and send valid tokens with each request

This mode is ideal for:
- Cloud deployments where persistent storage is unavailable
- Multi-tenant environments requiring strict isolation
- Containerized applications with read-only filesystems
- Serverless functions and ephemeral compute environments

**MCP Inspector**: No additional configuration needed with desktop OAuth client.

**Claude Code**: No additional configuration needed with desktop OAuth client.

### External OAuth 2.1 Provider Mode

The server supports an external OAuth 2.1 provider mode for scenarios where authentication is handled by an external system. In this mode, the MCP server does not manage the OAuth flow itself but expects valid bearer tokens in the Authorization header of tool calls.

**Enabling External OAuth 2.1 Provider Mode:**
```bash
# External OAuth provider mode requires OAuth 2.1 to be enabled
export MCP_ENABLE_OAUTH21=true
export EXTERNAL_OAUTH21_PROVIDER=true
uv run main.py --transport streamable-http
```

**How It Works:**
- **Protocol-level auth disabled**: MCP handshake (`initialize`) and `tools/list` do not require authentication
- **Tool-level auth required**: All tool calls must include `Authorization: Bearer <token>` header
- **External OAuth flow**: Your external system handles the OAuth flow and obtains Google access tokens
- **Token validation**: Server validates bearer tokens via Google's tokeninfo API
- **Multi-user support**: Each request is authenticated independently based on its bearer token

**Key Features:**
- **No local OAuth flow**: Server does not provide OAuth callback endpoints or manage OAuth state
- **Bearer token only**: All authentication via Authorization headers
- **Stateless by design**: Works seamlessly with `WORKSPACE_MCP_STATELESS_MODE=true`
- **External identity providers**: Integrate with your existing authentication infrastructure
- **Tool discovery**: Clients can list available tools without authentication

**Requirements:**
- Must be used with `MCP_ENABLE_OAUTH21=true`
- OAuth credentials still required for token validation (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`)
- External system must obtain valid Google OAuth access tokens (ya29.*)
- Each tool call request must include valid bearer token

**Use Cases:**
- Integrating with existing authentication systems
- Custom OAuth flows managed by your application
- API gateways that handle authentication upstream
- Multi-tenant SaaS applications with centralized auth
- Mobile or web apps with their own OAuth implementation


### VS Code MCP Client Support

<details>
<summary>🆚 <b>VS Code Configuration</b> <sub><sup>← Setup for VS Code MCP extension</sup></sub></summary>

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
</details>

### Claude Code MCP Client Support

<details>
<summary>🆚 <b>Claude Code Configuration</b> <sub><sup>← Setup for Claude Code MCP support</sup></sub></summary>

```json
claude mcp add --transport http workspace-mcp http://localhost:8000/mcp
```
</details>

#### Reverse Proxy Setup

If you're running the MCP server behind a reverse proxy (nginx, Apache, Cloudflare, etc.), you have two configuration options:

**Problem**: When behind a reverse proxy, the server constructs OAuth URLs using internal ports (e.g., `http://localhost:8000`) but external clients need the public URL (e.g., `https://your-domain.com`).

**Solution 1**: Set `WORKSPACE_EXTERNAL_URL` for all OAuth endpoints:
```bash
# This configures all OAuth endpoints to use your external URL
export WORKSPACE_EXTERNAL_URL="https://your-domain.com"
```

**Solution 2**: Set `GOOGLE_OAUTH_REDIRECT_URI` for just the callback:
```bash
# This only overrides the OAuth callback URL
export GOOGLE_OAUTH_REDIRECT_URI="https://your-domain.com/oauth2callback"
```

You also have options for:
| `OAUTH_CUSTOM_REDIRECT_URIS` *(optional)* | Comma-separated list of additional redirect URIs |
| `OAUTH_ALLOWED_ORIGINS` *(optional)* | Comma-separated list of additional CORS origins |

**Important**:
- Use `WORKSPACE_EXTERNAL_URL` when all OAuth endpoints should use the external URL (recommended for reverse proxy setups)
- Use `GOOGLE_OAUTH_REDIRECT_URI` when you only need to override the callback URL
- The redirect URI must exactly match what's configured in your Google Cloud Console
- Your reverse proxy must forward OAuth-related requests (`/oauth2callback`, `/oauth2/*`, `/.well-known/*`) to the MCP server

<details>
<summary>🚀 <b>Advanced uvx Commands</b> <sub><sup>← More startup options</sup></sub></summary>

```bash
# Configure credentials first (see Credential Configuration section)

# Start with specific tools only
uvx workspace-mcp --tools gmail drive calendar tasks

# Start with tool tiers (recommended for most users)
uvx workspace-mcp --tool-tier core      # Essential tools
uvx workspace-mcp --tool-tier extended  # Core + additional features
uvx workspace-mcp --tool-tier complete  # All tools

# Start in HTTP mode for debugging
uvx workspace-mcp --transport streamable-http
```
</details>

*Requires Python 3.10+ and [uvx](https://github.com/astral-sh/uv). The package is available on [PyPI](https://pypi.org/project/workspace-mcp).*

### Development Installation

For development or customization:

```bash
git clone https://github.com/taylorwilsdon/google_workspace_mcp.git
cd google_workspace_mcp
uv run main.py
```

**Development Installation (For Contributors)**:

<details>
<summary>🔧 <b>Developer Setup JSON</b> <sub><sup>← For contributors & customization</sup></sub></summary>

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
        "GOOGLE_OAUTH_CLIENT_ID": "your-client-id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "your-secret",
        "OAUTHLIB_INSECURE_TRANSPORT": "1"
      }
    }
  }
}
```
</details>

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

## <span style="color:#adbcbc">◆ Development</span>

### <span style="color:#72898f">Project Structure</span>

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

### Credential Store System

The server includes an abstract credential store API and a default backend for managing Google OAuth
credentials with support for multiple storage backends:

**Features:**
- **Abstract Interface**: `CredentialStore` base class defines standard operations (get, store, delete, list users)
- **Local File Storage**: `LocalDirectoryCredentialStore` implementation stores credentials as JSON files
- **Configurable Storage**: Environment variable `GOOGLE_MCP_CREDENTIALS_DIR` sets storage location
- **Multi-User Support**: Store and manage credentials for multiple Google accounts
- **Automatic Directory Creation**: Storage directory is created automatically if it doesn't exist

**Configuration:**
```bash
# Optional: Set custom credentials directory
export GOOGLE_MCP_CREDENTIALS_DIR="/path/to/credentials"

# Default locations (if GOOGLE_MCP_CREDENTIALS_DIR not set):
# - ~/.google_workspace_mcp/credentials (if home directory accessible)
# - ./.credentials (fallback)
```

**Usage Example:**
```python
from auth.credential_store import get_credential_store

# Get the global credential store instance
store = get_credential_store()

# Store credentials for a user
store.store_credential("user@example.com", credentials)

# Retrieve credentials
creds = store.get_credential("user@example.com")

# List all users with stored credentials
users = store.list_users()
```

The credential store automatically handles credential serialization, expiry parsing, and provides error handling for storage operations.

---

## <span style="color:#adbcbc">⊠ Security</span>

- **Credentials**: Never commit `.env`, `client_secret.json` or the `.credentials/` directory to source control!
- **OAuth Callback**: Uses `http://localhost:8000/oauth2callback` for development (requires `OAUTHLIB_INSECURE_TRANSPORT=1`)
- **Transport-Aware Callbacks**: Stdio mode starts a minimal HTTP server only for OAuth, ensuring callbacks work in all modes
- **Production**: Use HTTPS & OAuth 2.1 and configure accordingly
- **Scope Minimization**: Tools request only necessary permissions

---


---

## <span style="color:#adbcbc">≡ License</span>

MIT License - see `LICENSE` file for details.

---

Validations:
[![MCP Badge](https://lobehub.com/badge/mcp/taylorwilsdon-google_workspace_mcp)](https://lobehub.com/mcp/taylorwilsdon-google_workspace_mcp)

[![Verified on MseeP](https://mseep.ai/badge.svg)](https://mseep.ai/app/eebbc4a6-0f8c-41b2-ace8-038e5516dba0)


<div align="center">
<img width="842" alt="Batch Emails" src="https://github.com/user-attachments/assets/0876c789-7bcc-4414-a144-6c3f0aaffc06" />
</div>
