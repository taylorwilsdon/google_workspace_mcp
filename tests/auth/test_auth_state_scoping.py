"""Integration coverage for request-scoped auth identity against a real FastMCP server.

The unit tests in test_auth_info_middleware.py drive a fake context, so they can only
prove AuthInfoMiddleware asks for ``serializable=False`` -- not that FastMCP's scoping
behaves as the design assumes. These tests run the real middleware on a real server, so
the properties stay pinned across a FastMCP upgrade:

* middleware-written identity reaches the tool body in the same request (it does,
  because Context.__aenter__ shares ``_request_state`` by reference with the nested
  context that runs the tool);
* it does not survive into the next request;
* concurrent requests on one MCP session do not observe each other's principal;
* a fresh session state store stays empty;
* a store pre-seeded with legacy identity keeps its entries -- the design shadows
  rather than deletes -- yet those entries cannot influence a request read.
"""

import asyncio
from contextvars import ContextVar

import pytest
from fastmcp import Client, Context, FastMCP
from fastmcp.server.middleware import Middleware

from auth.auth_info_middleware import AuthInfoMiddleware
from auth.request_identity import get_request_identity

pytestmark = pytest.mark.asyncio

# Set per request from the tool arguments, so concurrent calls cannot race over a
# single shared slot -- otherwise the isolation test would be exercising the stub.
_active_caller: ContextVar[str | None] = ContextVar("active_caller", default=None)


def _session_state_keys(server: FastMCP) -> list[str]:
    """Every key in the server's session-scoped state store.

    Reaches into FastMCP internals deliberately, and in exactly one place: this is the
    surface whose unbounded growth motivated request-scoped identity, and it has no
    public accessor. A FastMCP upgrade that relocates it should break only here.
    """
    return sorted(
        key
        for collection in server._state_storage._cache.values()
        for key in collection.keys()
    )


class _StashCaller(Middleware):
    """Publish the in-flight call's ``caller`` argument to a request-local var."""

    async def on_call_tool(self, context, call_next):
        token = _active_caller.set((context.message.arguments or {}).get("caller"))
        try:
            return await call_next(context)
        finally:
            _active_caller.reset(token)


def _build_server(monkeypatch, authenticates):
    """Server wired with the real middleware, authenticating via the fastmcp_oauth path.

    ``authenticates`` maps a caller name to the email its validated access token should
    carry, or None to simulate a request that resolves no principal.
    """
    server = FastMCP("scoping-test")
    # Added first, so it wraps AuthInfoMiddleware and runs before it.
    server.add_middleware(_StashCaller())
    server.add_middleware(AuthInfoMiddleware())
    seen: list[tuple[str | None, str | None, str | None]] = []

    @server.tool
    async def whoami(caller: str, ctx: Context) -> str:
        identity = await get_request_identity(ctx)
        seen.append((caller, identity.email, identity.via))
        return str(identity.email)

    @server.tool
    async def seed_legacy_state(caller: str, ctx: Context) -> str:
        """Write session-scoped identity the way pre-fix code did."""
        await ctx.set_state("authenticated_user_email", "legacy@x")
        await ctx.set_state("authenticated_via", "bearer_token")
        return ctx.session_id

    def fake_get_access_token():
        email = authenticates(_active_caller.get())
        if email is None:
            return None
        return type("_Tok", (), {"email": email, "claims": {"email": email}})()

    monkeypatch.setattr(
        "auth.auth_info_middleware.get_access_token", fake_get_access_token
    )
    monkeypatch.setattr(
        "auth.auth_info_middleware.get_http_headers", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr("core.config.get_transport_mode", lambda: "streamable-http")
    return server, seen


async def test_identity_reaches_tool_and_leaves_store_empty(monkeypatch):
    server, seen = _build_server(monkeypatch, lambda caller: f"{caller}@x")

    async with Client(server) as client:
        for caller in ("a", "b", "c"):
            await client.call_tool("whoami", {"caller": caller})

    assert seen == [
        ("a", "a@x", "fastmcp_oauth"),
        ("b", "b@x", "fastmcp_oauth"),
        ("c", "c@x", "fastmcp_oauth"),
    ]
    # The leak this change exists to fix: identity must never reach the store.
    assert _session_state_keys(server) == []


async def test_identity_does_not_survive_into_the_next_request(monkeypatch):
    server, seen = _build_server(
        monkeypatch, lambda caller: "first@x" if caller == "first" else None
    )

    async with Client(server) as client:
        await client.call_tool("whoami", {"caller": "first"})
        await client.call_tool("whoami", {"caller": "second"})

    # The second request authenticates nobody, and must not inherit the first's principal.
    assert seen == [("first", "first@x", "fastmcp_oauth"), ("second", None, None)]
    assert _session_state_keys(server) == []


async def test_concurrent_requests_on_one_session_are_isolated(monkeypatch):
    server, seen = _build_server(monkeypatch, lambda caller: f"{caller}@x")

    async with Client(server) as client:
        await asyncio.gather(
            client.call_tool("whoami", {"caller": "alice"}),
            client.call_tool("whoami", {"caller": "bob"}),
        )

    # Under session-scoped state both requests collide on one key and the loser reads
    # the other's identity; request scope gives each its own.
    assert sorted(seen) == [
        ("alice", "alice@x", "fastmcp_oauth"),
        ("bob", "bob@x", "fastmcp_oauth"),
    ]
    assert _session_state_keys(server) == []


async def test_seeded_legacy_identity_is_shadowed_but_retained(monkeypatch):
    """Legacy session-scoped identity stays in the store and stays unreadable.

    Deliberately not asserting an empty store: the design shadows with a request-scoped
    None rather than deleting, so residue in a caller-supplied ``session_state_store``
    is expected to remain and to expire on FastMCP's own TTL.
    """
    server, seen = _build_server(monkeypatch, lambda caller: None)

    async with Client(server) as client:
        result = await client.call_tool("seed_legacy_state", {"caller": "seed"})
        session_id = result.content[0].text
        seeded = [
            f"{session_id}:authenticated_user_email",
            f"{session_id}:authenticated_via",
        ]
        assert _session_state_keys(server) == seeded

        await client.call_tool("whoami", {"caller": "later"})

    # The later request reads None rather than the seeded principal...
    assert seen == [("later", None, None)]
    # ...while the seeded entries remain in the store, undeleted.
    assert _session_state_keys(server) == seeded
