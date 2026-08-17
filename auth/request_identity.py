"""Request-scoped authenticated identity for the active MCP request.

``AuthInfoMiddleware`` re-derives the caller's identity on every tool and prompt
call and records it here. Both keys are written with ``serializable=False``, which
keeps them in FastMCP's request-scoped dict (``Context._request_state``) rather
than the session-scoped state store. That matters twice over:

* Identity cannot outlive the request it was derived from. A client that opens a
  fresh MCP session per tool call no longer accumulates state-store keys, and a
  request that fails authentication cannot observe an earlier request's principal.
* Concurrent requests sharing one MCP session each get their own identity instead
  of racing over a single session-scoped key.

``reset_request_identity`` shadows both keys with ``None`` at request entry.
``Context.get_state`` tests membership in the request-scoped dict before falling
back to the session store, so a ``None`` shadow neutralises any legacy
session-scoped identity without deleting it. Nothing in this module reclaims store
memory: residue left in a caller-supplied ``session_state_store`` stays put and
expires on FastMCP's own TTL. It simply cannot be read while a request is in
flight.

Every identity write goes through this module so that no authentication path can
accidentally choose session scope.
"""

import logging
from typing import NamedTuple

from fastmcp import Context
from fastmcp.server.dependencies import get_context

logger = logging.getLogger(__name__)

# The only two identity keys any code reads. Deliberately not a general-purpose
# auth-state bag: extra keys were removed because nothing consumed them.
_REQUEST_IDENTITY_STATE_KEYS = ("authenticated_user_email", "authenticated_via")
_EMAIL_KEY, _VIA_KEY = _REQUEST_IDENTITY_STATE_KEYS


class RequestIdentity(NamedTuple):
    """The principal resolved for the current request, if any."""

    email: str | None
    via: str | None


async def reset_request_identity(ctx: Context) -> None:
    """Shadow any inherited identity with request-scoped ``None``.

    Call once at request entry, before any authentication path runs, so a request
    that resolves no principal reads ``None`` instead of falling through to a
    session-scoped value left by an earlier request.
    """
    for key in _REQUEST_IDENTITY_STATE_KEYS:
        await ctx.set_state(key, None, serializable=False)


async def set_request_identity(ctx: Context, *, email: str, via: str) -> None:
    """Record the verified principal for the current request only.

    ``via`` names the authentication path that resolved ``email`` and is itself
    security-relevant: ``require_gateway_principal`` accepts a principal only when
    it came from ``gateway_assertion``.
    """
    await ctx.set_state(_EMAIL_KEY, email, serializable=False)
    await ctx.set_state(_VIA_KEY, via, serializable=False)


async def get_request_identity(ctx: Context | None = None) -> RequestIdentity | None:
    """Return the current request's principal, or ``None`` outside an MCP request.

    ``ctx`` defaults to the ambient FastMCP context. ``None`` is returned when
    there is no usable request context at all -- either no active ``Context``, or
    one whose MCP session is not yet established -- which lets callers raise their
    own domain error rather than leaking a FastMCP ``RuntimeError``. A live request
    that simply failed to authenticate yields ``RequestIdentity(None, None)``.
    """
    try:
        if ctx is None:
            ctx = get_context()
        return RequestIdentity(
            email=await ctx.get_state(_EMAIL_KEY),
            via=await ctx.get_state(_VIA_KEY),
        )
    except RuntimeError as exc:
        # FastMCP raises RuntimeError both for "no active context" and for
        # "session not established yet" (state keys are session-prefixed).
        logger.debug("No usable MCP request context for identity lookup: %s", exc)
        return None
