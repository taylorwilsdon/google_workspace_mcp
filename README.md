<!-- mcp-name: io.github.taylorwilsdon/workspace-mcp -->

<div align="center">

# <span style="color:#cad8d9">Google Workspace MCP Server</span> <img src="https://github.com/user-attachments/assets/b89524e4-6e6e-49e6-ba77-00d6df0c6e5c" width="80" align="right" />

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/workspace-mcp.svg)](https://pypi.org/project/workspace-mcp/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/workspace-mcp?period=total&units=INTERNATIONAL_SYSTEM&left_color=BLACK&right_color=BLUE&left_text=downloads)](https://pepy.tech/projects/workspace-mcp)
[![Website](https://img.shields.io/badge/Website-workspacemcp.com-green.svg)](https://workspacemcp.com)

*Full natural language control over Google Calendar, Drive, Gmail, Docs, Sheets, Slides, Forms, Tasks, Contacts, and Chat through all MCP clients, AI assistants and developer tools. Includes a full featured CLI for use with tools like Claude Code and Codex!*

**The most feature-complete Google Workspace MCP server**, with Remote OAuth2.1 multi-user support and 1-click Claude installation. With native OAuth 2.1, stateless mode and external auth server support, it's the only Workspace MCP you can host for your whole organization centrally & securely!

###### Support for all free Google accounts (Gmail, Docs, Drive etc) & Google Workspace plans (Starter, Standard, Plus, Enterprise, Non Profit) with expanded app options like Chat & Spaces. <br/><br /> Interested in a private, managed cloud instance? [That can be arranged.](https://workspacemcp.com/workspace-mcp-cloud)


</div>

<div align="center">
<a href="https://www.pulsemcp.com/servers/taylorwilsdon-google-workspace">
<img width="375" src="https://github.com/user-attachments/assets/0794ef1a-dc1c-447d-9661-9c704d7acc9d" align="center"/>
</a>
</div>

---

<div align="center">
<table>
<tr>
<td align="center">
<b>⚡ Start</b><br>
<sub>
<a href="#quick-start">Quick Start</a> · <a href="#prerequisites">Prerequisites</a><br>
<a href="#configuration">Google Cloud</a> · <a href="#-credential-configuration">Credentials</a>
</sub>
</td>
<td align="center">
<b>🧰 Tools</b><br>
<sub>
<a href="#-available-tools">All Tools</a> · <a href="#tool-tiers">Tool Tiers</a><br>
<a href="#cli">CLI</a> · <a href="#start-the-server">Start Server</a>
</sub>
</td>
<td align="center">
<b>🔌 Connect</b><br>
<sub>
<a href="#one-click-claude-desktop-install-claude-desktop-only-stdio-single-user">1-Click Install</a> · <a href="#connect-to-claude-desktop">Claude Desktop</a><br>
<a href="#claude-code-mcp-client-support">Claude Code</a> · <a href="#vs-code-mcp-client-support">VS Code</a> · <a href="#connect-to-lm-studio">LM Studio</a>
</sub>
</td>
<td align="center">
<b>🚀 Deploy</b><br>
<sub>
<a href="#oauth-21-support-multi-user-bearer-token-authentication">OAuth 2.1</a> · <a href="#stateless-mode-container-friendly">Stateless</a><br>
<a href="#external-oauth-21-provider-mode">External OAuth</a> · <a href="#reverse-proxy-setup">Reverse Proxy</a>
</sub>
</td>
<td align="center">
<b>📐 Develop</b><br>
<sub>
<a href="#-development">Architecture</a> · <a href="#local-development-setup">Dev Setup</a><br>
<a href="#-security">Security</a> · <a href="#-license">License</a>
</sub>
</td>
</tr>
</table>
</div>

**See it in action:**
<div align="center">
  <video width="400" src="https://github.com/user-attachments/assets/a342ebb4-1319-4060-a974-39d202329710"></video>
</div>

---

## <span style="color:#adbcbc">Overview</span>

Workspace MCP is the single most complete MCP server that integrates all major Google Workspace services with AI assistants. It supports both single-user operation and multi-user authentication via OAuth 2.1, making it a powerful backend for custom applications. Built with FastMCP for optimal performance, featuring advanced authentication handling, service caching, and streamlined development patterns. The entire toolset is available for CLI usage supporting both local and remote instances.

**Simplified Setup**: Now uses Google Desktop OAuth clients - no redirect URIs or port configuration needed!


## <span style="color:#adbcbc">Features</span>

> **12 services** &ensp;—&ensp; Gmail · Drive · Calendar · Docs · Sheets · Slides · Forms · Chat · Apps Script · Tasks · Contacts · Search

<table>
<tr>
<td valign="top" width="50%">

**📧 Gmail** — Complete email management, end-to-end coverage<br>
**📁 Drive** — File operations with sharing, permissions & Office formats<br>
**📅 Calendar** — Full event management with advanced features<br>
**📝 Docs** — Deep, fine-grained editing, formatting & comments<br>
**📊 Sheets** — Flexible cell management, formatting & conditional rules<br>
**🖼️ Slides** — Presentation creation, updates & content manipulation<br>
**📋 Forms** — Creation, publish settings & response management<br>
**💬 Chat** — Space management, messaging & reactions

</td>
<td valign="top" width="50%">

**⚡ Apps Script** — Cross-application workflow automation<br>
<sub>&ensp;Projects · deployments · versions · execution · debugging</sub>

**✅ Tasks** — Task & list management with hierarchy<br>
**👤 Contacts** — People API with groups & batch operations<br>
**🔍 Custom Search** — Programmable Search Engine integration

---

**🔐 Authentication & Security**<br>
<sub>OAuth 2.0 & 2.1 · auto token refresh · multi-user bearer tokens · transport-aware callbacks · CORS proxy</sub>

</td>
</tr>
</table>

---

## <span style="color:#adbcbc">Security & Compliance</span>

<table>
<tr>
<td valign="top" width="50%">

**For Security Teams**

This server sends no data anywhere except Google's APIs, on behalf of the authenticated user, using your own OAuth client credentials. There is no telemetry, no usage reporting, no analytics, no license server, and no SaaS dependency. The entire data path is: your infrastructure → Google APIs.

- **Fully open source** — every line is auditable in this repo
- **Your OAuth client, your GCP project** — credentials never leave your environment
- **You control the scopes** — read-only, granular per-service permissions, or full access
- **You control the network** — deploy behind your reverse proxy, in your VPC, on your own terms
- **No third-party services** — no intermediary servers, no token relays, no hosted backends
- **Stateless mode** — zero disk writes for locked-down container environments
- **Sensitive path blocking** — `.env`, `.ssh/`, `.aws/`, and credential files are blocked regardless of configuration

Full dependency tree in `pyproject.toml`, pinned in `uv.lock`.

</td>
<td valign="top" width="50%">

**For Legal & Procurement**

This project is [MIT licensed](LICENSE) — not "open core," not "source available," not "free with a CLA." There is no dual licensing, no commercial tier gating features, and no contributor license agreement.

- **Use commercially without restriction** — build products, sell services, deploy internally
- **Fork, embed, redistribute** — MIT requires only attribution
- **No CLA** — contributions remain under MIT
- **No telemetry to disclose** — nothing to flag in a privacy review
- **No network effects** — the server never contacts any endpoint you didn't configure
- **Standard dependency licenses** — MIT, Apache 2.0, and BSD throughout the dependency chain; no copyleft, no AGPL

The license is 21 lines and says what it means.

</td>
</tr>
</table>

---

## Quick Start

> Set credentials → pick a launch command → connect your client

<div align="center">

> 💡 **New to Workspace MCP?** Check out the **[Interactive Quick Start Guide →](https://workspacemcp.com/quick-start)** with step-by-step setup, screenshots, and troubleshooting tips!

</div>

<table>
<tr>
<td valign="top" width="50%">

**Confidential Client Quick Start**

```bash
# 1. Credentials
export GOOGLE_OAUTH_CLIENT_ID="..."
export GOOGLE_OAUTH_CLIENT_SECRET="..."

# 2. Launch — pick a tier
uvx workspace-mcp --tool-tier core       # essential tools
uvx workspace-mcp --tool-tier extended   # core + management ops
uvx workspace-mcp --tool-tier complete   # everything

# Or cherry-pick services
uv run main.py --tools gmail drive calendar
```

</td>
<td valign="top" width="50%">

**Secretless / Public OAuth 2.1 (PKCE) Quick Start**

```bash
# 1. Credentials
export MCP_ENABLE_OAUTH21=true
export GOOGLE_OAUTH_CLIENT_ID="..."
export WORKSPACE_MCP_PORT=8000
export GOOGLE_OAUTH_REDIRECT_URI="http://localhost:${WORKSPACE_MCP_PORT}/oauth2callback"
export OAUTHLIB_INSECURE_TRANSPORT=1
# Leave GOOGLE_OAUTH_CLIENT_SECRET unset for public PKCE clients
export FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY="$(openssl rand -hex 32)"

# 2. Launch — OAuth 2.1 requires HTTP transport
uvx workspace-mcp --transport streamable-http --tool-tier core
uvx workspace-mcp --transport streamable-http --tool-tier extended
uvx workspace-mcp --transport streamable-http --tool-tier complete

# Or cherry-pick services
uv run main.py --transport streamable-http --tools gmail drive calendar
```

</td>
</tr>
</table>

<sub>[Credential setup →](#-credential-configuration) · [All launch options →](#start-the-server) · [Tier details →](#tool-tiers)</sub>

<details open>
<summary><b>Environment Variable Reference</b></summary>
<sub>

| Variable | | Purpose |
|----------|:---:|---------|
| **🔐 Authentication** | | |
| `GOOGLE_OAUTH_CLIENT_ID` | **required** | OAuth client ID from Google Cloud |
| `GOOGLE_OAUTH_CLIENT_SECRET` | | OAuth client secret for confidential clients; optional for public OAuth 2.1 PKCE clients |
| `OAUTHLIB_INSECURE_TRANSPORT` | **required**&ast; | Set to `1` for development — allows `http://` redirect |
| `USER_GOOGLE_EMAIL` | | Default email for single-user auth |
| `GOOGLE_CLIENT_SECRET_PATH` | | Custom path to `client_secret.json` |
| `GOOGLE_MCP_CREDENTIALS_DIR` | | Credential directory — default `~/.google_workspace_mcp/credentials` |
| **🖥️ Server** | | |
| `WORKSPACE_MCP_BASE_URI` | | Base server URI (no port) — default `http://localhost` |
| `WORKSPACE_MCP_PORT` | | Listening port — default `8000` |
| `WORKSPACE_MCP_HOST` | | Bind host — default `0.0.0.0` |
| `WORKSPACE_EXTERNAL_URL` | | External URL for reverse proxy setups |
| `WORKSPACE_ATTACHMENT_DIR` | | Downloaded attachments dir — default `~/.workspace-mcp/attachments/` |
| `WORKSPACE_MCP_URL` | | Remote MCP endpoint URL for CLI |
| `ALLOWED_FILE_DIRS` | | Colon-separated allowlist for local file reads |
| **🔑 OAuth 2.1 & Multi-User** | | |
| `MCP_ENABLE_OAUTH21` | | `true` to enable OAuth 2.1 multi-user support |
| `EXTERNAL_OAUTH21_PROVIDER` | | `true` for external OAuth flow with bearer tokens |
| `WORKSPACE_MCP_STATELESS_MODE` | | `true` for stateless container-friendly operation |
| `GOOGLE_OAUTH_REDIRECT_URI` | | Override OAuth callback URL — default auto-constructed |
| `OAUTH_CUSTOM_REDIRECT_URIS` | | Comma-separated additional redirect URIs |
| `OAUTH_ALLOWED_ORIGINS` | | Comma-separated additional CORS origins |
| `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND` | | `memory`, `disk`, or `valkey` — see [storage backends](#oauth-proxy-storage-backends) |
| `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` | | Custom encryption key for OAuth proxy storage; required for public OAuth 2.1 clients when `GOOGLE_OAUTH_CLIENT_SECRET` is omitted |
| **🔧 Service Account** | | |
| `GOOGLE_SERVICE_ACCOUNT_KEY_FILE` | | Path to service account JSON key file (domain-wide delegation) |
| `GOOGLE_SERVICE_ACCOUNT_KEY_JSON` | | Inline service account JSON key (alternative to file) |
| **🔍 Custom Search** | | |
| `GOOGLE_PSE_API_KEY` | | API key for Programmable Search Engine |
| `GOOGLE_PSE_ENGINE_ID` | | Search Engine ID for PSE |

&ast;Required for development only. Claude Desktop stores credentials securely in the OS keychain — set them once in the extension pane.

</sub>
</details>

---

### One-Click Claude Desktop Install

> `.dxt` bundles server, deps & manifest — download → double-click → done. No terminal, no JSON editing.

1. **Download** the latest `google_workspace_mcp.dxt` from [Releases](https://github.com/taylorwilsdon/google_workspace_mcp/releases)
2. **Install** — double-click the file, Claude Desktop prompts to install
3. **Configure** — Settings → Extensions → Google Workspace MCP, paste your OAuth credentials
4. **Use it** — start a new Claude chat and call any Google Workspace tool

<div align="center">
  <video width="832" src="https://github.com/user-attachments/assets/83cca4b3-5e94-448b-acb3-6e3a27341d3a"></video>
</div>

---

### Prerequisites

**Python 3.10+** · **[uv/uvx](https://github.com/astral-sh/uv)** · **Google Cloud Project** with OAuth 2.0 credentials

### Configuration

<details open>
<summary><b>Google Cloud Setup</b></summary>

1. **Create Project** — [Open Console →](https://console.cloud.google.com/) → Create new project
2. **Create OAuth Credentials** — APIs & Services → Credentials → Create Credentials → OAuth Client ID
   - Choose **Desktop Application** for a public PKCE client (no redirect URIs needed) or **Web Application** for a confidential client
   - Download and note your Client ID and, if issued, Client Secret
3. **Enable APIs** — APIs & Services → Library, then enable each service:

   | | | | |
   |:--|:--|:--|:--|
   | [Calendar](https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com) | [Drive](https://console.cloud.google.com/flows/enableapi?apiid=drive.googleapis.com) | [Gmail](https://console.cloud.google.com/flows/enableapi?apiid=gmail.googleapis.com) | [Docs](https://console.cloud.google.com/flows/enableapi?apiid=docs.googleapis.com) |
   | [Sheets](https://console.cloud.google.com/flows/enableapi?apiid=sheets.googleapis.com) | [Slides](https://console.cloud.google.com/flows/enableapi?apiid=slides.googleapis.com) | [Forms](https://console.cloud.google.com/flows/enableapi?apiid=forms.googleapis.com) | [Tasks](https://console.cloud.google.com/flows/enableapi?apiid=tasks.googleapis.com) |
   | [Chat](https://console.cloud.google.com/flows/enableapi?apiid=chat.googleapis.com) | [People](https://console.cloud.google.com/flows/enableapi?apiid=people.googleapis.com) | [Custom Search](https://console.cloud.google.com/flows/enableapi?apiid=customsearch.googleapis.com) | [Apps Script](https://console.cloud.google.com/flows/enableapi?apiid=script.googleapis.com) |

4. **Set Credentials** — see [Environment Variable Reference](#quick-start) above, or:
   ```bash
   export GOOGLE_OAUTH_CLIENT_ID="your-client-id"
   export GOOGLE_OAUTH_CLIENT_SECRET="your-secret"
   ```
   For public OAuth 2.1 PKCE clients, omit `GOOGLE_OAUTH_CLIENT_SECRET` and set `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` instead.

<sub>[Full OAuth documentation →](https://developers.google.com/workspace/guides/auth-overview) · [Credential setup details →](#-credential-configuration)</sub>

</details>

### Google Custom Search Setup

<details open>
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

<details open>
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

> **📌 Transport Mode Guidance**: Use **streamable HTTP mode** (`--transport streamable-http`) for all modern MCP clients including Claude Code, VS Code MCP, and MCP Inspector. Stdio mode is only for clients with incomplete MCP specification support.

<details open>
<summary>▶ <b>Launch Commands</b> <sub><sup>← Choose your startup mode</sup></sub></summary>

<table>
<tr>
<td width="33%" align="center">

**▶ Legacy Mode**
```bash
uv run main.py
```
<sub>⚠️ Stdio mode (incomplete MCP clients only)</sub>

</td>
<td width="33%" align="center">

**◆ HTTP Mode (Recommended)**
```bash
uv run main.py \
  --transport streamable-http
```
<sub>✅ Full MCP spec compliance & OAuth 2.1</sub>

</td>
<td width="34%" align="center">

**@ Single User**
```bash
uv run main.py \
  --single-user
```
<sub>Simplified authentication</sub>
<sub>⚠️ Cannot be used with OAuth 2.1 mode</sub>

</td>
</tr>
<tr>
<td colspan="3">

<details open>
<summary>◆ <b>Advanced Options</b> <sub><sup>← Tool selection, tiers & Docker</sup></sub></summary>

**▶ Selective Tool Loading**
```bash
# Load specific services only
uv run main.py --tools gmail drive calendar
uv run main.py --tools sheets docs

# Combine with other flags
uv run main.py --single-user --tools gmail
```


**🔒 Read-Only Mode**
```bash
# Requests only read-only scopes & disables write tools
uv run main.py --read-only

# Combine with specific tools or tiers
uv run main.py --tools gmail drive --read-only
uv run main.py --tool-tier core --read-only
```
Read-only mode provides secure, restricted access by:
- Requesting only `*.readonly` OAuth scopes (e.g., `gmail.readonly`, `drive.readonly`)
- Automatically filtering out tools that require write permissions at startup
- Allowing read operations: list, get, search, and export across all services

**🔐 Granular Permissions**
```bash
# Per-service permission levels
uv run main.py --permissions gmail:organize drive:readonly

# Combine permissions with tier filtering
uv run main.py --permissions gmail:send drive:full --tool-tier core
```
Granular permissions mode provides service-by-service scope control:
- Format: `service:level` (one entry per service)
- Gmail levels: `readonly`, `organize`, `drafts`, `send`, `full` (cumulative)
- Tasks levels: `readonly`, `manage`, `full` (cumulative; `manage` allows create/update/move but denies `delete` and `clear_completed`)
- Other services currently support: `readonly`, `full`
- `--permissions` and `--read-only` are mutually exclusive
- `--permissions` cannot be combined with `--tools`; enabled services are determined by the `--permissions` entries (optionally filtered by `--tool-tier`)
- With `--tool-tier`, only tier-matched tools are enabled and only services that have tools in the selected tier are imported

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

**Available Services**: `gmail` • `drive` • `calendar` • `docs` • `sheets` • `forms` • `tasks` • `contacts` • `chat` • `search`

</details>

</td>
</tr>
</table>

</details>

### CLI

The `workspace-cli` command lists tools and calls them against a running server — with encrypted, disk-backed OAuth token caching so you only authenticate once. On first run it opens a browser for Google consent; subsequent runs reuse the cached tokens automatically.

Tokens are stored encrypted at `~/.workspace-mcp/cli-tokens/` using a Fernet key auto-generated at `~/.workspace-mcp/.cli-encryption-key`.

<details open>
<summary>▶ <b>workspace-cli Commands</b> <sub><sup>← Persistent OAuth, no re-auth on every call</sup></sub></summary>

<table>
<tr>
<td width="50%" align="center">

**▶ List Tools**
```bash
uv run workspace-cli list
uv run workspace-cli --url https://custom.server/mcp list

# Or, if installed globally:
workspace-cli list
workspace-cli --url https://custom.server/mcp list
```
<sub>View all available tools</sub>

</td>
<td width="50%" align="center">

**◆ Call a Tool**
```bash
uv run workspace-cli call search_gmail_messages \
  query="is:unread" max_results=5
```
<sub>Execute a tool with key=value arguments</sub>

</td>
</tr>
</table>

Set URL for remote endpoints with `--url` or the `WORKSPACE_MCP_URL` environment variable.

<details open>
<summary>≡ <b>Advanced: FastMCP CLI</b> <sub><sup>← inspect, install, discover</sup></sub></summary>

The upstream [FastMCP CLI](https://gofastmcp.com/cli) is also bundled and provides additional commands for schema inspection, client installation, and editor discovery. Note that `fastmcp` uses in-memory token storage, so each invocation may re-trigger the OAuth flow.

```bash
fastmcp inspect fastmcp_server.py                        # print tools, resources, prompts
fastmcp install claude-code fastmcp_server.py             # one-command client setup
fastmcp install cursor fastmcp_server.py
fastmcp discover                                          # find servers configured in editors
```

See `fastmcp --help` or the [FastMCP CLI docs](https://gofastmcp.com/cli) for the full command reference.

</details>

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

# Combine tier selection with granular permission levels
uv run main.py --permissions gmail:organize drive:full --tool-tier core
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

<details open>
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

<div align="center">

> 📖 **Looking for detailed parameters?** Visit the **[Complete Documentation →](https://workspacemcp.com/docs)** for comprehensive tool reference, examples, and API guides!

</div>

#### 📅 Google Calendar <sub>[`calendar_tools.py`](gcalendar/calendar_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`list_calendars`</sub> | <sub>Core</sub> | <sub>List accessible calendars</sub> |
| <sub>`get_events`</sub> | <sub>Core</sub> | <sub>Retrieve events with time range filtering</sub> |
| <sub>`manage_event`</sub> | <sub>Core</sub> | <sub>Create, update, or delete calendar events</sub> |
| <sub>`create_calendar`</sub> | <sub>Extended</sub> | <sub>Create a new secondary Google Calendar</sub> |
| <sub>`query_freebusy`</sub> | <sub>Extended</sub> | <sub>Query free/busy information for calendars</sub> |
| <sub>`manage_out_of_office`</sub> | <sub>Extended</sub> | <sub>Create, list, update, or delete Out of Office events</sub> |
| <sub>`manage_focus_time`</sub> | <sub>Extended</sub> | <sub>Create, list, update, or delete Focus Time events</sub> |

#### 📁 Google Drive <sub>[`drive_tools.py`](gdrive/drive_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`search_drive_files`</sub> | <sub>Core</sub> | <sub>Search files with query syntax</sub> |
| <sub>`get_drive_file_content`</sub> | <sub>Core</sub> | <sub>Read file content (Office formats)</sub> |
| <sub>`get_drive_file_download_url`</sub> | <sub>Core</sub> | <sub>Download Drive files to local disk</sub> |
| <sub>`create_drive_file`</sub> | <sub>Core</sub> | <sub>Create files or fetch from URLs</sub> |
| <sub>`create_drive_folder`</sub> | <sub>Core</sub> | <sub>Create empty folders in Drive or shared drives</sub> |
| <sub>`import_to_google_doc`</sub> | <sub>Core</sub> | <sub>Import files (MD, DOCX, HTML, etc.) as Google Docs</sub> |
| <sub>`get_drive_shareable_link`</sub> | <sub>Core</sub> | <sub>Get shareable links for a file</sub> |
| <sub>`list_drive_items`</sub> | <sub>Extended</sub> | <sub>List folder contents</sub> |
| <sub>`copy_drive_file`</sub> | <sub>Extended</sub> | <sub>Copy existing files (templates) with optional renaming</sub> |
| <sub>`update_drive_file`</sub> | <sub>Extended</sub> | <sub>Update file metadata, move between folders</sub> |
| <sub>`manage_drive_access`</sub> | <sub>Extended</sub> | <sub>Grant, update, revoke permissions, and transfer ownership</sub> |
| <sub>`set_drive_file_permissions`</sub> | <sub>Extended</sub> | <sub>Set link sharing and file-level sharing settings</sub> |
| <sub>`get_drive_file_permissions`</sub> | <sub>Complete</sub> | <sub>Get detailed file permissions</sub> |
| <sub>`check_drive_file_public_access`</sub> | <sub>Complete</sub> | <sub>Check public sharing status</sub> |

#### 📧 Gmail <sub>[`gmail_tools.py`](gmail/gmail_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`search_gmail_messages`</sub> | <sub>Core</sub> | <sub>Search with Gmail operators</sub> |
| <sub>`get_gmail_message_content`</sub> | <sub>Core</sub> | <sub>Retrieve message content</sub> |
| <sub>`get_gmail_messages_content_batch`</sub> | <sub>Core</sub> | <sub>Batch retrieve message content</sub> |
| <sub>`send_gmail_message`</sub> | <sub>Core</sub> | <sub>Send emails</sub> |
| <sub>`get_gmail_thread_content`</sub> | <sub>Extended</sub> | <sub>Get full thread content</sub> |
| <sub>`modify_gmail_message_labels`</sub> | <sub>Extended</sub> | <sub>Modify message labels</sub> |
| <sub>`list_gmail_labels`</sub> | <sub>Extended</sub> | <sub>List available labels</sub> |
| <sub>`list_gmail_filters`</sub> | <sub>Extended</sub> | <sub>List Gmail filters</sub> |
| <sub>`manage_gmail_label`</sub> | <sub>Extended</sub> | <sub>Create/update/delete labels</sub> |
| <sub>`manage_gmail_filter`</sub> | <sub>Extended</sub> | <sub>Create or delete Gmail filters</sub> |
| <sub>`draft_gmail_message`</sub> | <sub>Extended</sub> | <sub>Create drafts</sub> |
| <sub>`get_gmail_threads_content_batch`</sub> | <sub>Complete</sub> | <sub>Batch retrieve thread content</sub> |
| <sub>`batch_modify_gmail_message_labels`</sub> | <sub>Complete</sub> | <sub>Batch modify labels</sub> |
| <sub>`start_google_auth`</sub> | <sub>Complete</sub> | <sub>Legacy OAuth 2.0 auth (disabled when OAuth 2.1 is enabled)</sub> |

<details open>
<summary><b>📎 Email Attachments</b> <sub><sup>← Send emails with files</sup></sub></summary>

Both `send_gmail_message` and `draft_gmail_message` support attachments via two methods:

**Option 1: File Path** (local server only)
```python
attachments=[{"path": "/path/to/report.pdf"}]
```
Reads file from disk, auto-detects MIME type. Optional `filename` override.

**Option 2: Base64 Content** (works everywhere)
```python
attachments=[{
    "filename": "report.pdf",
    "content": "JVBERi0xLjQK...",  # base64-encoded
    "mime_type": "application/pdf"   # optional
}]
```

**⚠️ Centrally Hosted Servers**: When the MCP server runs remotely (cloud, shared instance), it cannot access your local filesystem. Use **Option 2** with base64-encoded content. Your MCP client must encode files before sending.

</details>

<details open>
<summary><b>📥 Downloaded Attachment Storage</b> <sub><sup>← Where downloaded files are saved</sup></sub></summary>

When downloading Gmail attachments (`get_gmail_attachment_content`) or Drive files (`get_drive_file_download_url`), files are saved to a persistent local directory rather than a temporary folder in the working directory.

**Default location:** `~/.workspace-mcp/attachments/`

Files are saved with their original filename plus a short UUID suffix for uniqueness (e.g., `invoice_a1b2c3d4.pdf`). In **stdio mode**, the tool returns the absolute file path for direct filesystem access. In **HTTP mode**, it returns a download URL via the `/attachments/{file_id}` endpoint.

To customize the storage directory:
```bash
export WORKSPACE_ATTACHMENT_DIR="/path/to/custom/dir"
```

Saved files expire after 1 hour and are cleaned up automatically.

</details>

#### 📝 Google Docs <sub>[`docs_tools.py`](gdocs/docs_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`get_doc_content`</sub> | <sub>Core</sub> | <sub>Extract document text</sub> |
| <sub>`create_doc`</sub> | <sub>Core</sub> | <sub>Create new documents</sub> |
| <sub>`modify_doc_text`</sub> | <sub>Core</sub> | <sub>Insert, replace, and richly format text with tab/segment targeting, append-to-segment support, advanced typography, and link management</sub> |
| <sub>`search_docs`</sub> | <sub>Extended</sub> | <sub>Find documents by name</sub> |
| <sub>`find_and_replace_doc`</sub> | <sub>Extended</sub> | <sub>Find and replace text</sub> |
| <sub>`list_docs_in_folder`</sub> | <sub>Extended</sub> | <sub>List docs in folder</sub> |
| <sub>`insert_doc_elements`</sub> | <sub>Extended</sub> | <sub>Add tables, lists, page breaks</sub> |
| <sub>`update_paragraph_style`</sub> | <sub>Extended</sub> | <sub>Apply advanced paragraph styling including headings, spacing, direction, pagination controls, shading, and bulleted/numbered/checkbox lists with nesting</sub> |
| <sub>`get_doc_as_markdown`</sub> | <sub>Extended</sub> | <sub>Export document as formatted Markdown with optional comments</sub> |
| <sub>`insert_doc_image`</sub> | <sub>Complete</sub> | <sub>Insert images from Drive/URLs</sub> |
| <sub>`update_doc_headers_footers`</sub> | <sub>Complete</sub> | <sub>Create or update headers and footers with correct segment-aware writes</sub> |
| <sub>`batch_update_doc`</sub> | <sub>Complete</sub> | <sub>Execute atomic multi-step Docs API operations including named ranges, section breaks, document/section layout, header/footer creation, segment-aware inserts, images, tables, and rich formatting</sub> |
| <sub>`inspect_doc_structure`</sub> | <sub>Complete</sub> | <sub>Analyze document structure, including safe insertion points, tables, section breaks, headers/footers, and named ranges</sub> |
| <sub>`export_doc_to_pdf`</sub> | <sub>Extended</sub> | <sub>Export document to PDF</sub> |
| <sub>`create_table_with_data`</sub> | <sub>Complete</sub> | <sub>Create data tables</sub> |
| <sub>`debug_table_structure`</sub> | <sub>Complete</sub> | <sub>Debug table issues</sub> |
| <sub>`list_document_comments`</sub> | <sub>Complete</sub> | <sub>List all document comments</sub> |
| <sub>`manage_document_comment`</sub> | <sub>Complete</sub> | <sub>Create, reply to, or resolve comments</sub> |

#### 📊 Google Sheets <sub>[`sheets_tools.py`](gsheets/sheets_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`read_sheet_values`</sub> | <sub>Core</sub> | <sub>Read cell ranges</sub> |
| <sub>`modify_sheet_values`</sub> | <sub>Core</sub> | <sub>Write/update/clear cells</sub> |
| <sub>`create_spreadsheet`</sub> | <sub>Core</sub> | <sub>Create new spreadsheets</sub> |
| <sub>`list_spreadsheets`</sub> | <sub>Extended</sub> | <sub>List accessible spreadsheets</sub> |
| <sub>`get_spreadsheet_info`</sub> | <sub>Extended</sub> | <sub>Get spreadsheet metadata</sub> |
| <sub>`format_sheet_range`</sub> | <sub>Extended</sub> | <sub>Apply colors, number formats, text wrapping, alignment, bold/italic, font size</sub> |
| <sub>`list_sheet_tables`</sub> | <sub>Extended</sub> | <sub>List structured tables with IDs, names, ranges, and columns</sub> |
| <sub>`create_sheet`</sub> | <sub>Complete</sub> | <sub>Add sheets to existing files</sub> |
| <sub>`append_table_rows`</sub> | <sub>Complete</sub> | <sub>Append rows to a structured table, auto-extending the table range</sub> |
| <sub>`list_spreadsheet_comments`</sub> | <sub>Complete</sub> | <sub>List all spreadsheet comments</sub> |
| <sub>`manage_spreadsheet_comment`</sub> | <sub>Complete</sub> | <sub>Create, reply to, or resolve comments</sub> |
| <sub>`manage_conditional_formatting`</sub> | <sub>Complete</sub> | <sub>Add, update, or delete conditional formatting rules</sub> |

#### 🖼️ Google Slides <sub>[`slides_tools.py`](gslides/slides_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`create_presentation`</sub> | <sub>Core</sub> | <sub>Create new presentations</sub> |
| <sub>`get_presentation`</sub> | <sub>Core</sub> | <sub>Retrieve presentation details</sub> |
| <sub>`batch_update_presentation`</sub> | <sub>Extended</sub> | <sub>Apply multiple updates</sub> |
| <sub>`get_page`</sub> | <sub>Extended</sub> | <sub>Get specific slide information</sub> |
| <sub>`get_page_thumbnail`</sub> | <sub>Extended</sub> | <sub>Generate slide thumbnails</sub> |
| <sub>`list_presentation_comments`</sub> | <sub>Complete</sub> | <sub>List all presentation comments</sub> |
| <sub>`manage_presentation_comment`</sub> | <sub>Complete</sub> | <sub>Create, reply to, or resolve comments</sub> |

#### 📋 Google Forms <sub>[`forms_tools.py`](gforms/forms_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`create_form`</sub> | <sub>Core</sub> | <sub>Create new forms</sub> |
| <sub>`get_form`</sub> | <sub>Core</sub> | <sub>Retrieve form details & URLs</sub> |
| <sub>`set_publish_settings`</sub> | <sub>Complete</sub> | <sub>Configure form settings</sub> |
| <sub>`get_form_response`</sub> | <sub>Complete</sub> | <sub>Get individual responses</sub> |
| <sub>`list_form_responses`</sub> | <sub>Extended</sub> | <sub>List all responses with pagination</sub> |
| <sub>`batch_update_form`</sub> | <sub>Complete</sub> | <sub>Apply batch updates (questions, settings)</sub> |

#### ✓ Google Tasks <sub>[`tasks_tools.py`](gtasks/tasks_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`list_tasks`</sub> | <sub>Core</sub> | <sub>List tasks with filtering</sub> |
| <sub>`get_task`</sub> | <sub>Core</sub> | <sub>Retrieve task details</sub> |
| <sub>`manage_task`</sub> | <sub>Core</sub> | <sub>Create, update, delete, or move tasks</sub> |
| <sub>`list_task_lists`</sub> | <sub>Complete</sub> | <sub>List task lists</sub> |
| <sub>`get_task_list`</sub> | <sub>Complete</sub> | <sub>Get task list details</sub> |
| <sub>`manage_task_list`</sub> | <sub>Complete</sub> | <sub>Create, update, delete task lists, or clear completed tasks</sub> |

#### 👤 Google Contacts <sub>[`contacts_tools.py`](gcontacts/contacts_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`search_contacts`</sub> | <sub>Core</sub> | <sub>Search contacts by name, email, phone</sub> |
| <sub>`get_contact`</sub> | <sub>Core</sub> | <sub>Retrieve detailed contact info</sub> |
| <sub>`list_contacts`</sub> | <sub>Core</sub> | <sub>List contacts with pagination</sub> |
| <sub>`manage_contact`</sub> | <sub>Core</sub> | <sub>Create, update, or delete contacts</sub> |
| <sub>`list_contact_groups`</sub> | <sub>Extended</sub> | <sub>List contact groups/labels</sub> |
| <sub>`get_contact_group`</sub> | <sub>Extended</sub> | <sub>Get group details with members</sub> |
| <sub>`manage_contacts_batch`</sub> | <sub>Complete</sub> | <sub>Batch create, update, or delete contacts</sub> |
| <sub>`manage_contact_group`</sub> | <sub>Complete</sub> | <sub>Create, update, delete groups, or modify membership</sub> |

#### 💬 Google Chat <sub>[`chat_tools.py`](gchat/chat_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`list_spaces`</sub> | <sub>Extended</sub> | <sub>List chat spaces/rooms</sub> |
| <sub>`get_messages`</sub> | <sub>Core</sub> | <sub>Retrieve space messages</sub> |
| <sub>`send_message`</sub> | <sub>Core</sub> | <sub>Send messages to spaces</sub> |
| <sub>`search_messages`</sub> | <sub>Core</sub> | <sub>Search across chat history</sub> |
| <sub>`create_reaction`</sub> | <sub>Core</sub> | <sub>Add emoji reaction to a message</sub> |
| <sub>`download_chat_attachment`</sub> | <sub>Extended</sub> | <sub>Download attachment from a chat message</sub> |

#### 🔍 Google Custom Search <sub>[`search_tools.py`](gsearch/search_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`search_custom`</sub> | <sub>Core</sub> | <sub>Perform web searches (supports site restrictions via sites parameter)</sub> |
| <sub>`get_search_engine_info`</sub> | <sub>Complete</sub> | <sub>Retrieve search engine metadata</sub> |

#### ⚡ Google Apps Script <sub>[`apps_script_tools.py`](gappsscript/apps_script_tools.py)</sub>

| <sub>Tool</sub> | <sub>Tier</sub> | <sub>Description</sub> |
|------|------|-------------|
| <sub>`list_script_projects`</sub> | <sub>Core</sub> | <sub>List accessible Apps Script projects</sub> |
| <sub>`get_script_project`</sub> | <sub>Core</sub> | <sub>Get complete project with all files</sub> |
| <sub>`get_script_content`</sub> | <sub>Core</sub> | <sub>Retrieve specific file content</sub> |
| <sub>`create_script_project`</sub> | <sub>Core</sub> | <sub>Create new standalone or bound project</sub> |
| <sub>`update_script_content`</sub> | <sub>Core</sub> | <sub>Update or create script files</sub> |
| <sub>`run_script_function`</sub> | <sub>Core</sub> | <sub>Execute function with parameters</sub> |
| <sub>`list_deployments`</sub> | <sub>Extended</sub> | <sub>List all project deployments</sub> |
| <sub>`manage_deployment`</sub> | <sub>Extended</sub> | <sub>Create, update, or delete script deployments</sub> |
| <sub>`list_script_processes`</sub> | <sub>Extended</sub> | <sub>View recent executions and status</sub> |

<sub>

**Tool Tier Legend:**<br>
<span style="color:#2d5b69">●</span> **Core** — Essential tools for basic functionality · Minimal API usage · Getting started<br>
<span style="color:#72898f">●</span> **Extended** — Core + additional features · Regular usage · Expanded capabilities<br>
<span style="color:#adbcbc">●</span> **Complete** — All available tools including advanced features · Power users · Full API access

</sub>

---

### Connect to Claude Desktop

The server supports two transport modes:

#### Stdio Mode (Legacy - For Clients with Incomplete MCP Support)

> **⚠️ Important**: Stdio mode is a **legacy fallback** for clients that don't properly implement the MCP specification with OAuth 2.1 and streamable HTTP support. **Claude Code and other modern MCP clients should use streamable HTTP mode** (`--transport streamable-http`) for proper OAuth flow and multi-user support.

In general, you should use the one-click DXT installer package for Claude Desktop.
If you are unable to for some reason, you can configure it manually via `claude_desktop_config.json`

**Manual Claude Configuration (Alternative)**

<details open>
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

### Connect to LM Studio

Add a new MCP server in LM Studio (Settings → MCP Servers) using the same JSON format:

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
      }
    }
  }
}
```


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

The server includes OAuth 2.1 support for bearer token authentication, enabling multi-user session management. **OAuth 2.1 automatically reuses your existing `GOOGLE_OAUTH_CLIENT_ID` and, for confidential clients, `GOOGLE_OAUTH_CLIENT_SECRET` credentials** - no additional Google-side configuration needed. Public PKCE clients are also supported: if you omit `GOOGLE_OAUTH_CLIENT_SECRET`, set `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` explicitly.

**When to use OAuth 2.1:**
- Multiple users accessing the same MCP server instance
- Need for bearer token authentication instead of passing user emails
- Building web applications or APIs on top of the MCP server
- Production environments requiring secure session management
- Browser-based clients requiring CORS support

**⚠️ Important: Mutually exclusive authentication modes**

OAuth 2.1 mode (`MCP_ENABLE_OAUTH21=true`) cannot be used together with `--single-user` or service account mode:
- **Single-user mode**: For legacy clients that pass user emails in tool calls
- **OAuth 2.1 mode**: For modern multi-user scenarios with bearer token authentication
- **Service account mode**: For headless/server-to-server use via domain-wide delegation

Choose one authentication method - combining incompatible modes will result in a startup error.

**Enabling OAuth 2.1:**
To enable OAuth 2.1, set the `MCP_ENABLE_OAUTH21` environment variable to `true`.

```bash
# OAuth 2.1 requires HTTP transport mode
export MCP_ENABLE_OAUTH21=true
uv run main.py --transport streamable-http
```

If `MCP_ENABLE_OAUTH21` is not set to `true`, the server will use legacy authentication, which is suitable for clients that do not support OAuth 2.1.

<details open>
<summary>🔐 <b>How the FastMCP GoogleProvider handles OAuth</b> <sub><sup>← Advanced OAuth 2.1 details</sup></sub></summary>

FastMCP ships a native `GoogleProvider` that we now rely on directly. It solves the two tricky parts of using Google OAuth with MCP clients:

1.  **Dynamic Client Registration**: Google still doesn't support OAuth 2.1 DCR, but the FastMCP provider exposes the full DCR surface and forwards registrations to Google using your fixed credentials. MCP clients register as usual and the provider hands them your Google client ID and, when configured, client secret under the hood.

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

### OAuth Proxy Storage Backends

The server supports pluggable storage backends for OAuth proxy state management via FastMCP 2.13.0+. Choose a backend based on your deployment needs.

**Available Backends:**

| Backend | Best For | Persistence | Multi-Server |
|---------|----------|-------------|--------------|
| Memory | Development, testing | ❌ | ❌ |
| Disk | Single-server production | ✅ | ❌ |
| Valkey/Redis | Distributed production | ✅ | ✅ |

**Configuration:**

```bash
# Memory storage (fast, no persistence)
export WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND=memory

# Disk storage (persists across restarts)
export WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND=disk
export WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY=~/.fastmcp/oauth-proxy

# Valkey/Redis storage (distributed, multi-server)
export WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND=valkey
export WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST=redis.example.com
export WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PORT=6379
```

> Disk support requires `workspace-mcp[disk]` (or `py-key-value-aio[disk]`) when installing from source.
> The official Docker image includes the `disk` extra by default.
> Valkey support is optional. Install `workspace-mcp[valkey]` (or `py-key-value-aio[valkey]`) only if you enable the Valkey backend.
> Windows: building `valkey-glide` from source requires MSVC C++ build tools with C11 support. If you see `aws-lc-sys` C11 errors, set `CFLAGS=/std:c11`.

<details open>
<summary>🔐 <b>Valkey/Redis Configuration Options</b></summary>

| Variable | Default | Description |
|----------|---------|-------------|
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST` | localhost | Valkey/Redis host |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PORT` | 6379 | Port (6380 auto-enables TLS) |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_DB` | 0 | Database number |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USE_TLS` | auto | Enable TLS (auto if port 6380) |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_USERNAME` | - | Authentication username |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_PASSWORD` | - | Authentication password |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_REQUEST_TIMEOUT_MS` | 5000 | Request timeout for remote hosts |
| `WORKSPACE_MCP_OAUTH_PROXY_VALKEY_CONNECTION_TIMEOUT_MS` | 10000 | Connection timeout for remote hosts |

**Encryption:** Disk and Valkey storage are encrypted with Fernet. The encryption key is derived from `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` if set, otherwise from `GOOGLE_OAUTH_CLIENT_SECRET`. Public OAuth 2.1 client setups without a client secret must set `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY`.

</details>

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
- **Protocol-level auth enabled**: All MCP requests (including `initialize` and `tools/list`) require a valid Bearer token, following the standard OAuth 2.1 flow. Unauthenticated requests receive a `401` with resource metadata pointing to Google's authorization server.
- **External OAuth flow**: Your external system handles the OAuth flow and obtains Google access tokens (`ya29.*`)
- **Token validation**: Server validates bearer tokens by calling Google's userinfo API
- **Multi-user support**: Each request is authenticated independently based on its bearer token
- **Resource metadata discovery**: The server serves `/.well-known/oauth-protected-resource` (RFC 9728) advertising Google as the authorization server and the required scopes

**Key Features:**
- **No local OAuth flow**: Server does not provide `/authorize`, `/token`, or `/register` endpoints — only resource metadata
- **Bearer token only**: All authentication via `Authorization: Bearer <token>` headers
- **Stateless by design**: Works seamlessly with `WORKSPACE_MCP_STATELESS_MODE=true`
- **External identity providers**: Integrate with your existing authentication infrastructure

**Requirements:**
- Must be used with `MCP_ENABLE_OAUTH21=true`
- OAuth client ID still required for token validation; client secret is optional for public clients (`GOOGLE_OAUTH_CLIENT_ID`, optional `GOOGLE_OAUTH_CLIENT_SECRET`)
- External system must obtain valid Google OAuth access tokens (ya29.*)
- Each tool call request must include valid bearer token

**Use Cases:**
- Integrating with existing authentication systems
- Custom OAuth flows managed by your application
- API gateways that handle authentication upstream
- Multi-tenant SaaS applications with centralized auth
- Mobile or web apps with their own OAuth implementation


### Service Account Mode (Domain-Wide Delegation)

> **WARNING: This mode uses Google Workspace domain-wide delegation, which grants the service account the ability to impersonate any user in your domain for the configured scopes. This is powerful and dangerous — do not use this unless you fully understand the security implications. A misconfigured service account with broad scopes can read, modify, and delete data across every user in your organization. Only use this in tightly controlled environments where you know exactly what you're doing.**

Service account mode allows the server to authenticate using a Google Cloud service account with domain-wide delegation instead of interactive OAuth flows. The service account impersonates a single configured domain user for all API calls.

**When to use service account mode:**
- Headless or unattended environments where no browser is available for OAuth consent
- Server-to-server integrations that need to act on behalf of a specific domain user
- CI/CD pipelines or automation scripts
- Environments where you cannot or do not want to manage per-user OAuth tokens

**Enabling Service Account Mode:**

```bash
# Option 1: Key file on disk
export GOOGLE_SERVICE_ACCOUNT_KEY_FILE="/path/to/service-account-key.json"
export USER_GOOGLE_EMAIL="user@yourdomain.com"
uv run main.py

# Option 2: Inline JSON key (e.g., from a secret manager)
export GOOGLE_SERVICE_ACCOUNT_KEY_JSON='{"type":"service_account","project_id":"...","private_key":"...","client_email":"..."}'
export USER_GOOGLE_EMAIL="user@yourdomain.com"
uv run main.py
```

**Prerequisites:**
1. A Google Cloud service account with a JSON key
2. Domain-wide delegation enabled for the service account in your Google Workspace Admin Console (Security → API controls → Domain-wide delegation)
3. The required OAuth scopes authorized for the service account's client ID in the Admin Console
4. `USER_GOOGLE_EMAIL` set to the domain user the service account will impersonate

**Incompatibilities:**
- Cannot be combined with `--single-user` mode
- Cannot be combined with `MCP_ENABLE_OAUTH21=true`
- Only one key source may be provided — set either `GOOGLE_SERVICE_ACCOUNT_KEY_FILE` or `GOOGLE_SERVICE_ACCOUNT_KEY_JSON`, not both

**Key Behaviors:**
- The OAuth callback server is not started (no interactive auth needed)
- Credentials directory permission checks are skipped
- **All operations impersonate the configured `USER_GOOGLE_EMAIL`** — any email addresses supplied in tool calls (e.g., `user_email` parameters) are ignored. This differs from OAuth modes where each user authenticates separately.
- The service account key is validated at startup (checks for required fields and correct type)

### VS Code MCP Client Support

> **✅ Recommended**: VS Code MCP extension properly supports the full MCP specification. **Always use HTTP transport mode** for proper OAuth 2.1 authentication.

<details open>
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

*Note: Make sure to start the server with `--transport streamable-http` when using VS Code MCP.*
</details>

### Claude Code MCP Client Support

> **✅ Recommended**: Claude Code is a modern MCP client that properly supports the full MCP specification. **Always use HTTP transport mode** with Claude Code for proper OAuth 2.1 authentication and multi-user support.

<details open>
<summary>🆚 <b>Claude Code Configuration</b> <sub><sup>← Setup for Claude Code MCP support</sup></sub></summary>

```bash
# Start the server in HTTP mode first
uv run main.py --transport streamable-http

# Then add to Claude Code
claude mcp add --transport http workspace-mcp http://localhost:8000/mcp

# Optional: install the bundled Claude skill for better Workspace tool routing
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/managing-google-workspace" ~/.claude/skills/managing-google-workspace
```

Or copy `skills/managing-google-workspace` into `~/.claude/skills/managing-google-workspace` if you prefer not to symlink it.
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

<details open>
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

<details open>
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
- **Prompt Injection**: This MCP server has the capability to retrieve your email, calendar events and drive files. Those emails, events and files could potentially contain prompt injections - i.e. hidden white text that tells it to forward your emails to a different address. You should exercise caution and in general, only connect trusted data to an LLM!
- **Credentials**: Never commit `.env`, `client_secret.json` or the `.credentials/` directory to source control!
- **OAuth Callback**: Uses `http://localhost:8000/oauth2callback` for development (requires `OAUTHLIB_INSECURE_TRANSPORT=1`)
- **Transport-Aware Callbacks**: Stdio mode starts a minimal HTTP server only for OAuth, ensuring callbacks work in all modes
- **Production**: Use HTTPS & OAuth 2.1 and configure accordingly
- **Scope Minimization**: Tools request only necessary permissions
- **Local File Access Control**: Tools that read local files (e.g., attachments, `file://` uploads) are restricted to the user's home directory by default. Override this with the `ALLOWED_FILE_DIRS` environment variable:
  ```bash
  # Colon-separated list of directories (semicolon on Windows) from which local file reads are permitted
  export ALLOWED_FILE_DIRS="/home/user/documents:/data/shared"
  ```
  Regardless of the allowlist, access to sensitive paths (`.env`, `.ssh/`, `.aws/`, `/etc/shadow`, credential files, etc.) is always blocked.

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
