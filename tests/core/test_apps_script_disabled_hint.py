"""Tests for the Apps Script user-setting hint in handle_http_errors."""

from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.utils import APPS_SCRIPT_USER_SETTINGS_URL, handle_http_errors
from gappsscript.apps_script_tools import _run_script_function_impl

SCRIPT_URI = "https://script.googleapis.com/v1/projects/abc123/content?alt=json"

DISABLED_403 = (
    b'{"error": {"code": 403, "message": "User has not enabled the Apps Script '
    b"API. Enable it by visiting https://script.google.com/home/usersettings "
    b'then retry.", "status": "PERMISSION_DENIED"}}'
)
SERVICE_ERROR_503 = b"<html><p>Service error -27.</p></html>"


def _raising_tool(status: int, content: bytes, uri: str):
    @handle_http_errors("get_script_project", is_read_only=True)
    async def tool(user_google_email: str = "user@example.com"):
        raise HttpError(httplib2.Response({"status": status}), content, uri=uri)

    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content"),
    [(403, DISABLED_403), (503, SERVICE_ERROR_503)],
    ids=["documented-403", "observed-503"],
)
async def test_disabled_apps_script_api_gets_settings_hint(status, content):
    tool = _raising_tool(status, content, SCRIPT_URI)

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert APPS_SCRIPT_USER_SETTINGS_URL in message
    assert f"HTTP {status}" in message
    assert "user@example.com" in message
    assert "re-authenticate" not in message
    assert "<html>" not in message


@pytest.mark.asyncio
async def test_other_apps_script_403_keeps_reauth_hint():
    content = (
        b'{"error": {"code": 403, "message": "The caller does not have '
        b'permission", "status": "PERMISSION_DENIED"}}'
    )
    tool = _raising_tool(403, content, SCRIPT_URI)

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert APPS_SCRIPT_USER_SETTINGS_URL not in message
    assert "re-authenticate" in message


@pytest.mark.asyncio
async def test_other_apps_script_503_keeps_google_error():
    content = b"<html><p>The service is currently unavailable.</p></html>"
    tool = _raising_tool(503, content, SCRIPT_URI)

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert APPS_SCRIPT_USER_SETTINGS_URL not in message
    assert "currently unavailable" in message


@pytest.mark.asyncio
async def test_503_from_other_services_is_not_attributed_to_apps_script():
    tool = _raising_tool(
        503, b"backend error", "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    )

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    assert APPS_SCRIPT_USER_SETTINGS_URL not in str(excinfo.value)


@pytest.mark.asyncio
async def test_run_script_function_surfaces_settings_hint():
    service = Mock()
    service.scripts().run.return_value.execute.side_effect = HttpError(
        httplib2.Response({"status": 503}),
        SERVICE_ERROR_503,
        uri="https://script.googleapis.com/v1/scripts/deploy123:run",
    )
    tool = handle_http_errors("run_script_function", service_type="script")(
        _run_script_function_impl
    )

    with pytest.raises(Exception) as excinfo:
        await tool(
            service,
            user_google_email="user@example.com",
            script_id="abc123",
            function_name="main",
            deployment_id="deploy123",
        )

    assert APPS_SCRIPT_USER_SETTINGS_URL in str(excinfo.value)
