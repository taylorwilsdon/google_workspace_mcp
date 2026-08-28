"""
Unit tests for authoring events whose two boundaries sit in different timezones.

A single `timezone` applied to both ends cannot express a flight: departing 13:45
Asia/Jerusalem and landing 17:50 Europe/Amsterdam is one event authored in two
zones. The previous behavior stripped the caller's explicit UTC offsets and then
stamped the one `timezone` onto both boundaries, so the arrival above was written
as 17:50 Israel time -- an hour off, silently, with the API reporting success.

`start_timezone` / `end_timezone` override `timezone` per boundary; each falls
back to `timezone` when omitted, so existing single-zone callers are unaffected.
"""

import os
import sys
import zoneinfo
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from gcalendar.calendar_tools import (
    _build_time_boundary,
    _create_event_impl,
    _modify_event_impl,
    manage_event,
)


def _unwrap(tool):
    """Unwrap FunctionTool + decorators to the original async function."""
    fn = tool.fn if hasattr(tool, "fn") else tool
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _create_mock_service():
    mock_service = Mock()
    mock_service.events().insert().execute = Mock(
        return_value={"id": "evt1", "htmlLink": "https://example.test/evt1"}
    )
    mock_service.events().get().execute = Mock(return_value={})
    mock_service.events().patch().execute = Mock(
        return_value={"id": "evt1", "htmlLink": "https://example.test/evt1"}
    )
    mock_service.events().update().execute = Mock(
        return_value={"id": "evt1", "htmlLink": "https://example.test/evt1"}
    )
    return mock_service


def _sent_body(mock_service, method="insert"):
    """Return the event body of the last insert/patch call."""
    calls = [
        c for c in getattr(mock_service.events(), method).call_args_list if c.kwargs
    ]
    return calls[-1].kwargs["body"]


# --- _build_time_boundary ----------------------------------------------------


def test_boundary_pairs_datetime_with_its_zone():
    assert _build_time_boundary("2026-08-21T17:50:00", "Europe/Amsterdam") == {
        "dateTime": "2026-08-21T17:50:00",
        "timeZone": "Europe/Amsterdam",
    }


def test_boundary_strips_offset_when_zone_given():
    """An explicit offset would override the IANA zone and defeat DST resolution."""
    assert _build_time_boundary("2026-08-21T17:50:00+03:00", "Europe/Amsterdam") == {
        "dateTime": "2026-08-21T17:50:00",
        "timeZone": "Europe/Amsterdam",
    }


def test_boundary_keeps_offset_when_no_zone_given():
    assert _build_time_boundary("2026-08-21T17:50:00+02:00", None) == {
        "dateTime": "2026-08-21T17:50:00+02:00"
    }


def test_boundary_handles_all_day_dates():
    assert _build_time_boundary("2026-08-21", "Europe/Amsterdam") == {
        "date": "2026-08-21"
    }


# --- create ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_writes_distinct_zones_per_boundary():
    service = _create_mock_service()
    await _create_event_impl(
        service=service,
        user_google_email="user@example.com",
        summary="Flight IZ1513 TLV -> AMS",
        start_time="2026-08-21T13:45:00",
        end_time="2026-08-21T17:50:00",
        start_timezone="Asia/Jerusalem",
        end_timezone="Europe/Amsterdam",
    )
    body = _sent_body(service)
    assert body["start"] == {
        "dateTime": "2026-08-21T13:45:00",
        "timeZone": "Asia/Jerusalem",
    }
    assert body["end"] == {
        "dateTime": "2026-08-21T17:50:00",
        "timeZone": "Europe/Amsterdam",
    }


@pytest.mark.asyncio
async def test_create_falls_back_to_event_wide_timezone():
    """Omitting the per-boundary params must behave exactly as before."""
    service = _create_mock_service()
    await _create_event_impl(
        service=service,
        user_google_email="user@example.com",
        summary="Standup",
        start_time="2026-08-21T09:00:00",
        end_time="2026-08-21T09:15:00",
        timezone="America/New_York",
    )
    body = _sent_body(service)
    assert body["start"]["timeZone"] == "America/New_York"
    assert body["end"]["timeZone"] == "America/New_York"


@pytest.mark.asyncio
async def test_create_allows_overriding_only_one_boundary():
    service = _create_mock_service()
    await _create_event_impl(
        service=service,
        user_google_email="user@example.com",
        summary="Red-eye",
        start_time="2026-08-28T19:00:00",
        end_time="2026-08-29T00:30:00",
        timezone="Europe/Amsterdam",
        end_timezone="Asia/Jerusalem",
    )
    body = _sent_body(service)
    assert body["start"]["timeZone"] == "Europe/Amsterdam"
    assert body["end"]["timeZone"] == "Asia/Jerusalem"


@pytest.mark.asyncio
async def test_create_without_any_timezone_keeps_offsets():
    service = _create_mock_service()
    await _create_event_impl(
        service=service,
        user_google_email="user@example.com",
        summary="Sync",
        start_time="2026-08-21T09:00:00Z",
        end_time="2026-08-21T09:15:00Z",
    )
    body = _sent_body(service)
    assert body["start"] == {"dateTime": "2026-08-21T09:00:00Z"}
    assert "timeZone" not in body["end"]


# --- update ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_writes_distinct_zones_per_boundary():
    service = _create_mock_service()
    await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt1",
        start_time="2026-08-28T19:00:00",
        end_time="2026-08-29T00:30:00",
        start_timezone="Europe/Amsterdam",
        end_timezone="Asia/Jerusalem",
    )
    body = _sent_body(service, "patch")
    assert body["start"] == {
        "dateTime": "2026-08-28T19:00:00",
        "timeZone": "Europe/Amsterdam",
    }
    assert body["end"] == {
        "dateTime": "2026-08-29T00:30:00",
        "timeZone": "Asia/Jerusalem",
    }


@pytest.mark.asyncio
async def test_update_falls_back_to_event_wide_timezone():
    service = _create_mock_service()
    await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt1",
        start_time="2026-08-21T09:00:00",
        end_time="2026-08-21T09:15:00",
        timezone="America/New_York",
    )
    body = _sent_body(service, "patch")
    assert body["start"]["timeZone"] == "America/New_York"
    assert body["end"]["timeZone"] == "America/New_York"


@pytest.mark.asyncio
async def test_update_leaves_untouched_boundary_absent():
    """Patch semantics: an omitted end must not be invented."""
    service = _create_mock_service()
    await _modify_event_impl(
        service=service,
        user_google_email="user@example.com",
        event_id="evt1",
        start_time="2026-08-21T09:00:00",
        start_timezone="Asia/Jerusalem",
    )
    body = _sent_body(service, "patch")
    assert body["start"]["timeZone"] == "Asia/Jerusalem"
    assert "end" not in body


# --- backward compatibility --------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_positional_call_still_binds_attachments_correctly():
    """New params must not shift existing positional bindings.

    `attachments` follows `timezone` in manage_event's signature, so inserting the
    new parameters between them would silently rebind a caller's attachment list
    to start_timezone -- which then goes out as start.timeZone and is rejected by
    Google. Appending them as keyword-only keeps every prior position intact.
    """
    service = _create_mock_service()
    fn = _unwrap(manage_event)
    await fn(
        service,
        "user@example.com",
        "create",
        "Legacy positional",
        "2026-08-21T09:00:00",
        "2026-08-21T09:15:00",
        None,  # event_id
        "primary",  # calendar_id
        "desc",  # description
        "loc",  # location
        None,  # attendees
        "America/New_York",  # timezone
        ["https://drive.google.com/file/d/abc123/view"],  # attachments
    )
    body = _sent_body(service)
    assert body["start"]["timeZone"] == "America/New_York"
    assert body["end"]["timeZone"] == "America/New_York"
    assert body.get("attachments") is not None


def test_new_timezone_params_are_keyword_only():
    """Guard the fix itself: positional callers can never reach these."""
    import inspect

    from gcalendar.calendar_tools import _create_event_impl, _modify_event_impl

    for fn in (_unwrap(manage_event), _create_event_impl, _modify_event_impl):
        params = inspect.signature(fn).parameters
        for name in ("start_timezone", "end_timezone"):
            assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{fn.__name__}.{name} must stay keyword-only"
            )


# --- timezone validation -----------------------------------------------------


def test_unresolvable_timezone_raises_rather_than_silently_dropping():
    """Falling back to the offset would book the event in the wrong zone.

    Silently ignoring an unrecognized zone discards the caller's stated intent
    with no error, which is worse than the bug: the event lands somewhere the
    caller never asked for. Fail loudly with a name they can act on instead.
    """
    with pytest.raises(ValueError, match="Unrecognized IANA timezone"):
        _build_time_boundary("2026-08-21T17:50:00+03:00", "Mars/Olympus_Mons")


def test_validation_error_names_the_offending_zone():
    with pytest.raises(ValueError, match="Mars/Olympus_Mons"):
        _build_time_boundary("2026-08-21T17:50:00", "Mars/Olympus_Mons")


def test_all_day_boundary_skips_timezone_validation():
    """An all-day date carries no zone, so a bad name is irrelevant, not fatal."""
    assert _build_time_boundary("2026-08-21", "Mars/Olympus_Mons") == {
        "date": "2026-08-21"
    }


def test_valid_zones_still_pass_validation():
    for zone in ("Asia/Jerusalem", "Europe/Amsterdam", "America/New_York", "UTC"):
        assert _build_time_boundary("2026-08-21T09:00:00", zone)["timeZone"] == zone


def test_zoneinfo_uses_packaged_tzdata_without_system_database():
    """Supported IANA zones remain available when the OS supplies no tz database."""
    original_tzpath = zoneinfo.TZPATH
    zoneinfo.reset_tzpath(())
    try:
        assert str(zoneinfo.ZoneInfo.no_cache("America/New_York")) == (
            "America/New_York"
        )
    finally:
        zoneinfo.reset_tzpath(original_tzpath)


@pytest.mark.asyncio
async def test_create_rejects_unresolvable_boundary_timezone():
    service = _create_mock_service()
    with pytest.raises(ValueError, match="Unrecognized IANA timezone"):
        await _create_event_impl(
            service=service,
            user_google_email="user@example.com",
            summary="Bad zone",
            start_time="2026-08-21T09:00:00",
            end_time="2026-08-21T09:15:00",
            end_timezone="Mars/Olympus_Mons",
        )
    # The mock records a bare events().insert() during setup, so assert that no
    # call carrying a request body was made: nothing reached the API.
    assert not [c for c in service.events().insert.call_args_list if c.kwargs]


# --- empty vs omitted timezone -----------------------------------------------


def test_empty_timezone_is_rejected_not_treated_as_omitted():
    """`""` is an invalid value, not an absent one."""
    with pytest.raises(ValueError, match="Unrecognized IANA timezone"):
        _build_time_boundary("2026-08-21T09:00:00", "")


def test_omitted_timezone_still_returns_bare_datetime():
    assert _build_time_boundary("2026-08-21T09:00:00", None) == {
        "dateTime": "2026-08-21T09:00:00"
    }


@pytest.mark.asyncio
async def test_empty_boundary_timezone_does_not_fall_back_to_event_wide():
    """The silent-substitution bug this PR exists to fix, in miniature.

    `start_timezone or timezone` let an explicitly empty boundary zone collapse to
    the event-wide one, so the caller got a zone they never asked for and no error.
    """
    service = _create_mock_service()
    with pytest.raises(ValueError, match="Unrecognized IANA timezone"):
        await _create_event_impl(
            service=service,
            user_google_email="user@example.com",
            summary="Empty zone",
            start_time="2026-08-21T09:00:00",
            end_time="2026-08-21T09:15:00",
            timezone="America/New_York",
            start_timezone="",
        )
    assert not [c for c in service.events().insert.call_args_list if c.kwargs]


@pytest.mark.asyncio
async def test_empty_boundary_timezone_rejected_without_event_wide_timezone():
    service = _create_mock_service()
    with pytest.raises(ValueError, match="Unrecognized IANA timezone"):
        await _create_event_impl(
            service=service,
            user_google_email="user@example.com",
            summary="Empty zone",
            start_time="2026-08-21T09:00:00",
            end_time="2026-08-21T09:15:00",
            end_timezone="",
        )
    assert not [c for c in service.events().insert.call_args_list if c.kwargs]


@pytest.mark.asyncio
async def test_update_rejects_empty_boundary_timezone():
    service = _create_mock_service()
    with pytest.raises(ValueError, match="Unrecognized IANA timezone"):
        await _modify_event_impl(
            service=service,
            user_google_email="user@example.com",
            event_id="evt1",
            start_time="2026-08-21T09:00:00",
            timezone="America/New_York",
            start_timezone="",
        )
    assert not [c for c in service.events().patch.call_args_list if c.kwargs]
