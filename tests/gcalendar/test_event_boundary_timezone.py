"""
Unit tests for per-boundary timezone resolution (EventBoundary).

Google returns each event boundary as {"dateTime": ..., "timeZone": ...}, and the
two do not agree by default: events.list/get normalize every dateTime in the
response into ONE zone (the calendar's own, absent a timeZone request param)
while leaving each boundary's IANA timeZone untouched. An arrival authored as
17:50 Europe/Amsterdam comes back as "2026-08-21T18:50:00+03:00" on an
Asia/Jerusalem calendar -- correct instant, wrong zone.

The formatter used to echo that normalized offset verbatim, so the authored
wall-clock was unrecoverable from the output. It now resolves each boundary into
its own zone. The instant never changes; only its presentation is corrected.

Conversion is gated on a resolvable IANA timeZone being present. Without one
there is no authored wall-clock to restore, so the returned value is echoed
verbatim -- see test_get_events_detailed_fields.py, which pins that behavior for
boundaries that carry no timeZone at all.
"""

import datetime
import os
import sys
from dataclasses import FrozenInstanceError

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_helpers import (
    EventBoundary,
    _format_event_time,
    parse_event_boundary,
)

# Outbound TLV->AMS: authored 13:45 Asia/Jerusalem -> 17:50 Europe/Amsterdam,
# returned by Google normalized into the calendar's Asia/Jerusalem zone.
OUTBOUND = {
    "start": {"dateTime": "2026-08-21T13:45:00+03:00", "timeZone": "Asia/Jerusalem"},
    "end": {"dateTime": "2026-08-21T18:50:00+03:00", "timeZone": "Europe/Amsterdam"},
}
# Return AMS->TLV: 19:00 Europe/Amsterdam -> 00:30 next-day Asia/Jerusalem.
RETURN = {
    "start": {"dateTime": "2026-08-28T20:00:00+03:00", "timeZone": "Europe/Amsterdam"},
    "end": {"dateTime": "2026-08-29T00:30:00+03:00", "timeZone": "Asia/Jerusalem"},
}


def test_foreign_zone_boundary_renders_in_its_own_zone():
    """The arrival reads 17:50+02:00, not the normalized 18:50+03:00."""
    assert _format_event_time(OUTBOUND, "end") == (
        "2026-08-21T17:50:00+02:00 [Europe/Amsterdam; weekday: Friday; ISO weekday: 5]"
    )


def test_home_zone_boundary_is_unchanged_but_labeled():
    assert _format_event_time(OUTBOUND, "start") == (
        "2026-08-21T13:45:00+03:00 [Asia/Jerusalem; weekday: Friday; ISO weekday: 5]"
    )


def test_return_leg_departure_renders_in_departure_zone():
    assert _format_event_time(RETURN, "start") == (
        "2026-08-28T19:00:00+02:00 [Europe/Amsterdam; weekday: Friday; ISO weekday: 5]"
    )


def test_return_leg_arrival_renders_in_arrival_zone():
    assert _format_event_time(RETURN, "end") == (
        "2026-08-29T00:30:00+03:00 [Asia/Jerusalem; weekday: Saturday; ISO weekday: 6]"
    )


def test_conversion_preserves_the_instant():
    """Presentation changes; the moment must not."""
    raw = datetime.datetime.fromisoformat(OUTBOUND["end"]["dateTime"])
    boundary = parse_event_boundary(OUTBOUND, "end")
    assert boundary is not None
    assert boundary.moment == raw


def test_weekday_is_computed_in_the_boundarys_own_zone():
    """23:30 Friday in Amsterdam must not be reported as Saturday."""
    item = {
        "start": {
            "dateTime": "2026-08-28T20:00:00+03:00",
            "timeZone": "Europe/Amsterdam",
        },
        "end": {
            "dateTime": "2026-08-29T00:30:00+03:00",
            "timeZone": "Europe/Amsterdam",
        },
    }
    assert _format_event_time(item, "end") == (
        "2026-08-28T23:30:00+02:00 [Europe/Amsterdam; weekday: Friday; ISO weekday: 5]"
    )


def test_dst_boundary_uses_the_correct_offset_for_the_date():
    """January in Amsterdam is +01:00, not the summer +02:00."""
    item = {
        "start": {
            "dateTime": "2026-01-15T12:00:00+02:00",
            "timeZone": "Asia/Jerusalem",
        },
        "end": {
            "dateTime": "2026-01-15T14:00:00+02:00",
            "timeZone": "Europe/Amsterdam",
        },
    }
    assert _format_event_time(item, "end") == (
        "2026-01-15T13:00:00+01:00 [Europe/Amsterdam; weekday: Thursday; ISO weekday: 4]"
    )


def test_offset_gap_widens_when_zones_switch_dst_on_different_dates():
    """The hard case: Israel and the EU do not change clocks on the same day.

    Israel switches on the Friday before the last Sunday in March, the EU on the
    last Sunday. For those two days the TLV/AMS gap is 2 hours, not its usual 1 --
    in 2027, March 26-27 only. Any implementation that hardcodes a fixed offset,
    or reuses one boundary's offset for the other, gets these dates wrong.
    """
    item = {
        # 17:50 Amsterdam (+01:00) returned normalized into Asia/Jerusalem (+03:00).
        "start": {
            "dateTime": "2027-03-26T13:45:00+03:00",
            "timeZone": "Asia/Jerusalem",
        },
        "end": {
            "dateTime": "2027-03-26T19:50:00+03:00",
            "timeZone": "Europe/Amsterdam",
        },
    }
    assert _format_event_time(item, "start") == (
        "2027-03-26T13:45:00+03:00 [Asia/Jerusalem; weekday: Friday; ISO weekday: 5]"
    )
    assert _format_event_time(item, "end") == (
        "2027-03-26T17:50:00+01:00 [Europe/Amsterdam; weekday: Friday; ISO weekday: 5]"
    )


def test_utc_z_boundary_with_iana_zone_converts():
    item = {
        "start": {"dateTime": "2026-08-21T10:45:00Z", "timeZone": "Europe/Amsterdam"},
        "end": {"dateTime": "2026-08-21T15:50:00Z", "timeZone": "Europe/Amsterdam"},
    }
    assert _format_event_time(item, "end") == (
        "2026-08-21T17:50:00+02:00 [Europe/Amsterdam; weekday: Friday; ISO weekday: 5]"
    )


def test_boundary_without_timezone_field_keeps_returned_value_verbatim():
    """The common single-zone case must not grow noise or shift."""
    item = {
        "start": {"dateTime": "2026-08-21T13:45:00+03:00"},
        "end": {"dateTime": "2026-08-21T14:45:00+03:00"},
    }
    assert _format_event_time(item, "end") == (
        "2026-08-21T14:45:00+03:00 [weekday: Friday; ISO weekday: 5]"
    )


def test_utc_z_without_timezone_field_keeps_its_z_spelling():
    """Regression: converting unconditionally rewrote 'Z' as '+00:00'."""
    item = {"start": {"dateTime": "2026-08-20T23:30:00Z"}}
    assert _format_event_time(item, "start") == (
        "2026-08-20T23:30:00Z [weekday: Thursday; ISO weekday: 4]"
    )


def test_unrecognized_timezone_keeps_returned_offset_and_raw_zone_name():
    item = {
        "start": {
            "dateTime": "2026-08-21T13:45:00+03:00",
            "timeZone": "Mars/Olympus_Mons",
        },
        "end": {
            "dateTime": "2026-08-21T14:45:00+03:00",
            "timeZone": "Mars/Olympus_Mons",
        },
    }
    assert _format_event_time(item, "end") == (
        "2026-08-21T14:45:00+03:00 [Mars/Olympus_Mons; weekday: Friday; ISO weekday: 5]"
    )

    boundary = parse_event_boundary(item, "end")
    assert boundary is not None
    assert boundary.timezone == "Mars/Olympus_Mons"
    assert boundary.timezone_resolved is False


def test_all_day_boundary_is_unaffected():
    item = {"start": {"date": "2026-08-21"}, "end": {"date": "2026-08-29"}}
    assert _format_event_time(item, "start") == (
        "2026-08-21 [weekday: Friday; ISO weekday: 5]"
    )
    assert _format_event_time(item, "end") == (
        "2026-08-29 [weekday: Saturday; ISO weekday: 6; exclusive all-day end]"
    )


def test_all_day_boundary_parses_as_all_day():
    boundary = parse_event_boundary({"start": {"date": "2026-08-21"}}, "start")
    assert boundary is not None
    assert boundary.is_all_day is True
    assert boundary.moment is None
    assert boundary.timezone is None


def test_malformed_boundary_falls_back_to_raw_value():
    item = {"start": {"dateTime": "not-a-timestamp"}}
    assert _format_event_time(item, "start") == "not-a-timestamp"


def test_missing_boundary_does_not_raise():
    assert parse_event_boundary({}, "start") is None


def test_naive_datetime_without_offset_falls_back_to_raw():
    """Google always sends an offset; a naive value is malformed, not local time."""
    item = {"start": {"dateTime": "2026-08-21T13:45:00", "timeZone": "Asia/Jerusalem"}}
    assert _format_event_time(item, "start") == "2026-08-21T13:45:00"


def test_boundary_isoformat_matches_render_stamp():
    boundary = parse_event_boundary(OUTBOUND, "end")
    assert boundary is not None
    assert boundary.isoformat() == "2026-08-21T17:50:00+02:00"
    assert boundary.render().startswith(boundary.isoformat())


def test_boundary_is_frozen_and_hashable():
    """Frozen dataclass: safe to compare/cache, cannot be mutated behind a caller."""
    a = parse_event_boundary(OUTBOUND, "end")
    b = parse_event_boundary(OUTBOUND, "end")
    assert a is not None and b is not None
    assert isinstance(a, EventBoundary)
    assert a == b and hash(a) == hash(b)
    with pytest.raises(FrozenInstanceError):
        setattr(a, "raw", "mutated")
