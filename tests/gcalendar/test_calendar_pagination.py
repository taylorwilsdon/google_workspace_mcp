"""Tests that calendar list tools surface pagination instead of truncating."""

import sys
import os
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar import calendar_tools


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _calendar_list_service(response):
    service = Mock()
    service.calendarList.return_value.list.return_value.execute = Mock(
        return_value=response
    )
    return service


def _events_service(response):
    service = Mock()
    service.events.return_value.list.return_value.execute = Mock(return_value=response)
    return service


@pytest.mark.asyncio
async def test_list_calendars_reports_next_page_token():
    service = _calendar_list_service(
        {"items": [{"id": "cal1", "summary": "Work"}], "nextPageToken": "tok123"}
    )

    result = await _unwrap(calendar_tools.list_calendars)(
        service=service, user_google_email="user@example.com"
    )

    assert "Next page token: tok123" in result


@pytest.mark.asyncio
async def test_list_calendars_forwards_page_token():
    service = _calendar_list_service({"items": [{"id": "cal1", "summary": "Work"}]})

    result = await _unwrap(calendar_tools.list_calendars)(
        service=service,
        user_google_email="user@example.com",
        max_results=10,
        page_token="tok123",
    )

    call_kwargs = service.calendarList.return_value.list.call_args.kwargs
    assert call_kwargs["pageToken"] == "tok123"
    assert call_kwargs["maxResults"] == 10
    # No further pages, so nothing is appended.
    assert "Next page token" not in result


@pytest.mark.asyncio
async def test_get_events_reports_next_page_token():
    service = _events_service(
        {
            "items": [
                {
                    "id": "evt1",
                    "summary": "Standup",
                    "start": {"dateTime": "2026-09-06T10:00:00Z"},
                    "end": {"dateTime": "2026-09-06T10:15:00Z"},
                }
            ],
            "nextPageToken": "tok456",
        }
    )

    result = await _unwrap(calendar_tools.get_events)(
        service=service, user_google_email="user@example.com"
    )

    assert "Next page token: tok456" in result
    assert service.events.return_value.list.call_args.kwargs.get("pageToken") is None


@pytest.mark.asyncio
async def test_get_events_empty_page_with_more_pages_is_not_stated_as_no_events():
    service = _events_service({"items": [], "nextPageToken": "tok789"})

    result = await _unwrap(calendar_tools.get_events)(
        service=service,
        user_google_email="user@example.com",
        page_token="tok456",
        time_min="2026-09-06T00:00:00Z",
    )

    assert "No events found" not in result
    assert "more pages remain" in result
    assert "Next page token: tok789" in result
    assert "Pagination time_min: 2026-09-06T00:00:00Z" in result
    call_kwargs = service.events.return_value.list.call_args.kwargs
    assert call_kwargs["pageToken"] == "tok456"
    assert call_kwargs["timeMin"] == "2026-09-06T00:00:00Z"


@pytest.mark.asyncio
async def test_get_events_empty_final_page_still_says_no_events():
    service = _events_service({"items": []})

    result = await _unwrap(calendar_tools.get_events)(
        service=service, user_google_email="user@example.com"
    )

    assert result.startswith("No events found")
    assert "Next page token" not in result
