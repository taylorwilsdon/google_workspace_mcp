"""Tests for attachment handling and pagination in the calendar tools."""

import sys
import os

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar import calendar_tools


FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


class _Request:
    def __init__(self, recorder, name, kwargs):
        self._recorder = recorder
        self._name = name
        self._kwargs = kwargs

    def execute(self):
        self._recorder.append((self._name, self._kwargs))
        return {
            "id": "evt1",
            "summary": "demo",
            "htmlLink": "https://example.test/evt1",
        }


class _Events:
    def __init__(self, recorder):
        self._recorder = recorder

    def get(self, **kwargs):
        return _Request(self._recorder, "get", kwargs)

    def patch(self, **kwargs):
        return _Request(self._recorder, "patch", kwargs)

    def insert(self, **kwargs):
        return _Request(self._recorder, "insert", kwargs)


class _CalendarService:
    """Minimal Calendar service stub; _http is None so no Drive lookup happens."""

    _http = None

    def __init__(self):
        self.calls = []

    def events(self):
        return _Events(self.calls)


@pytest.mark.asyncio
async def test_manage_event_update_sends_attachments():
    service = _CalendarService()

    await _unwrap(calendar_tools.manage_event)(
        service=service,
        user_google_email="user@example.com",
        action="update",
        event_id="evt1",
        summary="new title",
        attachments=[FILE_ID],
    )

    patch_kwargs = next(kw for name, kw in service.calls if name == "patch")
    assert patch_kwargs["supportsAttachments"] is True
    assert patch_kwargs["body"]["attachments"] == [
        {
            "fileUrl": f"https://drive.google.com/open?id={FILE_ID}",
            "title": "Drive Attachment",
            "mimeType": "application/vnd.google-apps.drive-sdk",
        }
    ]


@pytest.mark.asyncio
async def test_manage_event_update_accepts_attachments_as_only_field():
    service = _CalendarService()

    result = await _unwrap(calendar_tools.manage_event)(
        service=service,
        user_google_email="user@example.com",
        action="update",
        event_id="evt1",
        attachments=[FILE_ID],
    )

    assert "Successfully modified event" in result
    patch_kwargs = next(kw for name, kw in service.calls if name == "patch")
    assert "attachments" in patch_kwargs["body"]


@pytest.mark.asyncio
async def test_manage_event_update_extracts_file_id_from_drive_url():
    service = _CalendarService()

    await _unwrap(calendar_tools.manage_event)(
        service=service,
        user_google_email="user@example.com",
        action="update",
        event_id="evt1",
        attachments=[f"https://docs.google.com/document/d/{FILE_ID}/edit"],
    )

    patch_kwargs = next(kw for name, kw in service.calls if name == "patch")
    assert patch_kwargs["body"]["attachments"][0]["fileUrl"].endswith(FILE_ID)


@pytest.mark.asyncio
@pytest.mark.parametrize("attachments", [[], None])
async def test_manage_event_update_distinguishes_empty_and_omitted_attachments(
    attachments,
):
    service = _CalendarService()
    await _unwrap(calendar_tools.manage_event)(
        service=service,
        user_google_email="user@example.com",
        action="update",
        event_id="evt1",
        summary="new title",
        attachments=attachments,
    )
    patch_kwargs = next(kw for name, kw in service.calls if name == "patch")
    if attachments is None:
        assert "attachments" not in patch_kwargs["body"]
    else:
        assert patch_kwargs["body"]["attachments"] == []
        assert patch_kwargs["supportsAttachments"] is True
