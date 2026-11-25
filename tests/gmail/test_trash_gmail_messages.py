"""Tests for `trash_gmail_messages`."""

import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError
from gmail.gmail_tools import trash_gmail_messages


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


@pytest.mark.asyncio
async def test_trash_adds_trash_label_in_one_batch_call():
    service = Mock()
    batch_modify = service.users.return_value.messages.return_value.batchModify

    result = await _unwrap(trash_gmail_messages)(
        service=service,
        user_google_email="user@example.com",
        message_ids=["m1", "m2"],
    )

    batch_modify.assert_called_once_with(
        userId="me", body={"ids": ["m1", "m2"], "addLabelIds": ["TRASH"]}
    )
    batch_modify.return_value.execute.assert_called_once()
    assert "Moved 2 message(s) to Trash" in result
    assert "user@example.com" in result


@pytest.mark.asyncio
async def test_trash_rejects_empty_message_ids():
    service = Mock()

    with pytest.raises(UserInputError, match="message_ids must not be empty"):
        await _unwrap(trash_gmail_messages)(
            service=service,
            user_google_email="user@example.com",
            message_ids=[],
        )

    service.users.assert_not_called()
