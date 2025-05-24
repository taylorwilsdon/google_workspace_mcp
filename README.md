<div align="center">

# Google Workspace MCP Server

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![UV](https://img.shields.io/badge/Package%20Installer-UV-blueviolet)](https://github.com/astral-sh/uv)

<img src="https://github.com/user-attachments/assets/b89524e4-6e6e-49e6-ba77-00d6df0c6e5c" width="200" />

**Connect MCP Clients, AI Assistants and more to Google Workspace services through the Model Context Protocol**

</div>

---

## 📑 Table of Contents

- [Overview](#-overview)
- [Features](#-features)
- [Quick Start](#-quick-start)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Configuration](#configuration)
  - [Start the Server](#start-the-server)
  - [Connecting to the Server](#connecting-to-the-server)
  - [Integration with Open WebUI](#integration-with-open-webui)
  - [First-time Authentication](#first-time-authentication)
- [Available Tools](#-available-tools)
  - [Calendar](#calendar)
  - [Google Drive](#google-drive)
  - [Gmail](#gmail)
  - [Google Docs](#google-docs)
- [Development](#-development)
  - [Project Structure](#project-structure)
  - [Port Handling for OAuth](#port-handling-for-oauth)
  - [Debugging](#debugging)
  - [Adding New Tools](#adding-new-tools)
- [Security Notes](#-security-notes)
- [License](#-license)

---

## 🌐 Overview

The Google Workspace MCP Server integrates Google Workspace services (Calendar, Drive, Gmail, and Docs) with AI assistants and other applications using the Model Context Protocol (MCP). This allows AI systems to access and interact with user data from Google Workspace applications securely and efficiently.

---

## ✨ Features

- **🔐 OAuth 2.0 Authentication**: Securely connects to Google APIs using user-authorized credentials with automatic token refresh and centralized authentication flow
- **📅 Google Calendar Integration**: Full calendar management - list calendars, fetch events, create/modify/delete events with support for all-day and timed events
- **📁 Google Drive Integration**: Search files, list folder contents, read file content, and create new files
- **📧 Gmail Integration**: Complete email management - search messages, retrieve content, send emails, and create drafts
- **📄 Google Docs Integration**: Search for documents, read document content, list documents in folders, and create new documents
- **🔄 Multiple Transport Options**: Streamable HTTP + SSE fallback
- **🔌 `mcpo` Compatibility**: Easily expose the server as an OpenAPI endpoint for integration with tools like Open WebUI
- **🧩 Extensible Design**: Simple structure for adding support for more Google Workspace APIs and tools
- **🔄 Integrated OAuth Callback**: Handles the OAuth redirect directly within the server on port 8000
- **⚡ Thread-Safe Session Management**: Robust session handling with thread-safe architecture for improved reliability

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **[uv](https://github.com/astral-sh/uv)** package installer (or pip)
- **Google Cloud Project** with OAuth 2.0 credentials enabled for required APIs (Calendar, Drive, Gmail, Docs)

### Installation

```bash
# Clone the repository (replace with the actual URL if different)
git clone https://github.com/your-username/google_workspace_mcp.git
cd google_workspace_mcp

# Create a virtual environment and install dependencies
uv venv
source .venv/bin/activate  # On Windows use `.venv\Scripts\activate`
uv pip install -e .
```

### Configuration

1. Create **OAuth 2.0 Credentials** (Desktop application type) in the [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Google Calendar API**, **Google Drive API**, **Gmail API**, and **Google Docs API** for your project.
3. Download the OAuth client credentials as `client_secret.json` and place it in the project's root directory.
4. Add the following redirect URI to your OAuth client configuration in the Google Cloud Console. Note that `http://localhost:8000` is the default base URI and port, which can be customized via environment variables (`WORKSPACE_MCP_BASE_URI` and `WORKSPACE_MCP_PORT`). If you change these, you must update the redirect URI in the Google Cloud Console accordingly.
   ```
   http://localhost:8000/oauth2callback
   ```
5. **⚠️ Important**: Ensure `client_secret.json` is added to your `.gitignore` file and never committed to version control.

### Server Configuration

The server's base URL and port can be customized using environment variables:

- `WORKSPACE_MCP_BASE_URI`: Sets the base URI for the server (default: `http://localhost`). This affects the `server_url` used for Gemini native function calling and the `OAUTH_REDIRECT_URI`.
- `WORKSPACE_MCP_PORT`: Sets the port the server listens on (default: `8000`). This affects the `server_url`, `port`, and `OAUTH_REDIRECT_URI`.

Example usage:

```bash
export WORKSPACE_MCP_BASE_URI="https://my-custom-domain.com"
export WORKSPACE_MCP_PORT="9000"
uv run main.py
```

### Environment Setup

The server uses HTTP for localhost OAuth callbacks during development. Set this environment variable before running the server:

```bash
# Allow HTTP for localhost OAuth callbacks (development only!)
export OAUTHLIB_INSECURE_TRANSPORT=1
```

Without this, you might encounter an "OAuth 2 MUST utilize HTTPS" error during the authentication flow.

### Start the Server

Choose one of the following methods to run the server:

<details>
<summary><b>HTTP Server Mode</b></summary>

```bash
python main.py
# or using uv
uv run main.py
```

Runs the server with an HTTP transport layer on port 8000.
</details>

<details>
<summary><b>Single-User Mode</b></summary>

For simplified single-user environments, you can run the server in single-user mode, which bypasses session-to-OAuth mapping and uses any available credentials from the `.credentials` directory:

```bash
python main.py --single-user
# or using uv
uv run main.py --single-user
```

In single-user mode:
- The server automatically finds and uses any valid credentials in the `.credentials` directory
- No session mapping is required - the server uses the first valid credential file found
- Useful for development, testing, or single-user deployments
- Still requires initial OAuth authentication to create credential files

This mode is particularly helpful when you don't need multi-user session management and want simplified credential handling.
</details>

<details>
<summary><b>Using mcpo (Recommended for Open WebUI and other OpenAPI spec compatible clients)</b></summary>

Requires `mcpo` installed (`uv pip install mcpo` or `pip install mcpo`).

```bash
# Ensure config.json points to your project directory
mcpo --config config.json --port 8000
```

See the [Integration with Open WebUI](#integration-with-open-webui) section for a `config.json` example.
</details>

<details>
<summary><b>Using Docker</b></summary>

You can build and run the server using the provided [`Dockerfile`](Dockerfile).

```bash
# Build the Docker image
docker build -t google-workspace-mcp .

# Run the Docker container
# The -p flag maps the container port 8000 to the host port 8000
# The -v flag mounts the current directory to /app inside the container
# This is useful for development to pick up code changes without rebuilding
docker run -p 8000:8000 -v $(pwd):/app google-workspace-mcp
```

The `smithery.yaml` file is configured to start the server correctly within the Docker container.
</details>

#### Important Ports

The default ports are `8000`, but can be changed via the `WORKSPACE_MCP_PORT` environment variable.

| Service | Default Port | Description |
|---------|------|-------------|
| OAuth Callback | `8000` | Handled internally by the server via the `/oauth2callback` route |
| HTTP Mode Server | `8000` | Default when using HTTP transport |

### Connecting to the Server

The server supports multiple connection methods:

**Claude Desktop:**
> Can run anywhere and be used via `mcp-remote` or invoked locally either with `uv run main.py` as the arg or by using `mcp-remote` with localhost.

<img width="810" alt="image" src="https://github.com/user-attachments/assets/7f91aa4e-6763-4dc8-8368-05049aa5c2c7" />


**config.json:**
```json
{
  "mcpServers": {
    "Google workspace": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:8000/mcp”
      ]
    }
  }
}
```


<summary><b>Using mcpo (Recommended for OpenAPI Spec Access)</b></summary>

1. Install `mcpo`: `uv pip install mcpo` or `pip install mcpo`
2. Create a `config.json` (see [Integration with Open WebUI](#integration-with-open-webui))
3. Run `mcpo` pointing to your config: `mcpo --config config.json --port 8000 [--api-key YOUR_SECRET_KEY]`
4. The MCP server API will be available at: `http://localhost:8000/gworkspace` (or the name defined in `config.json`)
5. OpenAPI documentation (Swagger UI) available at: `http://localhost:8000/gworkspace/docs`

<summary><b>HTTP Mode</b></summary>

1. Start the server in HTTP mode (see [Start the Server](#start-the-server))
2. Send MCP JSON requests directly to `http://localhost:8000`
3. Useful for testing with tools like `curl` or custom HTTP clients
4. Can be used to serve  Claude Desktop & other MCP clients yet to integrate the new Streamable HTTP transport via mcp-remote:
5. You can also serve in SSE fallback mode if preferred.

### Integration with Open WebUI

To use this server as a tool provider within Open WebUI:

1. **Create `mcpo` Configuration**:
   Create a file named `config.json` with the following structure. **Replace `/path/to/google_workspace_mcp` with the actual absolute path to this project directory.**

   ```json
   {
     "mcpServers": {
       "gworkspace": {
         "command": "uv",
         "args": [
           "run",
           "main.py"
         ],
         "options": {
           "cwd": "/path/to/google_workspace_mcp",
           "env": {
             "OAUTHLIB_INSECURE_TRANSPORT": "1"
           }
         }
       }
     }
   }
   ```
   *Note: Using `uv run main.py` ensures the correct virtual environment is used.*

2. **Start the `mcpo` Server**:
   ```bash
   # Make sure OAUTHLIB_INSECURE_TRANSPORT=1 is set in your shell environment
   # OR rely on the 'env' setting in config.json
   export OAUTHLIB_INSECURE_TRANSPORT=1
   mcpo --port 8000 --config config.json --api-key "your-optional-secret-key"
   ```
   This command starts the `mcpo` proxy, which in turn manages the `google_workspace_mcp` server process based on the configuration.

3. **Configure Open WebUI**:
   - Navigate to your Open WebUI settings
   - Go to "Connections" -> "Tools"
   - Click "Add Tool"
   - Enter the Server URL: `http://localhost:8000/gworkspace` (matching the `mcpo` base URL and server name from `config.json`)
   - If you used an `--api-key` with `mcpo`, enter it as the API Key
   - Save the configuration
   - The Google Workspace tools should now be available when interacting with models in Open WebUI

### First-time Authentication

When a tool requiring Google API access is called:

- **If `user_google_email` is provided to the tool and credentials are missing/invalid**: The server automatically initiates the OAuth 2.0 flow. An authorization URL will be returned in the MCP response (or printed to the console).
- **If `user_google_email` is NOT provided and credentials are missing/invalid**: The tool will return an error message guiding the LLM to use the centralized `start_google_auth` tool. The LLM should then call `start_google_auth` with the user's email and the appropriate `service_name` (e.g., "Google Calendar", "Google Docs", "Gmail", "Google Drive"). This will also return an authorization URL.

**Steps for the User (once an authorization URL is obtained):**

1. Open the provided authorization URL in a web browser.
2. Log in to the Google account and grant the requested permissions for the specified service.
3. After authorization, Google will redirect the browser to `http://localhost:8000/oauth2callback` (or your configured redirect URI).
4. The MCP server handles this callback, exchanges the authorization code for tokens, and securely stores the credentials.
5. The LLM can then retry the original request. Subsequent calls for the same user and service should work without re-authentication until the refresh token expires or is revoked.

---

## 🧰 Available Tools

> **Note**: The first use of any tool for a specific Google service may trigger the OAuth authentication flow if valid credentials are not already stored and `user_google_email` is provided to the tool. If authentication is required and `user_google_email` is not provided to the tool, the LLM should use the centralized `start_google_auth` tool (defined in `core/server.py`) with the user's email and the appropriate `service_name`.

### 📅 Google Calendar

Source: [`gcalendar/calendar_tools.py`](gcalendar/calendar_tools.py)

| Tool | Description | Parameters |
|------|-------------|------------|
| `start_google_auth` | (Centralized in `core/server.py`) Initiates the OAuth 2.0 authentication flow for a specific Google account and service. Use this when no valid credentials are available or if a tool fails due to missing authentication and an email was not provided to it. | • `user_google_email` (required): The user's Google email address<br>• `service_name` (required): The Google service name (e.g., "Google Calendar", "Google Docs", "Gmail", "Google Drive") |
| `list_calendars` | Lists all calendars accessible to the authenticated user. | • `user_google_email` (optional): Used if session is not authenticated<br>• `mcp_session_id` (injected automatically) |
| `get_events` | Retrieves upcoming events from a specified calendar within a time range. | • `calendar_id` (optional): Calendar ID (default: `primary`)<br>• `time_min` (optional): Start time (RFC3339 or `YYYY-MM-DD`)<br>• `time_max` (optional): End time (RFC3339 or `YYYY-MM-DD`)<br>• `max_results` (optional): Max number of events (default: 25)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |
| `create_event` | Creates a new calendar event. Supports all-day and timed events. | • `summary` (required): Event title<br>• `start_time` (required): Start time (RFC3339 or `YYYY-MM-DD`)<br>• `end_time` (required): End time (RFC3339 or `YYYY-MM-DD`)<br>• `calendar_id` (optional): Calendar ID (default: `primary`)<br>• `description`, `location`, `attendees`, `timezone` (optional)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |
| `modify_event` | Updates an existing event by ID. Only provided fields will be modified. | • `event_id` (required): ID of the event to modify<br>• `calendar_id` (optional): Calendar ID (default: `primary`)<br>• `summary`, `start_time`, `end_time`, `description`, `location`, `attendees`, `timezone` (optional)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |
| `delete_event` | Deletes an event by ID. | • `event_id` (required): ID of the event to delete<br>• `calendar_id` (optional): Calendar ID (default: `primary`)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |

> ℹ️ All Calendar tools support authentication via the current MCP session (`mcp_session_id`) or fallback to `user_google_email`. If neither is available and authentication is required, the tool will return an error prompting the LLM to use the centralized `start_google_auth` tool with the user's email and `service_name="Google Calendar"`.

> 🕒 Date/Time Parameters: Tools accept both full RFC3339 timestamps (e.g., 2024-05-12T10:00:00Z) and simple dates (e.g., 2024-05-12). The server automatically formats them as needed.

### 📁 Google Drive

Source: [`gdrive/drive_tools.py`](gdrive/drive_tools.py)

| Tool | Description | Parameters |
|------|-------------|------------|
| `search_drive_files` | Searches for files and folders across the user's Drive | • `query` (required): Search query string (e.g., `name contains 'report'`)<br>• `max_results` (optional): Maximum number of files to return |
| `get_drive_file_content` | Retrieves the content of a specific file | • `file_id` (required): The ID of the file<br>• `mime_type` (optional): Specify the desired export format |
| `list_drive_items` | Lists files and folders within a specific folder or the root | • `folder_id` (optional): The ID of the folder to list (defaults to root)<br>• `max_results` (optional): Maximum number of items to return |
| `create_drive_file` | Creates a new file in Google Drive | • `name` (required): The desired name for the new file<br>• `content` (required): The text content to write into the file<br>• `folder_id` (optional): The ID of the parent folder<br>• `mime_type` (optional): The MIME type of the file (defaults to `text/plain`) |

> **Query Syntax**: For Google Drive search queries, see [Drive Search Query Syntax](https://developers.google.com/drive/api/guides/search-files)

### 📧 Gmail

Source: [`gmail/gmail_tools.py`](gmail/gmail_tools.py)

| Tool                      | Description                                                                      | Parameters |
|---------------------------|----------------------------------------------------------------------------------|------------|
| `search_gmail_messages`   | Search email messages using standard Gmail search operators (from, subject, etc). | • `query` (required): Search string (e.g., `"from:foo subject:bar is:unread"`)<br>• `user_google_email` (optional)<br>• `page_size` (optional, default: 10)<br>• `mcp_session_id` (injected automatically) |
| `get_gmail_message_content`| Get subject, sender, and *plain text* body of an email by message ID.            | • `message_id` (required)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |
| `send_gmail_message`      | Send a plain text email using the user's Gmail account.                           | • `to` (required): Recipient email address<br>• `subject` (required)<br>• `body` (required)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |
| `draft_gmail_message`     | Create a draft email in the user's Gmail account.                                 | • `subject` (required): Email subject<br>• `body` (required): Email body (plain text)<br>• `to` (optional): Recipient email address<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |


> **Query Syntax**: For Gmail search queries, see [Gmail Search Query Syntax](https://support.google.com/mail/answer/7190)

### 📝 Google Docs

Source: [`gdocs/docs_tools.py`](gdocs/docs_tools.py)

| Tool                 | Description                                                                         | Parameters |
|----------------------|-------------------------------------------------------------------------------------|------------|
| `search_docs`        | Search for Google Docs by name (using Drive API).                                   | • `query` (required): Text to search for in Doc names<br>• `user_google_email` (optional)<br>• `page_size` (optional, default: 10)<br>• `mcp_session_id` (injected automatically) |
| `get_doc_content`    | Retrieve the plain text content of a Google Doc by its document ID.                 | • `document_id` (required)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |
| `list_docs_in_folder`| List all Google Docs inside a given Drive folder (by folder ID, default = `root`).  | • `folder_id` (optional, default: `'root'`)<br>• `user_google_email` (optional)<br>• `page_size` (optional, default: 100)<br>• `mcp_session_id` (injected automatically) |
| `create_doc`         | Create a new Google Doc, optionally with initial content.                           | • `title` (required): Name for the doc<br>• `content` (optional, default: empty)<br>• `user_google_email` (optional)<br>• `mcp_session_id` (injected automatically) |

---

## 🛠️ Development

### Project Structure

```
google_workspace_mcp/
├── .venv/             # Virtual environment (created by uv)
├── auth/              # OAuth handling logic (google_auth.py, oauth_manager.py)
├── core/              # Core MCP server logic (server.py)
├── gcalendar/         # Google Calendar tools (calendar_tools.py)
├── gdocs/             # Google Docs tools (docs_tools.py)
├── gdrive/            # Google Drive tools (drive_tools.py)
├── gmail/             # Gmail tools (gmail_tools.py)
├── .gitignore         # Git ignore file
├── client_secret.json # Google OAuth Credentials (DO NOT COMMIT)
├── config.json        # Example mcpo configuration
├── main.py            # Main server entry point (imports tools)
├── mcp_server_debug.log # Log file for debugging
├── pyproject.toml     # Project metadata and dependencies (for uv/pip)
├── README.md          # This file
├── uv.lock            # uv lock file
```

### Port Handling for OAuth

The server cleverly handles the OAuth 2.0 redirect URI (`/oauth2callback`) without needing a separate web server framework:

- It utilizes the built-in HTTP server capabilities of the underlying MCP library when run in HTTP mode or via `mcpo`
- A custom MCP route is registered specifically for `/oauth2callback` on port `8000`
- When Google redirects the user back after authorization, the MCP server intercepts the request on this route
- The `auth` module extracts the authorization code and completes the token exchange
- This requires `OAUTHLIB_INSECURE_TRANSPORT=1` to be set when running locally, as the callback uses `http://localhost`

### Debugging

<details>
<summary><b>Log File</b></summary>

Check `mcp_server_debug.log` for detailed logs, including authentication steps and API calls. Enable debug logging if needed.
</details>

<details>
<summary><b>OAuth Issues</b></summary>

- Verify `client_secret.json` is correct and present
- Ensure the correct redirect URI (`http://localhost:8000/oauth2callback`) is configured in Google Cloud Console
- Confirm the necessary APIs (Calendar, Drive, Gmail) are enabled in your Google Cloud project
- Check that `OAUTHLIB_INSECURE_TRANSPORT=1` is set in the environment where the server process runs
- Look for specific error messages during the browser-based OAuth flow
</details>

<details>
<summary><b>Tool Errors</b></summary>

Check the server logs for tracebacks or error messages returned from the Google APIs.
</details>

### Adding New Tools

1. Choose or create the appropriate module (e.g., `gdocs/gdocs_tools.py`)
2. Import necessary libraries (Google API client library, etc.)
3. Define an `async` function for your tool logic. Use type hints for parameters
4. Decorate the function with `@server.tool("your_tool_name")`
5. Inside the function, get authenticated credentials:

```python
from auth.google_auth import get_credentials, CONFIG_CLIENT_SECRETS_PATH
# ...
credentials = await asyncio.to_thread(
    get_credentials,
    user_google_email=your_user_email_variable, # Optional, can be None if session_id is primary
    required_scopes=YOUR_SPECIFIC_SCOPES_LIST,  # e.g., [CALENDAR_READONLY_SCOPE]
    client_secrets_path=CONFIG_CLIENT_SECRETS_PATH,
    session_id=your_mcp_session_id_variable    # Usually injected via Header
)
if not credentials or not credentials.valid:
    # Handle missing/invalid credentials, possibly by calling start_auth_flow
    # from auth.google_auth (which is what service-specific start_auth tools do)
    pass
```

6. Build the Google API service client: `service = build('drive', 'v3', credentials=credentials)`
7. Implement the logic to call the Google API
8. Handle potential errors gracefully
9. Return the results as a JSON-serializable dictionary or list
10. Import the tool function in [`main.py`](main.py) so it gets registered with the server
11. Define necessary service-specific scope constants in your tool's module
12. Update `pyproject.toml` if new dependencies are required

> **Scope Management**: The global `SCOPES` list in [`config/google_config.py`](config/google_config.py) is used for the initial OAuth consent screen. Individual tools should request the minimal `required_scopes` they need when calling `get_credentials`.

---

## 🔒 Security Notes

- **`client_secret.json`**: This file contains sensitive credentials. **NEVER** commit it to version control. Ensure it's listed in your `.gitignore` file. Store it securely.

- **User Tokens**: Authenticated user credentials (refresh tokens) are stored locally in files like `credentials-<user_id_hash>.json`. Protect these files as they grant access to the user's Google account data. Ensure they are also in `.gitignore`.

- **OAuth Callback Security**: The use of `http://localhost` for the OAuth callback is standard for installed applications during development but requires `OAUTHLIB_INSECURE_TRANSPORT=1`. For production deployments outside of localhost, you **MUST** use HTTPS for the callback URI and configure it accordingly in Google Cloud Console.

- **`mcpo` Security**: If using `mcpo` to expose the server over the network, consider:
  - Using the `--api-key` option for basic authentication
  - Running `mcpo` behind a reverse proxy (like Nginx or Caddy) to handle HTTPS termination, proper logging, and more robust authentication
  - Binding `mcpo` only to trusted network interfaces if exposing it beyond localhost

- **Scope Management**: The server requests specific OAuth scopes (permissions) for Calendar, Drive, and Gmail. Users grant access based on these scopes during the initial authentication. Do not request broader scopes than necessary for the implemented tools.

---

## Screenshots:
<img width="1018" alt="image" src="https://github.com/user-attachments/assets/656cea40-1f66-40c1-b94c-5a2c900c969d" />
<img src="https://github.com/user-attachments/assets/d3c2a834-fcca-4dc5-8990-6d6dc1d96048" />


## 📄 License

This project is licensed under the MIT License - see the `LICENSE` file for details.
