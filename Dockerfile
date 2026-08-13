FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and uv
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
COPY main.py fastmcp_server.py ./
COPY auth/ auth/
COPY core/ core/
COPY gappsscript/ gappsscript/
COPY gcalendar/ gcalendar/
COPY gchat/ gchat/
COPY gcontacts/ gcontacts/
COPY gdocs/ gdocs/
COPY gdrive/ gdrive/
COPY gforms/ gforms/
COPY gmail/ gmail/
COPY gsearch/ gsearch/
COPY gsheets/ gsheets/
COPY gslides/ gslides/
COPY gtasks/ gtasks/

# Install Python dependencies using uv sync, create non-root user, and set up store_creds volume
# --extra otel ships the OpenTelemetry SDK/exporter so tracing can be enabled at
# runtime via OTEL_* env vars; it stays a no-op unless an OTLP endpoint is set.
RUN uv sync --frozen --no-dev --extra disk --extra otel \
    && useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app \
    && mkdir -p /app/store_creds \
    && chown -R app:app /app/store_creds \
    && chmod 755 /app/store_creds

USER app

# Expose port (use default of 8000 if PORT not set)
EXPOSE 8000
# Expose additional port if PORT environment variable is set to a different value
ARG PORT
EXPOSE ${PORT:-8000}

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD sh -c 'curl -f http://localhost:${PORT:-8000}/health || exit 1'

# Set environment variables for Python startup args
ENV TOOL_TIER=""
ENV TOOLS=""

# Use entrypoint for the base command and CMD for args
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["uv run main.py --transport streamable-http ${TOOL_TIER:+--tool-tier $TOOL_TIER} ${TOOLS:+--tools $TOOLS}"]
