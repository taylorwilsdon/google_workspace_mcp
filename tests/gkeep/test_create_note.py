"""
Unit tests for Google Keep MCP `create_note` tool.
"""

import pytest
from unittest.mock import Mock
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import create_note
from .test_utils import unwrap, make_note_dict, make_list_note_dict


# ---------------------------------------------------------------------------
# create_note
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_note_text():
    """create_note should create a text note."""
    mock_service = Mock()
    created_note = make_note_dict(name="notes/new1", title="New Note", text="Some text")
    mock_service.notes().create().execute.return_value = created_note

    result = await unwrap(create_note)(
        service=mock_service,
        user_google_email="test@example.com",
        title="New Note",
        text="Some text",
    )

    assert "Note Created" in result
    assert "New Note" in result
    assert "notes/new1" in result


@pytest.mark.asyncio
async def test_create_note_list():
    """create_note should create a checklist note."""
    mock_service = Mock()
    items = [{"text": {"text": "Buy milk"}, "checked": False}]
    created_note = make_list_note_dict(name="notes/new2", title="Shopping", items=items)
    mock_service.notes().create().execute.return_value = created_note

    result = await unwrap(create_note)(
        service=mock_service,
        user_google_email="test@example.com",
        title="Shopping",
        list_items=items,
    )

    assert "Note Created" in result
    assert "Shopping" in result
    assert "Buy milk" in result


@pytest.mark.asyncio
async def test_create_note_title_only():
    """create_note should create a note with just a title."""
    mock_service = Mock()
    created_note = make_note_dict(name="notes/new3", title="Empty Note", text="")
    created_note["body"] = {}
    mock_service.notes().create().execute.return_value = created_note

    result = await unwrap(create_note)(
        service=mock_service,
        user_google_email="test@example.com",
        title="Empty Note",
    )

    assert "Note Created" in result
    assert "Empty Note" in result
