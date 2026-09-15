"""Suite-wide fixtures.

Currently one: containment for environment variables that PRODUCTION code
writes into ``os.environ``, which pytest's ``monkeypatch`` cannot undo because
it did not set them.
"""

import pytest

# OAuthConfig._apply_fastmcp_google_env writes these when they are absent and a
# client_id is configured (auth/oauth_config.py). monkeypatch restores only what
# it set itself, so without pinning, the first test to construct a configured
# OAuthConfig leaves its client id -- and its client SECRET -- in the
# environment for every test that follows.
_FASTMCP_ENV_VARS = (
    "FASTMCP_SERVER_AUTH",
    "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET",
    "FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL",
    "FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH",
)


@pytest.fixture(autouse=True)
def pinned_fastmcp_env():
    """Stop OAuthConfig's env writes outliving the test that made them.

    Pre-setting each name makes the internal ``_set_if_absent`` a no-op, and
    monkeypatch CAN restore a value it set itself.

    Deliberately autouse, and deliberately at the SUITE root rather than in
    tests/auth/. Both placements were measured on 2026-09-08: scoped to
    tests/auth/ the directory came back clean, and the full suite still leaked
    FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID='env-id' from
    tests/core/test_http_oauth_scope_config.py. The leak follows OAuthConfig,
    not a directory, so a fixture placed where the class is heavily tested
    reports success while the actual containment boundary sits elsewhere.

    Opt-in was the original design and is what let it spread: only three tests
    in the whole suite had asked for the protection while four modules leaked.
    A fixture that must be remembered is one the next test added will forget.

    USES ITS OWN MonkeyPatch, not the shared ``monkeypatch`` fixture, and that
    is load-bearing rather than stylistic. ``tests/auth/conftest.py``'s
    ``isolated_gateway_config`` calls ``monkeypatch.undo()`` in its teardown and
    then rebuilds the config singleton. Sharing one MonkeyPatch means that
    ``undo()`` erases THESE pins first, so the rebuild finds the names absent
    and writes the real client id and secret back into the environment -- after
    every assertion in the test body has already run and passed. Measured
    2026-09-08 with a fake ambient secret: shared, the value survives the whole
    session; with a private MonkeyPatch, it is gone. The suite is green either
    way, which is precisely why this needs saying here.
    """
    patcher = pytest.MonkeyPatch()
    for name in _FASTMCP_ENV_VARS:
        patcher.setenv(name, "pinned-by-test")
    try:
        yield
    finally:
        patcher.undo()
