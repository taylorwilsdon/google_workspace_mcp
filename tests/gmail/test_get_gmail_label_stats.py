import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gmail.gmail_tools import get_gmail_label_stats


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _mock_service(label_response: dict) -> Mock:
    """Build a mock Gmail service that returns the given label response."""
    service = Mock()
    service.users.return_value.labels.return_value.get.return_value.execute.return_value = label_response
    return service


@pytest.mark.asyncio
async def test_inbox_default():
    """Default label_id='INBOX' returns formatted stats."""
    response = {
        "id": "INBOX",
        "name": "INBOX",
        "type": "system",
        "messagesTotal": 1234,
        "messagesUnread": 56,
        "threadsTotal": 800,
        "threadsUnread": 30,
    }
    service = _mock_service(response)
    fn = _unwrap(get_gmail_label_stats)

    result = await fn(service, user_google_email="test@example.com")

    assert "INBOX" in result
    assert "1234" in result
    assert "56" in result
    assert "800" in result
    assert "30" in result
    service.users().labels().get.assert_called_once_with(userId="me", id="INBOX")


@pytest.mark.asyncio
async def test_custom_label_id():
    """Passing a custom label_id calls the API with that ID."""
    response = {
        "id": "Label_42",
        "name": "Projects",
        "type": "user",
        "messagesTotal": 10,
        "messagesUnread": 3,
        "threadsTotal": 7,
        "threadsUnread": 2,
    }
    service = _mock_service(response)
    fn = _unwrap(get_gmail_label_stats)

    result = await fn(
        service, user_google_email="test@example.com", label_id="Label_42"
    )

    assert "Projects" in result
    assert "Label_42" in result
    assert "user" in result
    assert "10" in result
    assert "3" in result
    service.users().labels().get.assert_called_once_with(userId="me", id="Label_42")


@pytest.mark.asyncio
async def test_zero_counts():
    """Labels with zero messages/threads are handled gracefully."""
    response = {
        "id": "TRASH",
        "name": "TRASH",
        "type": "system",
        "messagesTotal": 0,
        "messagesUnread": 0,
        "threadsTotal": 0,
        "threadsUnread": 0,
    }
    service = _mock_service(response)
    fn = _unwrap(get_gmail_label_stats)

    result = await fn(service, user_google_email="test@example.com", label_id="TRASH")

    assert "TRASH" in result
    assert "Messages total:  0" in result
    assert "Threads total:   0" in result


@pytest.mark.asyncio
async def test_missing_optional_fields():
    """When API response omits count fields, defaults to 0."""
    response = {
        "id": "STARRED",
        "name": "STARRED",
        "type": "system",
    }
    service = _mock_service(response)
    fn = _unwrap(get_gmail_label_stats)

    result = await fn(service, user_google_email="test@example.com", label_id="STARRED")

    assert "STARRED" in result
    assert "Messages total:  0" in result
    assert "Messages unread: 0" in result
    assert "Threads total:   0" in result
    assert "Threads unread:  0" in result
