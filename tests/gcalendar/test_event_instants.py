import datetime
from unittest.mock import Mock

import pytest

from gcalendar.calendar_tools import (
    _build_time_boundary,
    _create_event_impl,
    _modify_event_impl,
    _saved_event_times,
)


@pytest.mark.parametrize("action", ["create", "update"])
@pytest.mark.parametrize(
    "start,end,expected_start,expected_end,minutes",
    [
        (
            "2026-09-17T09:50:00+01:00",
            "2026-09-17T11:35:00-07:00",
            "2026-09-17T09:50:00+01:00",
            "2026-09-17T19:35:00+01:00",
            585,
        ),
        (
            "2026-09-26T20:20:00-07:00",
            "2026-09-27T13:45:00+01:00",
            "2026-09-27T04:20:00+01:00",
            "2026-09-27T13:45:00+01:00",
            565,
        ),
    ],
)
@pytest.mark.asyncio
async def test_flights_preserve_instants_and_echo_saved_times(
    action, start, end, expected_start, expected_end, minutes
):
    service = Mock()
    service.events().get().execute.return_value = {}
    saved = {
        "id": "flight",
        "start": {"dateTime": expected_start, "timeZone": "Europe/London"},
        "end": {"dateTime": expected_end, "timeZone": "Europe/London"},
    }
    method = "insert" if action == "create" else "patch"
    getattr(service.events(), method)().execute.return_value = saved
    kwargs = dict(
        service=service,
        user_google_email="user@example.com",
        start_time=start,
        end_time=end,
        timezone="Europe/London",
    )
    if action == "create":
        result = await _create_event_impl(summary="Flight", **kwargs)
    else:
        result = await _modify_event_impl(event_id="flight", **kwargs)
    body = [
        c.kwargs["body"]
        for c in getattr(service.events(), method).call_args_list
        if c.kwargs
    ][-1]
    assert body["start"] == saved["start"]
    assert body["end"] == saved["end"]
    assert expected_start in result and expected_end in result
    assert f"Elapsed duration: {minutes} minutes" in result


@pytest.mark.parametrize(
    "value,zone,expected",
    [
        ("2026-03-08T09:30:00Z", "America/Los_Angeles", "2026-03-08T01:30:00-08:00"),
        ("2026-03-08T10:30:00Z", "America/Los_Angeles", "2026-03-08T03:30:00-07:00"),
        ("2026-11-01T08:30:00Z", "America/Los_Angeles", "2026-11-01T01:30:00-07:00"),
        ("2026-11-01T09:30:00Z", "America/Los_Angeles", "2026-11-01T01:30:00-08:00"),
        ("2026-09-26T20:20:00-07:00", "Asia/Kolkata", "2026-09-27T08:50:00+05:30"),
    ],
)
def test_conversion_preserves_dst_fold_and_date_rollover(value, zone, expected):
    boundary = _build_time_boundary(value, zone)
    assert boundary == {"dateTime": expected, "timeZone": zone}
    assert datetime.datetime.fromisoformat(
        value.replace("Z", "+00:00")
    ) == datetime.datetime.fromisoformat(expected)


@pytest.mark.asyncio
async def test_recurring_event_retains_iana_zone_for_expansion():
    service = Mock()
    service.events().insert().execute.return_value = {}
    await _create_event_impl(
        service=service,
        user_google_email="user@example.com",
        summary="Weekly",
        start_time="2026-03-02T17:00:00Z",
        end_time="2026-03-02T18:00:00Z",
        timezone="America/Los_Angeles",
        recurrence=["RRULE:FREQ=WEEKLY;COUNT=3"],
    )
    body = [
        c.kwargs["body"] for c in service.events().insert.call_args_list if c.kwargs
    ][-1]
    assert body["start"] == {
        "dateTime": "2026-03-02T09:00:00-08:00",
        "timeZone": "America/Los_Angeles",
    }
    assert body["recurrence"] == ["RRULE:FREQ=WEEKLY;COUNT=3"]


def test_saved_duration_exposes_incorrect_return_repair():
    result = _saved_event_times(
        {
            "start": {"dateTime": "2026-09-26T21:20:00+01:00"},
            "end": {"dateTime": "2026-09-27T13:45:00+01:00"},
        }
    )
    assert "Elapsed duration: 985 minutes" in result


def test_saved_output_uses_google_response_and_all_day_exclusive_end():
    assert "All-day span: 2 days (end date exclusive)" in _saved_event_times(
        {"start": {"date": "2026-09-17"}, "end": {"date": "2026-09-19"}}
    )
    assert "All-day span: 1 day (end date exclusive)" in _saved_event_times(
        {"start": {"date": "2026-09-17"}, "end": {"date": "2026-09-18"}}
    )
    assert "Elapsed duration" not in _saved_event_times({})


def test_saved_duration_uses_elapsed_time_across_a_dst_transition():
    result = _saved_event_times(
        {
            "start": {
                "dateTime": "2026-03-08T01:30:00-08:00",
                "timeZone": "America/Los_Angeles",
            },
            "end": {
                "dateTime": "2026-03-08T03:30:00-07:00",
                "timeZone": "America/Los_Angeles",
            },
        }
    )
    assert "Elapsed duration: 60 minutes" in result


@pytest.mark.parametrize("boundary", [None, [], {"dateTime": "bad"}, {"dateTime": 12}])
def test_malformed_saved_boundary_does_not_turn_a_successful_write_into_an_error(
    boundary,
):
    assert "Elapsed duration" not in _saved_event_times(
        {"start": boundary, "end": {"dateTime": "2026-09-17T11:35:00-07:00"}}
    )


@pytest.mark.parametrize("value", ["tomorrow at 3pmT", "2026-09-17T25:00:00Z"])
def test_unparseable_timestamp_with_zone_names_the_value(value):
    with pytest.raises(ValueError, match="Invalid RFC3339 timestamp"):
        _build_time_boundary(value, "Europe/London")


@pytest.mark.parametrize("suffix", ["Z", "z"])
def test_utc_designator_is_case_insensitive(suffix):
    assert _build_time_boundary(f"2026-09-17T18:35:00{suffix}", "Europe/London") == {
        "dateTime": "2026-09-17T19:35:00+01:00",
        "timeZone": "Europe/London",
    }


def test_multi_week_duration_is_not_rendered_in_exponent_notation():
    result = _saved_event_times(
        {
            "start": {"dateTime": "2026-09-01T09:00:00Z"},
            "end": {"dateTime": "2026-09-15T09:00:00Z"},
        }
    )
    assert "Elapsed duration: 20160 minutes (1209600 seconds)" in result
