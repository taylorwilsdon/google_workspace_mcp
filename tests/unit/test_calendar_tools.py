"""
E2E tests for Google Calendar tools with mocked API responses.

Tests cover:
- _parse_reminders_json: JSON parsing, validation, truncation
- _apply_transparency_if_valid: valid/invalid transparency values
- Calendar utility functions
"""

import json
import pytest
from unittest.mock import MagicMock

from gcalendar.calendar_tools import (
    _parse_reminders_json,
    _apply_transparency_if_valid,
)


class TestParseRemindersJson:
    """Tests for _parse_reminders_json helper."""

    def test_valid_json_string(self):
        reminders = _parse_reminders_json(
            '[{"method": "popup", "minutes": 10}]',
            "test_func"
        )
        assert len(reminders) == 1
        assert reminders[0]["method"] == "popup"
        assert reminders[0]["minutes"] == 10

    def test_valid_list_input(self):
        reminders = _parse_reminders_json(
            [{"method": "email", "minutes": 30}],
            "test_func"
        )
        assert len(reminders) == 1
        assert reminders[0]["method"] == "email"

    def test_none_returns_empty(self):
        assert _parse_reminders_json(None, "test_func") == []

    def test_empty_string_returns_empty(self):
        # Empty JSON string should fail JSON parsing and return empty
        result = _parse_reminders_json("", "test_func")
        assert result == []

    def test_invalid_json_returns_empty(self):
        result = _parse_reminders_json("not valid json", "test_func")
        assert result == []

    def test_non_array_json_returns_empty(self):
        result = _parse_reminders_json('{"method": "popup"}', "test_func")
        assert result == []

    def test_more_than_5_reminders_truncated(self):
        reminders_input = [
            {"method": "popup", "minutes": i}
            for i in range(10)
        ]
        result = _parse_reminders_json(reminders_input, "test_func")
        assert len(result) == 5

    def test_invalid_method_skipped(self):
        result = _parse_reminders_json(
            [{"method": "sms", "minutes": 10}],
            "test_func"
        )
        assert len(result) == 0

    def test_negative_minutes_skipped(self):
        result = _parse_reminders_json(
            [{"method": "popup", "minutes": -5}],
            "test_func"
        )
        assert len(result) == 0

    def test_minutes_over_max_skipped(self):
        result = _parse_reminders_json(
            [{"method": "popup", "minutes": 50000}],
            "test_func"
        )
        assert len(result) == 0

    def test_missing_method_skipped(self):
        result = _parse_reminders_json(
            [{"minutes": 10}],
            "test_func"
        )
        assert len(result) == 0

    def test_missing_minutes_skipped(self):
        result = _parse_reminders_json(
            [{"method": "popup"}],
            "test_func"
        )
        assert len(result) == 0

    def test_mixed_valid_and_invalid(self):
        result = _parse_reminders_json(
            [
                {"method": "popup", "minutes": 10},  # valid
                {"method": "sms", "minutes": 5},      # invalid method
                {"method": "email", "minutes": 30},   # valid
            ],
            "test_func"
        )
        assert len(result) == 2

    def test_non_dict_type_returns_empty(self):
        result = _parse_reminders_json(12345, "test_func")
        assert result == []

    def test_popup_and_email_both_valid(self):
        result = _parse_reminders_json(
            [
                {"method": "popup", "minutes": 10},
                {"method": "email", "minutes": 30},
            ],
            "test_func"
        )
        assert len(result) == 2
        methods = {r["method"] for r in result}
        assert methods == {"popup", "email"}

    def test_zero_minutes_valid(self):
        result = _parse_reminders_json(
            [{"method": "popup", "minutes": 0}],
            "test_func"
        )
        assert len(result) == 1

    def test_max_minutes_valid(self):
        result = _parse_reminders_json(
            [{"method": "popup", "minutes": 40320}],
            "test_func"
        )
        assert len(result) == 1

    def test_case_insensitive_method(self):
        result = _parse_reminders_json(
            [{"method": "POPUP", "minutes": 10}],
            "test_func"
        )
        # Method is lower-cased in the function
        assert len(result) == 1
        assert result[0]["method"] == "popup"


class TestApplyTransparency:
    """Tests for _apply_transparency_if_valid helper."""

    def test_opaque_applied(self):
        event = {}
        _apply_transparency_if_valid(event, "opaque", "test")
        assert event["transparency"] == "opaque"

    def test_transparent_applied(self):
        event = {}
        _apply_transparency_if_valid(event, "transparent", "test")
        assert event["transparency"] == "transparent"

    def test_none_not_applied(self):
        event = {}
        _apply_transparency_if_valid(event, None, "test")
        assert "transparency" not in event

    def test_invalid_value_not_applied(self):
        event = {}
        _apply_transparency_if_valid(event, "invisible", "test")
        assert "transparency" not in event

    def test_empty_string_not_applied(self):
        event = {}
        _apply_transparency_if_valid(event, "", "test")
        assert "transparency" not in event
