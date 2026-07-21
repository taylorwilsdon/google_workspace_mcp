"""Unit tests for the reminder-formatting helpers used by get_events and
list_calendars detailed output."""

from gcalendar.calendar_helpers import (
    _format_reminder_overrides,
    _format_reminders,
)


# ---------------------------------------------------------------------------
# _format_reminder_overrides (used for event overrides and calendar defaults)
# ---------------------------------------------------------------------------


def test_format_reminder_overrides_single():
    assert (
        _format_reminder_overrides([{"method": "popup", "minutes": 10}])
        == "popup 10 min before"
    )


def test_format_reminder_overrides_multiple():
    assert (
        _format_reminder_overrides(
            [
                {"method": "popup", "minutes": 10},
                {"method": "email", "minutes": 1440},
            ]
        )
        == "popup 10 min before, email 1440 min before"
    )


def test_format_reminder_overrides_empty_returns_none():
    assert _format_reminder_overrides([]) == "None"


def test_format_reminder_overrides_missing_minutes():
    # Defensive: an override without minutes should still render its method.
    assert _format_reminder_overrides([{"method": "popup"}]) == "popup"


def test_format_reminder_overrides_missing_method():
    assert _format_reminder_overrides([{"minutes": 30}]) == "unknown 30 min before"


# ---------------------------------------------------------------------------
# _format_reminders (an event's reminders object: useDefault + overrides)
# ---------------------------------------------------------------------------


def test_format_reminders_use_default():
    assert _format_reminders({"useDefault": True}) == "Using calendar default reminders"


def test_format_reminders_use_default_ignores_overrides():
    # Per the Calendar API, overrides are ignored when useDefault is true.
    assert (
        _format_reminders(
            {"useDefault": True, "overrides": [{"method": "popup", "minutes": 5}]}
        )
        == "Using calendar default reminders"
    )


def test_format_reminders_custom_overrides():
    assert (
        _format_reminders(
            {"useDefault": False, "overrides": [{"method": "email", "minutes": 60}]}
        )
        == "email 60 min before"
    )


def test_format_reminders_no_default_no_overrides():
    assert _format_reminders({"useDefault": False}) == "No reminders"


def test_format_reminders_none_input():
    # Absent reminders data (e.g. field not returned) renders as "None".
    assert _format_reminders(None) == "None"


def test_format_reminders_empty_dict():
    assert _format_reminders({}) == "None"
