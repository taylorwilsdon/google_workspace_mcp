"""
Shared fixtures for unit tests.
"""

import os
import sys
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

# Ensure project root is on path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))


@pytest.fixture
def mock_gmail_service():
    """Create a mock Gmail API service."""
    service = MagicMock()

    # users().messages().list()
    list_req = MagicMock()
    list_req.execute = MagicMock(return_value={
        "messages": [
            {"id": "msg_001", "threadId": "thread_001"},
            {"id": "msg_002", "threadId": "thread_002"},
        ]
    })
    service.users().messages().list.return_value = list_req

    # users().messages().get()
    get_req = MagicMock()
    get_req.execute = MagicMock(return_value={
        "id": "msg_001",
        "threadId": "thread_001",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Test Subject"},
                {"name": "From", "value": "sender@example.com"},
            ],
            "mimeType": "text/plain",
            "body": {
                "data": "SGVsbG8gV29ybGQ="  # base64 for "Hello World"
            }
        }
    })
    service.users().messages().get.return_value = get_req

    # users().messages().attachments().get()
    att_req = MagicMock()
    att_req.execute = MagicMock(return_value={
        "data": "dGVzdCBhdHRhY2htZW50IGRhdGE=",  # base64 for "test attachment data"
        "size": 20
    })
    service.users().messages().attachments().get.return_value = att_req

    # users().threads().get()
    thread_req = MagicMock()
    thread_req.execute = MagicMock(return_value={
        "id": "thread_001",
        "messages": [
            {
                "id": "msg_001",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Thread Subject"},
                        {"name": "From", "value": "alice@example.com"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": "Rmlyc3QgbWVzc2FnZQ=="}  # "First message"
                }
            }
        ]
    })
    service.users().threads().get.return_value = thread_req

    # users().messages().send()
    send_req = MagicMock()
    send_req.execute = MagicMock(return_value={
        "id": "sent_001",
        "threadId": "thread_sent_001",
        "labelIds": ["SENT"]
    })
    service.users().messages().send.return_value = send_req

    # users().drafts().create()
    draft_req = MagicMock()
    draft_req.execute = MagicMock(return_value={
        "id": "draft_001",
        "message": {"id": "msg_draft_001", "threadId": "thread_draft_001"}
    })
    service.users().drafts().create.return_value = draft_req

    # users().labels().list()
    labels_req = MagicMock()
    labels_req.execute = MagicMock(return_value={
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "SENT", "name": "SENT", "type": "system"},
            {"id": "Label_1", "name": "Custom Label", "type": "user"},
        ]
    })
    service.users().labels().list.return_value = labels_req

    return service


@pytest.fixture
def mock_calendar_service():
    """Create a mock Calendar API service."""
    service = MagicMock()

    # calendarList().list()
    cal_list_req = MagicMock()
    cal_list_req.execute = MagicMock(return_value={
        "items": [
            {"id": "primary", "summary": "My Calendar", "primary": True},
            {"id": "work@group.calendar.google.com", "summary": "Work"},
        ]
    })
    service.calendarList().list.return_value = cal_list_req

    # events().list()
    events_req = MagicMock()
    events_req.execute = MagicMock(return_value={
        "items": [
            {
                "id": "evt_001",
                "summary": "Team Meeting",
                "start": {"dateTime": "2026-03-01T10:00:00Z"},
                "end": {"dateTime": "2026-03-01T11:00:00Z"},
            }
        ]
    })
    service.events().list.return_value = events_req

    # events().insert()
    insert_req = MagicMock()
    insert_req.execute = MagicMock(return_value={
        "id": "evt_new_001",
        "summary": "New Event",
        "htmlLink": "https://calendar.google.com/event?eid=xxx",
    })
    service.events().insert.return_value = insert_req

    # events().update()
    update_req = MagicMock()
    update_req.execute = MagicMock(return_value={
        "id": "evt_001",
        "summary": "Updated Event",
    })
    service.events().update.return_value = update_req

    # events().delete()
    delete_req = MagicMock()
    delete_req.execute = MagicMock()
    service.events().delete.return_value = delete_req

    return service


@pytest.fixture
def mock_drive_service():
    """Create a mock Drive API service."""
    service = MagicMock()

    # files().list()
    list_req = MagicMock()
    list_req.execute = MagicMock(return_value={
        "files": [
            {"id": "file_001", "name": "Document.docx", "mimeType": "application/vnd.google-apps.document"},
            {"id": "file_002", "name": "Spreadsheet.xlsx", "mimeType": "application/vnd.google-apps.spreadsheet"},
        ]
    })
    service.files().list.return_value = list_req

    # files().get()
    get_req = MagicMock()
    get_req.execute = MagicMock(return_value={
        "id": "file_001",
        "name": "Document.docx",
        "mimeType": "application/vnd.google-apps.document",
        "webViewLink": "https://docs.google.com/document/d/file_001/edit",
    })
    service.files().get.return_value = get_req

    # files().create()
    create_req = MagicMock()
    create_req.execute = MagicMock(return_value={
        "id": "file_new_001",
        "name": "New File",
    })
    service.files().create.return_value = create_req

    return service
