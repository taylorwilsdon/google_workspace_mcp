"""
Unit tests for _correct_time_format_for_api defensive input normalization.

Covers the case where LLM-driven MCP clients double-encode JSON string args,
passing values like '"2026-05-15T00:00:00Z"' with literal quotes inside the
string, or the literal string "null"/"None" instead of omitting the param.
"""

import logging

import pytest

from gcalendar.calendar_helpers import _correct_time_format_for_api


@pytest.mark.parametrize("empty", [None, "", "   "])
def test_empty_returns_none(empty: str | None) -> None:
    assert _correct_time_format_for_api(empty, "time_min") is None


@pytest.mark.parametrize(
    "sentinel", ['"null"', '"NULL"', "'null'", "null", "None", '"none"', "  null  "]
)
def test_null_sentinel_returns_none(sentinel: str) -> None:
    # Some clients pass the literal string "null"/"None" instead of omitting the param.
    assert _correct_time_format_for_api(sentinel, "time_min") is None


def test_plain_date_only_normalizes_to_rfc3339_utc() -> None:
    assert (
        _correct_time_format_for_api("2026-05-15", "time_min") == "2026-05-15T00:00:00Z"
    )


def test_double_quoted_date_only_is_stripped() -> None:
    # The value the LLM emitted as `time_min: "2026-05-15"` ended up double-encoded
    # into a string containing the literal quote characters.
    assert (
        _correct_time_format_for_api('"2026-05-15"', "time_min")
        == "2026-05-15T00:00:00Z"
    )


def test_single_quoted_date_only_is_stripped() -> None:
    assert (
        _correct_time_format_for_api("'2026-05-15'", "time_min")
        == "2026-05-15T00:00:00Z"
    )


def test_double_quoted_rfc3339_is_stripped() -> None:
    # Real failure mode observed in the wild: timeMin arrived as the string
    # `"2026-05-15T00:00:00Z"` with quotes inside the value, producing
    # `timeMin=%222026-05-15T00:00:00Z%22` on the Calendar API request URL,
    # which returns HTTP 400 Bad Request.
    out = _correct_time_format_for_api('"2026-05-15T00:00:00Z"', "time_min")
    assert out == "2026-05-15T00:00:00Z"


def test_whitespace_and_quotes_combined_are_stripped() -> None:
    out = _correct_time_format_for_api('  "2026-05-15T10:30:00Z"  ', "time_min")
    assert out == "2026-05-15T10:30:00Z"


@pytest.mark.parametrize(
    ("time_str", "timezone", "expected"),
    [
        ("2026-11-10T00:00:00", "Asia/Kolkata", "2026-11-09T18:30:00Z"),
        ("2026-11-10", "Asia/Kolkata", "2026-11-09T18:30:00Z"),
        ("2026-07-01T09:00:00", "America/New_York", "2026-07-01T13:00:00Z"),
        ("2026-01-01T09:00:00", "America/New_York", "2026-01-01T14:00:00Z"),
        ("2026-11-10T00:00:00", None, "2026-11-10T00:00:00Z"),
        ("2026-11-10T00:00:00", "Not/AZone", "2026-11-10T00:00:00Z"),
        ("0001-01-01T00:00:00", "Asia/Kolkata", "0001-01-01T00:00:00Z"),
        ("9999-12-31", "America/Los_Angeles", "9999-12-31T00:00:00Z"),
    ],
)
def test_offset_free_values_use_timezone(
    time_str: str, timezone: str | None, expected: str
) -> None:
    assert _correct_time_format_for_api(time_str, "time_min", timezone) == expected


def test_unrepresentable_offset_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="gcalendar.calendar_helpers"):
        _correct_time_format_for_api("0001-01-01T00:00:00", "time_min", "Asia/Kolkata")
    assert "Asia/Kolkata" in caplog.text


def test_unquoted_rfc3339_passes_through() -> None:
    # Regression: well-formed values must not be mangled.
    out = _correct_time_format_for_api("2026-05-15T10:30:00Z", "time_min")
    assert out == "2026-05-15T10:30:00Z"
