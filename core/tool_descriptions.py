"""Tool descriptions taken from everything above a docstring's first standard section."""

import inspect
import re
from collections.abc import Callable
from itertools import takewhile
from typing import Any

_END_OF_DESCRIPTION = re.compile(
    r"^(Args|Arguments|Parameters|Params|Returns|Return|Yields|Raises"
    r"|Examples|Example):\s*$"
)


def docstring_description(fn: Callable[..., Any]) -> str | None:
    """Return the docstring up to its first Args/Returns/Examples section, or None."""
    lines = (inspect.getdoc(fn) or "").splitlines()
    kept = takewhile(lambda line: not _END_OF_DESCRIPTION.match(line), lines)
    return "\n".join(kept).strip() or None
