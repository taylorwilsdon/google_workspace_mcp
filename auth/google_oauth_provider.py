"""Google OAuth defaults independent of protocol-level token requirements."""

from fastmcp.server.auth.providers.google import GoogleProvider as _GoogleProvider
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull


class GoogleProvider(_GoogleProvider):
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # FastMCP falls back to required_scopes for scope-less authorization,
        # even when DCR/CIMD defaults advertise the enabled Workspace scopes.
        # Keep bearer validation minimal while requesting the configured defaults.
        # An explicit scope selection must remain the client's choice.
        if not params.scopes:
            params = params.model_copy(
                update={"scopes": self._default_scope_str.split()}
            )
        return await super().authorize(client, params)
