"""
Unit tests for pagination in get_events and list_calendars.

gcalendar was the only package in the repo with no pagination: the request was
built without pageToken and nextPageToken was dropped from the response. A
caller received one page and no signal that more existed, and an empty page
carrying a nextPageToken produced "No events found", which the Calendar API can
make untrue.

These tests assert both directions: the token is forwarded on the request, and
it is surfaced on the response.
"""

import datetime
import os
import sys
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import get_events, list_calendars

EMAIL = "user@example.com"


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _event(event_id="evt-1", summary="Standup"):
    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": "2026-04-06T09:00:00Z"},
        "end": {"dateTime": "2026-04-06T09:15:00Z"},
    }


def _events_service(items, next_page_token=None):
    response = {"items": items}
    if next_page_token is not None:
        response["nextPageToken"] = next_page_token
    service = Mock()
    service.events().list().execute = Mock(return_value=response)
    return service


def _calendars_service(items, next_page_token=None):
    response = {"items": items}
    if next_page_token is not None:
        response["nextPageToken"] = next_page_token
    service = Mock()
    service.calendarList().list().execute = Mock(return_value=response)
    return service


async def _get_events(service, **kwargs):
    return await _unwrap(get_events)(
        service=service,
        user_google_email=EMAIL,
        time_min="2026-04-06T00:00:00Z",
        time_max="2026-04-07T00:00:00Z",
        **kwargs,
    )


class TestGetEventsPagination:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty_first_page", [False, True])
    @pytest.mark.parametrize("detailed", [False, True])
    async def test_default_query_can_continue_using_response_parameters(
        self, empty_first_page, detailed
    ):
        service = _events_service([])
        service.events().list().execute.side_effect = [
            {
                "items": [] if empty_first_page else [_event()],
                "nextPageToken": "token-next",
            },
            {"items": [_event("evt-2", "Next event")]},
        ]
        fn = _unwrap(get_events)
        with patch(
            "gcalendar.calendar_tools.datetime.datetime", wraps=datetime.datetime
        ) as clock:
            clock.now.side_effect = [
                datetime.datetime(2026, 4, 6, tzinfo=datetime.timezone.utc),
                datetime.datetime(2026, 4, 7, tzinfo=datetime.timezone.utc),
            ]
            first_page = await fn(
                service=service, user_google_email=EMAIL, detailed=detailed
            )
            first_params = service.events().list.call_args.kwargs.copy()
            continuation = dict(
                line.split(": ", 1)
                for line in first_page.splitlines()
                if line.startswith(("Next page token: ", "Pagination time_min: "))
            )

            last_page = await fn(
                service=service,
                user_google_email=EMAIL,
                detailed=detailed,
                page_token=continuation["Next page token"],
                time_min=continuation["Pagination time_min"],
            )

        assert service.events().list.call_args.kwargs == {
            **first_params,
            "pageToken": "token-next",
        }
        assert clock.now.call_count == 1
        assert "evt-2" in last_page
        assert "Next page token" not in last_page
        assert "Pagination time_min" not in last_page

    @pytest.mark.asyncio
    async def test_forwards_the_page_token(self):
        service = _events_service([_event()])

        await _get_events(service, page_token="token-abc")

        assert service.events().list.call_args.kwargs["pageToken"] == "token-abc"

    @pytest.mark.asyncio
    async def test_omits_page_token_when_not_given(self):
        service = _events_service([_event()])

        await _get_events(service)

        assert "pageToken" not in service.events().list.call_args.kwargs

    @pytest.mark.asyncio
    async def test_surfaces_the_next_page_token(self):
        service = _events_service([_event()], next_page_token="token-next")

        result = await _get_events(service)

        assert "Next page token: token-next" in result

    @pytest.mark.asyncio
    async def test_says_nothing_about_pages_when_the_last_one_is_reached(self):
        service = _events_service([_event()])

        result = await _get_events(service)

        assert "Next page token" not in result

    @pytest.mark.asyncio
    async def test_an_empty_page_with_more_behind_it_is_not_reported_as_no_events(self):
        """The API can return an empty page alongside a nextPageToken."""
        service = _events_service([], next_page_token="token-next")

        result = await _get_events(service)

        assert "No events found" not in result
        assert "more pages remain" in result
        assert "Next page token: token-next" in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("time_min", [None, "", "   ", "null", '"null"', "None"])
    async def test_a_continuation_without_time_min_is_rejected(self, time_min):
        """With time_min omitted the range starts at "now", which moves between
        calls, so the continuation would page a different query than the token
        came from."""
        service = _events_service([_event()])
        # The helper primes the mock by calling list() once; ignore that setup call.
        service.events().list.reset_mock()

        with pytest.raises(ValueError, match="page_token requires time_min"):
            await _unwrap(get_events)(
                service=service,
                user_google_email=EMAIL,
                page_token="token-abc",
                time_min=time_min,
            )

        service.events().list.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_continuation_with_time_min_is_allowed(self):
        service = _events_service([_event()])

        await _get_events(service, page_token="token-abc")

        params = service.events().list.call_args.kwargs
        assert params["pageToken"] == "token-abc"
        assert params["timeMin"] == "2026-04-06T00:00:00Z"

    @pytest.mark.asyncio
    async def test_a_continuation_without_time_min_is_allowed_when_not_expanding(self):
        """Unexpanded queries omit timeMin entirely, so nothing drifts."""
        service = _events_service([_event()])

        await _unwrap(get_events)(
            service=service,
            user_google_email=EMAIL,
            page_token="token-abc",
            single_events=False,
        )

        params = service.events().list.call_args.kwargs
        assert params["pageToken"] == "token-abc"
        assert "timeMin" not in params

    @pytest.mark.asyncio
    async def test_a_genuinely_empty_range_still_reports_no_events(self):
        service = _events_service([])

        result = await _get_events(service)

        assert "No events found" in result
        assert "Next page token" not in result


class TestListCalendarsPagination:
    @pytest.mark.asyncio
    async def test_forwards_page_token_and_max_results(self):
        service = _calendars_service([{"id": "primary", "summary": "Work"}])

        await _unwrap(list_calendars)(
            service=service,
            user_google_email=EMAIL,
            max_results=10,
            page_token="token-abc",
        )

        params = service.calendarList().list.call_args.kwargs
        assert params["pageToken"] == "token-abc"
        assert params["maxResults"] == 10

    @pytest.mark.asyncio
    async def test_sends_no_paging_arguments_by_default(self):
        """Omitting both keeps the API's own defaults, as before this change."""
        service = _calendars_service([{"id": "primary", "summary": "Work"}])

        await _unwrap(list_calendars)(service=service, user_google_email=EMAIL)

        params = service.calendarList().list.call_args.kwargs
        assert "pageToken" not in params
        assert "maxResults" not in params

    @pytest.mark.asyncio
    async def test_surfaces_the_next_page_token(self):
        service = _calendars_service(
            [{"id": "primary", "summary": "Work"}], next_page_token="token-next"
        )

        result = await _unwrap(list_calendars)(service=service, user_google_email=EMAIL)

        assert "Successfully listed 1 calendars" in result
        assert "Next page token: token-next" in result

    @pytest.mark.asyncio
    async def test_an_empty_page_with_more_behind_it_is_not_reported_as_no_calendars(
        self,
    ):
        service = _calendars_service([], next_page_token="token-next")

        result = await _unwrap(list_calendars)(service=service, user_google_email=EMAIL)

        assert "No calendars found" not in result
        assert "Next page token: token-next" in result

    @pytest.mark.asyncio
    async def test_a_genuinely_empty_account_still_reports_no_calendars(self):
        service = _calendars_service([])

        result = await _unwrap(list_calendars)(service=service, user_google_email=EMAIL)

        assert "No calendars found" in result
