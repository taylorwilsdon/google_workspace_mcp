FROM python:3.11-slim

# Create non-root user and app directory
RUN useradd --create-home --shell /bin/bash app \
    && mkdir -p /app \
    && chown app:app /app

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install --no-cache-dir uv

# Copy with correct ownership (avoids expensive chown -R later)
COPY --chown=app:app . .

# Create store_creds directory
RUN mkdir -p /app/store_creds && chmod 755 /app/store_creds

# Switch to non-root user before installing deps
USER app

# Install Python dependencies using uv sync
RUN uv sync --frozen --no-dev

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
CMD ["uv run main.py --transport streamable-http ${TOOL_TIER:+--tool-tier \"$TOOL_TIER\"} ${TOOLS:+--tools $TOOLS}"]
