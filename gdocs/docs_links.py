"""Shared Google Docs link-target resolution.

The Docs API represents external and internal links with different fields.  Keep
that API interpretation separate from output-format-specific rendering so the
plain-text and Markdown readers agree about which target a link refers to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


LinkTargetKind = Literal["url", "heading", "bookmark", "tab", "unknown"]


@dataclass(frozen=True)
class LinkTarget:
    """A normalized link target returned by the Google Docs API."""

    kind: LinkTargetKind
    value: str | None = None
    tab_id: str | None = None


def resolve_link_target(link: dict[str, Any] | None) -> LinkTarget | None:
    """Resolve a Docs API link in URL, heading, bookmark, tab order.

    ``headingId`` and ``bookmarkId`` are legacy forms of the newer nested
    ``heading`` and ``bookmark`` targets.  Empty link styles are not links;
    non-empty shapes without a recognized target are retained as ``unknown``.
    """
    if not link:
        return None

    url = link.get("url")
    if url:
        return LinkTarget("url", str(url))

    heading = link.get("heading")
    heading_id, heading_tab_id = _internal_target_values(heading)
    if not heading_id:
        heading_id = _as_string(link.get("headingId"))
        heading_tab_id = heading_tab_id or _as_string(link.get("tabId"))
    if heading_id:
        return LinkTarget("heading", heading_id, heading_tab_id)

    bookmark = link.get("bookmark")
    bookmark_id, bookmark_tab_id = _internal_target_values(bookmark)
    if not bookmark_id:
        bookmark_id = _as_string(link.get("bookmarkId"))
        bookmark_tab_id = bookmark_tab_id or _as_string(link.get("tabId"))
    if bookmark_id:
        return LinkTarget("bookmark", bookmark_id, bookmark_tab_id)

    tab_id = _as_string(link.get("tabId"))
    if tab_id:
        return LinkTarget("tab", tab_id)

    return LinkTarget("unknown")


def text_run_link(elements: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    """Return the link of the paragraph element at ``index`` if it is a text run.

    Style changes split one link into several runs, so renderers compare
    neighboring runs to annotate a link once rather than once per run.
    """
    if 0 <= index < len(elements):
        return elements[index].get("textRun", {}).get("textStyle", {}).get("link")
    return None


def _internal_target_values(target: Any) -> tuple[str | None, str | None]:
    """Extract a target ID and optional tab from scalar or nested API fields."""
    if isinstance(target, dict):
        target_id = _as_string(target.get("id"))
        tab_id = _as_string(target.get("tabId"))
        return target_id, tab_id
    return _as_string(target), None


def _as_string(value: Any) -> str | None:
    """Normalize a populated API identifier while treating empty values as absent."""
    if value is None or value == "":
        return None
    return str(value)
