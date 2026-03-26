from types import SimpleNamespace

import pytest

from auth.google_auth import GoogleAuthenticationError, get_authenticated_google_service


@pytest.mark.asyncio
async def test_get_authenticated_google_service_skips_preflight_outside_stdio(
    monkeypatch,
):
    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def fake_start_auth_flow(**kwargs):  # noqa: ARG001
        return "auth-url"

    def fail_if_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("callback preflight should not run outside stdio")

    monkeypatch.setattr("auth.google_auth.get_fastmcp_session_id", lambda: None)
    monkeypatch.setattr("auth.google_auth.get_fastmcp_context", None)
    monkeypatch.setattr("auth.google_auth.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("auth.google_auth.get_credentials", lambda **kwargs: None)
    monkeypatch.setattr(
        "auth.google_auth.get_transport_mode", lambda: "streamable-http"
    )
    monkeypatch.setattr(
        "auth.google_auth.get_oauth_redirect_uri",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr("auth.google_auth.get_oauth_config", lambda: SimpleNamespace())
    monkeypatch.setattr("auth.google_auth.start_auth_flow", fake_start_auth_flow)
    monkeypatch.setattr(
        "auth.oauth_callback_server.ensure_oauth_callback_available",
        fail_if_called,
    )

    with pytest.raises(GoogleAuthenticationError, match="auth-url"):
        await get_authenticated_google_service(
            service_name="gmail",
            version="v1",
            tool_name="test_tool",
            user_google_email="user@gmail.com",
            required_scopes=["scope.a"],
        )
