"""Regression tests for OAuth PKCE flow wiring."""

import os
import sys
from unittest.mock import patch


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from auth.google_auth import create_oauth_flow  # noqa: E402
from auth.google_auth import load_client_secrets_from_env  # noqa: E402


DUMMY_CLIENT_CONFIG = {
    "web": {
        "client_id": "dummy-client-id.apps.googleusercontent.com",
        "client_secret": "dummy-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


def test_create_oauth_flow_autogenerates_verifier_when_missing():
    expected_flow = object()
    with (
        patch(
            "auth.google_auth.load_client_secrets_from_env",
            return_value=DUMMY_CLIENT_CONFIG,
        ),
        patch(
            "auth.google_auth.Flow.from_client_config",
            return_value=expected_flow,
        ) as mock_from_client_config,
    ):
        flow = create_oauth_flow(
            scopes=["openid"],
            redirect_uri="http://localhost/callback",
            state="oauth-state-1",
        )

    assert flow is expected_flow
    args, kwargs = mock_from_client_config.call_args
    assert args[0] == DUMMY_CLIENT_CONFIG
    assert kwargs["autogenerate_code_verifier"] is True
    assert "code_verifier" not in kwargs


def test_create_oauth_flow_preserves_callback_verifier():
    expected_flow = object()
    with (
        patch(
            "auth.google_auth.load_client_secrets_from_env",
            return_value=DUMMY_CLIENT_CONFIG,
        ),
        patch(
            "auth.google_auth.Flow.from_client_config",
            return_value=expected_flow,
        ) as mock_from_client_config,
    ):
        flow = create_oauth_flow(
            scopes=["openid"],
            redirect_uri="http://localhost/callback",
            state="oauth-state-2",
            code_verifier="saved-verifier",
        )

    assert flow is expected_flow
    args, kwargs = mock_from_client_config.call_args
    assert args[0] == DUMMY_CLIENT_CONFIG
    assert kwargs["code_verifier"] == "saved-verifier"
    assert kwargs["autogenerate_code_verifier"] is False


def test_create_oauth_flow_file_config_still_enables_pkce():
    expected_flow = object()
    with (
        patch("auth.google_auth.load_client_secrets_from_env", return_value=None),
        patch("auth.google_auth.os.path.exists", return_value=True),
        patch(
            "auth.google_auth.Flow.from_client_secrets_file",
            return_value=expected_flow,
        ) as mock_from_file,
    ):
        flow = create_oauth_flow(
            scopes=["openid"],
            redirect_uri="http://localhost/callback",
            state="oauth-state-3",
        )

    assert flow is expected_flow
    _args, kwargs = mock_from_file.call_args
    assert kwargs["autogenerate_code_verifier"] is True
    assert "code_verifier" not in kwargs


def test_create_oauth_flow_allows_disabling_autogenerate_without_verifier():
    expected_flow = object()
    with (
        patch(
            "auth.google_auth.load_client_secrets_from_env",
            return_value=DUMMY_CLIENT_CONFIG,
        ),
        patch(
            "auth.google_auth.Flow.from_client_config",
            return_value=expected_flow,
        ) as mock_from_client_config,
    ):
        flow = create_oauth_flow(
            scopes=["openid"],
            redirect_uri="http://localhost/callback",
            state="oauth-state-4",
            autogenerate_code_verifier=False,
        )

    assert flow is expected_flow
    _args, kwargs = mock_from_client_config.call_args
    assert kwargs["autogenerate_code_verifier"] is False
    assert "code_verifier" not in kwargs


def test_load_client_secrets_from_env_supports_public_client():
    with patch.dict(
        os.environ,
        {
            "GOOGLE_OAUTH_CLIENT_ID": "public-client-id.apps.googleusercontent.com",
            "GOOGLE_OAUTH_REDIRECT_URI": "http://localhost:8000/oauth2callback",
        },
        clear=True,
    ):
        config = load_client_secrets_from_env()

    assert config is not None
    assert "installed" in config
    assert (
        config["installed"]["client_id"]
        == "public-client-id.apps.googleusercontent.com"
    )
    assert config["installed"]["client_secret"] == ""
