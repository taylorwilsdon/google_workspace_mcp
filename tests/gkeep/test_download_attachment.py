"""
Unit tests for Google Keep MCP `download_attachment` tool.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import download_attachment
from .test_utils import unwrap


# ---------------------------------------------------------------------------
# download_attachment
# ---------------------------------------------------------------------------

FAKE_BYTES = b"fake-image-data"
ATTACHMENT_NAME = "notes/abc/attachments/att1"
MIME_TYPE = "image/png"


async def _call_download(mock_service, **overrides):
    """Helper to call unwrapped download_attachment with defaults."""
    kwargs = {
        "service": mock_service,
        "user_google_email": "test@example.com",
        "attachment_name": ATTACHMENT_NAME,
        "mime_type": MIME_TYPE,
    }
    kwargs.update(overrides)
    return await unwrap(download_attachment)(**kwargs)


def _make_service(return_value=FAKE_BYTES):
    mock_service = Mock()
    mock_service.media().download().execute.return_value = return_value
    return mock_service


@pytest.mark.asyncio
async def test_download_attachment_dict_response():
    """dict response from API should return an error message."""
    mock_service = _make_service(
        {"name": ATTACHMENT_NAME, "mimeType": ["image/png"]}
    )

    result = await _call_download(mock_service)

    assert "metadata instead of binary data" in result
    assert ATTACHMENT_NAME in result


@pytest.mark.asyncio
@patch("auth.oauth_config.is_stateless_mode", return_value=True)
async def test_download_attachment_stateless(_mock_stateless):
    """In stateless mode, return base64 preview without saving to disk."""
    result = await _call_download(_make_service())

    assert "Stateless mode" in result
    assert "Base64 preview:" in result
    assert ATTACHMENT_NAME in result
    assert MIME_TYPE in result


@pytest.mark.asyncio
@patch("core.config.get_transport_mode", return_value="stdio")
@patch("core.attachment_storage.get_attachment_storage")
@patch("auth.oauth_config.is_stateless_mode", return_value=False)
async def test_download_attachment_stdio(
    _mock_stateless, mock_get_storage, _mock_transport
):
    """In stdio mode, save to disk and return file path."""
    mock_saved = MagicMock()
    mock_saved.path = "/tmp/attachments/att1"
    mock_saved.file_id = "file-123"
    mock_get_storage.return_value.save_attachment.return_value = mock_saved

    result = await _call_download(_make_service())

    assert "Attachment downloaded" in result
    assert "Saved to: /tmp/attachments/att1" in result
    assert MIME_TYPE in result
    mock_get_storage.return_value.save_attachment.assert_called_once()


@pytest.mark.asyncio
@patch("core.attachment_storage.get_attachment_url", return_value="https://localhost/dl/file-123")
@patch("core.config.get_transport_mode", return_value="streamable-http")
@patch("core.attachment_storage.get_attachment_storage")
@patch("auth.oauth_config.is_stateless_mode", return_value=False)
async def test_download_attachment_http(
    _mock_stateless, mock_get_storage, _mock_transport, _mock_get_url
):
    """In HTTP mode, save to disk and return download URL."""
    mock_saved = MagicMock()
    mock_saved.path = "/tmp/attachments/att1"
    mock_saved.file_id = "file-123"
    mock_get_storage.return_value.save_attachment.return_value = mock_saved

    result = await _call_download(_make_service())

    assert "Attachment downloaded" in result
    assert "Download URL: https://localhost/dl/file-123" in result
    assert "expire after 1 hour" in result
    mock_get_storage.return_value.save_attachment.assert_called_once()
