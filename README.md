<!-- mcp-name: io.github.taylorwilsdon/workspace-mcp -->

<div align="center">

# <span style="color:#cad8d9">Google Workspace MCP Server</span> <img src="https://github.com/user-attachments/assets/b89524e4-6e6e-49e6-ba77-00d6df0c6e5c" width="80" align="right" />

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/workspace-mcp.svg)](https://pypi.org/project/workspace-mcp/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/workspace-mcp?period=total&units=NONE&left_color=GREY&right_color=BLUE&left_text=pypi+downloads)](https://pepy.tech/projects/workspace-mcp)
[![MCP Toplist](https://mcptoplist.com/badge/glama%2Ftaylorwilsdon%2Fgoogle_workspace_mcp.svg)](https://mcptoplist.com/server/glama%2Ftaylorwilsdon%2Fgoogle_workspace_mcp)
[![Website](https://img.shields.io/badge/Website-workspacemcp.com-green.svg)](https://workspacemcp.com/?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=badge-website)

*Full natural language control over Google Calendar, Drive, Gmail, Docs, Sheets, Slides, Forms, Tasks, Contacts, and Chat through all MCP clients, AI assistants and developer tools.*
*Includes a full featured CLI & Code Mode for use with tools like Claude Code and Codex!*

**The fastest, most feature-complete Google Workspace MCP server** is in a class of it's own: it can do things that Google's own tooling and the built in integrations with Claude and ChatGPT can't come close to with multi-user support, rich fine-grained editing tools and the most extensive coverage of any Workspace AI integration in existence. 

By leveraging native OAuth 2.1, stateless deployment capability and external auth server & gateway passthrough auth support, it's also the only Workspace MCP you can host for your whole organization centrally & securely! Supports all free Google accounts & Google Workspace plans with expanded app options like Chat & Spaces.

</div>

<p align="center">
  <a href="https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=hero-docs">
    <img src="https://img.shields.io/badge/Read%20the%20Docs-0969DA?style=for-the-badge&logo=readthedocs&logoColor=white" alt="Read the Docs">
  </a><a href="https://workspacemcp.com/quick-start?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=hero-quickstart">
    <img src="https://img.shields.io/badge/Quick%20Start-2EA44F?style=for-the-badge" alt="Quick Start Guide">
  </a>
</p>

<div align="center">
<a href="https://www.pulsemcp.com/servers/taylorwilsdon-google-workspace">
<img width="375" src="https://github.com/user-attachments/assets/0794ef1a-dc1c-447d-9661-9c704d7acc9d" align="center"/>
</a>
</div>

<div align="center">
<a href="https://workspacemcp.com/workspace-mcp-cloud?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=hero-cloud"><img width="400" height="53" alt="workspace-cloud" src="https://github.com/user-attachments/assets/349a6bab-c83a-4254-be2e-4ac6a9e8b3e5" /></a>
</div>

---

**See it in action:**
<div align="center">
  <video width="400" src="https://github.com/user-attachments/assets/a342ebb4-1319-4060-a974-39d202329710"></video>
</div>

---

## What It Does

Workspace MCP connects AI assistants to all twelve major Google Workspace services - 120+ tools behind a single MCP server, with OAuth 2.1 multi-user auth, three progressive tool tiers, read-only mode, a full CLI, and stateless container deployment. It runs locally over stdio for legacy clients and remotely over streamable HTTP with full implementation of the latest MCP spec.

The README covers just enough to get you running, with extensive documentation on the website:

| Where to go | What you'll find |
|:---|:---|
| **[Quick&nbsp;Start](https://workspacemcp.com/quick-start?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=nav-quickstart)** | Google Cloud setup, credentials, and client connection with screenshots |
| **[Full&nbsp;Documentation](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=nav-docs)** | Every tool, parameter, and auth mode |
| **[Advanced&nbsp;Deployment](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=nav-deployment)** | Reverse proxy & nginx config, origin validation, credential store backends (GCS/CMEK), [trusted-gateway identity](https://workspacemcp.com/docs/deployment/gateway-identity?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=nav-gateway-identity), and the complete environment variable reference |
| **[Client&nbsp;Setup&nbsp;Guides](https://workspacemcp.com/guides?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=nav-guides)** | Claude Desktop/web Connectors, ChatGPT Developer Mode, and more |
| **[FAQ&nbsp;&&nbsp;Troubleshooting](https://workspacemcp.com/welcome/faq?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=nav-faq)** | Enabling Google APIs, OAuth errors, redirect URIs, Google Chat setup, client quirks |

## <span style="color:#adbcbc">Security & Compliance</span>

<table>
<tr>
<td valign="top" width="50%">

**For Security Teams**

By default, this server sends no data anywhere except Google's APIs, on behalf of the authenticated user, using your own OAuth client credentials. There is no usage reporting, analytics, license server, or SaaS dependency outside optional OTel support for your own usage.

- **Fully open source** — every line is auditable in this repo
- **Your OAuth client, your GCP project** — credentials never leave your environment & you control scopes
- **You control the network** — deploy behind your reverse proxy, in your VPC, on your own terms
- **Stateless mode** — zero disk writes for locked-down container environments
- **Sensitive path blocking** — local file reads default to the managed attachment directory, and `validate_file_path()` also blocks `.env` and `.env.*` files, the server's own credentials directory, and common credential stores such as `~/.ssh/` and `~/.aws/` as a best-effort backstop if `ALLOWED_FILE_DIRS` is broadened

Full dependency tree in `pyproject.toml`, pinned in `uv.lock`.

</td>
<td valign="top" width="50%">

**For Legal & Procurement**

This project is [MIT licensed](LICENSE) — 

- **Use commercially without restriction** — build products, sell services, deploy internally
- **Fork, embed, redistribute** — MIT requires only attribution
- **No CLA** — contributions remain under MIT
- **No built-in telemetry to disclose** — optional tracing is off unless you configure it
- **No network effects** — the server never contacts any endpoint you didn't configure
- **Standard dependency licenses** — MIT, Apache 2.0, and BSD throughout the dependency chain; no copyleft, no AGPL
</td>
</tr>
</table>

## Services

<table width="100%" align="center">
<tr>
<td align="center" width="25%">
<h3>📧</h3><a href="https://workspacemcp.com/gmail?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-gmail"><b>Gmail</b></a><br>
<sub>15 tools - search, send, draft,<br>labels, filters, attachments</sub>
</td>
<td align="center" width="25%">
<h3>📁</h3><a href="https://workspacemcp.com/google-drive?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-drive"><b>Drive</b></a><br>
<sub>16 tools - search, create, share,<br>import Office files</sub>
</td>
<td align="center" width="25%">
<h3>📅</h3><a href="https://workspacemcp.com/google-calendar?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-calendar"><b>Calendar</b></a><br>
<sub>7 tools - events, sharing, free/busy,<br>Out of Office, Focus Time</sub>
</td>
<td align="center" width="25%">
<h3>📝</h3><a href="https://workspacemcp.com/google-docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-docs"><b>Docs</b></a><br>
<sub>19 tools - edit, style, tables,<br>tabs, comments, export</sub>
</td>
</tr>
<tr>
<td align="center" width="25%">
<h3>📊</h3><a href="https://workspacemcp.com/google-sheets?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-sheets"><b>Sheets</b></a><br>
<sub>14 tools - ranges, tables,<br>formatting, conditional rules</sub>
</td>
<td align="center" width="25%">
<h3>🖼️</h3><a href="https://workspacemcp.com/google-slides?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-slides"><b>Slides</b></a><br>
<sub>6 tools - create, read, batch update,<br>slide images, comments</sub>
</td>
<td align="center" width="25%">
<h3>📋</h3><a href="https://workspacemcp.com/google-forms?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-forms"><b>Forms</b></a><br>
<sub>6 tools - build forms, publish,<br>read responses</sub>
</td>
<td align="center" width="25%">
<h3>✅</h3><a href="https://workspacemcp.com/google-tasks?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-tasks"><b>Tasks</b></a><br>
<sub>6 tools - tasks & lists<br>with hierarchy</sub>
</td>
</tr>
<tr>
<td align="center" width="25%">
<h3>👤</h3><a href="https://workspacemcp.com/google-contacts?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-contacts"><b>Contacts</b></a><br>
<sub>8 tools - people, groups,<br>batch operations</sub>
</td>
<td align="center" width="25%">
<h3>💬</h3><a href="https://workspacemcp.com/google-chat?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-chat"><b>Chat</b></a><br>
<sub>6 tools - spaces, messages,<br>search, reactions</sub>
</td>
<td align="center" width="25%">
<h3>🔍</h3><a href="https://workspacemcp.com/google-search?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-search"><b>Custom Search</b></a><br>
<sub>2 tools - programmable<br>web search</sub>
</td>
<td align="center" width="25%">
<h3>⚡</h3><a href="https://workspacemcp.com/google-apps-script?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-google-apps-script"><b>Apps Script</b></a><br>
<sub>15 tools - write, deploy,<br>run & debug scripts</sub>
</td>
</tr>
</table>

Each page lists every tool with its tier, parameters, required scopes, and example prompts. The [complete reference](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-docs-all) covers all twelve in one place.

> 💬 **Google Chat** needs a one-time Chat app configuration and a Workspace account - see the [Chat setup FAQ](https://workspacemcp.com/welcome/faq?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=services-chat-faq).

## Quick Start

> Set credentials → pick a launch command → connect your client. Full walkthrough with screenshots: **[workspacemcp.com/quick-start](https://workspacemcp.com/quick-start?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=quickstart-hero)**

You'll need an OAuth client from [Google Cloud Console](https://console.cloud.google.com/) in a project with the Google APIs enabled for the services you plan to use. The docs have [one-click enable links for every API](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=quickstart-enable-apis#authentication) plus a single `gcloud services enable` command that covers them all, and the [quick start guide](https://workspacemcp.com/quick-start?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=quickstart-inline) walks through the whole setup in about five minutes.

<details>
<summary><b>Let your coding agent do the setup</b> - paste this prompt into Claude Code or any agent with shell access</summary>

<br>

The agent runs every `gcloud` step and checks its own work. Google has no CLI or API for the consent screen or a standard OAuth client, so it hands you two short console steps and gives you the exact links. The prompt tells it never to read or repeat your client secret.

```text
Set up the Google Workspace MCP server (https://github.com/taylorwilsdon/google_workspace_mcp) on this machine. Use the gcloud CLI for everything it supports, check each step, and stop and ask me when something fails or needs a decision.

Ask me first, all at once:
1. A new Google Cloud project or an existing one (list mine with `gcloud projects list`).
2. Which services: gmail, drive, calendar, docs, sheets, slides, forms, tasks, contacts, chat, search, appscript (default: gmail drive calendar docs sheets).
3. Which tool tier (core, extended, complete) and whether to run read-only.
4. Which MCP client to connect (default: this one).

Safety rules for the whole run:
- Show me every command that creates or changes something before you run it. Read-only commands are fine.
- Never print, echo, log or paste the OAuth client secret, and never ask me to paste it into chat. Don't open the client secret file with a file-reading tool; only use the commands below that print the client ID and client type.
- Change only the project I picked. Don't touch billing, IAM roles, org policies or service accounts, and don't create keys.
- Don't put credentials in any git repository or in a file another process can read.

Steps:
1. Check that `gcloud` and `uv` are installed and that `gcloud auth list` shows an active account. If I need to log in, tell me to run `! gcloud auth login` myself, since it opens a browser.
2. Create the project or select the one I picked (`gcloud projects create`, then `gcloud config set project`).
3. Enable the APIs for my services in a single `gcloud services enable` call: gmail.googleapis.com, drive.googleapis.com (drive, appscript), calendar-json.googleapis.com, docs.googleapis.com, sheets.googleapis.com, slides.googleapis.com, forms.googleapis.com, tasks.googleapis.com, people.googleapis.com (contacts, chat), chat.googleapis.com, customsearch.googleapis.com (search), script.googleapis.com (appscript). Confirm with `gcloud services list --enabled`. If I chose appscript, also tell me to open https://script.google.com/home/usersettings and turn on "Google Apps Script API". Warn me first that the switch is per user but applies to every app I authorize, not just this server, and that gcloud can't set it.
4. Console step: send me to https://console.cloud.google.com/auth/overview?project=<PROJECT_ID> to set up the consent screen. Audience is Internal if my account belongs to a Google Workspace organization, otherwise External, with my email added as a test user under Audience. Tell me that External apps in Testing status get refresh tokens that expire after 7 days. If I chose chat, also point me to the one-time Chat app configuration in https://workspacemcp.com/welcome/faq. Wait until I say it's done.
5. Console step: send me to https://console.cloud.google.com/auth/clients/create?project=<PROJECT_ID> to create a client with application type "Desktop app", download its JSON, and tell you the downloaded file's path. Wait for the path.
6. Run `mkdir -p ~/.config/workspace-mcp && chmod 700 ~/.config/workspace-mcp`, move the file to ~/.config/workspace-mcp/client_secret.json (ask me before replacing one that is already there), and `chmod 600` it. Check it without showing the secret: `python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); k=next(iter(d)); print(k, d[k]["client_id"])' ~/.config/workspace-mcp/client_secret.json`. The type should be "installed" and the client ID should end in .apps.googleusercontent.com.
7. Start the server over streamable HTTP with OAuth 2.1. Check that nothing is listening on port 8000, then give me this command to run in a separate terminal that I leave open: `env -u GOOGLE_OAUTH_CLIENT_ID -u GOOGLE_OAUTH_CLIENT_SECRET PORT=8000 MCP_ENABLE_OAUTH21=true WORKSPACE_MCP_HOST=127.0.0.1 GOOGLE_CLIENT_SECRET_PATH=~/.config/workspace-mcp/client_secret.json uvx workspace-mcp --transport streamable-http --tool-tier <tier> --tools <services>`, plus `--read-only` if I chose it. If I chose search, first tell me that Google has closed the Custom Search JSON API to new customers and discontinues it on January 1, 2027 (https://developers.google.com/custom-search/v1/overview), and ask whether to drop search. If I keep it, tell me to create the API key and Programmable Search Engine myself, run `read -rs GOOGLE_PSE_API_KEY && export GOOGLE_PSE_API_KEY` (fish: `read -gsx GOOGLE_PSE_API_KEY`) in my own terminal so the key stays out of shell history, and add `GOOGLE_PSE_ENGINE_ID=<id>` before `uvx`; never ask me for the key. Don't start it as your own background job, since it would stop when this session ends, and don't install a launchd or systemd service unless I ask. When I say it's running, check that `curl -s http://localhost:8000/health` reports "healthy", then register it with my MCP client. For Claude Code: `claude mcp add --scope user --transport http workspace-mcp http://localhost:8000/mcp`. For other clients, follow https://workspacemcp.com/guides.
8. Verify: have me restart or reconnect the client and sign in with Google in the browser (in Claude Code, run `/mcp` and choose workspace-mcp). Then call one read-only tool, such as listing my calendars or searching my Drive.

Finish with a short report: project ID, APIs enabled, where the credentials file is, the command that starts the server (it must be running whenever I use the tools), the client config you changed, the tool call that proved it works, and anything I still need to do.
```

</details>

> **Agentic OAuth clients:** this server only calls Google's REST APIs, so your OAuth client does not need Google's "Use this client for an AI-powered agent" setting. If your organization wants agent traffic labeled for admin controls, create a separate client with that setting enabled; it can't be changed on an existing client after November 12, 2026.

<table>
<tr>
<td valign="top" width="50%">

**Confidential Client**

```bash
# 1. Credentials
export GOOGLE_OAUTH_CLIENT_ID="..."
export GOOGLE_OAUTH_CLIENT_SECRET="..."

# 2. Launch - pick a tier
uvx workspace-mcp --tool-tier core       # essential tools
uvx workspace-mcp --tool-tier extended   # core + management ops
uvx workspace-mcp --tool-tier complete   # everything

# Or cherry-pick services
uvx workspace-mcp --tools gmail drive calendar
```

</td>
<td valign="top" width="50%">

**OAuth 2.1 (PKCE)**

```bash
# 1. Credentials - MCP clients connect with PKCE and no
#    secret, but Google still requires one server-side
export MCP_ENABLE_OAUTH21=true
export GOOGLE_OAUTH_CLIENT_ID="..."
export GOOGLE_OAUTH_CLIENT_SECRET="..."
#    Alternatively, point GOOGLE_CLIENT_SECRET_PATH at a client_secret.json
#    that contains the client id and secret (env vars take precedence).
export WORKSPACE_MCP_PORT=8000
export GOOGLE_OAUTH_REDIRECT_URI="http://localhost:${WORKSPACE_MCP_PORT}/oauth2callback"
export OAUTHLIB_INSECURE_TRANSPORT=1

# 2. Launch - OAuth 2.1 requires HTTP transport
uvx workspace-mcp --transport streamable-http --tool-tier core
```

</td>
</tr>
</table>

**Tool tiers** keep context windows lean: `core` is the essential set, `extended` adds management operations, `complete` loads everything. Combine with `--tools <service> ...`, `--read-only`, or per-service `--permissions`, and subtract individual tools with `--disabled-tools <name> ...` - details in the [server modes docs](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=tiers-server-modes#server-modes).

**Per-request toolsets** let HTTP clients sharing one server see different tools, using the same headers as the GitHub MCP server. `X-MCP-Toolsets` takes comma-separated service names (the `--tools` names, e.g. `gmail,drive`) and `X-MCP-Tools` takes comma-separated tool names (e.g. `search_gmail_messages`); when both are sent the request gets the union. Names ignore case and whitespace, unknown names match nothing, and a blank or missing header leaves the tool list unchanged. Headers only narrow what the server already loaded: a tool removed by the tier, `--tools`, `--read-only`, `--permissions` or `--disabled-tools` stays unavailable, and calls to tools outside the request's selection fail as unknown tools.

## Connect Your Client

**Claude Desktop, web & mobile** - run the server in HTTP mode and add it as a **Connector** (Settings → Connectors → Add custom connector). This is the recommended path; the [Connector guide](https://workspacemcp.com/guides/claude-connectors?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=clients-connectors) has step-by-step screenshots. Legacy stdio configuration remains available for clients without Connector support - see the [FAQ](https://workspacemcp.com/welcome/faq?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=clients-connectors-faq).

**Claude Code**

```bash
# Start the server in HTTP mode, then:
claude mcp add --transport http workspace-mcp http://localhost:8000/mcp

# Optional: install the bundled skill for better Workspace tool routing
ln -s "$(pwd)/skills/managing-google-workspace" ~/.claude/skills/managing-google-workspace
```

**ChatGPT** - connect via Developer Mode with the [ChatGPT guide](https://workspacemcp.com/guides/chatgpt-developer-mode?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=clients-chatgpt).

**VS Code, LM Studio, Open WebUI, and everything else** - any MCP client works over streamable HTTP (recommended) or stdio. Client-specific walkthroughs live in the [guides](https://workspacemcp.com/guides?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=clients-guides) and [FAQ](https://workspacemcp.com/welcome/faq?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=clients-guides-faq).

## CLI

`workspace-cli` lists and calls tools against a running server with encrypted, disk-backed OAuth token caching - authenticate once, script forever:

```bash
uv run workspace-cli list
uv run workspace-cli call search_gmail_messages query="is:unread" max_results=5
```

Install globally with `uv tool install .` from this repo. ⚠️ Don't use `uvx workspace-cli` - an abandoned PyPI package squats that name.

## Deployment & Advanced Configuration

Everything you need to run this in production lives in two places. The [documentation](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-docs) covers auth modes and server configuration:

- **[OAuth 2.1 multi-user auth](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-oauth21#authentication)** - bearer tokens, required for remote or shared HTTP endpoints
- **[Stateless container mode](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-stateless#authentication)** - zero disk writes for locked-down deployments
- **[OAuth proxy storage backends](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-proxy-storage#authentication)** - memory, disk, or Valkey/Redis for distributed setups
- **[External OAuth provider mode](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-external-oauth#authentication)** - bring your own auth server, validate bearer tokens only
- **[Service accounts with domain-wide delegation](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-service-accounts#authentication)** - per-request user impersonation with an optional domain allowlist
- **[Trusted-gateway identity](https://workspacemcp.com/docs/deployment/gateway-identity?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-gateway-identity)** - proxy-verified per-user isolation with Pomerium, Cloudflare Access, oauth2-proxy, or any JWKS-verifiable gateway
- **[OpenTelemetry tracing](https://workspacemcp.com/docs?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-otel#server-modes)** - optional, off unless you configure an OTLP endpoint
- **Docker** - `docker build -t workspace-mcp . && docker run -p 8000:8000 workspace-mcp`

The **[Advanced Deployment guide](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-advanced)** covers self-hosting specifics: reverse proxy setup with `WORKSPACE_EXTERNAL_URL` (including the nginx `Origin: null` consent workaround, the `WORKSPACE_MCP_ALLOW_NULL_ORIGIN_CONSENT` escape hatch, and the `Referrer-Policy` pitfall), origin validation and VS Code webview allowlisting, credential store backends (local directory or GCS with CMEK enforcement), and the **[complete environment variable reference](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#environment-variables)**.

Production tuning is all environment variables; the names below link to their reference entries.

- **Google API requests:** [`WORKSPACE_MCP_GOOGLE_API_WORKERS`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_GOOGLE_API_WORKERS), [`WORKSPACE_MCP_GOOGLE_API_TIMEOUT_SECONDS`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_GOOGLE_API_TIMEOUT_SECONDS)
- **External OAuth token validation:** [`WORKSPACE_MCP_TOKEN_VALIDATION_WORKERS`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_TOKEN_VALIDATION_WORKERS), [`WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL)
- **Memory ceilings:** [`WORKSPACE_MCP_MAX_FILE_BYTES`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_MAX_FILE_BYTES), [`WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_STATELESS_INLINE_MAX_BYTES), [`WORKSPACE_MCP_MAX_OFFICE_XML_BYTES`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_MAX_OFFICE_XML_BYTES)
- **Text returned per call:** `get_drive_file_content` returns long content a page at a time by default, so a long file does not fill the model's context. `WORKSPACE_MCP_MAX_CONTENT_CHARS` sets the page size (default `40000`; `0` returns whole content by default). Each page ends with a note giving the `offset` to read on from. This is only the default: a client can always pass `full_content=true` to get the whole file in one call. Text being read page by page is kept in memory for ten minutes so later pages are not downloaded again, within `WORKSPACE_MCP_PAGED_CONTENT_CACHE_BYTES` per process (default `33554432`, 32 MiB; `0` keeps nothing).
- **Sessions and health probes:** [`WORKSPACE_MCP_SESSION_IDLE_TIMEOUT`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_SESSION_IDLE_TIMEOUT), [`WORKSPACE_MCP_READINESS_TIMEOUT_SECONDS`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_READINESS_TIMEOUT_SECONDS)
- **Several instances behind a load balancer:** `WORKSPACE_MCP_STATELESS_HTTP=true` serves streamable HTTP without MCP sessions, so any instance can answer any request, including from clients on the older session-based protocol. Unlike `WORKSPACE_MCP_STATELESS_MODE`, it keeps the configured auth mode and persistence; state that must survive between requests (OAuth proxy storage, for example) needs a shared backend such as Valkey. `WORKSPACE_MCP_SESSION_IDLE_TIMEOUT` has no effect with it on.
- **DNS-rebinding protection:** `WORKSPACE_MCP_ALLOWED_HOSTS`, a comma-separated list of hostnames without ports (for example `workspace-mcp,10.0.0.5`). When set, requests whose `Host` is not loopback, listed, or the host of `WORKSPACE_EXTERNAL_URL` or an allowed origin get `403`, so include every name clients and health probes use. Unset, any `Host` is accepted.
- **Hosted servers without access to the caller's disk:** [`WORKSPACE_MCP_DISABLE_LOCAL_FILES`](https://workspacemcp.com/docs/deployment?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=deploy-env-vars#WORKSPACE_MCP_DISABLE_LOCAL_FILES)
  With local files disabled, `create_drive_file`, `update_drive_file` and the import tools offer `return_upload_url`: the caller PUTs the bytes to the returned URL. If the file sits in a cloud-mirrored folder (Drive for desktop), copy it to a local directory first, since mirrored paths can stall mid-upload.
- **OAuth 2.1 token refresh and lifetimes:** see [`.env.oauth21`](.env.oauth21)
- **OAuth 2.1 consent screen:** `WORKSPACE_MCP_OAUTH_PROXY_REQUIRE_CONSENT=remember` shows the OAuth proxy's consent screen once per browser for each MCP client instead of on every authorization; cross-site navigations are still prompted. The default, `always`, prompts every time. Any other value fails startup.
- **OAuth 2.1 Google account choice:** `WORKSPACE_MCP_OAUTH_PROXY_GOOGLE_PROMPT` sets the `prompt` the OAuth proxy sends to Google's authorization endpoint (default `consent`); external provider mode ignores it. `select_account consent` shows Google's account chooser, so a user signed in to Google with another account can pick the right one, and still returns a refresh token. The only other accepted value is `consent`; anything else fails startup.

**Server instructions:** `WORKSPACE_MCP_SERVER_INSTRUCTIONS` sets the `instructions` text the server returns when a client connects. Clients that defer tools behind a search step, such as Claude Code with tool search, see only tool names and these instructions until they search, so say what the server covers and when to use it, for example `This server is the user's Google Workspace (Gmail, Calendar, Drive). Use these tools for any email, calendar, or file request.` Put the most important sentence first, since clients may truncate (Claude Code keeps the first 2,048 characters). In single-user mode, the `USER_GOOGLE_EMAIL` account context follows this text.

For downloads, horizontally scaled or scale-to-zero HTTP deployments (Cloud Run, Kubernetes) can stage attachments in Google Cloud Storage instead of instance-local disk, whose `/attachments/{id}` URL may reach a different instance. Install the `gcs` extra and set `WORKSPACE_MCP_FILES_GCS_BUCKET` to a bucket name; objects are written under `WORKSPACE_MCP_FILES_GCS_PREFIX` (default `attachments`) and handed out as V4 signed URLs valid for one hour, which the client fetches straight from GCS. This also lets stateless mode return download URLs instead of inline files. stdio ignores the setting, since its client reads the local file path directly. The runtime service account needs object read and write access on the bucket — `roles/storage.objectUser`, or `roles/storage.objectCreator` plus `roles/storage.objectViewer` — and, without an exported private key, `roles/iam.serviceAccountTokenCreator` on itself so URLs can be signed through IAM. Read access is not optional: a signed URL carries the signer's authority, so without `storage.objects.get` the upload and the signing both succeed while the client's download fails with `403`. Objects are not deleted by the server, so add a bucket lifecycle rule that removes them after a few hours.

## Security Best Practices

By default this server sends no data anywhere except Google's APIs, using your own OAuth client credentials - no usage reporting, analytics, license server, or SaaS dependency. MIT licensed with no CLA, no dual licensing, and no copyleft in the dependency chain. The full security posture - scope minimization, sensitive-path blocking, stateless mode - is documented at [workspacemcp.com](https://workspacemcp.com/privacy?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=security-privacy).

A few things worth internalizing before you connect an LLM to your email:

- **Prompt injection is real.** Emails, docs, and events can contain hidden instructions. Only connect trusted data to an LLM, and be deliberate about which write tools you enable.
- **Never commit** `.env`, `client_secret.json`, or `.credentials/` to source control.
- **Local file reads are sandboxed** to the managed attachment directory. Broaden with `ALLOWED_FILE_DIRS` only if you trust the client and its data sources, and keep it as narrow as possible. `.env`, `.env.*`, `~/.ssh/`, `~/.aws/`, the server's credentials directory, and similar paths are blocked as a backstop, but no blocklist can cover every secret on every machine: configure the server for your environment, set `WORKSPACE_MCP_DISABLE_LOCAL_FILES=true` if you don't need local reads, and ideally run it in a sandbox (a container or dedicated user) with nothing sensitive on disk.
- **Production** deployments should use HTTPS and OAuth 2.1.

## Development

```bash
uv sync --group dev    # install deps
uv run ruff check .    # lint
uv run pytest          # test
```

Single-file service modules live in `g<service>/`, tools are registered with `@server.tool` decorators, and tiers are defined in `core/tool_tiers.yaml`. PRs welcome.

## License

MIT - see [`LICENSE`](LICENSE). The license is 21 lines and says what it means.

_Workspace MCP is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Google LLC. Google and Google Workspace are trademarks of Google LLC._

---

Validations:
[![MCP Badge](https://lobehub.com/badge/mcp/taylorwilsdon-google_workspace_mcp)](https://lobehub.com/mcp/taylorwilsdon-google_workspace_mcp)
