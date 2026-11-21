"""Tests for rate-limit and quota 403/429 handling in handle_http_errors."""

import fastmcp
import httplib2
import pytest
from googleapiclient.errors import HttpError

from core.utils import handle_http_errors

GMAIL_URI = "https://gmail.googleapis.com/gmail/v1/users/me/messages/abc123?alt=json"

GMAIL_RATE_LIMIT_403 = (
    b'{"error": {"code": 403, "message": "Quota exceeded for quota metric '
    b"'Total Query Cost' and limit 'Units per minute per user' of service "
    b'\'gmail.googleapis.com\'.", "errors": [{"message": "Quota exceeded", '
    b'"domain": "usageLimits", "reason": "rateLimitExceeded"}]}}'
)

USER_RATE_LIMIT_403 = (
    b'{"error": {"code": 403, "message": "User-rate limit exceeded.", '
    b'"errors": [{"domain": "usageLimits", "reason": "userRateLimitExceeded"}]}}'
)

QUOTA_EXCEEDED_403 = (
    b'{"error": {"code": 403, "message": "Quota exceeded.", '
    b'"errors": [{"domain": "usageLimits", "reason": "quotaExceeded"}]}}'
)

DAILY_LIMIT_403 = (
    b'{"error": {"code": 403, "message": "Daily Limit Exceeded", '
    b'"errors": [{"domain": "usageLimits", "reason": "dailyLimitExceeded"}]}}'
)

RESOURCE_EXHAUSTED_403 = (
    b'{"error": {"code": 403, "message": "Quota exceeded.", '
    b'"status": "RESOURCE_EXHAUSTED"}}'
)

GRPC_QUOTA_FAILURE_403 = (
    b'{"error": {"code": 403, "message": "Quota exceeded.", "details": ['
    b'{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": []}]}}'
)

RATE_LIMIT_429 = (
    b'{"error": {"code": 429, "message": "Too many concurrent requests for user."}}'
)


def _raising_tool(status: int, content: bytes, uri: str = GMAIL_URI):
    @handle_http_errors("get_gmail_message_content", is_read_only=True)
    async def tool(user_google_email: str = "user@example.com"):
        raise HttpError(httplib2.Response({"status": status}), content, uri=uri)

    return tool


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "content"),
    [
        (403, GMAIL_RATE_LIMIT_403),
        (403, USER_RATE_LIMIT_403),
        (403, QUOTA_EXCEEDED_403),
        (403, DAILY_LIMIT_403),
        (403, RESOURCE_EXHAUSTED_403),
        (403, GRPC_QUOTA_FAILURE_403),
        (429, RATE_LIMIT_429),
    ],
    ids=[
        "rateLimitExceeded-403",
        "userRateLimitExceeded-403",
        "quotaExceeded-403",
        "dailyLimitExceeded-403",
        "RESOURCE_EXHAUSTED-403",
        "QuotaFailure-403",
        "http-429",
    ],
)
async def test_rate_limit_error_does_not_suggest_reauth(status, content):
    tool = _raising_tool(status, content)

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "rate limit or quota exceeded" in message
    assert "not an authentication failure" in message
    assert "re-authenticate" not in message
    assert "bearer token" not in message


@pytest.mark.asyncio
async def test_permission_denied_403_still_suggests_reauth():
    content = (
        b'{"error": {"code": 403, "message": "Request had insufficient '
        b'authentication scopes.", "errors": [{"domain": "global", '
        b'"reason": "insufficientPermissions"}]}}'
    )
    tool = _raising_tool(403, content)

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "re-authenticate" in message
    assert "rate limit" not in message


@pytest.mark.asyncio
async def test_rate_limit_error_respects_mask_error_details(monkeypatch):
    monkeypatch.setattr(fastmcp.settings, "mask_error_details", True)
    tool = _raising_tool(403, GMAIL_RATE_LIMIT_403)

    with pytest.raises(Exception) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "HTTP 403" in message
    assert "Total Query Cost" not in message
    assert "rate limit or quota exceeded" in message
    assert "re-authenticate" not in message
