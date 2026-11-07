"""
Middleware that lets callers use camelCase argument names on snake_case tools.
"""

import logging
import re
from typing import Any, Dict, Optional

from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext

logger = logging.getLogger(__name__)

# Split on lowercase/digit -> uppercase ("timeMin") and on the tail of an
# acronym run followed by a new word ("fooURLValue" -> "foo_URL_Value").
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def to_snake_case(name: str) -> str:
    """Convert a camelCase argument name to snake_case (``timeMin`` -> ``time_min``)."""
    return _CAMEL_BOUNDARY.sub("_", name).lower()


class CamelCaseArgumentsMiddleware(Middleware):
    """Translate camelCase tool arguments to their snake_case equivalents.

    Every tool in this server declares snake_case parameters with
    ``additionalProperties: false``, so callers that mirror the Google API
    field names (``calendarId``, ``timeMin``, ``maxResults``, ...) fail schema
    validation before the request ever reaches Google. This middleware renames
    an argument key when all of the following hold:

    * the key is not itself a declared parameter of the tool,
    * its snake_case conversion is a declared parameter, and
    * the caller did not also pass the snake_case key explicitly.

    Everything else passes through untouched, so existing snake_case callers
    (and genuinely invalid arguments) behave exactly as before.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next: CallNext):
        arguments = context.message.arguments
        if arguments:
            normalized = await self._normalize_arguments(context, arguments)
            if normalized is not None:
                context = context.copy(
                    message=context.message.model_copy(update={"arguments": normalized})
                )
        return await call_next(context)

    async def _normalize_arguments(
        self, context: MiddlewareContext, arguments: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Return arguments with camelCase keys renamed, or None if unchanged."""
        if not context.fastmcp_context:
            return None

        tool = await context.fastmcp_context.fastmcp.get_tool(context.message.name)
        if tool is None:
            # Unknown tool — let the normal "tool not found" handling run.
            return None

        properties = (tool.parameters or {}).get("properties")
        if not properties:
            return None

        renames = {}
        for key in arguments:
            if key in properties:
                continue
            snake_key = to_snake_case(key)
            if (
                snake_key != key
                and snake_key in properties
                and snake_key not in arguments
            ):
                renames[key] = snake_key

        # If two distinct keys normalize to the same parameter (e.g. "iCalUid"
        # and "iCalUID"), the request is ambiguous — leave those keys untouched
        # so schema validation rejects them instead of silently picking one.
        target_counts: Dict[str, int] = {}
        for snake_key in renames.values():
            target_counts[snake_key] = target_counts.get(snake_key, 0) + 1
        renames = {
            key: snake_key
            for key, snake_key in renames.items()
            if target_counts[snake_key] == 1
        }

        if not renames:
            return None

        logger.debug(
            "[CamelCaseArgumentsMiddleware] Renaming arguments for tool '%s': %s",
            context.message.name,
            renames,
        )
        return {renames.get(key, key): value for key, value in arguments.items()}
