"""Tests for the startup screen renderer."""

import pytest

from core.startup_ui import (
    StartupDisplay,
    collapse_home,
    elide,
    wordmark_lines,
)


@pytest.fixture
def display():
    """A colorless display with a fixed width, capturing every emitted line."""
    lines: list[str] = []
    ui = StartupDisplay(lines.append, color=False, width=64)
    ui.lines = lines
    return ui


def test_elide_keeps_both_ends():
    assert elide("abcdefghij", 20) == "abcdefghij"
    elided = elide("abcdefghij", 5)
    assert len(elided) == 5
    assert elided.startswith("ab") and elided.endswith("ij")


def test_collapse_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert collapse_home(str(tmp_path)) == "~"
    assert collapse_home(str(tmp_path / "creds")) == "~/creds"
    assert collapse_home("/etc/creds") == "/etc/creds"


def test_rules_span_the_full_width(display):
    display.rule()
    display.rule("Configuration")
    assert all(len(line) == display.width for line in display.lines)


def test_banner_aligns_the_info_column(display):
    display.banner(["one", "two", None, "four", "five", "six"])
    offsets = {
        line.index(text)
        for line, text in zip(
            display.lines, ["one", "two", None, "four", "five", "six"]
        )
        if text
    }
    assert len(offsets) == 1


def test_banner_tolerates_a_short_info_column(display):
    display.banner(["only"])
    assert len(display.lines) == 6
    assert display.lines[0].endswith("only")


def test_fields_align_values_and_mark_state(display):
    display.fields(
        [
            (
                "Credentials",
                [("SHORT", "yes", "on"), ("MUCH_LONGER_NAME", "no", "off")],
            ),
            ("Modes", [("MID_NAME", "true", "warn")]),
        ]
    )
    rows = [line for line in display.lines if line.startswith("    ")]
    assert rows[0].index("yes") == rows[1].index("no") == rows[2].index("true")
    assert rows[0].strip().startswith("●")
    assert rows[1].strip().startswith("○")


def test_fields_elide_values_that_would_overflow(display):
    display.fields([(None, [("KEY", "x" * 200, "on")])])
    assert len(display.lines[0]) <= display.width


def test_grid_wraps_into_rows_of_the_requested_width(display):
    display.grid([("*", f"svc{i}") for i in range(7)], columns=3)
    assert len(display.lines) == 3
    assert display.lines[0].count("*") == 3
    assert display.lines[2].count("*") == 1


def test_grid_columns_line_up(display):
    display.grid([("*", "a"), ("*", "bbbb"), ("*", "cc"), ("*", "d")], columns=2)
    assert display.lines[0].index("bbbb") == display.lines[1].index("d")


def test_notice_wraps_and_indents_continuations(display):
    display.notice("word " * 40)
    assert display.lines[0].startswith("  ! ")
    assert all(line.startswith("    ") for line in display.lines[1:])
    assert all(len(line) <= display.width for line in display.lines)


def test_step_detail_is_optional(display):
    display.step("Ready")
    display.step("HTTP server", "http://localhost:8000")
    assert display.lines[0] == "  ✓ Ready"
    assert "http://localhost:8000" in display.lines[1]


def test_color_disabled_emits_no_escape_codes(display):
    display.rule("Configuration")
    display.fields([("Modes", [("KEY", "value", "warn")])])
    display.step("Ready")
    display.notice("careful")
    assert all("\033" not in line for line in display.lines)


def test_color_enabled_wraps_painted_text():
    lines: list[str] = []
    ui = StartupDisplay(lines.append, color=True, width=64)
    ui.step("Ready")
    assert lines[0].startswith("  \033[1;32m✓\033[0m")


def test_wordmark_lines_are_six_rows_with_a_gap(display):
    rows = wordmark_lines(
        display,
        version="1.2.3",
        transport="stdio",
        mode="multi-user",
        python_version="3.11.13",
        url=None,
        flags=["read-only"],
    )
    assert len(rows) == 6
    assert rows[2] is None
    assert rows[5] is None
    assert rows[3] == "stdio · multi-user · read-only"
