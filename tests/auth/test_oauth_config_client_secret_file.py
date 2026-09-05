"""Tests for the OAuthConfig client-secrets-file fallback.

The OAuth 2.1 protocol auth path (auth/oauth_config.py) must honor the same
client secrets file as the legacy per-user Google grant flow: environment
variables take precedence, and values missing from the environment fall back
to GOOGLE_CLIENT_SECRET_PATH (or the default client_secret.json).
"""

import json
import os

import pytest

from auth import oauth_config as oauth_config_module
from auth.client_secrets import get_client_secrets_path, load_client_secrets_file
from auth.oauth_config import OAuthConfig

_OAUTH_ENV_VARS = (
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_CLIENT_SECRET_PATH",
    "GOOGLE_CLIENT_SECRETS",
    "MCP_ENABLE_OAUTH21",
    "EXTERNAL_OAUTH21_PROVIDER",
    "WORKSPACE_MCP_STATELESS_MODE",
    "FASTMCP_SERVER_AUTH",
    "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_ID",
    "FASTMCP_SERVER_AUTH_GOOGLE_CLIENT_SECRET",
    "FASTMCP_SERVER_AUTH_GOOGLE_BASE_URL",
    "FASTMCP_SERVER_AUTH_GOOGLE_REDIRECT_PATH",
)


@pytest.fixture(autouse=True)
def clean_oauth_env(monkeypatch):
    """Start from a known environment so ambient values cannot leak in."""
    for var in _OAUTH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _write_client_secret(tmp_path, section_name, client_id, client_secret=None):
    section = {"client_id": client_id}
    if client_secret is not None:
        section["client_secret"] = client_secret
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({section_name: section}))
    return str(path)


def test_get_client_secrets_path_priority(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", "/explicit.json")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRETS", "/legacy.json")
    assert get_client_secrets_path() == "/explicit.json"

    monkeypatch.delenv("GOOGLE_CLIENT_SECRET_PATH")
    assert get_client_secrets_path() == "/legacy.json"

    monkeypatch.delenv("GOOGLE_CLIENT_SECRETS")
    assert get_client_secrets_path().endswith(os.path.join("client_secret.json"))


def test_load_client_secrets_file_rejects_null_document(tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text("null")

    with pytest.raises(ValueError, match="top-level JSON object"):
        load_client_secrets_file(str(path))


def test_load_client_secrets_file_rejects_non_object_top_level(tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps(["web"]))

    with pytest.raises(ValueError, match="top-level JSON object"):
        load_client_secrets_file(str(path))


def test_load_client_secrets_file_rejects_non_object_web_section(tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"web": None}))

    with pytest.raises(ValueError, match="'web' section must be a JSON object"):
        load_client_secrets_file(str(path))


def test_file_provides_credentials_when_env_unset(monkeypatch, tmp_path):
    path = _write_client_secret(tmp_path, "web", "file-id", "file-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", path)

    cfg = OAuthConfig()

    assert cfg.client_id == "file-id"
    assert cfg.client_secret == "file-secret"
    assert cfg.is_configured() is True
    assert cfg.is_public_client() is False
    metadata = cfg.get_authorization_server_metadata()
    assert metadata["token_endpoint_auth_methods_supported"] == [
        "client_secret_post",
        "client_secret_basic",
    ]


def test_env_takes_precedence_over_file(monkeypatch, tmp_path):
    path = _write_client_secret(tmp_path, "web", "file-id", "file-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", path)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")

    cfg = OAuthConfig()

    assert cfg.client_id == "env-id"
    assert cfg.client_secret == "env-secret"


def test_env_id_with_secret_from_file(monkeypatch, tmp_path):
    """Client id via env var, secret only in the client secrets file."""
    path = _write_client_secret(tmp_path, "web", "shared-id", "file-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", path)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "shared-id")

    cfg = OAuthConfig()

    assert cfg.client_id == "shared-id"
    assert cfg.client_secret == "file-secret"
    assert cfg.client_secrets_file == path
    assert cfg.is_public_client() is False


def test_file_secret_ignored_when_it_belongs_to_another_client(monkeypatch, tmp_path):
    """A file for a different client id must not be paired with the env client.

    Google rejects a client_id/client_secret pair that does not belong
    together, so a public client must stay public rather than silently become
    a broken confidential one.
    """
    path = _write_client_secret(tmp_path, "web", "other-id", "other-secret")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", path)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")

    cfg = OAuthConfig()

    assert cfg.client_id == "env-id"
    assert cfg.client_secret is None
    assert cfg.client_secrets_file is None
    assert cfg.is_public_client() is True


def test_installed_section_without_secret_stays_public_client(monkeypatch, tmp_path):
    path = _write_client_secret(tmp_path, "installed", "file-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", path)

    cfg = OAuthConfig()

    assert cfg.client_id == "file-id"
    assert cfg.client_secret is None
    assert cfg.is_configured() is True
    assert cfg.is_public_client() is True


def test_explicit_missing_file_raises_under_oauth21(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "GOOGLE_CLIENT_SECRET_PATH", str(tmp_path / "does-not-exist.json")
    )
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")

    with pytest.raises(ValueError, match="Failed to load client secrets file"):
        OAuthConfig()


def test_explicit_malformed_file_raises_under_oauth21(monkeypatch, tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"unexpected": {}}))
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", str(path))
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")

    with pytest.raises(ValueError, match="Failed to load client secrets file"):
        OAuthConfig()


def test_explicit_missing_file_is_not_fatal_without_oauth21(monkeypatch, tmp_path):
    """stdio, service account and legacy 2.0 resolve credentials lazily.

    A stale GOOGLE_CLIENT_SECRET_PATH must not take the whole server down for
    modes that never need the OAuth 2.1 client credentials at startup.
    """
    monkeypatch.setenv(
        "GOOGLE_CLIENT_SECRET_PATH", str(tmp_path / "does-not-exist.json")
    )

    cfg = OAuthConfig()

    assert cfg.client_id is None
    assert cfg.client_secret is None
    assert cfg.is_configured() is False


def test_explicit_path_expands_user(monkeypatch, tmp_path):
    """A ~ prefix reaches the server unexpanded from container/client configs."""
    path = _write_client_secret(tmp_path, "web", "home-id", "home-secret")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET_PATH", "~/client_secret.json")

    assert get_client_secrets_path() == path

    cfg = OAuthConfig()

    assert cfg.client_id == "home-id"
    assert cfg.client_secret == "home-secret"


def test_default_path_used_when_no_path_env(monkeypatch, tmp_path):
    path = _write_client_secret(tmp_path, "web", "default-id", "default-secret")
    monkeypatch.setattr(oauth_config_module, "get_client_secrets_path", lambda: path)

    cfg = OAuthConfig()

    assert cfg.client_id == "default-id"
    assert cfg.client_secret == "default-secret"


def test_missing_default_file_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(
        oauth_config_module,
        "get_client_secrets_path",
        lambda: str(tmp_path / "does-not-exist.json"),
    )

    cfg = OAuthConfig()

    assert cfg.client_id is None
    assert cfg.client_secret is None
    assert cfg.is_configured() is False


def test_malformed_default_file_is_ignored(monkeypatch, tmp_path):
    path = tmp_path / "client_secret.json"
    path.write_text("{not-json")
    monkeypatch.setattr(
        oauth_config_module, "get_client_secrets_path", lambda: str(path)
    )

    cfg = OAuthConfig()

    assert cfg.client_id is None
    assert cfg.client_secret is None


def test_no_file_access_when_env_complete(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")

    def _unused():
        raise AssertionError("client secrets file should not be read")

    monkeypatch.setattr(oauth_config_module, "get_client_secrets_path", _unused)

    cfg = OAuthConfig()

    assert cfg.client_id == "env-id"
    assert cfg.client_secret == "env-secret"


def test_legacy_load_client_secrets_prefers_env(monkeypatch, tmp_path):
    from auth import google_auth

    path = _write_client_secret(tmp_path, "web", "file-id", "file-secret")
    monkeypatch.setattr(google_auth, "CONFIG_CLIENT_SECRETS_PATH", path)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "env-id")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "env-secret")

    creds = google_auth.load_client_secrets(path)

    assert creds["client_id"] == "env-id"
    assert creds["client_secret"] == "env-secret"


def test_legacy_load_client_secrets_falls_back_to_file(monkeypatch, tmp_path):
    from auth import google_auth

    path = _write_client_secret(tmp_path, "web", "file-id", "file-secret")
    monkeypatch.setattr(google_auth, "CONFIG_CLIENT_SECRETS_PATH", path)

    creds = google_auth.load_client_secrets(path)

    assert creds["client_id"] == "file-id"
    assert creds["client_secret"] == "file-secret"
