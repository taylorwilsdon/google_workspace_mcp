"""Finding 5: per-request domain-wide delegation must not start without an allowlist.

Domain-wide delegation lets the service account act as any user in the Workspace
domain. When the impersonation subject is fixed by ``USER_GOOGLE_EMAIL`` that is a
single account and needs no allowlist. Trusted-gateway mode makes the subject vary
per request, so ``DWD_ALLOWED_DOMAINS`` becomes the only bound on which accounts can
be impersonated -- and it used to default to empty, i.e. unbounded.
"""

import pytest

from auth.oauth_config import OAuthConfig


def _service_account_gateway_env(monkeypatch):
    """Service account + trusted gateway: the per-request impersonation combination."""
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "/fake/key.json")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON", raising=False)
    monkeypatch.setenv("TRUST_GATEWAY_IDENTITY", "true")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "false")
    monkeypatch.setenv("EXTERNAL_OAUTH21_PROVIDER", "false")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "false")
    monkeypatch.setenv("GATEWAY_IDENTITY_JWKS_URL", "https://gw/jwks.json")
    monkeypatch.setenv("GATEWAY_IDENTITY_AUDIENCE", "workspace-mcp")


def test_per_request_dwd_without_allowlist_fails_startup(monkeypatch):
    _service_account_gateway_env(monkeypatch)
    monkeypatch.delenv("DWD_ALLOWED_DOMAINS", raising=False)

    with pytest.raises(ValueError, match="DWD_ALLOWED_DOMAINS"):
        OAuthConfig()


def test_per_request_dwd_with_allowlist_starts(monkeypatch):
    _service_account_gateway_env(monkeypatch)
    monkeypatch.setenv("DWD_ALLOWED_DOMAINS", "corp.com, partner.io")

    config = OAuthConfig()

    assert config.dwd_allowed_domains == ["corp.com", "partner.io"]
    assert config.service_account_enabled is True


def test_fixed_subject_dwd_does_not_require_allowlist(monkeypatch):
    """Single-user service-account deployments keep working without an allowlist.

    The subject is USER_GOOGLE_EMAIL, so there is no caller-influenced impersonation
    to bound and requiring the allowlist would be a breaking change with no gain.
    """
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_KEY_FILE", "/fake/key.json")
    monkeypatch.delenv("GOOGLE_SERVICE_ACCOUNT_KEY_JSON", raising=False)
    monkeypatch.setenv("TRUST_GATEWAY_IDENTITY", "false")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "false")
    monkeypatch.delenv("DWD_ALLOWED_DOMAINS", raising=False)

    config = OAuthConfig()

    assert config.service_account_enabled is True
    assert config.dwd_allowed_domains == []
