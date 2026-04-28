"""
Unit tests for Google Keep MCP `read_note` tool.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import read_note
from .test_utils import unwrap, make_note_dict, make_list_note_dict


# ---------------------------------------------------------------------------
# read_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_note_returns_full_text():
    """read_note should return the full text content without truncation."""
    mock_service = Mock()
    long_text = "A" * 300
    note = make_note_dict(text=long_text)
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(read_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "A" * 300 in result
    assert "..." not in result


@pytest.mark.asyncio
async def test_read_note_returns_full_checklist():
    """read_note should return all checklist items."""
    mock_service = Mock()
    note = make_list_note_dict()
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(read_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/list123",
    )

    assert "[ ] Item 1" in result
    assert "[x] Item 2" in result
    assert "Checklist" in result


@pytest.mark.asyncio
async def test_read_note_empty_body():
    """read_note should handle notes with no body."""
    mock_service = Mock()
    note = make_note_dict(text="")
    note["body"] = {}
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(read_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "(empty note)" in result


@pytest.mark.asyncio
async def test_read_note_does_not_include_metadata():
    """read_note should not include timestamps or attachment metadata."""
    mock_service = Mock()
    note = make_note_dict(
        attachments=[
            {"name": "notes/abc/attachments/att1", "mimeType": ["image/png"]},
        ]
    )
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(read_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "Hello world" in result
    assert "Attachments" not in result
    assert "Created:" not in result
    assert "Updated:" not in result
