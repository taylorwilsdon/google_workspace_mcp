"""Shared fixtures for auth tests."""

import pytest

from auth import oauth_config

# Trusted-gateway mode is the first branch in AuthInfoMiddleware, so ambient values
# for these silently reroute every middleware test.
_GATEWAY_ENV_VARS = (
    "MCP_TRUST_GATEWAY_IDENTITY",
    "GATEWAY_IDENTITY_HEADER",
    "GATEWAY_IDENTITY_JWKS_URL",
    "GATEWAY_IDENTITY_AUDIENCE",
    "GATEWAY_IDENTITY_ISSUER",
    "GATEWAY_IDENTITY_ALGORITHMS",
)


@pytest.fixture(autouse=True)
def isolated_gateway_config(monkeypatch):
    """Run each auth test against a gateway-disabled config, then restore.

    ``get_oauth_config`` memoises a module-level singleton (auth/oauth_config.py),
    so both halves matter: clearing the env before the reload stops ambient
    configuration from leaking in, and reloading again on teardown stops a test that
    set gateway variables from leaving a contaminated singleton behind for whatever
    runs next.
    """
    for var in _GATEWAY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    oauth_config.reload_oauth_config()
    try:
        yield
    finally:
        monkeypatch.undo()
        oauth_config.reload_oauth_config()
