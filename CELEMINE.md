The Dockerfile is a production runtime image, not a development environment. You can still work inside it by overriding the entrypoint and keeping the virtualenv off the bind mount.

The image copies the repo into /app, runs uv sync --frozen --no-dev --extra disk --extra otel, and starts as the non-root app user with this entrypoint:


Dockerfile
Ln 45–47

ENTRYPOINT ["/bin/sh", "-c"]
CMD ["exec /app/.venv/bin/python main.py --transport streamable-http ${TOOL_TIER:+--tool-tier \"$TOOL_TIER\"} ${TOOLS:+--tools $TOOLS}"]
--no-dev means pytest and ruff are not installed. The virtualenv lives at /app/.venv, so mounting the repo onto /app hides it. The host .venv is also a macOS environment and will not run in this Linux image.

Live edit
Build once, then start a shell with the repo mounted and the container virtualenv on its own volume:


docker build -t workspace-mcp .
docker run -it --rm \
  --name workspace-mcp-dev \
  --user root \
  --entrypoint /bin/bash \
  -p 8000:8000 \
  -v "$PWD":/app \
  -v rkspace-mcp-venv:/app/.venv \
  --env-file .env \
  -e GOOGLE_MCP_CREDENTIALS_DIR=/app/store_creds \
  workspace-mcp
--user root is for the dev shell. The image's app user will not own the files Docker Desktop mounts from your Mac, so uv sync would fail as that user. --entrypoint /bin/bash replaces sh -c; without it, extra arguments are not passed through as a normal command.

On the first shell, install the dev group into that volume. Later shells reuse it:


uv sync --frozen --extra disk --extra otel --group dev
uv run pytest
uv run ruff check .
uv run python main.py --transport streamable-http
Edits on the host show up immediately because /app is the repo. The workspace-mcp-venv volume keeps /app/.venv across container restarts.

docker-compose.yml is the run configuration (port 8000, client_secret.json, credential volume). It does not mount the source tree, so it will not pick up local edits.

Editor
There is no .devcontainer. After the container is running, use Dev Containers: Attach to Running Container in Cursor or VS Code and open /app. The interpreter is /app/.venv/bin/python.

Cloudflare
`cloudflare/` is the `workspace-mcp` Worker. It is not a public origin. The orgs Worker reaches it through the `WORKSPACE_MCP` service binding and spreads requests across a pool of containers running this Dockerfile.

The container starts in stateless external-OAuth mode. Each request carries the member's Google access token. The process checks that token and does not store it. `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` are Worker secrets used only to validate the token with Google.

The Agent Durable Object keeps the credential. A connection whose URL is `https://workspace-mcp.internal/mcp` is that hop. The catalog entry for Google uses that URL with a bearer connection policy. Put the member's Google refresh token in the policy secret. Connect exchanges it for an access token and stores both on that member's Agent DO. Tool calls send only the access token. The Agent refreshes about a minute before expiry, and once more if Google rejects the access token. A rotated refresh token replaces the stored one. The same OAuth client id and secret must be set on the orgs Worker (`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`) and on this Worker. A `ya29.` access token still connects, and it cannot be refreshed.

Deploy from `cloudflare/` with Docker running: `pnpm deploy`. The first deploy builds this image for linux/amd64.
