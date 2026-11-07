"""
Unit tests for Google Sheets helper utilities.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import pytest

from core.utils import UserInputError
from gsheets.sheets_helpers import (
    _clamp_a1_read_rows,
    _column_to_index,
    _parse_a1_part,
    _quote_sheet_title_for_a1,
)


def test_column_to_index_valid_letters():
    assert _column_to_index("A") == 0
    assert _column_to_index("B") == 1
    assert _column_to_index("Z") == 25
    assert _column_to_index("AA") == 26
    assert _column_to_index("AL") == 37


def test_column_to_index_rejects_empty_and_non_letters():
    assert _column_to_index("") is None
    assert _column_to_index("A1") is None
    assert _column_to_index("B2") is None
    assert _column_to_index("A:B") is None
    assert _column_to_index("Sheet1") is None
    assert _column_to_index("5") is None
    assert _column_to_index("A$B") is None


def test_column_to_index_rejects_trailing_whitespace():
    """Regex anchors alone allow a trailing newline; fullmatch must reject it."""
    assert _column_to_index("A\n") is None
    assert _column_to_index("AA\n") is None
    assert _column_to_index("A ") is None
    assert _column_to_index("\nA") is None


def test_parse_a1_part_rejects_trailing_newline():
    assert _parse_a1_part("B2") == (1, 1)
    with pytest.raises(UserInputError, match="Invalid A1 range part"):
        _parse_a1_part("B2\n")


def test_quote_sheet_title_quotes_titles_with_trailing_newline():
    assert _quote_sheet_title_for_a1("Sheet1") == "Sheet1"
    assert _quote_sheet_title_for_a1("Sheet1\n") == "'Sheet1\n'"


@pytest.mark.parametrize(
    ("range_name", "expected"),
    [
        ("A1:B10", "A1:B10"),
        ("A1:Z1000", "A1:Z1000"),
        ("Sheet1!A500:A600", "Sheet1!A500:A600"),
    ],
)
def test_clamp_a1_read_rows_leaves_in_budget_ranges_unchanged(range_name, expected):
    clamped, note = _clamp_a1_read_rows(range_name)
    assert clamped == expected
    assert note is None


@pytest.mark.parametrize(
    ("range_name", "expected"),
    [
        ("A:Z", "A1:Z1000"),
        ("A1:Z", "A1:Z1000"),
        ("Sheet1!A1:Z5000", "Sheet1!A1:Z1000"),
        ("'My Sheet'!A100:Z", "'My Sheet'!A100:Z1099"),
        ("'My Sheet'", "'My Sheet'!1:1000"),
        ("'O''Brien'", "'O''Brien'!1:1000"),
        ("1:5000", "1:1000"),
        ("AA10:ZZ2000", "AA10:ZZ1009"),
    ],
)
def test_clamp_a1_read_rows_closes_open_or_oversized_ranges(range_name, expected):
    clamped, note = _clamp_a1_read_rows(range_name)
    assert clamped == expected
    assert note is not None
    assert range_name in note
    assert expected in note
    assert "max 1000 rows" in note


def test_clamp_a1_read_rows_respects_custom_max():
    clamped, note = _clamp_a1_read_rows("Sheet1!A1:Z500", max_rows=50)
    assert clamped == "Sheet1!A1:Z50"
    assert note is not None
    assert "max 50 rows" in note


def test_clamp_a1_read_rows_passes_through_named_ranges():
    # A bare sheet title is indistinguishable from a named range without
    # spreadsheet metadata, so it must also be preserved.
    for range_name in ("MyNamedRange", "Sheet1", "Sheet1!SalesData"):
        clamped, note = _clamp_a1_read_rows(range_name)
        assert clamped == range_name
        assert note is None
