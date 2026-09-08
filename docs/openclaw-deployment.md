# OpenClaw managed HTTP deployment (opt-in)

OpenClaw and similar agent gateways can spawn multiple concurrent stdio MCP
processes across agent sessions. In a local, single-user setup those
processes share the same on-disk Google OAuth refresh-token file. Concurrent
access-token refreshes (especially around Calendar writes) can rotate or
overwrite the refresh token and lead to repeated, disruptive
reauthorization prompts.

The `openclaw` deployment preset addresses this by running **one durable
local Streamable HTTP process** instead of one stdio process per session, so
credential refreshes for a given Google account are coordinated through a
single in-process lock rather than racing across processes.

This is entirely opt-in. Without `--preset openclaw` (or the equivalent env
var), the server's default stdio behavior is unchanged.

## Enabling the preset

```bash
workspace-mcp --preset openclaw
```

or, equivalently:

```bash
WORKSPACE_MCP_DEPLOYMENT_PRESET=openclaw workspace-mcp
```

The preset:

- Forces Streamable HTTP transport (overriding `--transport`/`WORKSPACE_MCP_TRANSPORT`
  if set to `stdio`).
- Binds to `127.0.0.1` by default (respects an explicit `WORKSPACE_MCP_HOST`
  if you've set one — e.g. to expose the service more broadly behind your own
  reverse proxy, which remains an explicit choice you make separately).
- Serves the MCP endpoint at `/mcp` (FastMCP's default Streamable HTTP path).
- Uses the same port resolution as every other Streamable HTTP deployment
  (`WORKSPACE_MCP_PORT`/`PORT`, default `8000`).

Combine it with your usual auth configuration
(`GOOGLE_OAUTH_CLIENT_ID`/`GOOGLE_OAUTH_CLIENT_SECRET`, or a client secrets
file) exactly as you would for any other Streamable HTTP deployment of this
server. No new credential backend is introduced: the preset reuses the
existing `LocalDirectoryCredentialStore` (or your configured backend) so a
single durable process holds one canonical copy of the credential file.

## Ownership boundary

- **OpenClaw** authorizes *agents* to talk to the local Workspace MCP
  service (transport-level access).
- **Workspace MCP** retains and refreshes the underlying Google OAuth
  credentials, coordinating concurrent refreshes internally so that no
  matter how many agent sessions call in concurrently, only one refresh
  happens per account at a time and every caller observes the same,
  consistent, already-refreshed token.

Restarting OpenClaw does not require re-consenting to Google: as long as the
Workspace MCP process (and its credentials directory) persists, the stored
refresh token remains valid across OpenClaw session churn and across
restarts of the Workspace MCP process itself.

## systemd unit example

Run one persistent instance, independent of any particular OpenClaw session:

```ini
# /etc/systemd/system/workspace-mcp.service
[Unit]
Description=Google Workspace MCP (OpenClaw managed HTTP mode)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=youruser
Environment=WORKSPACE_MCP_DEPLOYMENT_PRESET=openclaw
Environment=WORKSPACE_MCP_PORT=8000
Environment=GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
Environment=GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
Environment=WORKSPACE_MCP_CREDENTIALS_DIR=/home/youruser/.google_workspace_mcp/credentials
ExecStart=/usr/local/bin/workspace-mcp
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/home/youruser/.google_workspace_mcp

[Install]
WantedBy=default.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now workspace-mcp.service
```

## OpenClaw Streamable HTTP registration example

Register the running instance as a shared Streamable HTTP MCP server rather
than letting OpenClaw spawn its own stdio subprocess per session:

```json
{
  "mcpServers": {
    "google-workspace": {
      "transport": "streamable-http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Adjust the host/port to match `WORKSPACE_MCP_HOST`/`WORKSPACE_MCP_PORT` if
you've overridden the defaults. Because the service is bound to loopback by
default, it is only reachable from processes on the same host; exposing it
beyond that (a reverse proxy, a different bind address) is a deliberate,
separate choice — see the main README's deployment section for
`WORKSPACE_EXTERNAL_URL` and origin-validation notes.

## Health / status

`GET /status` on the running instance reports non-secret deployment status —
useful for confirming the single durable instance is up and authorized
without ever exposing token material:

```json
{
  "status": "running",
  "transport": "streamable-http",
  "account_authorized": true,
  "refresh_capable": true,
  "enabled_services": "all",
  "enabled_scopes": ["..."]
}
```

`account_authorized` and `refresh_capable` never include token or refresh
token values — only booleans derived from whether a credential file exists
and whether it carries a refresh token.
