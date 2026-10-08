from urllib.parse import parse_qs, urlparse

import pytest
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from auth.google_oauth_provider import GoogleProvider
from auth.scopes import GMAIL_READONLY_SCOPE, PROTOCOL_AUTH_SCOPES


@pytest.mark.asyncio
@pytest.mark.parametrize("requested_scopes", [None, [], PROTOCOL_AUTH_SCOPES])
async def test_authorization_defaults_without_requiring_optional_scopes(
    requested_scopes,
):
    defaults = [*PROTOCOL_AUTH_SCOPES, GMAIL_READONLY_SCOPE]
    provider = GoogleProvider(
        client_id="google-client",
        client_secret="google-secret",
        base_url="https://mcp.example.test",
        required_scopes=PROTOCOL_AUTH_SCOPES,
        valid_scopes=defaults,
        client_storage=MemoryStore(),
        require_authorization_consent=False,
    )
    params = AuthorizationParams(
        state="client-state",
        scopes=requested_scopes,
        code_challenge="challenge",
        redirect_uri=AnyUrl("https://client.example.test/callback"),
        redirect_uri_provided_explicitly=True,
    )
    client = OAuthClientInformationFull(
        client_id="mcp-client", redirect_uris=[params.redirect_uri]
    )

    url = await provider.authorize(client, params)
    query = parse_qs(urlparse(url).query)
    expected = requested_scopes or defaults
    assert query["scope"][0].split() == expected
    transaction = await provider._transaction_store.get(key=query["state"][0])
    assert transaction.scopes == expected
    assert provider.required_scopes == PROTOCOL_AUTH_SCOPES
    assert params.scopes == requested_scopes
