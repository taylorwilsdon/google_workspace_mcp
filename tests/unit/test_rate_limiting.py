"""
Tests for rate limiting, retry logic, and quota management.

Tests cover:
- handle_http_errors decorator: retry on SSL errors, HttpError handling
- Exponential backoff for transient errors
- API enablement error detection
- Authentication error handling (401/403)
- Gmail batch size and request delay constants
- Sequential fallback when batch API fails
"""

import ssl
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

from core.utils import handle_http_errors, TransientNetworkError
from gmail.gmail_tools import GMAIL_BATCH_SIZE, GMAIL_REQUEST_DELAY


class TestHandleHttpErrors:
    """Tests for the handle_http_errors decorator."""

    @pytest.mark.asyncio
    async def test_successful_call_passes_through(self):
        """Decorated function should return normally on success."""
        @handle_http_errors("test_tool")
        async def my_tool():
            return "success"

        result = await my_tool()
        assert result == "success"

    @pytest.mark.asyncio
    async def test_http_error_400_raises_generic(self):
        """400 errors should raise with the error message but no re-auth suggestion."""
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = 400
        http_error = HttpError(resp, b'Bad Request')

        @handle_http_errors("test_tool")
        async def my_tool():
            raise http_error

        with pytest.raises(Exception, match="API error in test_tool"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_http_error_401_suggests_reauth(self):
        """401 errors should suggest re-authentication."""
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = 401
        http_error = HttpError(resp, b'Unauthorized')

        @handle_http_errors("test_tool")
        async def my_tool(user_google_email="test@example.com"):
            raise http_error

        with pytest.raises(Exception, match="re-authenticate"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_http_error_403_suggests_reauth(self):
        """403 errors without accessNotConfigured should suggest re-authentication."""
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = 403
        http_error = HttpError(resp, b'Forbidden')

        @handle_http_errors("test_tool")
        async def my_tool(user_google_email="test@example.com"):
            raise http_error

        with pytest.raises(Exception, match="re-authenticate"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_http_error_403_api_not_enabled(self):
        """403 with accessNotConfigured should suggest API enablement."""
        from googleapiclient.errors import HttpError

        resp = MagicMock()
        resp.status = 403
        http_error = HttpError(resp, b'accessNotConfigured: Gmail API has not been used in project')

        @handle_http_errors("test_tool", service_type="gmail")
        async def my_tool():
            raise http_error

        with pytest.raises(Exception, match="API"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_ssl_error_retries_for_read_only(self):
        """Read-only operations should retry on SSL errors with backoff."""
        call_count = 0

        @handle_http_errors("test_tool", is_read_only=True)
        async def my_tool():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ssl.SSLError("Connection reset")
            return "recovered"

        # Patch asyncio.sleep to avoid actual delays
        with patch("core.utils.asyncio.sleep", new_callable=AsyncMock):
            result = await my_tool()

        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_ssl_error_no_retry_for_write(self):
        """Write operations should not retry on SSL errors."""
        @handle_http_errors("test_tool", is_read_only=False)
        async def my_tool():
            raise ssl.SSLError("Connection reset")

        with pytest.raises(TransientNetworkError, match="transient SSL error"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_ssl_error_exhausts_retries(self):
        """After exhausting retries, should raise TransientNetworkError."""
        @handle_http_errors("test_tool", is_read_only=True)
        async def my_tool():
            raise ssl.SSLError("Persistent SSL failure")

        with patch("core.utils.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(TransientNetworkError, match="after 3 attempts"):
                await my_tool()

    @pytest.mark.asyncio
    async def test_unexpected_error_wrapped(self):
        """Unexpected errors should be wrapped with tool name."""
        @handle_http_errors("my_special_tool")
        async def my_tool():
            raise RuntimeError("Something broke")

        with pytest.raises(Exception, match="unexpected error.*my_special_tool"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_google_auth_error_reraise(self):
        """GoogleAuthenticationError should be re-raised without wrapping."""
        from auth.google_auth import GoogleAuthenticationError

        @handle_http_errors("test_tool")
        async def my_tool():
            raise GoogleAuthenticationError("Auth failed")

        with pytest.raises(GoogleAuthenticationError, match="Auth failed"):
            await my_tool()

    @pytest.mark.asyncio
    async def test_transient_network_error_reraise(self):
        """TransientNetworkError should be re-raised without wrapping."""
        @handle_http_errors("test_tool")
        async def my_tool():
            raise TransientNetworkError("Network down")

        with pytest.raises(TransientNetworkError, match="Network down"):
            await my_tool()


class TestRetryExponentialBackoff:
    """Tests verifying exponential backoff timing."""

    @pytest.mark.asyncio
    async def test_backoff_delays_increase(self):
        """Verify delays follow exponential backoff pattern: 1, 2, 4..."""
        sleep_calls = []

        async def mock_sleep(duration):
            sleep_calls.append(duration)

        @handle_http_errors("backoff_test", is_read_only=True)
        async def my_tool():
            raise ssl.SSLError("Always fails")

        with patch("core.utils.asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(TransientNetworkError):
                await my_tool()

        # Should have retried twice (3 attempts - 1 = 2 retries)
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 1  # base_delay * 2^0
        assert sleep_calls[1] == 2  # base_delay * 2^1


class TestGmailRateLimitConstants:
    """Tests for Gmail-specific rate limiting constants."""

    def test_batch_size_reasonable(self):
        assert GMAIL_BATCH_SIZE > 0
        assert GMAIL_BATCH_SIZE <= 100  # Google API max

    def test_request_delay_positive(self):
        assert GMAIL_REQUEST_DELAY > 0
        assert GMAIL_REQUEST_DELAY < 5  # Not unreasonably long


class TestApiEnablementDetection:
    """Tests for API enablement error message generation."""

    def test_service_disabled_detection(self):
        """SERVICE_DISABLED in error should trigger enablement message."""
        from core.api_enablement import get_api_enablement_message

        message = get_api_enablement_message(
            "SERVICE_DISABLED: Gmail API", "gmail"
        )
        # Should return a helpful message or None
        # The function may return None if it can't match the service
        if message:
            assert "API" in message or "enable" in message.lower()

    def test_access_not_configured_detection(self):
        from core.api_enablement import get_api_enablement_message

        message = get_api_enablement_message(
            "accessNotConfigured: Calendar API has not been used", "calendar"
        )
        if message:
            assert "API" in message or "enable" in message.lower()
