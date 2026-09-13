"""A legacy single-client registry must not shadow call-time resolution.

`load_registry_from_env` synthesizes a one-client registry from
GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET when no registry document
is configured. That registry carries no routing information -- one client, no
emails or domains map -- so it can only ever name the same credentials the
existing environment-and-file resolution would reach on its own.

Answering from it anyway would not be merely redundant. `get_oauth_config()` is
a process-lifetime singleton, so a client taken from it reflects the
environment as it stood when the config was FIRST built, while the fallback
path reads `os.environ` at call time. Deployments that never asked for
multi-client would silently have a call-time read converted into a start-up
read.

`resolve_oauth_client` therefore returns None for a legacy-env registry, and
these tests pin that -- separately from the registry-document case, which must
still be honoured, so that a fix for one cannot quietly disable the other.
"""

import pytest

from auth.oauth_clients import (
    SOURCE_LEGACY_ENV,
    SOURCE_REGISTRY_DOCUMENT,
    load_registry_from_env,
)
from auth.oauth_config import OAuthConfig
from auth.google_auth import resolve_oauth_client


REGISTRY_DOCUMENT = """
{
  "clients": {
    "work": {"client_id": "work-id.apps.googleusercontent.com",
             "client_secret": "work-secret"}
  },
  "emails": {"someone@example.com": "work"},
  "default": "work"
}
"""


@pytest.fixture
def legacy_env(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENTS_FILE", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENTS", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "legacy-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "legacy-secret")


def test_legacy_env_registry_is_labelled_as_such(legacy_env):
    registry = load_registry_from_env()

    assert registry is not None
    assert registry.source == SOURCE_LEGACY_ENV


def test_registry_document_is_labelled_as_such(monkeypatch, tmp_path):
    path = tmp_path / "clients.json"
    path.write_text(REGISTRY_DOCUMENT)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS_FILE", str(path))

    registry = load_registry_from_env()

    assert registry is not None
    assert registry.source == SOURCE_REGISTRY_DOCUMENT


def test_legacy_env_registry_defers_to_call_time_resolution(legacy_env, monkeypatch):
    """The whole point: a legacy registry must not answer for the flow."""
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)

    assert cfg.client_registry is not None
    assert cfg.client_registry.source == SOURCE_LEGACY_ENV
    assert resolve_oauth_client(user_google_email="anyone@example.com") is None
    assert resolve_oauth_client() is None


def test_registry_document_is_still_honoured(monkeypatch, tmp_path):
    """The deferral above must not disable the feature it sits next to."""
    path = tmp_path / "clients.json"
    path.write_text(REGISTRY_DOCUMENT)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENTS_FILE", str(path))
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)

    resolved = resolve_oauth_client(user_google_email="someone@example.com")

    assert resolved is not None
    assert resolved.key == "work"


def test_explicit_client_key_still_works_against_a_legacy_registry(
    legacy_env, monkeypatch
):
    """Deferral is for the unselected case only.

    A caller naming a client key has asked a question the fallback path cannot
    answer, so the registry must still be consulted even when it came from the
    legacy environment pair.
    """
    cfg = OAuthConfig()
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: cfg)
    (key,) = cfg.client_registry.keys

    resolved = resolve_oauth_client(client_key=key)

    assert resolved is not None
    assert resolved.client_id == "legacy-id.apps.googleusercontent.com"
