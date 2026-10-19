"""get_events and query_freebusy across calendars and accounts ("all")."""

from unittest.mock import Mock

import httplib2
import pytest
from googleapiclient.errors import HttpError

from auth.multi_account import AccountService
from core.utils import UserInputError
from gcalendar.calendar_tools import get_events, query_freebusy


def _unwrap(tool):
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _http_error(status=404):
    return HttpError(httplib2.Response({"status": status}), b"not found")


def _event(event_id, summary, start, end, uid=None, **extra):
    return {
        "id": event_id,
        "iCalUID": uid or f"{event_id}@google.com",
        "summary": summary,
        "start": {"dateTime": start, "timeZone": "America/New_York"},
        "end": {"dateTime": end, "timeZone": "America/New_York"},
        "htmlLink": f"https://calendar.google.com/event?eid={event_id}",
        **extra,
    }


def _cal(cal_id, summary, role="owner", primary=False):
    return {"id": cal_id, "summary": summary, "accessRole": role, "primary": primary}


def _calendar_service(calendars, events_by_calendar, freebusy=None, time_zone=None):
    """Calendar API mock: calendarList, events.list per calendar, freebusy."""
    service = Mock()
    service.calendarList().list().execute.return_value = {"items": calendars}
    service.events_calls = []

    def events_list(**params):
        service.events_calls.append(params)
        calendar_id = params["calendarId"]
        if calendar_id == "primary":
            calendar_id = next(c["id"] for c in calendars if c.get("primary"))
        request = Mock()
        value = events_by_calendar.get(calendar_id, {"items": []})
        if isinstance(value, Exception):
            request.execute.side_effect = value
        else:
            request.execute.return_value = {"timeZone": time_zone, **value}
        return request

    service.events().list.side_effect = events_list
    service.freebusy_bodies = []

    def freebusy_query(body):
        service.freebusy_bodies.append(body)
        request = Mock()
        request.execute.return_value = {
            "calendars": {
                item["id"]: (freebusy or {}).get(item["id"], {"busy": []})
                for item in body["items"]
            }
        }
        return request

    service.freebusy().query.side_effect = freebusy_query
    return service


def _two_accounts():
    """The personal account is free in the evening; the work account is not."""
    shared = "dinner-invite@google.com"
    personal = _calendar_service(
        [
            _cal("personal@example.com", "personal@example.com", primary=True),
            _cal("side@group.calendar.google.com", "Side projects"),
            _cal("en.usa#holiday@group.v.calendar.google.com", "Holidays", "reader"),
        ],
        {
            "personal@example.com": {
                "items": [
                    _event(
                        "p1",
                        "Dinner",
                        "2026-10-20T21:00:00-04:00",
                        "2026-10-20T22:00:00-04:00",
                        uid=shared,
                    ),
                    _event(
                        "p2",
                        "Standup",
                        "2026-10-20T09:00:00-04:00",
                        "2026-10-20T09:15:00-04:00",
                    ),
                ]
            },
            "side@group.calendar.google.com": {
                "items": [
                    {
                        "id": "s1",
                        "iCalUID": "s1@google.com",
                        "summary": "Errands",
                        "start": {"date": "2026-10-20"},
                        "end": {"date": "2026-10-21"},
                    }
                ]
            },
            "en.usa#holiday@group.v.calendar.google.com": {
                "items": [
                    {
                        "id": "h1",
                        "summary": "Some Holiday",
                        "start": {"date": "2026-10-20"},
                        "end": {"date": "2026-10-21"},
                    }
                ]
            },
        },
        time_zone="America/New_York",
    )
    work = _calendar_service(
        [_cal("work@example.com", "work@example.com", primary=True)],
        {
            "work@example.com": {
                "items": [
                    _event(
                        "w1",
                        "Review",
                        "2026-10-20T18:00:00-04:00",
                        "2026-10-20T18:50:00-04:00",
                    ),
                    _event(
                        "w2",
                        "Workshop",
                        "2026-10-20T19:05:00-04:00",
                        "2026-10-20T21:00:00-04:00",
                    ),
                    _event(
                        "w3",
                        "Dinner",
                        "2026-10-20T21:00:00-04:00",
                        "2026-10-20T22:00:00-04:00",
                        uid=shared,
                    ),
                ]
            }
        },
        time_zone="America/New_York",
    )
    return [
        AccountService(email="personal@example.com", service=personal),
        AccountService(email="work@example.com", service=work),
    ]


async def _events(service, user="all", **kwargs):
    kwargs.setdefault("time_min", "2026-10-20T00:00:00-04:00")
    kwargs.setdefault("time_max", "2026-10-21T00:00:00-04:00")
    return await _unwrap(get_events)(service=service, user_google_email=user, **kwargs)


@pytest.mark.asyncio
async def test_all_accounts_merges_own_calendars_by_time():
    accounts = _two_accounts()

    result = await _events(accounts)

    order = [
        result.index(f'"{name}"')
        for name in ("Errands", "Standup", "Review", "Workshop", "Dinner")
    ]
    assert order == sorted(order)
    assert "own calendars of personal@example.com, work@example.com" in result
    assert "Account / calendar: work@example.com / primary" in result
    assert '"Side projects" (side@group.calendar.google.com)' in result
    # Subscribed calendars (holidays, other people's) are not the user's time.
    assert "Some Holiday" not in result
    # The invitation on both accounts is one event, found in two places.
    assert result.count('"Dinner"') == 1
    assert "Also on: work@example.com / primary" in result
    for account in accounts:
        for params in account.service.events_calls:
            assert params["timeMin"] == "2026-10-20T00:00:00-04:00"
            assert params["singleEvents"] is True
            assert params["orderBy"] == "startTime"


@pytest.mark.asyncio
async def test_calendar_all_includes_subscribed_calendars():
    result = await _events(_two_accounts(), calendar_id="All")
    assert "Some Holiday" in result
    assert "every calendar of" in result


@pytest.mark.asyncio
async def test_all_accounts_with_primary_reads_only_primary_calendars():
    accounts = _two_accounts()

    result = await _events(accounts, calendar_id="primary")

    assert "Errands" not in result
    assert "Review" in result
    assert [p["calendarId"] for p in accounts[0].service.events_calls] == ["primary"]


@pytest.mark.asyncio
async def test_calendar_all_for_one_account():
    account = _two_accounts()[0]

    result = await _events(
        account.service, user="personal@example.com", calendar_id="all"
    )

    assert "Errands" in result and "Standup" in result
    assert "Review" not in result


@pytest.mark.asyncio
async def test_one_account_api_failure_reaches_the_tools_error_handling():
    service = _calendar_service([], {})
    service.calendarList().list().execute.side_effect = _http_error(403)
    with pytest.raises(HttpError):
        await _events(service, user="personal@example.com", calendar_id="all")


@pytest.mark.asyncio
async def test_all_day_events_sort_in_their_calendars_zone():
    pacific = _calendar_service(
        [_cal("a@example.com", "a", primary=True)],
        {
            "a@example.com": {
                "items": [
                    {
                        "id": "d",
                        "summary": "Day off",
                        "start": {"date": "2026-10-14"},
                        "end": {"date": "2026-10-15"},
                    },
                    {
                        "id": "e",
                        "summary": "Evening",
                        "start": {
                            "dateTime": "2026-10-13T19:00:00-07:00",
                            "timeZone": "America/Los_Angeles",
                        },
                        "end": {
                            "dateTime": "2026-10-13T20:00:00-07:00",
                            "timeZone": "America/Los_Angeles",
                        },
                    },
                ]
            }
        },
        time_zone="America/Los_Angeles",
    )

    result = await _events(
        [AccountService(email="a@example.com", service=pacific)],
        time_min="2026-10-13",
        time_max="2026-10-15",
    )

    assert result.index('"Evening"') < result.index('"Day off"')


@pytest.mark.asyncio
async def test_unreadable_accounts_and_calendars_are_named_not_fatal():
    broken = _calendar_service(
        [_cal("x@example.com", "x", primary=True)],
        {"x@example.com": _http_error()},
    )
    accounts = [
        AccountService(email="a@example.com", error="signed out"),
        AccountService(email="x@example.com", service=broken),
        _two_accounts()[1],
    ]

    result = await _events(accounts)

    assert "Review" in result
    assert "- a@example.com: signed out" in result
    assert "Calendars not included:\n- x@example.com / primary:" in result


@pytest.mark.asyncio
async def test_nothing_read_is_not_reported_as_no_events():
    result = await _events([AccountService(email="a@example.com", error="signed out")])
    assert "Could not read any calendar" in result
    assert "No events found" not in result


@pytest.mark.asyncio
async def test_max_results_caps_the_merged_list_and_says_so():
    result = await _events(_two_accounts(), max_results=2)

    assert "Retrieved 2 events" in result
    assert "Review" not in result
    assert "More events exist" in result


@pytest.mark.parametrize(
    "kwargs",
    [{"event_id": "e1"}, {"page_token": "t"}, {"single_events": False}],
)
@pytest.mark.asyncio
async def test_single_calendar_only_options_are_refused(kwargs):
    with pytest.raises(UserInputError):
        await _events(_two_accounts(), **kwargs)


@pytest.mark.asyncio
async def test_declined_only_when_declined_on_every_copy():
    def _invite(event_id, response):
        return _event(
            event_id,
            "Offsite",
            "2026-10-20T10:00:00-04:00",
            "2026-10-20T11:00:00-04:00",
            uid="offsite@google.com",
            attendees=[{"email": "me", "self": True, "responseStatus": response}],
        )

    def _account(email, response):
        return AccountService(
            email=email,
            service=_calendar_service(
                [_cal(email, email, primary=True)],
                {email: {"items": [_invite(email, response)]}},
            ),
        )

    mixed = await _events(
        [_account("a@example.com", "declined"), _account("b@example.com", "accepted")]
    )
    both = await _events(
        [_account("a@example.com", "declined"), _account("b@example.com", "declined")]
    )

    assert '"Offsite" (Starts' in mixed
    assert '"Offsite" (declined)' in both


@pytest.mark.asyncio
async def test_freebusy_all_accounts_combines_busy_periods():
    personal = _calendar_service(
        [
            _cal("personal@example.com", "personal", primary=True),
            _cal("side@group.calendar.google.com", "Side projects"),
        ],
        {},
        freebusy={
            "side@group.calendar.google.com": {
                "busy": [
                    {"start": "2026-10-20T22:30:00Z", "end": "2026-10-20T23:00:00Z"}
                ]
            },
        },
    )
    work = _calendar_service(
        [_cal("work@example.com", "work", primary=True)],
        {},
        freebusy={
            "work@example.com": {
                "busy": [
                    {"start": "2026-10-20T22:00:00Z", "end": "2026-10-20T22:50:00Z"},
                    {"start": "2026-10-20T23:05:00Z", "end": "2026-10-21T01:00:00Z"},
                ]
            }
        },
    )

    result = await _unwrap(query_freebusy)(
        service=[
            AccountService(email="personal@example.com", service=personal),
            AccountService(email="work@example.com", service=work),
        ],
        user_google_email="all",
        time_min="2026-10-20T21:00:00Z",
        time_max="2026-10-21T04:00:00Z",
    )

    assert "Busy on any of these calendars, combined (UTC):" in result
    assert "- 2026-10-20T22:00:00Z to 2026-10-20T23:00:00Z" in result
    assert "- 2026-10-20T23:05:00Z to 2026-10-21T01:00:00Z" in result
    assert "- personal@example.com / primary: free" in result
    assert '"Side projects" (side@group.calendar.google.com): busy' in result
    assert [item["id"] for item in personal.freebusy_bodies[0]["items"]] == [
        "personal@example.com",
        "side@group.calendar.google.com",
    ]


@pytest.mark.asyncio
async def test_freebusy_never_claims_free_when_something_was_not_read():
    personal = _calendar_service(
        [_cal("personal@example.com", "personal", primary=True)],
        {},
        freebusy={"personal@example.com": {"errors": [{"reason": "notFound"}]}},
    )
    accounts = [
        AccountService(email="personal@example.com", service=personal),
        AccountService(email="work@example.com", error="signed out"),
    ]

    result = await _unwrap(query_freebusy)(
        service=accounts,
        user_google_email="all",
        time_min="2026-10-20T21:00:00Z",
        time_max="2026-10-21T04:00:00Z",
    )

    assert "free on every calendar" not in result
    assert "Busy on the calendars that could be read" in result
    assert "- work@example.com: signed out" in result

    nothing = await _unwrap(query_freebusy)(
        service=[accounts[1]],
        user_google_email="all",
        time_min="2026-10-20T21:00:00Z",
        time_max="2026-10-21T04:00:00Z",
    )
    assert "Could not read any calendar" in nothing


@pytest.mark.asyncio
async def test_freebusy_splits_more_than_fifty_calendars():
    calendars = [_cal(f"c{i}@example.com", f"c{i}") for i in range(120)]
    calendars[0]["primary"] = True
    service = _calendar_service(calendars, {})

    await _unwrap(query_freebusy)(
        service=service,
        user_google_email="a@example.com",
        time_min="2026-10-20T21:00:00Z",
        time_max="2026-10-21T04:00:00Z",
        calendar_ids=["all"],
    )

    assert [len(b["items"]) for b in service.freebusy_bodies] == [50, 50, 20]


@pytest.mark.asyncio
async def test_partly_read_calendars_are_not_reported_as_no_events():
    service = _calendar_service(
        [
            _cal("a@example.com", "a", primary=True),
            _cal("b@group.calendar.google.com", "B"),
        ],
        {"b@group.calendar.google.com": _http_error(403)},
    )
    result = await _events(service, user="a@example.com", calendar_id="all")
    assert "that could be read (some calendars could not" in result

    service = _calendar_service(
        [_cal("a@example.com", "a", primary=True)],
        {"a@example.com": _http_error(403)},
    )
    result = await _events(service, user="a@example.com", calendar_id="all")
    assert "Could not read any calendar" in result


@pytest.mark.asyncio
async def test_freebusy_treats_a_missing_calendar_as_not_read():
    service = _calendar_service([_cal("a@example.com", "a", primary=True)], {})
    service.freebusy().query.side_effect = None
    service.freebusy().query().execute.return_value = {"calendars": {}}

    result = await _unwrap(query_freebusy)(
        service=service,
        user_google_email="a@example.com",
        time_min="2026-10-20T21:00:00Z",
        time_max="2026-10-21T04:00:00Z",
        calendar_ids=["all"],
    )

    assert "free on every calendar" not in result
    assert "not read (calendar: notReturned)" in result


@pytest.mark.asyncio
async def test_freebusy_refuses_all_mixed_with_other_ids():
    with pytest.raises(UserInputError):
        await _unwrap(query_freebusy)(
            service=_calendar_service([], {}),
            user_google_email="a@example.com",
            time_min="2026-10-20T21:00:00Z",
            time_max="2026-10-21T04:00:00Z",
            calendar_ids=["all", "x@group.calendar.google.com"],
        )
