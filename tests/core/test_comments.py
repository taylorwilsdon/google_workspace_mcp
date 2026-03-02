"""Tests for core comments module."""

import sys
import os
import pytest
from unittest.mock import Mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.comments import _read_comments_impl


@pytest.mark.asyncio
async def test_read_comments_includes_quoted_text():
    """Verify that quotedFileContent.value is surfaced in the output."""
    mock_service = Mock()
    mock_service.comments.return_value.list.return_value.execute = Mock(
        return_value={
            "comments": [
                {
                    "id": "c1",
                    "content": "Needs a citation here.",
                    "author": {"displayName": "Alice"},
                    "createdTime": "2025-01-15T10:00:00Z",
                    "modifiedTime": "2025-01-15T10:00:00Z",
                    "resolved": False,
                    "quotedFileContent": {
                        "mimeType": "text/html",
                        "value": "the specific text that was highlighted",
                    },
                    "replies": [],
                },
                {
                    "id": "c2",
                    "content": "General comment without anchor.",
                    "author": {"displayName": "Bob"},
                    "createdTime": "2025-01-16T09:00:00Z",
                    "modifiedTime": "2025-01-16T09:00:00Z",
                    "resolved": False,
                    "replies": [],
                },
            ]
        }
    )

    result = await _read_comments_impl(mock_service, "document", "doc123")

    # Comment with anchor text should show the quoted text
    assert "Quoted text: the specific text that was highlighted" in result
    assert "Needs a citation here." in result

    # Comment without anchor text should not have a "Quoted text" line between Bob's author and content
    # The output uses literal \n joins, so split on that
    parts = result.split("\\n")
    bob_section_started = False
    for part in parts:
        if "Author: Bob" in part:
            bob_section_started = True
        if bob_section_started and "Quoted text:" in part:
            pytest.fail(
                "Comment without quotedFileContent should not show 'Quoted text'"
            )
        if bob_section_started and "Content: General comment" in part:
            break


@pytest.mark.asyncio
async def test_read_comments_empty():
    """Verify empty comments returns appropriate message."""
    mock_service = Mock()
    mock_service.comments.return_value.list.return_value.execute = Mock(
        return_value={"comments": []}
    )

    result = await _read_comments_impl(mock_service, "document", "doc123")
    assert "No comments found" in result


@pytest.mark.asyncio
async def test_read_comments_with_replies():
    """Verify replies are included in output."""
    mock_service = Mock()
    mock_service.comments.return_value.list.return_value.execute = Mock(
        return_value={
            "comments": [
                {
                    "id": "c1",
                    "content": "Question?",
                    "author": {"displayName": "Alice"},
                    "createdTime": "2025-01-15T10:00:00Z",
                    "modifiedTime": "2025-01-15T10:00:00Z",
                    "resolved": False,
                    "quotedFileContent": {"value": "some text"},
                    "replies": [
                        {
                            "id": "r1",
                            "content": "Answer.",
                            "author": {"displayName": "Bob"},
                            "createdTime": "2025-01-15T11:00:00Z",
                            "modifiedTime": "2025-01-15T11:00:00Z",
                        }
                    ],
                }
            ]
        }
    )

    result = await _read_comments_impl(mock_service, "document", "doc123")
    assert "Question?" in result
    assert "Answer." in result
    assert "Bob" in result
    assert "Quoted text: some text" in result
