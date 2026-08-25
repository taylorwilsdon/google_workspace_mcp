FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

COPY . .

# Install Python dependencies using uv sync
# --extra otel ships the OpenTelemetry SDK/exporter so tracing can be enabled at
# runtime via OTEL_* env vars; it stays a no-op unless an OTLP endpoint is set.
RUN uv sync --frozen --no-dev --extra disk --extra otel \
    && chmod +x /app/docker-entrypoint.sh

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app

# Give read and write access to the store_creds volume
RUN mkdir -p /app/store_creds \
    && chown -R app:app /app/store_creds \
    && chmod 755 /app/store_creds

USER app

# Expose port (use default of 8000 if PORT not set)
EXPOSE 8000
# Expose additional port if PORT environment variable is set to a different value
ARG PORT
EXPOSE ${PORT:-8000}

# Health check — resolve listener port like main.py: PORT, then WORKSPACE_MCP_PORT, then 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD sh -c 'p="${PORT:-${WORKSPACE_MCP_PORT:-8000}}"; curl -f "http://127.0.0.1:${p}/health" || exit 1'

# Optional tool selection via env (consumed by docker-entrypoint.sh, not shell CMD)
ENV TOOL_TIER=""
ENV TOOLS=""
# Containers typically need all interfaces; override locally with WORKSPACE_MCP_HOST=127.0.0.1
ENV WORKSPACE_MCP_HOST="0.0.0.0"

# Exec-form entrypoint — no shell interpolation of TOOLS/TOOL_TIER
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["--transport", "streamable-http"]
