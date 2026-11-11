from urllib.parse import parse_qs, urlparse

import pytest
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.memory import MemoryStore

from auth.oauth_proxy_config import (
    MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS,
    MAX_OAUTH_REFRESH_TOKEN_EXPIRY_SECONDS,
    MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS,
    OAUTH_ACCESS_TOKEN_EXPIRY_ENV,
    OAUTH_GOOGLE_PROMPT_ENV,
    OAUTH_REFRESH_TOKEN_EXPIRY_ENV,
    OAUTH_REQUIRE_CONSENT_ENV,
    OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV,
    get_oauth_proxy_consent_kwargs,
    get_oauth_proxy_expiry_kwargs,
    get_oauth_proxy_google_prompt_kwargs,
)


def test_token_expiry_env_defaults_leave_fastmcp_behaviour_unchanged(monkeypatch):
    monkeypatch.delenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, raising=False)
    monkeypatch.delenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, raising=False)
    monkeypatch.delenv(OAUTH_REFRESH_TOKEN_EXPIRY_ENV, raising=False)

    assert get_oauth_proxy_expiry_kwargs() == {}


def test_token_expiry_env_is_forwarded_to_the_provider_kwargs(monkeypatch):
    monkeypatch.setenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, "120")
    monkeypatch.setenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, "86400")
    monkeypatch.setenv(OAUTH_REFRESH_TOKEN_EXPIRY_ENV, "2592000")

    assert get_oauth_proxy_expiry_kwargs() == {
        "token_expiry_threshold_seconds": 120,
        "fastmcp_access_token_expiry_seconds": 86400,
        "fallback_refresh_token_expiry_seconds": 2592000,
    }


def test_refresh_token_expiry_env_can_be_set_on_its_own(monkeypatch):
    monkeypatch.delenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, raising=False)
    monkeypatch.delenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, raising=False)
    monkeypatch.setenv(OAUTH_REFRESH_TOKEN_EXPIRY_ENV, "2592000")

    assert get_oauth_proxy_expiry_kwargs() == {
        "fallback_refresh_token_expiry_seconds": 2592000
    }


@pytest.mark.parametrize("value", ["not-a-number", "-1", "   "])
def test_token_expiry_env_ignores_unusable_values(monkeypatch, value):
    monkeypatch.setenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, value)
    monkeypatch.setenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, value)
    monkeypatch.setenv(OAUTH_REFRESH_TOKEN_EXPIRY_ENV, value)

    assert get_oauth_proxy_expiry_kwargs() == {}


def test_zero_is_valid_only_for_token_expiry_threshold(monkeypatch):
    monkeypatch.setenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, "0")
    monkeypatch.setenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, "0")
    monkeypatch.setenv(OAUTH_REFRESH_TOKEN_EXPIRY_ENV, "0")

    assert get_oauth_proxy_expiry_kwargs() == {"token_expiry_threshold_seconds": 0}


def test_token_expiry_env_rejects_values_above_safe_limits(monkeypatch):
    monkeypatch.setenv(
        OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV,
        str(MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS + 1),
    )
    monkeypatch.setenv(
        OAUTH_ACCESS_TOKEN_EXPIRY_ENV,
        str(MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS + 1),
    )
    monkeypatch.setenv(
        OAUTH_REFRESH_TOKEN_EXPIRY_ENV,
        str(MAX_OAUTH_REFRESH_TOKEN_EXPIRY_SECONDS + 1),
    )

    assert get_oauth_proxy_expiry_kwargs() == {}


def test_token_expiry_env_accepts_safe_limit_boundaries(monkeypatch):
    monkeypatch.setenv(
        OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV,
        str(MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS),
    )
    monkeypatch.setenv(
        OAUTH_ACCESS_TOKEN_EXPIRY_ENV,
        str(MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS),
    )
    monkeypatch.setenv(
        OAUTH_REFRESH_TOKEN_EXPIRY_ENV,
        str(MAX_OAUTH_REFRESH_TOKEN_EXPIRY_SECONDS),
    )

    assert get_oauth_proxy_expiry_kwargs() == {
        "token_expiry_threshold_seconds": 300,
        "fastmcp_access_token_expiry_seconds": 2_592_000,
        "fallback_refresh_token_expiry_seconds": 31_536_000,
    }


@pytest.mark.parametrize("value", [None, "", "   "])
def test_consent_env_unset_leaves_fastmcp_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(OAUTH_REQUIRE_CONSENT_ENV, raising=False)
    else:
        monkeypatch.setenv(OAUTH_REQUIRE_CONSENT_ENV, value)

    assert get_oauth_proxy_consent_kwargs() == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("always", True),
        ("ALWAYS", True),
        ("remember", "remember"),
        (" Remember ", "remember"),
    ],
)
def test_consent_env_maps_to_the_provider_kwarg(monkeypatch, value, expected):
    monkeypatch.setenv(OAUTH_REQUIRE_CONSENT_ENV, value)

    assert get_oauth_proxy_consent_kwargs() == {
        "require_authorization_consent": expected
    }


@pytest.mark.parametrize(
    "value", ["rememberr", "never", "false", "0", "external", "true"]
)
def test_consent_env_rejects_unsupported_values(monkeypatch, value):
    monkeypatch.setenv(OAUTH_REQUIRE_CONSENT_ENV, value)

    with pytest.raises(ValueError, match=OAUTH_REQUIRE_CONSENT_ENV):
        get_oauth_proxy_consent_kwargs()


@pytest.mark.parametrize("value", [None, "", "   "])
def test_google_prompt_env_unset_leaves_fastmcp_default(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(OAUTH_GOOGLE_PROMPT_ENV, raising=False)
    else:
        monkeypatch.setenv(OAUTH_GOOGLE_PROMPT_ENV, value)

    assert get_oauth_proxy_google_prompt_kwargs() == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("select_account consent", "select_account consent"),
        (" Select_Account   CONSENT ", "select_account consent"),
        ("select_account\tconsent", "select_account consent"),
        ("consent", "consent"),
    ],
)
def test_google_prompt_env_maps_to_extra_authorize_params(monkeypatch, value, expected):
    monkeypatch.setenv(OAUTH_GOOGLE_PROMPT_ENV, value)

    assert get_oauth_proxy_google_prompt_kwargs() == {
        "extra_authorize_params": {"prompt": expected}
    }


@pytest.mark.parametrize(
    "value",
    [
        "none",
        "select_account",
        "consent select_account",
        "consent consent",
        "none none",
        "login",
        "select-account",
        "consent,select_account",
    ],
)
def test_google_prompt_env_rejects_unsupported_values(monkeypatch, value):
    monkeypatch.setenv(OAUTH_GOOGLE_PROMPT_ENV, value)

    with pytest.raises(ValueError, match=OAUTH_GOOGLE_PROMPT_ENV):
        get_oauth_proxy_google_prompt_kwargs()


def test_google_prompt_reaches_google_authorize_url(monkeypatch):
    monkeypatch.setenv(OAUTH_GOOGLE_PROMPT_ENV, "select_account consent")
    provider = GoogleProvider(
        client_id="client-id.apps.googleusercontent.com",
        client_secret="client-secret",
        base_url="https://workspace-mcp.example.test",
        jwt_signing_key="k" * 32,
        client_storage=MemoryStore(),
        **get_oauth_proxy_google_prompt_kwargs(),
    )

    url = provider._build_upstream_authorize_url("txn-id", {"scopes": ["openid"]})
    query = parse_qs(urlparse(url).query)

    assert query["prompt"] == ["select_account consent"]
    assert query["access_type"] == ["offline"]
