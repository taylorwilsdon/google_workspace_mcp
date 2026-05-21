import pytest

from auth.google_auth import start_auth_flow


class _FakeFlow:
    code_verifier = "verifier-123"

    def __init__(self):
        self.authorization_kwargs = None

    def authorization_url(self, **kwargs):
        self.authorization_kwargs = kwargs
        return "https://accounts.google.test/auth", "unused-state"


class _FakeSessionStore:
    def __init__(self):
        self.stored_state = None

    def store_oauth_state(self, oauth_state, *, session_id=None, code_verifier=None):
        self.stored_state = {
            "oauth_state": oauth_state,
            "session_id": session_id,
            "code_verifier": code_verifier,
        }


async def _fake_to_thread(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def _patch_start_auth_flow_dependencies(
    monkeypatch,
    *,
    transport_mode,
    oauth21_enabled=False,
):
    flow = _FakeFlow()
    store = _FakeSessionStore()

    async def fake_determine_oauth_prompt(**kwargs):  # noqa: ARG001
        return "consent"

    monkeypatch.setattr("auth.google_auth.get_current_scopes", lambda: ["scope.a"])
    monkeypatch.setattr("auth.google_auth.create_oauth_flow", lambda **kwargs: flow)
    monkeypatch.setattr(
        "auth.google_auth._determine_oauth_prompt",
        fake_determine_oauth_prompt,
    )
    monkeypatch.setattr("auth.google_auth.get_fastmcp_session_id", lambda: None)
    monkeypatch.setattr("auth.google_auth.get_oauth21_session_store", lambda: store)
    monkeypatch.setattr("auth.google_auth.get_transport_mode", lambda: transport_mode)
    monkeypatch.setattr("auth.google_auth.is_oauth21_enabled", lambda: oauth21_enabled)
    monkeypatch.setattr("auth.google_auth.asyncio.to_thread", _fake_to_thread)

    return flow, store


@pytest.mark.asyncio
async def test_start_auth_flow_auto_opens_browser_for_legacy_stdio(monkeypatch):
    opened_urls = []
    flow, store = _patch_start_auth_flow_dependencies(
        monkeypatch, transport_mode="stdio"
    )
    monkeypatch.setattr(
        "auth.google_auth.webbrowser.open",
        lambda url: opened_urls.append(url) or True,
    )

    message = await start_auth_flow(
        user_google_email="user@example.com",
        service_name="Google Gmail",
        redirect_uri="http://localhost:8000/oauth2callback",
    )

    assert opened_urls == ["https://accounts.google.test/auth"]
    assert flow.authorization_kwargs["login_hint"] == "user@example.com"
    assert flow.authorization_kwargs["access_type"] == "offline"
    assert flow.authorization_kwargs["prompt"] == "consent"
    assert store.stored_state["code_verifier"] == "verifier-123"
    assert "automatically opened in your browser" in message
    assert "Authorization URL: https://accounts.google.test/auth" in message


@pytest.mark.asyncio
async def test_start_auth_flow_does_not_open_browser_outside_stdio(monkeypatch):
    flow, _store = _patch_start_auth_flow_dependencies(
        monkeypatch, transport_mode="streamable-http"
    )

    def fail_if_opened(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("browser should not open outside stdio transport")

    monkeypatch.setattr("auth.google_auth.webbrowser.open", fail_if_opened)

    message = await start_auth_flow(
        user_google_email="user@example.com",
        service_name="Google Gmail",
        redirect_uri="http://localhost:8000/oauth2callback",
    )

    assert flow.authorization_kwargs["login_hint"] == "user@example.com"
    assert "automatically opened in your browser" not in message
    assert "Authorization URL: https://accounts.google.test/auth" in message


@pytest.mark.asyncio
async def test_start_auth_flow_does_not_open_browser_in_oauth21_mode(monkeypatch):
    _flow, _store = _patch_start_auth_flow_dependencies(
        monkeypatch, transport_mode="stdio", oauth21_enabled=True
    )

    def fail_if_opened(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("browser should not open in OAuth 2.1 mode")

    monkeypatch.setattr("auth.google_auth.webbrowser.open", fail_if_opened)

    message = await start_auth_flow(
        user_google_email="user@example.com",
        service_name="Google Gmail",
        redirect_uri="http://localhost:8000/oauth2callback",
    )

    assert "automatically opened in your browser" not in message
    assert "Authorization URL: https://accounts.google.test/auth" in message
