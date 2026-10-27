"""
Tool descriptions taken from the whole guidance part of a tool's docstring.

FastMCP publishes only the docstring's first free-text section as a tool's
description. Its Google-style parser treats any ``Word:`` line followed by an
indented block as a new section, so guidance under headings such as
``Important:`` or ``Actions:`` never reaches the model, and neither does
anything after the first such heading.
"""

import inspect
import re
from collections.abc import Callable
from typing import Any

# Top-level docstring headings that end the published description. Args become
# the input schema's parameter descriptions; Returns, Raises and Examples are
# kept for code readers only, because examples restate the input schema in
# call syntax and every published token is paid on each request.
_END_OF_DESCRIPTION = re.compile(
    r"^(Args|Arguments|Parameters|Params|Returns|Return|Yields|Raises"
    r"|Examples|Example):\s*$"
)


def docstring_description(fn: Callable[..., Any]) -> str | None:
    """Return the text of a tool's docstring above its first standard section.

    The text is returned verbatim, including headings such as ``Important:``
    and their indented blocks. Returns None when the function has no
    docstring, so the caller can fall back to FastMCP's default.
    """
    doc = inspect.getdoc(fn)
    if not doc:
        return None
    kept = []
    for line in doc.splitlines():
        if _END_OF_DESCRIPTION.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip() or None
