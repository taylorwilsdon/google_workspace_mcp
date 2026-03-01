"""Pause / maintenance mode middleware.

Provides a minimal mechanism to temporarily pause the service without code
changes elsewhere. When paused, all HTTP requests except health checks return
503 with a JSON body indicating paused status.

Pause can be activated by either:
1. Setting environment variable WORKSPACE_MCP_PAUSED=true
2. Creating a flag file (default: .workspace_mcp_paused) in the working dir.
   Override path via WORKSPACE_MCP_PAUSE_FILE env variable.

This is intentionally lightweight and avoids dependency on FastMCP internals.
"""

import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def _pause_flag_file() -> str:
    return os.getenv(
        "WORKSPACE_MCP_PAUSE_FILE", os.path.join(os.getcwd(), ".workspace_mcp_paused")
    )


def is_paused() -> bool:
    """Determine if the service is currently paused."""
    env_paused = os.getenv("WORKSPACE_MCP_PAUSED", "false").lower() == "true"
    file_paused = os.path.exists(_pause_flag_file())
    return env_paused or file_paused


class PauseMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[override]
        if is_paused():
            path = request.url.path
            # Allow health endpoint to report paused state differently
            if path == "/health":
                # Return paused indicator for health endpoint
                return JSONResponse(
                    {"status": "paused", "service": "workspace-mcp"}, status_code=200
                )
            return JSONResponse(
                {"status": "paused", "message": "Service temporarily paused"},
                status_code=503,
            )
        return await call_next(request)


__all__ = ["PauseMiddleware", "is_paused"]
