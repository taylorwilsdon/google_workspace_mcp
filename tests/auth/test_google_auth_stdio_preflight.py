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
        "auth.oauth_callback_server.get_transport_mode", lambda: "streamable-http"
    )
    monkeypatch.setattr(
        "auth.google_auth.get_oauth_redirect_uri",
        lambda: "http://localhost:8000/oauth2callback",
    )
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
            allow_auth_flow=True,
        )


@pytest.mark.asyncio
async def test_get_authenticated_google_service_skips_oauth_flow_when_disallowed(
    monkeypatch,
):
    """An optional service whose credentials lack its scope must not start an
    OAuth flow (which opens a browser in stdio); it fails so the caller degrades."""

    async def fake_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def fail_start_auth_flow(**kwargs):  # noqa: ARG001
        raise AssertionError("OAuth flow must not start for an optional service")

    monkeypatch.setattr("auth.google_auth.get_fastmcp_session_id", lambda: None)
    monkeypatch.setattr("auth.google_auth.get_fastmcp_context", None)
    monkeypatch.setattr("auth.google_auth.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr("auth.google_auth.get_credentials", lambda **kwargs: None)
    monkeypatch.setattr("auth.google_auth.start_auth_flow", fail_start_auth_flow)

    with pytest.raises(GoogleAuthenticationError, match="not starting OAuth"):
        await get_authenticated_google_service(
            service_name="people",
            version="v1",
            tool_name="test_tool",
            user_google_email="user@gmail.com",
            required_scopes=["scope.a"],
            allow_auth_flow=False,
        )
