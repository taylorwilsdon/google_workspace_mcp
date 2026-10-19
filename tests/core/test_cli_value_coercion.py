"""Tests for workspace-cli key=value argument coercion."""

import pytest

from core.cli import _coerce_cli_value


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("is:unread", "is:unread"),
        ("gmail drive calendar", "gmail drive calendar"),
        # Quotes must survive for Gmail phrase queries.
        ('"grow therapy"', '"grow therapy"'),
        ('"grow therapy" newer_than:15mo', '"grow therapy" newer_than:15mo'),
        ("2026/04/16", "2026/04/16"),
        ("", ""),
    ],
)
def test_strings_are_preserved_verbatim(raw, expected):
    assert _coerce_cli_value(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("3", 3),
        ("-2", -2),
        ("3.5", 3.5),
        ("true", True),
        ("false", False),
        ("null", None),
        ('["a", "b"]', ["a", "b"]),
        ('[["a", "b"]]', [["a", "b"]]),
        ('{"k": 1}', {"k": 1}),
    ],
)
def test_json_like_values_are_coerced(raw, expected):
    assert _coerce_cli_value(raw) == expected
