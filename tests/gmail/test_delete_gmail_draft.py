"""Tests for the delete_gmail_draft tool (draft rascunho family, F1-owned implementation).

NOTE: gmail.gmail_tools.delete_gmail_draft does not exist yet in this worktree --
family F1 is implementing it in parallel on gmail/gmail_tools.py. These tests are
written against the agreed contract and will fail to import/collect until that
implementation lands and this worktree is merged with it. That failure mode is
expected at authoring time.
"""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import delete_gmail_draft


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_delete_gmail_draft_calls_drafts_delete_with_correct_id():
    mock_service = Mock()
    mock_service.users().drafts().delete().execute.return_value = {}

    await _unwrap(delete_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft123",
    )

    delete_kwargs = (
        mock_service.users.return_value.drafts.return_value.delete.call_args.kwargs
    )
    assert delete_kwargs["userId"] == "me"
    assert delete_kwargs["id"] == "draft123"
    assert mock_service.users.return_value.drafts.return_value.delete.return_value.execute.call_count == 1


@pytest.mark.asyncio
async def test_delete_gmail_draft_mentions_draft_id_and_deletion_in_confirmation():
    mock_service = Mock()
    mock_service.users().drafts().delete().execute.return_value = {}

    result = await _unwrap(delete_gmail_draft)(
        service=mock_service,
        user_google_email="user@example.com",
        draft_id="draft123",
    )

    assert isinstance(result, str)
    assert "draft123" in result
    assert "delet" in result.lower()
