FROM python:3.11-slim
WORKDIR /app
# Install uv for faster dependency management
RUN pip install --no-cache-dir uv
COPY . .
# Install Python dependencies using uv sync
RUN uv sync --frozen --no-dev
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
# Health check using Python's urllib (no external dependencies needed)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health').read()" || exit 1
# Set environment variables for Python startup args
ENV TOOL_TIER=""
ENV TOOLS=""
# Use entrypoint for the base command and CMD for args
ENTRYPOINT ["/bin/sh", "-c"]
CMD ["uv run main.py --transport streamable-http ${TOOL_TIER:+--tool-tier \"$TOOL_TIER\"} ${TOOLS:+--tools $TOOLS}"]
