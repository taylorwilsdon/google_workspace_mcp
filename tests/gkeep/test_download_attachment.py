"""
Unit tests for Google Keep MCP `download_attachment` tool.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import download_attachment
from .test_utils import unwrap


# ---------------------------------------------------------------------------
# download_attachment (attachment download)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_attachment():
    """download_attachment should return attachment info."""
    mock_service = Mock()
    mock_service.media().download().execute.return_value = b"fake-image-data"

    result = await unwrap(download_attachment)(
        service=mock_service,
        user_google_email="test@example.com",
        attachment_name="notes/abc/attachments/att1",
        mime_type="image/png",
    )

    assert "Attachment downloaded" in result
    assert "notes/abc/attachments/att1" in result
    assert "image/png" in result


@pytest.mark.asyncio
async def test_download_attachment_dict_response():
    """download_attachment should handle dict responses."""
    mock_service = Mock()
    mock_service.media().download().execute.return_value = {
        "name": "notes/abc/attachments/att1",
        "mimeType": ["image/png"],
    }

    result = await unwrap(download_attachment)(
        service=mock_service,
        user_google_email="test@example.com",
        attachment_name="notes/abc/attachments/att1",
        mime_type="image/png",
    )

    assert "Attachment downloaded" in result
