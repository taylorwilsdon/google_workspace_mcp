"""
Unit tests for Google Keep MCP helpers.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_helpers import Note, format_note, format_note_content, build_note_body
from .test_utils import make_note_dict, make_list_note_dict


# ---------------------------------------------------------------------------
# Dataclass: Note.from_api
# ---------------------------------------------------------------------------


def test_note_from_api_text():
    """Note.from_api should parse a text note dict."""
    data = make_note_dict(title="My Note", text="Hello")
    note = Note.from_api(data)

    assert note.name == "notes/abc123"
    assert note.title == "My Note"
    assert note.text == "Hello"
    assert note.list_items is None
    assert note.create_time == "2025-01-01T00:00:00Z"


def test_note_from_api_list():
    """Note.from_api should parse a checklist note dict."""
    data = make_list_note_dict()
    note = Note.from_api(data)

    assert note.title == "Checklist"
    assert note.text is None
    assert note.list_items is not None
    assert len(note.list_items) == 2
    assert note.list_items[0].text == "Item 1"
    assert note.list_items[0].checked is False
    assert note.list_items[1].text == "Item 2"
    assert note.list_items[1].checked is True


def test_note_from_api_with_attachments():
    """Note.from_api should parse attachments."""
    data = make_note_dict(
        attachments=[
            {"name": "notes/abc/attachments/att1", "mimeType": ["image/png"]},
        ]
    )
    note = Note.from_api(data)

    assert len(note.attachments) == 1
    assert note.attachments[0].name == "notes/abc/attachments/att1"
    assert note.attachments[0].mime_types == ["image/png"]


def test_note_from_api_with_permissions():
    """Note.from_api should parse permissions."""
    data = make_note_dict(
        permissions=[
            {"name": "perm1", "role": "OWNER", "email": "owner@example.com"},
            {"name": "perm2", "role": "WRITER", "email": "writer@example.com"},
        ]
    )
    note = Note.from_api(data)

    assert len(note.permissions) == 2
    assert note.permissions[0].role == "OWNER"
    assert note.permissions[1].email == "writer@example.com"


def test_note_from_api_nested_list_items():
    """Note.from_api should parse nested checklist children."""
    data = {
        "name": "notes/nested",
        "title": "Nested",
        "body": {
            "list": {
                "listItems": [
                    {
                        "text": {"text": "Parent"},
                        "checked": False,
                        "childListItems": [
                            {"text": {"text": "Child 1"}, "checked": True},
                            {"text": {"text": "Child 2"}, "checked": False},
                        ],
                    }
                ]
            }
        },
    }
    note = Note.from_api(data)

    assert len(note.list_items) == 1
    assert note.list_items[0].text == "Parent"
    assert len(note.list_items[0].children) == 2
    assert note.list_items[0].children[0].text == "Child 1"
    assert note.list_items[0].children[0].checked is True


def test_note_from_api_deep_nested_list_items():
    """Note.from_api should parse nested checklist children."""
    data = {
        "name": "notes/nested",
        "title": "Nested",
        "body": {
            "list": {
                "listItems": [
                    {
                        "text": {"text": "Parent"},
                        "checked": False,
                        "childListItems": [
                            {
                                "text": { "text": "Sub-list" },
                                "childListItems": [
                                    {"text": {"text": "Child 1"}, "checked": True},
                                    {"text": {"text": "Child 2"}, "checked": False},
                                ],
                            },
                        ],
                    }
                ]
            }
        },
    }
    note = Note.from_api(data)

    assert len(note.list_items) == 1
    assert note.list_items[0].text == "Parent"
    assert len(note.list_items[0].children) == 1
    assert note.list_items[0].children[0].text == "Sub-list"
    assert len(note.list_items[0].children[0].children) == 2
    assert note.list_items[0].children[0].children[0].text == "Child 1"
    assert note.list_items[0].children[0].children[0].checked is True
    assert note.list_items[0].children[0].children[1].text == "Child 2"
    assert note.list_items[0].children[0].children[1].checked is False


# ---------------------------------------------------------------------------
# Helper: format_note (with Note dataclass)
# ---------------------------------------------------------------------------


def test_format_note_text():
    """format_note should format a text Note."""
    note = Note.from_api(make_note_dict())
    result = format_note(note)

    assert "notes/abc123" in result
    assert "Test Note" in result
    assert "Hello world" in result


def test_format_note_truncates():
    """format_note should truncate long text to 200 chars."""
    note = Note.from_api(make_note_dict(text="B" * 300))
    result = format_note(note)

    assert "..." in result
    assert "B" * 300 not in result


# ---------------------------------------------------------------------------
# Helper: format_note_content (with Note dataclass)
# ---------------------------------------------------------------------------


def test_format_note_content_full_text():
    """format_note_content should return full text without truncation."""
    note = Note.from_api(make_note_dict(text="A" * 500))
    result = format_note_content(note)
    assert "A" * 500 in result
    assert "..." not in result


def test_format_note_content_checklist():
    """format_note_content should return all checklist items."""
    note = Note.from_api(make_list_note_dict())
    result = format_note_content(note)
    assert "[ ] Item 1" in result
    assert "[x] Item 2" in result


def test_format_note_content_deep_checklist():
    """format_note_content should return all checklist items."""
    data = {
        "name": "notes/nested",
        "title": "Nested",
        "body": {
            "list": {
                "listItems": [
                    {
                        "text": {"text": "Parent"},
                        "checked": False,
                        "childListItems": [
                            {
                                "text": { "text": "Sub-list" },
                                "childListItems": [
                                    {"text": {"text": "Child 1"}, "checked": True},
                                    {"text": {"text": "Child 2"}, "checked": False},
                                ],
                            },
                        ],
                    }
                ]
            }
        },
    }

    result = format_note_content(Note.from_api(data))
    assert "  - [ ] Sub-list" in result
    assert "    - [x] Child 1" in result
    assert "    - [ ] Child 2" in result


# ---------------------------------------------------------------------------
# Helper: build_note_body
# ---------------------------------------------------------------------------


def test_build_note_body_text():
    """build_note_body should build a text note body."""
    result = build_note_body("Title", text="Hello")
    assert result == {"title": "Title", "body": {"text": {"text": "Hello"}}}


def test_build_note_body_list():
    """build_note_body should build a list note body."""
    items = [{"text": {"text": "Item"}, "checked": False}]
    result = build_note_body("Title", list_items=items)
    assert result == {"title": "Title", "body": {"list": {"listItems": items}}}


def test_build_note_body_title_only():
    """build_note_body should build a title-only body."""
    result = build_note_body("Title")
    assert result == {"title": "Title"}


def test_build_note_body_list_takes_precedence():
    """build_note_body should prefer list_items over text when both given."""
    items = [{"text": {"text": "Item"}, "checked": False}]
    result = build_note_body("Title", text="Hello", list_items=items)
    assert "list" in result["body"]
    assert "text" not in result["body"]
