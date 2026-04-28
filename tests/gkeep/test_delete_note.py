"""
Unit tests for Google Keep MCP `delete_note` tool.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import delete_note
from .test_utils import unwrap


# ---------------------------------------------------------------------------
# delete_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_note():
    """delete_note should delete and confirm."""
    mock_service = Mock()
    mock_service.notes().delete().execute.return_value = {}

    result = await unwrap(delete_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "deleted" in result.lower()
    assert "notes/abc123" in result


@pytest.mark.asyncio
async def test_delete_note_short_id():
    """delete_note should accept short ID."""
    mock_service = Mock()
    mock_service.notes().delete().execute.return_value = {}

    result = await unwrap(delete_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="abc123",
    )

    assert "deleted" in result.lower()
    assert "notes/abc123" in result
