import fastmcp
from fastmcp.server.http import StreamableHTTPASGIApp
import pytest

from auth.oauth_config import is_stateless_mode, reload_oauth_config
from core.server import SecureFastMCP, get_session_idle_timeout

_ENV = "WORKSPACE_MCP_SESSION_IDLE_TIMEOUT"


def _session_manager(app):
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        endpoint = getattr(endpoint, "app", endpoint)
        if isinstance(endpoint, StreamableHTTPASGIApp):
            return endpoint.session_manager
    raise AssertionError("streamable-HTTP endpoint not found")


async def _idle_timeout_after_startup(**http_app_kwargs):
    app = SecureFastMCP(name="test_server").http_app(**http_app_kwargs)
    async with app.router.lifespan_context(app):
        return _session_manager(app).session_idle_timeout


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 3660), ("", 3660), (" 600 ", 600), ("0", None)],
)
def test_get_session_idle_timeout(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(_ENV, raising=False)
    else:
        monkeypatch.setenv(_ENV, raw)

    assert get_session_idle_timeout() == expected


@pytest.mark.parametrize("raw", ["-1", "abc", "1.5"])
def test_get_session_idle_timeout_rejects_invalid(monkeypatch, raw):
    monkeypatch.setenv(_ENV, raw)

    with pytest.raises(ValueError, match=_ENV):
        get_session_idle_timeout()


@pytest.mark.asyncio
async def test_http_app_default_outlasts_google_access_token(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)

    assert await _idle_timeout_after_startup() == 3600 + 60


@pytest.mark.asyncio
async def test_http_app_applies_idle_timeout_at_startup(monkeypatch):
    monkeypatch.setenv(_ENV, "600")

    assert await _idle_timeout_after_startup() == 600


@pytest.mark.asyncio
async def test_http_app_leaves_idle_timeout_unset_when_disabled(monkeypatch):
    monkeypatch.setenv(_ENV, "0")

    assert await _idle_timeout_after_startup() is None


@pytest.mark.asyncio
async def test_http_app_zero_defers_to_fastmcp_timeout(monkeypatch):
    monkeypatch.setenv(_ENV, "0")
    monkeypatch.setattr(fastmcp.settings, "http_session_idle_timeout", 300)

    assert await _idle_timeout_after_startup() == 300


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [None, 300])
async def test_http_app_preserves_explicit_timeout(monkeypatch, timeout):
    monkeypatch.setenv(_ENV, "invalid")

    assert await _idle_timeout_after_startup(session_idle_timeout=timeout) == timeout


@pytest.mark.asyncio
async def test_http_app_skips_stateless_session_manager(monkeypatch):
    monkeypatch.setenv(_ENV, "invalid")

    assert await _idle_timeout_after_startup(stateless_http=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("http_app_kwargs", [{}, {"stateless_http": None}])
async def test_http_app_skips_fastmcp_stateless_setting(monkeypatch, http_app_kwargs):
    monkeypatch.setenv(_ENV, "invalid")
    monkeypatch.setattr(fastmcp.settings, "stateless_http", True)

    assert await _idle_timeout_after_startup(**http_app_kwargs) is None


@pytest.mark.asyncio
async def test_http_app_applies_timeout_when_fastmcp_settings_stateful(monkeypatch):
    monkeypatch.setenv(_ENV, "600")
    monkeypatch.setenv("MCP_ENABLE_OAUTH21", "true")
    monkeypatch.setenv("WORKSPACE_MCP_STATELESS_MODE", "true")
    reload_oauth_config()
    assert is_stateless_mode()
    monkeypatch.setattr(fastmcp.settings, "stateless_http", False)

    assert await _idle_timeout_after_startup() == 600
