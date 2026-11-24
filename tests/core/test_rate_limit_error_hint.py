"""Tests for rate-limit and quota 403/429 handling in handle_http_errors."""

from collections.abc import Awaitable, Callable

import fastmcp
import httplib2
import pytest
from fastmcp.exceptions import ToolError
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

ERROR_INFO_403 = (
    b'{"error": {"code": 403, "message": "Quota exceeded.", "details": ['
    b'{"@type": "type.googleapis.com/google.rpc.ErrorInfo", '
    b'"reason": "RATE_LIMIT_EXCEEDED", "domain": "googleapis.com"}]}}'
)

TOP_LEVEL_REASON_403 = (
    b'{"error": {"code": 403, "message": "Rate limit exceeded.", '
    b'"reason": "rateLimitExceeded"}}'
)

BATCH_LIST_403 = (
    b'[{"error": {"code": 403, "message": "User-rate limit exceeded.", '
    b'"errors": [{"reason": "userRateLimitExceeded"}]}}]'
)

RATE_LIMIT_429 = (
    b'{"error": {"code": 429, "message": "Too many concurrent requests for user."}}'
)


def _raising_tool(
    status: int, content: bytes, uri: str = GMAIL_URI
) -> Callable[..., Awaitable[None]]:
    @handle_http_errors("get_gmail_message_content", is_read_only=True)
    async def tool(user_google_email: str = "user@example.com") -> None:
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
        (403, ERROR_INFO_403),
        (403, TOP_LEVEL_REASON_403),
        (403, BATCH_LIST_403),
        (429, RATE_LIMIT_429),
    ],
    ids=[
        "rateLimitExceeded-403",
        "userRateLimitExceeded-403",
        "quotaExceeded-403",
        "dailyLimitExceeded-403",
        "RESOURCE_EXHAUSTED-403",
        "QuotaFailure-403",
        "ErrorInfo-RATE_LIMIT_EXCEEDED-403",
        "top-level-reason-403",
        "list-payload-403",
        "http-429",
    ],
)
async def test_rate_limit_error_does_not_suggest_reauth(
    status: int, content: bytes
) -> None:
    tool = _raising_tool(status, content)

    with pytest.raises(ToolError) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "rate limit or quota exceeded" in message
    assert "not an authentication failure" in message
    assert "re-authenticate" not in message
    assert "bearer token" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b'{"error": {"code": 403, "message": "Request had insufficient '
        b'authentication scopes.", "errors": [{"domain": "global", '
        b'"reason": "insufficientPermissions"}]}}',
        b'{"error": {"code": 403, "message": "Drive storage quota exceeded.", '
        b'"errors": [{"domain": "usageLimits", "reason": "storageQuotaExceeded"}]}}',
        b"<html><body>403 Forbidden</body></html>",
    ],
    ids=["insufficientPermissions", "storageQuotaExceeded", "non-json"],
)
async def test_non_rate_limit_403_still_suggests_reauth(content: bytes) -> None:
    tool = _raising_tool(403, content)

    with pytest.raises(ToolError) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "re-authenticate" in message
    assert "rate limit" not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        b'{"error": {"message": "Forbidden", "reason": []}}',
        b'{"error": {"message": "Forbidden", "errors": 123, "details": 456}}',
        b'{"error": {"message": "Forbidden", "errors": [{"reason": []}], "details": [{"reason": {}, "@type": 1}]}}',
    ],
    ids=["unhashable-reason", "non-list-collections", "unhashable-detail-fields"],
)
async def test_malformed_403_fields_do_not_raise_type_error(content: bytes) -> None:
    tool = _raising_tool(403, content)

    with pytest.raises(ToolError) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "re-authenticate" in message
    assert "rate limit" not in message


@pytest.mark.asyncio
async def test_rate_limit_error_respects_mask_error_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fastmcp.settings, "mask_error_details", True)
    tool = _raising_tool(403, GMAIL_RATE_LIMIT_403)

    with pytest.raises(ToolError) as excinfo:
        await tool(user_google_email="user@example.com")

    message = str(excinfo.value)
    assert "HTTP 403" in message
    assert "Total Query Cost" not in message
    assert "rate limit or quota exceeded" in message
    assert "re-authenticate" not in message
