"""
Unit tests for Google Keep MCP `get_note` tool.
"""

import pytest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import get_note
from .test_utils import unwrap, make_note_dict, make_list_note_dict


# ---------------------------------------------------------------------------
# get_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_note_with_full_name():
    """get_note should accept full resource name."""
    mock_service = Mock()
    note = make_note_dict()
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(get_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "Test Note" in result
    assert "notes/abc123" in result
    assert "Hello world" in result
    mock_service.notes().get.assert_called_with(name="notes/abc123")



@pytest.mark.asyncio
async def test_get_note_with_short_id():
    """get_note should accept short ID and prepend notes/ prefix."""
    mock_service = Mock()
    note = make_note_dict()
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(get_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="abc123",
    )

    assert "Test Note" in result

    # Verify that the short ID was normalized with "notes/" prefix
    mock_service.notes().get.assert_called_with(name="notes/abc123")


@pytest.mark.asyncio
async def test_get_note_list_type():
    """get_note should format checklist notes properly."""
    mock_service = Mock()
    note = make_list_note_dict()
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(get_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/list123",
    )

    assert "Checklist" in result
    assert "[ ] Item 1" in result
    assert "[x] Item 2" in result


@pytest.mark.asyncio
async def test_get_note_with_attachments():
    """get_note should show attachment info."""
    mock_service = Mock()
    note = make_note_dict(
        attachments=[
            {"name": "notes/abc/attachments/att1", "mimeType": ["image/png"]},
        ]
    )
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(get_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "Attachments: 1" in result
    assert "image/png" in result


@pytest.mark.asyncio
async def test_get_note_truncates_long_text():
    """get_note should truncate text body to 200 characters."""
    mock_service = Mock()
    long_text = "A" * 300
    note = make_note_dict(text=long_text)
    mock_service.notes().get().execute.return_value = note

    result = await unwrap(get_note)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
    )

    assert "..." in result
    assert "A" * 300 not in result
