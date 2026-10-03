"""handle_http_errors points users at their Apps Script API setting.

With the per-user "Google Apps Script API" switch off, script.googleapis.com
answers 403 "User has not enabled the Apps Script API" or, in practice, 503
"Service error -27" with an HTML page. Both should produce an actionable hint
rather than a raw error or a re-authentication suggestion.
"""

import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.utils import APPS_SCRIPT_USER_SETTINGS_URL, handle_http_errors

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
async def test_503_from_other_services_is_not_attributed_to_apps_script():
    tool = _raising_tool(
        503, b"backend error", "https://gmail.googleapis.com/gmail/v1/users/me/messages"
    )

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    assert APPS_SCRIPT_USER_SETTINGS_URL not in str(excinfo.value)
