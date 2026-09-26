<!-- mcp-name: io.github.taylorwilsdon/workspace-mcp -->

<div align="center">

# <span style="color:#cad8d9">Google Workspace MCP Server</span> <img src="https://github.com/user-attachments/assets/b89524e4-6e6e-49e6-ba77-00d6df0c6e5c" width="80" align="right" />

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/workspace-mcp.svg)](https://pypi.org/project/workspace-mcp/)
[![PyPI Downloads](https://static.pepy.tech/personalized-badge/workspace-mcp?period=total&units=NONE&left_color=GREY&right_color=BLUE&left_text=pypi+downloads)](https://pepy.tech/projects/workspace-mcp)
[![MCP Toplist](https://mcptoplist.com/badge/glama%2Ftaylorwilsdon%2Fgoogle_workspace_mcp.svg)](https://mcptoplist.com/server/glama%2Ftaylorwilsdon%2Fgoogle_workspace_mcp)
[![Website](https://img.shields.io/badge/Website-workspacemcp.com-green.svg)](https://workspacemcp.com/?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=badge-website)

*Full natural language control over Google Calendar, Drive, Gmail, Docs, Sheets, Slides, Forms, Tasks, Contacts, and Chat through all MCP clients, AI assistants and developer tools.*
*Includes a full featured CLI & Code Mode for use with tools like Claude Code and Codex!*

**The most feature-complete Google Workspace MCP server** is in a class of it's own: it can do things that Google's own tooling and the built in integrations with Claude and ChatGPT can't come close to with multi-user support, rich fine-grained editing tools and the most extensive coverage of any Workspace AI integration in existence. 

By leveraging native OAuth 2.1, stateless deployment capability and external auth server & gateway passthrough auth support, it's also the only Workspace MCP you can host for your whole organization centrally & securely!

Supports all free Google accounts & Google Workspace plans with expanded app options like Chat & Spaces. <br/>Interested in a managed cloud instance? [That can be arranged](https://workspacemcp.com/workspace-mcp-cloud?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=hero-cloud) (starting at $5/mo).


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
- **Sensitive path blocking** — local file reads default to the managed attachment directory, and `validate_file_path()` still blocks `.env*` files plus common home-directory credential stores such as `~/.ssh/` and `~/.aws/` even if `ALLOWED_FILE_DIRS` is broadened

Full dependency tree in `pyproject.toml`, pinned in `uv.lock`.

</td>
<td valign="top" width="50%">

**For Legal & Procurement**

This project is [MIT licensed](LICENSE) — not "open core," not "source available," not "free with a CLA." There is no dual licensing, no commercial tier gating features, and no contributor license agreement.

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
<sub>7 tools - events, free/busy,<br>Out of Office, Focus Time</sub>
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
<sub>7 tools - create, batch update,<br>thumbnails, comments</sub>
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
Optional per-download payload ceiling for container deployments: set `WORKSPACE_MCP_MAX_FILE_BYTES` to a positive byte count (e.g. `5242880` for 5 MiB) to reject Drive / Gmail / Chat / Google Docs downloads that would otherwise be fully buffered in-process. Unset or `0` leaves the total size uncapped; uncapped Drive transfers still use 256 KiB transport chunks instead of the Google client's 100 MiB default. This is a file-size limit, not a process-RSS limit: leave headroom for parsing, base64/JSON representation, and concurrent tool calls. Invalid or negative values fail server startup instead of silently disabling the limit. Downloads streamed directly to disk are not subject to this in-memory payload ceiling.

Hosted deployments where the server cannot see the caller's disk can set `WORKSPACE_MCP_DISABLE_LOCAL_FILES=true`. Tools then stop advertising server-side `file_path` parameters and refuse local paths with guidance to pass a URL or inline content instead. Stateless mode implies this setting. It is off by default, including for streamable HTTP, because a server on `localhost` shares the client's filesystem.

On such a server uploads can bypass it entirely: `create_drive_file`, `update_drive_file` and the `import_to_google_*` tools accept `return_upload_url=true`, which opens a Google Drive resumable upload session and returns its pre-authorized URL. The client then `PUT`s the bytes straight to Google (no Authorization header), so large or binary files never pass through the MCP server or the model context. The two settings are two sides of one switch: `return_upload_url` is advertised only when `WORKSPACE_MCP_DISABLE_LOCAL_FILES` is set (or in stateless mode), and a server with local file access refuses it, since `file_path` is the route there.

Office files (`.docx`, `.xlsx`, `.pptx`) are ZIP archives, so the ceiling above bounds only their compressed size. Text extraction separately applies `WORKSPACE_MCP_MAX_OFFICE_XML_BYTES` (default `26214400`, 25 MiB) as independent limits on expanded XML and extracted UTF-8 text. A file beyond either limit is reported as too large to extract. These limits bound input and output size, not process memory: the parsed XML tree measured roughly 14 to 30 times the XML size, so lower the value on small containers. `0` removes both limits; invalid or negative values fail server startup.

Advanced OAuth 2.1 deployments affected by concurrent client token refreshes can tune FastMCP's early-refresh threshold and client-facing access-token lifetime, and deployments that want to bound how long per-login records stay in the OAuth proxy storage backend can shorten the client-facing refresh-token lifetime. See [`.env.oauth21`](.env.oauth21) for the bounded settings, recommended values, and security tradeoffs. The first two settings reduce how often the race occurs; they do not add a grace period to FastMCP's one-time-use refresh-token rotation.

## Security Best Practices

By default this server sends no data anywhere except Google's APIs, using your own OAuth client credentials - no usage reporting, analytics, license server, or SaaS dependency. MIT licensed with no CLA, no dual licensing, and no copyleft in the dependency chain. The full security posture - scope minimization, sensitive-path blocking, stateless mode - is documented at [workspacemcp.com](https://workspacemcp.com/privacy?utm_source=github.com&utm_medium=referral&utm_campaign=readme&utm_content=security-privacy).

A few things worth internalizing before you connect an LLM to your email:

- **Prompt injection is real.** Emails, docs, and events can contain hidden instructions. Only connect trusted data to an LLM, and be deliberate about which write tools you enable.
- **Never commit** `.env`, `client_secret.json`, or `.credentials/` to source control.
- **Local file reads are sandboxed** to the managed attachment directory. Broaden with `ALLOWED_FILE_DIRS` only if you trust the client and its data sources; `.env*`, `~/.ssh/`, `~/.aws/`, and similar paths are always blocked.
- **Production** deployments should use HTTPS and OAuth 2.1.

## Workspace administration (opt-in, registered operations only)

The source includes guarded admin operations for Admin SDK Directory, Data Transfer, Reports, Enterprise License Manager, Google Vault, Alert Center, Groups Settings, Cloud Identity, Chrome Management, Chrome Policy, Access Context Manager, Contact Delegation, and Gmail mail delegates, and a recoverable user offboarding workflow. Admin services are **not** in the default launch or the existing five-service launcher. This is not a full Workspace administration implementation: only a phone-number tenant patch, restricted custom roles and schemas, and payload-free ChromeOS reboot commands are newly registered; printer creation, Chrome connector and certificate methods, domain writes, and other methods remain excluded with a stated reason. Context-aware access levels are read-only, and Gmail exposes mail delegates and bounded mailbox-setting reads, not mailbox-preference writes. See [the capability and gap report](docs/admin-capability-coverage.md) before enabling any admin service.

Selecting `admin-directory` exposes `get_admin_user`, `list_admin_capabilities`, `get_offboarding_status`, `admin_operation`, and `confirm_admin_operation`. `admin_operation` accepts only an operation ID from the pinned registry, with parameters and body checked against that operation's schema; there is no free-form URL, method, or field. Customer parameters are set by the server, and every user, group, member, domain, and role reference is checked against the admin's own customer. Reads return bounded fields. Writes return a proposal with a short-lived one-use token, and `confirm_admin_operation` executes it only for the same admin after rechecking the admin, customer, permission, and every reference. Operations in the other APIs also need their own service selected: `admin-datatransfer`, `admin-licensing`, `admin-reports`, `admin-vault`, `admin-alertcenter`, or `admin-groupssettings`. Vault and Alert Center take no customer parameter; Google scopes those calls to the signed-in admin's organization, and every account reference and returned alert is checked against the customer. Vault export download locations and alert payloads are not returned in lists or written to the audit log. Vault long-running operations (`matters.count`, `operations.get`, `operations.list`) return only the operation name, done flag, numeric error code, and the documented search count fields; operation metadata, error messages, and any other result, such as a finished export, are dropped.

Cloud Identity is split into seven opt-in services, each with its own `cloud-identity.*` scopes and never `cloud-platform`: `admin-cloudidentity-groups` (groups and memberships), `admin-cloudidentity-devices` (devices and device users), `admin-cloudidentity-sso` (inbound SAML and OIDC profiles and SSO assignments), `admin-cloudidentity-policies`, `admin-cloudidentity-invitations`, `admin-cloudidentity-domains` (allowlisted domains), and `admin-cloudidentity-orgunits` (the v1beta1 shared-drive organizational unit memberships). The installed Google client's bundled Cloud Identity documents are older than the pinned revision, so the verified client is rebuilt from the discovery documents pinned in `gadmin/discovery/`; nothing is fetched at call time. Customer names are set by the server, organizational units are read from Directory inside the customer, and a group, device, profile, assignment, or policy name is changed only after a fresh read shows it belongs to the customer; read results owned by another customer are refused. Device wipe, block, and delete, SSO profile and assignment changes, policy changes, and allowlisted-domain changes are destructive. OIDC client secrets and SAML signing certificates are never accepted, so `idpCredentials.add` is excluded and OIDC profiles are created or changed without a secret; operation results return only the operation name, done flag, and numeric error code.

Directory devices, calendar resources, and Chrome printers are three more opt-in services on the Directory client: `admin-directory-devices` (ChromeOS and mobile devices), `admin-directory-resources` (buildings, calendar resources, and features), and `admin-directory-printers` (Chrome printers and print servers). Each has its own `admin.directory.device.*`, `admin.directory.resource.calendar*`, or `admin.chrome.printers*` scopes; only deleting a mobile device needs the full `device.mobile` scope, which the `destructive` level adds. The customer is set by the server, and every device, printer, print server, building, calendar resource, and feature ID a write names, including each element of a batch (at most 50), is read afresh inside the customer at proposal and again at confirmation; one unknown or foreign ID refuses the whole call. Devices move only to an organizational unit given as `id:{id}` and verified inside the customer. Device moves and deletes, ChromeOS disable and deprovision, mobile block and wipes, and printer, print-server, building, resource, and feature deletes are destructive; ChromeOS re-enable and mobile approve are `manage`. Device reads never return hardware addresses, IMEI or serial numbers of phones, recent users, networks, or files; printer and print-server reads never return the URI, which can embed credentials. Only a ChromeOS `REBOOT` remote command without a payload is supported. Its request needs a fresh customer-scoped device read and destructive confirmation; command reads omit payload, messages, and unsupported result fields. Printer and print-server creation (an unverifiable caller-supplied URI), other remote commands, and the full-replacement update methods are excluded. Tenant phone-number patches, custom role create/metadata patch/delete, and custom schema create/metadata patch/delete are separate confirmed `admin-directory` operations with narrow input fields. A customer record is read before tenant contact changes; new role privileges must match a fresh customer-scoped privilege list, system roles cannot be changed, and role deletion is blocked while assignments exist. Only an empty custom schema can be deleted.

Chrome Management and Chrome Policy are five more opt-in services: `admin-chrome-reports` (fleet, app, print, and SaaS usage reports and app details), `admin-chrome-telemetry` (device, user, and event telemetry), `admin-chrome-profiles` (managed browser profiles and profile commands), `admin-chrome-insights` (security insights), and `admin-chrome-policy` (policy schemas, resolved policies, and org-unit and group policy changes). Each has its own `chrome.management.*` scopes; reports and telemetry are read-only at every level. The Chrome Management client is rebuilt from the discovery document pinned in `gadmin/discovery/`, because the installed copy lacks 17 of its methods. Customer names are set by the server. A browser profile is deleted or sent a command only after a fresh read inside the customer; every org-unit or group policy target in a batch (at most 20) is read afresh from Directory; and a policy value is accepted only if every member is a boolean, integer, or enum field of the schema read afresh from Google, so no URL, credential, or free text is stored in a proposal. Profile deletion, profile commands (`clearBrowsingData` with boolean cache and cookie flags, or `extensionUpdateCheck`), enabling or disabling security insights, and every policy change are destructive. Reads never return attestation credentials, network reports, print-job titles, app URIs, or string policy values. Connector configurations, certificate provisioning, network and certificate definitions, policy file uploads, telemetry notification channels, and per-user browsing breakdowns are excluded.

Context-aware access policies and levels are read through `admin-access-context`, which reads Access Context Manager with the `cloud-platform` scope because Google offers no narrower one. That scope is requested only when this service is selected, including in read-only mode, and never by any other service or the default launch. On every call the server finds the Google Cloud organization with Cloud Resource Manager `organizations.search` on the same verified credentials, and uses it only if it is the single active organization linked to the admin's customer ID; the caller never supplies it. Policies are listed only under that organization, and a policy or its access levels are read only after a fresh read shows an organization-level policy with no folder or project scopes. Access levels return only documented condition fields. Access level and policy changes are excluded because Google tells Workspace customers to change access levels only in the Admin console; service perimeters, IAM, Cloud access bindings, and long-running operations are excluded as Google Cloud resources. The OAuth client's Google Cloud project needs the Access Context Manager and Cloud Resource Manager APIs enabled, and the admin needs Access Context Manager read permission on the organization.

Delegation has two more opt-in services. `admin-contact-delegation` lists, adds, and removes a user's contact delegates with the `admin.contact.delegation.readonly` and `admin.contact.delegation` scopes; Google publishes no discovery document for it, so a hand-written document for its three documented methods is pinned in `gadmin/discovery/`. Both users are verified in the customer, a delegate is removed only after a fresh list shows it, and responses carry delegate addresses only, never contact data. `admin-gmail-delegates` lists, reads, adds, and removes Gmail mail delegates. Google allows these methods only for a service account with domain-wide delegation acting as the mailbox owner, so they run only in the existing service-account mode, never with an admin OAuth grant, and no OAuth consent requests a Gmail scope for them. Before a token is minted, the admin must be a super admin and the mailbox owner an active user of the admin's customer, both read afresh, and the owner must pass `DWD_ALLOWED_DOMAINS`; the token carries only `gmail.settings.basic` (reads) or `gmail.settings.sharing` (writes). Delegate responses keep the delegate address and verification status only. Nine additional DWD-only reads return bounded auto-forwarding, IMAP, POP, vacation (no subject or body), language, send-as identity (no signature or SMTP credentials), and forwarding-address settings. A confirmed `updateLanguage` accepts a restricted locale code. Other mailbox-setting writes, filters, messages, drafts, threads, labels, encryption methods, and push channels remain excluded. Using it needs an Admin console domain-wide delegation grant of those two scopes to the service account, which is not configured here.

The offboarding tools `plan_user_offboarding` and `advance_user_offboarding` require **all three** opt-in services: `admin-directory`, `admin-datatransfer`, and `admin-licensing`. The final account deletion also needs `admin-vault`; without it every earlier step still runs, but deletion is never proposed. `--read-only` hides both offboarding tools and `confirm_admin_operation`, and so does a launch where every selected admin service is at `readonly`; each handler also checks the operation's risk against the permission level (`readonly`, `manage`, `destructive`). Read-only Directory scopes are separate from user-security, group, group-member, org-unit, role-management, transfer-write, and licensing scopes. Each admin API requires the corresponding Google API, admin privileges, edition or license entitlements (Vault in particular), and approved OAuth grants. Licensing, Alert Center, and Groups Settings do not offer a read-only scope, so their reads need `manage`; Reports is read-only at every level.

Planning performs Google reads and persists a local workflow, but does not change a Google account. Execution advances one write at a time, requires fresh one-use confirmations for suspension, each license removal, and final deletion, and stops on unsupported or unverifiable steps. Google must report each transferred application's completion before any license removal or account deletion. With `admin-contact-delegation` selected, the user's contact delegates are removed one confirmed write at a time; with `admin-gmail-delegates` in service-account mode, mail delegates are listed for manual review; with `admin-vault` selected, planning finds holds that cover the user's account or organizational unit (or an ancestor) by paging every visible open matter. Final deletion always requires a fresh Vault hold check, run before the deletion is proposed and again at its confirmation, that finds no covering hold; a missing `admin-vault` service, an incomplete check, or a covering hold blocks it, with no override. Vault retention rules, matters the admin cannot see, contact delegation where the user is someone else's delegate, and anything a selected service did not check stay manual gaps. Treat the persisted status as progress through the registered API steps, **not** proof that all data and access have been handled. The workflow needs a durable private filesystem for its workflow and confirmation state; do not deploy it to a stateless or multi-node setup without shared atomic storage and locking.

This source branch has not been installed into an MCP launcher. An administrator's exact identity and customer ID must be verified before tenant access. Installation, activation, OAuth grant changes, and live writes require separate review and approval.

## Development

```bash
uv sync --group dev    # install deps
uv run ruff check .    # lint
uv run pytest          # test
```

Single-file service modules live in `g<service>/`, tools are registered with `@server.tool` decorators, and tiers are defined in `core/tool_tiers.yaml`. PRs welcome.

## License

MIT - see [`LICENSE`](LICENSE). The license is 21 lines and says what it means.

---

Validations:
[![MCP Badge](https://lobehub.com/badge/mcp/taylorwilsdon-google_workspace_mcp)](https://lobehub.com/mcp/taylorwilsdon-google_workspace_mcp)
