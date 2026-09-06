import pytest

from auth.oauth_proxy_config import (
    MAX_OAUTH_ACCESS_TOKEN_EXPIRY_SECONDS,
    MAX_OAUTH_TOKEN_EXPIRY_THRESHOLD_SECONDS,
    OAUTH_ACCESS_TOKEN_EXPIRY_ENV,
    OAUTH_REQUIRE_CONSENT_ENV,
    OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV,
    get_oauth_proxy_consent_kwargs,
    get_oauth_proxy_expiry_kwargs,
)


def test_token_expiry_env_defaults_leave_fastmcp_behaviour_unchanged(monkeypatch):
    monkeypatch.delenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, raising=False)
    monkeypatch.delenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, raising=False)

    assert get_oauth_proxy_expiry_kwargs() == {}


def test_token_expiry_env_is_forwarded_to_the_provider_kwargs(monkeypatch):
    monkeypatch.setenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, "120")
    monkeypatch.setenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, "86400")

    assert get_oauth_proxy_expiry_kwargs() == {
        "token_expiry_threshold_seconds": 120,
        "fastmcp_access_token_expiry_seconds": 86400,
    }


@pytest.mark.parametrize("value", ["not-a-number", "-1", "   "])
def test_token_expiry_env_ignores_unusable_values(monkeypatch, value):
    monkeypatch.setenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, value)
    monkeypatch.setenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, value)

    assert get_oauth_proxy_expiry_kwargs() == {}


def test_zero_is_valid_only_for_token_expiry_threshold(monkeypatch):
    monkeypatch.setenv(OAUTH_TOKEN_EXPIRY_THRESHOLD_ENV, "0")
    monkeypatch.setenv(OAUTH_ACCESS_TOKEN_EXPIRY_ENV, "0")

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

    assert get_oauth_proxy_expiry_kwargs() == {
        "token_expiry_threshold_seconds": 300,
        "fastmcp_access_token_expiry_seconds": 2_592_000,
    }


def test_consent_env_default_leaves_fastmcp_behaviour_unchanged(monkeypatch):
    monkeypatch.delenv(OAUTH_REQUIRE_CONSENT_ENV, raising=False)

    assert get_oauth_proxy_consent_kwargs() == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("always", True),
        ("TRUE", True),
        ("on", True),
        ("remember", "remember"),
        ("External", "external"),
        ("never", False),
        ("0", False),
    ],
)
def test_consent_env_maps_to_the_provider_kwarg(monkeypatch, value, expected):
    monkeypatch.setenv(OAUTH_REQUIRE_CONSENT_ENV, value)

    assert get_oauth_proxy_consent_kwargs() == {
        "require_authorization_consent": expected
    }


@pytest.mark.parametrize("value", ["maybe", "  ", "rememberr"])
def test_consent_env_ignores_unusable_values(monkeypatch, value):
    monkeypatch.setenv(OAUTH_REQUIRE_CONSENT_ENV, value)

    assert get_oauth_proxy_consent_kwargs() == {}
