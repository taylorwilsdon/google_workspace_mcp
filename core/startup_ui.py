"""
Terminal presentation for the Google Workspace MCP startup sequence.

Centralises the ANSI palette, section rules and column alignment used by
``main.py`` so the startup screen reads as one coherent block instead of a
stream of ad-hoc prints. Every method renders through a caller-supplied
``emit`` callable, which keeps the transport-safety rules (stdout must stay
clean for JSON-RPC) in one place in ``main.py``.
"""

import os
import shutil
import sys
import textwrap
from typing import Callable, Iterable, Sequence

# Rule width bounds. Narrow enough to survive a split pane, wide enough that
# credential paths do not wrap.
MIN_WIDTH = 60
MAX_WIDTH = 76

# Hues, used only where the color itself carries meaning.
BLUE = "\033[1;34m"
RED = "\033[1;31m"
YELLOW = "\033[1;33m"
GREEN = "\033[1;32m"
CYAN = "\033[0;36m"

# Emphasis is expressed with attributes rather than hardcoded neutrals: SGR 1
# and SGR 2 compose with whatever foreground the terminal already uses, so the
# screen stays legible on black, white and tinted backgrounds alike. A fixed
# grey such as SGR 90 would assume a near-black background. Terminals that
# ignore SGR 2 simply render the text at full strength, which is still legible.
BOLD = "\033[1m"
FAINT = "\033[2m"
RESET = "\033[0m"

# state -> (glyph, glyph color, value color)
_FIELD_STATES = {
    "on": ("●", GREEN, ""),
    "off": ("○", FAINT, FAINT),
    "warn": ("●", YELLOW, YELLOW),
}

# state -> (glyph, color)
_STEP_STATES = {
    "ok": ("✓", GREEN),
    "skip": ("·", FAINT),
    "warn": ("!", YELLOW),
    "fail": ("✗", RED),
}

# (plain, colored) pairs. The plain form drives column alignment; the block
# characters below are all single-width so ``len`` is an accurate measure.
_LOGO_ROWS = (
    ("██████╗", (BLUE, "██████", RED, "╗")),
    ("██╔════╝", (BLUE, "██", "", "╔════╝")),
    ("██║  ███╗", (BLUE, "██", "", "║  ", YELLOW, "███", "", "╗")),
    ("██║   ██║", (BLUE, "██", "", "║   ", YELLOW, "██", "", "║")),
    ("╚██████╔╝", (BLUE, "╚█████", GREEN, "█╔╝")),
    (" ╚═════╝", ("", " ", BLUE, "╚════", GREEN, "═╝")),
)
_LOGO_INDENT = 5
_LOGO_GUTTER = 5
_LOGO_WIDTH = max(len(plain) for plain, _ in _LOGO_ROWS)

# Floor for the value column so a very wide label cannot squeeze it to nothing.
_MIN_VALUE = 24


def supports_color() -> bool:
    """Report whether stderr can render ANSI color."""
    return (
        sys.stderr.isatty()
        and os.getenv("NO_COLOR") is None
        and os.getenv("TERM") != "dumb"
    )


def elide(value: str, limit: int) -> str:
    """Shorten ``value`` from the middle so both ends stay recognisable."""
    if len(value) <= limit:
        return value
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return f"{value[:head]}…{value[-tail:]}"


def collapse_home(path: str) -> str:
    """Render ``path`` with the user's home directory as ``~``."""
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


class StartupDisplay:
    """Renders the startup screen through a caller-supplied emit function."""

    def __init__(
        self,
        emit: Callable[[str], None],
        *,
        color: bool | None = None,
        width: int | None = None,
    ):
        self.emit = emit
        self.color = supports_color() if color is None else color
        self.width = width or self._terminal_width()

    @staticmethod
    def _terminal_width() -> int:
        columns = shutil.get_terminal_size((80, 24)).columns
        return max(MIN_WIDTH, min(MAX_WIDTH, columns - 2))

    def paint(self, code: str, text: str) -> str:
        """Wrap ``text`` in an ANSI ``code`` when color is enabled."""
        if not self.color or not code:
            return text
        return f"{code}{text}{RESET}"

    def blank(self) -> None:
        self.emit("")

    def rule(self, title: str | None = None) -> None:
        """Draw a full-width rule, optionally labelled with a section title."""
        if not title:
            self.emit(self.paint(FAINT, "━" * self.width))
            return
        trailing = max(3, self.width - len(title) - 4)
        self.emit(
            f"{self.paint(FAINT, '━━')} {self.paint(BOLD, title)} "
            f"{self.paint(FAINT, '━' * trailing)}"
        )

    def banner(self, info: Sequence[str | None]) -> None:
        """Print the wordmark with ``info`` aligned in a fixed right column."""
        for index, (plain, segments) in enumerate(_LOGO_ROWS):
            logo = "".join(
                self.paint(segments[i], segments[i + 1])
                for i in range(0, len(segments), 2)
            )
            line = f"{' ' * _LOGO_INDENT}{logo}"
            detail = info[index] if index < len(info) else None
            if detail:
                pad = _LOGO_WIDTH - len(plain) + _LOGO_GUTTER
                line += f"{' ' * pad}{detail}"
            self.emit(line.rstrip())

    def heading(self, text: str) -> None:
        """Dim sub-heading that groups related rows inside a section."""
        self.emit(f"  {self.paint(BOLD, text)}")

    def fields(
        self, groups: Sequence[tuple[str | None, Sequence[tuple[str, str, str]]]]
    ) -> None:
        """Render labelled groups of ``(label, value, state)`` rows in one column."""
        labels = [label for _, rows in groups for label, _, _ in rows]
        if not labels:
            return
        pad = max(len(label) for label in labels)
        # Rows are indented 4, then glyph + space, then the label column and a
        # two-space gutter. Whatever is left of the rule belongs to the value.
        limit = max(_MIN_VALUE, self.width - pad - 8)
        for index, (title, rows) in enumerate(groups):
            if index:
                self.blank()
            if title:
                self.heading(title)
            for label, value, state in rows:
                glyph, glyph_color, value_color = _FIELD_STATES[state]
                self.emit(
                    f"    {self.paint(glyph_color, glyph)} "
                    f"{label.ljust(pad)}  "
                    f"{self.paint(value_color, elide(value, limit))}"
                )

    def grid(self, cells: Sequence[tuple[str, str]], columns: int = 4) -> None:
        """Lay ``(icon, label)`` cells out in aligned columns."""
        if not cells:
            return
        pad = max(len(label) for _, label in cells)
        for start in range(0, len(cells), columns):
            row = cells[start : start + columns]
            rendered = [
                f"{icon} {self.paint(BOLD, label)}{' ' * (pad - len(label))}"
                for icon, label in row
            ]
            self.emit(f"  {'  '.join(rendered).rstrip()}")

    def step(self, text: str, detail: str | None = None, state: str = "ok") -> None:
        """Report a completed startup step, optionally with a trailing detail."""
        glyph, color = _STEP_STATES[state]
        line = f"  {self.paint(color, glyph)} {text}"
        if detail:
            line += f"  {self.paint(CYAN, detail)}"
        self.emit(line)

    def detail(self, text: str) -> None:
        """Continuation line beneath a step."""
        self.emit(f"    {self.paint(FAINT, text)}")

    def notice(self, text: str, state: str = "warn") -> None:
        """Wrap and print an advisory paragraph."""
        glyph, color = _STEP_STATES[state]
        body = textwrap.wrap(" ".join(text.split()), width=self.width - 4) or [""]
        self.emit(f"  {self.paint(color, glyph)} {body[0]}")
        for line in body[1:]:
            self.emit(f"    {line}")

    def section(self, title: str) -> None:
        """Open a titled section with the surrounding whitespace it needs."""
        self.blank()
        self.rule(title)
        self.blank()


def wordmark_lines(
    display: StartupDisplay,
    *,
    version: str,
    transport: str,
    mode: str,
    python_version: str,
    url: str | None,
    flags: Iterable[str] = (),
) -> list[str | None]:
    """Build the six-line info column that sits beside the wordmark."""
    descriptor = " · ".join([transport, mode, *flags])
    return [
        display.paint(BOLD, "Google Workspace"),
        f"{display.paint(BOLD, 'MCP Server')}  {display.paint(CYAN, 'v' + version)}",
        None,
        display.paint(CYAN, descriptor),
        display.paint(CYAN, f"Python {python_version}"),
        display.paint(CYAN, url) if url else None,
    ]
