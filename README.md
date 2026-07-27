<div align="center">

# Workspace Lens

**A hosted, remote MCP server that connects Claude to your Google Workspace.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Website](https://img.shields.io/badge/Website-workspace.lens.io.vn-3B82F6.svg)](https://workspace.lens.io.vn)

Read and write Docs, Sheets, Slides, Drive, Forms, Apps Script, and Tasks through
natural conversation — no install, no API keys, no server to run. Copy a URL,
add it as a connector in Claude.ai, and authenticate with your Google account.

*Not affiliated with Google LLC.*

</div>

---

## Overview

Workspace Lens is a [Model Context Protocol](https://modelcontextprotocol.io) server
that runs entirely on Cloudflare Workers. Unlike most MCP servers for Google
Workspace, there is nothing to install or self-host: the server is already running,
and you connect to it over HTTP the same way you'd add any other Claude.ai connector.

- **Zero configuration** — no OAuth app to register, no credentials file, no `.env`
- **Multi-user by design** — each Claude.ai user authenticates with their own Google
  account; tokens are stored per-user, not shared
- **Bring your own Google Cloud project (optional)** — for a dedicated, isolated
  setup instead of the shared tier

## Quick Start

1. **Copy the MCP URL**: `https://office.lens.io.vn/mcp`
2. **Add it as a connector** — in Claude.ai: Settings → Connectors → Add custom connector, paste the URL
3. **Authenticate once** — authorize with your Google account; Claude can now read and write your Workspace files

See [workspace.lens.io.vn](https://workspace.lens.io.vn) for the full walkthrough, or
[BYOC registration](https://auth.lens.io.vn/byoc-register) to use your own Google Cloud OAuth app.

## What's included

| Service | Tools |
|---|---|
| 📝 Google Docs | Create, read, edit; rich content with headings, tables, images |
| 📊 Google Sheets | Read/write spreadsheets; formatted sheets with charts, formulas, themes |
| 🎞️ Google Slides | Build presentations from outlines; slides, shapes, images, text |
| 📁 Google Drive | Create/manage files and folders; sharing and permissions |
| 📋 Google Forms | Create/update forms; read responses, configure publish settings |
| ⚙️ Apps Script | Read/update script projects; versions, deployments, running functions |
| ✅ Google Tasks | Create/manage task lists and tasks |

This is the tool set exposed by the public `office.lens.io.vn` endpoint (the `office`
worker — see [cloudflare-worker/README.md](cloudflare-worker/README.md)), currently the
only deployed worker.

## Pricing

| | Shared (free) | BYOC |
|---|---|---|
| Cost | Free forever | Free + your own Google Cloud project |
| OAuth credentials | Shared | Your own |
| Setup | Copy the URL, done | [Register your app](https://auth.lens.io.vn/byoc-register) |
| Isolation | Shared token storage | Dedicated tenant, no shared storage |

## Architecture

Workspace Lens runs as Cloudflare Workers using Durable Objects for per-session MCP
state, with Google OAuth delegated to a centralized auth worker rather than handled
per-deployment. For the full technical breakdown — worker topology, wrangler configs,
the delegated-OAuth flow, and dev/deploy commands — see
**[cloudflare-worker/README.md](cloudflare-worker/README.md)**.

The `landing/` directory is the React marketing site at
[workspace.lens.io.vn](https://workspace.lens.io.vn).

## Development

```bash
# Worker (this is what actually implements the MCP server)
cd cloudflare-worker
npm install
npm run dev            # wrangler dev
npm run type-check
npm test

# Landing site
cd landing
npm install
npm run dev            # vite
```

CI runs type-check and tests on every PR ([.github/workflows/ci.yml](.github/workflows/ci.yml)).

## Security & Privacy

See [SECURITY.md](SECURITY.md) for responsible disclosure, and the hosted service's
[Privacy Policy](https://workspace.lens.io.vn/privacy) /
[Terms of Service](https://workspace.lens.io.vn/terms) for what data is collected and
how to request deletion.

## License & Provenance

MIT licensed — see [LICENSE](LICENSE). This project began as a fork of
[taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp)
(a self-hosted Python MCP server) and was rewritten from scratch as a TypeScript
Cloudflare Workers service; none of the current implementation is Python.

Questions or data-deletion requests: [manhtuongdz@gmail.com](mailto:manhtuongdz@gmail.com) ·
[GitHub](https://github.com/tuongbeo/google_workspace_mcp)
