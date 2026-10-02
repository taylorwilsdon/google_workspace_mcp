"""
Resolution of semantic anchors to Google Docs indices.

Docs write requests address raw indices, and every write shifts the indices that
follow it. Resolving a human-meaningful target ("after the Results heading") at
execution time rather than at generation time removes the stale-index class of
error, where an edit lands on unrelated content without failing.
"""

import logging
from typing import Any, Optional

from gdocs.docs_helpers import _paragraph_element_text, utf16_length

logger = logging.getLogger(__name__)

# Named styles that count as a heading for after_heading / before_heading.
HEADING_STYLES = ("TITLE", "SUBTITLE")


class AnchorResolutionError(Exception):
    """Raised when an anchor matches zero or more than one location."""


def _is_heading(style: dict[str, Any]) -> bool:
    named_style = style.get("namedStyleType", "")
    return named_style.startswith("HEADING_") or named_style in HEADING_STYLES


def _aligned_paragraph_text(paragraph: dict[str, Any]) -> str:
    """Paragraph text whose character offsets match the document's indices."""
    return "".join(
        _paragraph_element_text(element) for element in paragraph.get("elements", [])
    )


def iter_paragraphs(body: dict[str, Any]):
    """Yield (start_index, end_index, text, paragraph_style) for each paragraph."""
    for element in body.get("content", []):
        paragraph = element.get("paragraph")
        if paragraph is None:
            continue
        yield (
            element.get("startIndex", 0),
            element.get("endIndex", 0),
            _aligned_paragraph_text(paragraph),
            paragraph.get("paragraphStyle", {}),
        )


def _only_match(matches: list[int], anchor_kind: str, needle: str) -> int:
    if not matches:
        raise AnchorResolutionError(
            f"{anchor_kind} '{needle}' was not found in the document."
        )
    if len(matches) > 1:
        raise AnchorResolutionError(
            f"{anchor_kind} '{needle}' matches {len(matches)} locations "
            f"(at indices {', '.join(str(m) for m in matches)}). "
            "Use a longer, unique anchor or target an index directly."
        )
    return matches[0]


def resolve_heading(body: dict[str, Any], heading: str, position: str) -> int:
    """
    Resolve a heading's text to an insertion index.

    position "after" returns the index just past the heading paragraph, which is
    the start of its following paragraph. position "before" returns the heading's
    own start index.
    """
    wanted = heading.strip().casefold()
    matches = []
    for start_index, end_index, text, style in iter_paragraphs(body):
        if not _is_heading(style):
            continue
        if text.strip().casefold() == wanted:
            matches.append(end_index if position == "after" else start_index)
    kind = "after_heading" if position == "after" else "before_heading"
    return _only_match(matches, kind, heading)


def resolve_anchor_text(body: dict[str, Any], anchor: str, position: str) -> int:
    """
    Resolve literal text to an index at its start ("before") or end ("after").

    The search runs per paragraph, so an anchor spanning a paragraph break will
    not match; that is deliberate, since such an anchor is ambiguous to place.
    """
    matches = []
    for start_index, _end_index, text, _style in iter_paragraphs(body):
        offset = text.find(anchor)
        while offset != -1:
            matches.append(
                start_index
                + utf16_length(text[:offset])
                + (utf16_length(anchor) if position == "after" else 0)
            )
            offset = text.find(anchor, offset + 1)
    return _only_match(matches, "anchor_text", anchor)


def resolve_operation_anchor(
    body: dict[str, Any],
    after_heading: Optional[str] = None,
    before_heading: Optional[str] = None,
    anchor_text: Optional[str] = None,
    anchor_position: str = "after",
) -> int:
    """Resolve whichever anchor an operation carries into a document index."""
    if after_heading is not None:
        return resolve_heading(body, after_heading, "after")
    if before_heading is not None:
        return resolve_heading(body, before_heading, "before")
    if anchor_text is not None:
        if anchor_position not in ("before", "after"):
            raise AnchorResolutionError(
                f"anchor_position must be 'before' or 'after', got '{anchor_position}'."
            )
        return resolve_anchor_text(body, anchor_text, anchor_position)
    raise AnchorResolutionError("No anchor provided.")
