from types import SimpleNamespace

import pytest

from core.server import start_google_auth


@pytest.mark.asyncio
async def test_start_google_auth_skips_preflight_outside_stdio(monkeypatch):
    async def fake_start_auth_flow(**kwargs):  # noqa: ARG001
        return "auth-url"

    def fail_if_called(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("callback preflight should not run outside stdio")

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr("core.server.get_transport_mode", lambda: "streamable-http")
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)
    monkeypatch.setattr(
        "auth.oauth_callback_server.ensure_oauth_callback_available",
        fail_if_called,
    )

    result = await start_google_auth("Gmail", "user@gmail.com")

    assert result == "auth-url"


@pytest.mark.asyncio
async def test_start_google_auth_preflights_in_stdio(monkeypatch):
    calls = []

    async def fake_to_thread(fn, *args, **kwargs):
        calls.append((fn.__name__, args, kwargs))
        return fn(*args, **kwargs)

    async def fake_start_auth_flow(**kwargs):  # noqa: ARG001
        return "auth-url"

    def fake_ensure(transport_mode, port, base_uri):
        calls.append(("ensure", (transport_mode, port, base_uri), {}))
        return True, ""

    monkeypatch.setattr("core.server.is_oauth21_enabled", lambda: False)
    monkeypatch.setattr("core.server.check_client_secrets", lambda: None)
    monkeypatch.setattr("core.server.get_transport_mode", lambda: "stdio")
    monkeypatch.setattr("core.server.asyncio.to_thread", fake_to_thread)
    monkeypatch.setattr(
        "core.server.get_oauth_redirect_uri_for_current_mode",
        lambda: "http://localhost:8000/oauth2callback",
    )
    monkeypatch.setattr("core.server.start_auth_flow", fake_start_auth_flow)
    monkeypatch.setattr(
        "auth.oauth_config.get_oauth_config",
        lambda: SimpleNamespace(port=8000, base_uri="http://localhost"),
    )
    monkeypatch.setattr(
        "auth.oauth_callback_server.ensure_oauth_callback_available",
        fake_ensure,
    )

    result = await start_google_auth("Gmail", "user@gmail.com")

    assert result == "auth-url"
    assert ("ensure", ("stdio", 8000, "http://localhost"), {}) in calls
