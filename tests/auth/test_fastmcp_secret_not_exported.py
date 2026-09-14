"""The OAuth client secret must not be written into the process environment.

`OAuthConfig._apply_fastmcp_google_env` mirrored the configured client secret
into `FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET`. Nothing reads that variable --
not this repository, not the pinned FastMCP -- so the write placed a live
credential in `os.environ` for no consumer, where every child process inherits
it.

Two properties are pinned separately, because a change that satisfied only one
would still be wrong:

  * the secret is not exported (this is the fix), and
  * a value the operator set themselves is left alone (this is what the fix
    must not break -- `_set_if_absent` never overwrote an existing value, and
    an operator configuring FastMCP directly must stay in control).
"""

import os

import pytest

from auth.oauth_config import OAuthConfig

SECRET_VAR = "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET"
CLIENT_ID_VAR = "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID"


@pytest.fixture
def configured_env(monkeypatch):
    """A client id AND secret, which is what makes the write path run at all."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "probe.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "probe-client-secret")
    monkeypatch.delenv(SECRET_VAR, raising=False)
    monkeypatch.delenv(CLIENT_ID_VAR, raising=False)


def test_client_secret_is_not_written_into_the_environment(configured_env):
    config = OAuthConfig()

    # Assert on a boolean rather than on the value: a bare
    # `assert os.environ.get(SECRET_VAR) is None` renders the real secret into
    # pytest output at exactly the moment this guard stops working, which is
    # also the moment someone pastes that output into an issue.
    exported = SECRET_VAR in os.environ
    assert not exported, f"{SECRET_VAR} was exported (value withheld)"

    # The config itself must still hold the secret -- this is about where it is
    # NOT put, not about failing to read it.
    assert config.client_secret == "probe-client-secret"


def test_an_operator_supplied_value_is_still_left_alone(monkeypatch):
    """Removing the write must not start clobbering a value someone else set."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "probe.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "probe-client-secret")
    monkeypatch.setenv(SECRET_VAR, "operator-supplied")

    OAuthConfig()

    assert os.environ[SECRET_VAR] == "operator-supplied"


def test_the_non_secret_mirror_is_unchanged(configured_env):
    """Scope check: this change removes the SECRET write and nothing else.

    Without this, the fix could quietly delete the whole mirror and every other
    assertion here would still pass.
    """
    OAuthConfig()

    assert os.environ.get(CLIENT_ID_VAR) == "probe.apps.googleusercontent.com"
