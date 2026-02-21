"""
Unit tests for Google Keep MCP `list_notes` tool.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import list_notes, LIST_NOTES_PAGE_SIZE_MAX
from .test_utils import unwrap, make_note_dict


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_notes_returns_notes():
    """list_notes should return formatted note summaries."""
    mock_service = Mock()
    note1 = make_note_dict(name="notes/1", title="Note One", text="Content 1")
    note2 = make_note_dict(name="notes/2", title="Note Two", text="Content 2")
    mock_service.notes().list().execute.return_value = {
        "notes": [note1, note2],
    }

    result = await unwrap(list_notes)(
        service=mock_service,
        user_google_email="test@example.com",
    )

    assert "Note One" in result
    assert "Note Two" in result
    assert "notes/1" in result
    assert "notes/2" in result


@pytest.mark.asyncio
async def test_list_notes_empty():
    """list_notes should indicate when no notes are found."""
    mock_service = Mock()
    mock_service.notes().list().execute.return_value = {"notes": []}

    result = await unwrap(list_notes)(
        service=mock_service,
        user_google_email="test@example.com",
    )

    assert "No notes found" in result


@pytest.mark.asyncio
async def test_list_notes_with_pagination_token():
    """list_notes should include next page token when present."""
    mock_service = Mock()
    note = make_note_dict()
    mock_service.notes().list().execute.return_value = {
        "notes": [note],
        "nextPageToken": "token123",
    }

    result = await unwrap(list_notes)(
        service=mock_service,
        user_google_email="test@example.com",
    )

    assert "token123" in result


@pytest.mark.asyncio
async def test_list_notes_passes_filter():
    """list_notes should pass filter_query param to the API."""
    mock_service = Mock()
    mock_service.notes().list().execute.return_value = {"notes": []}

    await unwrap(list_notes)(
        service=mock_service,
        user_google_email="test@example.com",
        filter_query="trashed=true",
    )

    mock_service.notes().list.assert_called_with(pageSize=25, filter="trashed=true")


@pytest.mark.asyncio
async def test_list_notes_caps_page_size():
    """list_notes should cap page_size to the max."""
    mock_service = Mock()
    mock_service.notes().list().execute.return_value = {"notes": []}

    await unwrap(list_notes)(
        service=mock_service,
        user_google_email="test@example.com",
        page_size=5000,
    )

    # Verify pageSize was capped to the maximum
    call_kwargs = mock_service.notes().list.call_args.kwargs
    assert call_kwargs.get("pageSize") == LIST_NOTES_PAGE_SIZE_MAX
