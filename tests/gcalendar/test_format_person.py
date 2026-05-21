"""Unit tests for the _format_person helper used by get_events detailed output."""

from gcalendar.calendar_helpers import _format_person


def test_format_person_name_and_email():
    assert (
        _format_person({"displayName": "Ada Lovelace", "email": "ada@example.com"})
        == "Ada Lovelace <ada@example.com>"
    )


def test_format_person_name_only():
    assert _format_person({"displayName": "Ada Lovelace"}) == "Ada Lovelace"


def test_format_person_email_only():
    assert _format_person({"email": "ada@example.com"}) == "<ada@example.com>"


def test_format_person_empty_dict_returns_none():
    # Caller relies on None to skip emitting the line entirely.
    assert _format_person({}) is None


def test_format_person_none_input_returns_none():
    assert _format_person(None) is None


def test_format_person_strips_whitespace():
    assert (
        _format_person({"displayName": "  Ada  ", "email": "  ada@example.com  "})
        == "Ada <ada@example.com>"
    )


def test_format_person_empty_strings_returns_none():
    # Whitespace-only fields should be treated the same as missing.
    assert _format_person({"displayName": "  ", "email": ""}) is None
