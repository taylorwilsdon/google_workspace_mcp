"""Warning filters for known third-party startup noise."""

from __future__ import annotations

import warnings


def install_startup_warning_filters() -> None:
    """Install narrow warning filters needed before importing server dependencies."""
    try:
        from authlib.deprecate import AuthlibDeprecationWarning
    except ImportError:  # pragma: no cover - only relevant if FastMCP drops Authlib
        return

    warnings.filterwarnings(
        "ignore",
        message=r"authlib\.jose module is deprecated.*",
        category=AuthlibDeprecationWarning,
    )
