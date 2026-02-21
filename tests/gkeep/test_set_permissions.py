"""
Unit tests for Google Keep MCP `set_permissions` tool.
"""

import pytest
from unittest.mock import AsyncMock, Mock, patch
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gkeep.keep_tools import set_permissions
from .test_utils import unwrap, make_note_dict


@pytest.mark.asyncio
async def test_set_permissions_add_writers():
    """set_permissions should remove old permissions and add new ones."""
    mock_service = Mock()
    note = make_note_dict(
        permissions=[
            {
                "name": "notes/abc123/permissions/perm1",
                "role": "OWNER",
                "email": "owner@example.com",
            },
            {
                "name": "notes/abc123/permissions/perm2",
                "role": "WRITER",
                "email": "old@example.com",
            },
        ]
    )
    mock_service.notes().get().execute.return_value = note
    mock_service.notes().permissions().batchDelete().execute.return_value = {}
    mock_service.notes().permissions().batchCreate().execute.return_value = {
        "permissions": [
            {
                "name": "notes/abc123/permissions/perm3",
                "role": "WRITER",
                "email": "new@example.com",
            },
        ]
    }

    result = await unwrap(set_permissions)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
        emails=["new@example.com"],
    )

    assert "Removed 1" in result
    assert "Added 1" in result
    assert "new@example.com" in result


@pytest.mark.asyncio
async def test_set_permissions_remove_all():
    """set_permissions with empty emails should remove all non-owner permissions."""
    mock_service = Mock()
    note = make_note_dict(
        permissions=[
            {
                "name": "notes/abc123/permissions/perm1",
                "role": "OWNER",
                "email": "owner@example.com",
            },
            {
                "name": "notes/abc123/permissions/perm2",
                "role": "WRITER",
                "email": "writer@example.com",
            },
        ]
    )
    mock_service.notes().get().execute.return_value = note
    mock_service.notes().permissions().batchDelete().execute.return_value = {}

    result = await unwrap(set_permissions)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="abc123",
        emails=[],
    )

    assert "Removed 1" in result
    assert "No new permissions added" in result


@pytest.mark.asyncio
async def test_set_permissions_no_existing_writers():
    """set_permissions should skip batchDelete when no non-owner permissions exist."""
    mock_service = Mock()
    note = make_note_dict(
        permissions=[
            {
                "name": "notes/abc123/permissions/perm1",
                "role": "OWNER",
                "email": "owner@example.com",
            },
        ]
    )
    mock_service.notes().get().execute.return_value = note
    mock_service.notes().permissions().batchCreate().execute.return_value = {
        "permissions": [
            {
                "name": "notes/abc123/permissions/perm2",
                "role": "WRITER",
                "email": "new@example.com",
            },
        ]
    }

    # Verify batchDelete was NOT called since there were no non-owner permissions
    mock_service.notes().permissions().batchDelete.assert_not_called()

    result = await unwrap(set_permissions)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
        emails=["new@example.com"],
    )

    assert "Removed 0" in result
    assert "Added 1" in result


@pytest.mark.asyncio
async def test_set_permissions_multiple_emails():
    """set_permissions should handle multiple email addresses."""
    mock_service = Mock()
    note = make_note_dict(permissions=[])
    mock_service.notes().get().execute.return_value = note
    mock_service.notes().permissions().batchCreate().execute.return_value = {
        "permissions": [
            {"name": "notes/abc/permissions/p1", "role": "WRITER", "email": "a@example.com"},
            {"name": "notes/abc/permissions/p2", "role": "WRITER", "email": "b@example.com"},
        ]
    }

    result = await unwrap(set_permissions)(
        service=mock_service,
        user_google_email="test@example.com",
        note_id="notes/abc123",
        emails=["a@example.com", "b@example.com"],
    )

    assert "Added 2" in result
    assert "a@example.com" in result
    assert "b@example.com" in result
