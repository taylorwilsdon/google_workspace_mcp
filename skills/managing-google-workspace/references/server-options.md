# Server Options Reference

These mirror the MCP server's own flags. **Default setup**: use stdio transport with OAuth 2.0 (no flags needed). Only change these for specific deployment requirements.

---

## Transport

| Option | Use case |
|--------|----------|
| `--transport streamable-http` | **Recommended.** Full MCP spec compliance, OAuth 2.1 |
| _(default: stdio)_ | Legacy fallback for clients with incomplete MCP support |

## Auth modes

| Mode | Flag / Env | Use case |
|------|-----------|----------|
| Single-user | `--single-user` | One user, cached credentials, CLI or local MCP. Cannot combine with OAuth 2.1. Note: `user_google_email` is still required by the tool schema -- this flag only affects credential lookup |
| OAuth 2.0 (default) | _(no flag)_ | Multi-user with browser OAuth flow per session |
| OAuth 2.1 | `MCP_ENABLE_OAUTH21=true` | Multi-user HTTP deployment, per-session tokens |
| External OAuth | `EXTERNAL_OAUTH21_PROVIDER=true` | External OAuth flow with bearer tokens. Requires OAuth 2.1 |
| Stateless | `WORKSPACE_MCP_STATELESS_MODE=true` | Multi-user + stateless, requires OAuth 2.1 |

## Tool filtering

| Option | Effect |
|--------|--------|
| `--read-only` | Restricts to read-only scopes (no send, create, delete) |
| `--tools gmail drive` | Load only specific services |
| `--tool-tier core` | Minimal tools (core / extended / complete) |
| `--tool-tier extended` | Balanced set -- core + management tools |
| `--permissions gmail:organize drive:readonly` | Per-service permission levels. Mutually exclusive with `--read-only` and `--tools` |

Gmail permission levels: `readonly`, `organize`, `drafts`, `send`, `full` (cumulative).
Tasks permission levels: `readonly`, `manage`, `full` (cumulative; `manage` allows create/update/move but excludes delete).
Other services: `readonly`, `full`.

### Plugin users: env var overrides

Plugin users cannot pass CLI args directly. Use `WORKSPACE_MCP_TOOLS` and `WORKSPACE_MCP_TOOL_TIER` in the `env` block of `~/.claude/settings.json` to filter tools without overriding the plugin MCP config:

```json
{
  "env": {
    "WORKSPACE_MCP_TOOLS": "gmail,drive",
    "WORKSPACE_MCP_TOOL_TIER": "core"
  }
}
```

`WORKSPACE_MCP_TOOLS` accepts a comma-separated list of services. `WORKSPACE_MCP_TOOL_TIER` accepts `core`, `extended`, or `complete`. CLI args take precedence if both are provided.

## MCP config examples

```json
{"command": "uvx", "args": ["workspace-mcp"], "env": {"GOOGLE_OAUTH_CLIENT_ID": "...", "GOOGLE_OAUTH_CLIENT_SECRET": "..."}}
{"command": "uvx", "args": ["workspace-mcp", "--single-user"], "env": {"GOOGLE_OAUTH_CLIENT_ID": "...", "GOOGLE_OAUTH_CLIENT_SECRET": "..."}}
{"command": "uvx", "args": ["workspace-mcp", "--single-user", "--read-only"], "env": {"GOOGLE_OAUTH_CLIENT_ID": "...", "GOOGLE_OAUTH_CLIENT_SECRET": "..."}}
{"command": "uvx", "args": ["workspace-mcp", "--single-user", "--tools", "gmail", "drive"], "env": {"GOOGLE_OAUTH_CLIENT_ID": "...", "GOOGLE_OAUTH_CLIENT_SECRET": "..."}}
{"command": "uvx", "args": ["workspace-mcp", "--single-user", "--tool-tier", "core"], "env": {"GOOGLE_OAUTH_CLIENT_ID": "...", "GOOGLE_OAUTH_CLIENT_SECRET": "..."}}
{"command": "uvx", "args": ["workspace-mcp", "--transport", "streamable-http"], "env": {"MCP_ENABLE_OAUTH21": "true", "GOOGLE_OAUTH_CLIENT_ID": "...", "GOOGLE_OAUTH_CLIENT_SECRET": "..."}}
```

## Deployment

**Claude Desktop**: Run an instance and connect via a [Connector](https://workspacemcp.com/quick-start). See the quick start guide for full setup instructions.

**Docker**:
```bash
docker build -t workspace-mcp .
docker run -p 8000:8000 -e GOOGLE_OAUTH_CLIENT_ID=... -e GOOGLE_OAUTH_CLIENT_SECRET=... workspace-mcp --transport streamable-http
```

## Credential loading priority

1. Environment variables (including those from `~/.claude/settings.local.json` or `~/.claude/settings.json` `env` blocks)
2. `.env` file in project root -- **will not work with `uvx`** (uvx runs outside the repo directory)
3. `client_secret.json` via `GOOGLE_CLIENT_SECRET_PATH` env var
4. Default `client_secret.json` in project root
