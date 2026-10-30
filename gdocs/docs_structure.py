"""
Google Docs Document Structure Parsing and Analysis

This module provides utilities for parsing and analyzing the structure
of Google Docs documents, including finding tables, cells, and other elements.
"""

import logging
from collections.abc import Iterator
from typing import Any, Optional

from gdocs.docs_helpers import _paragraph_element_text, utf16_length

from core.utils import UserInputError

logger = logging.getLogger(__name__)

# Limit returned ranges; the count remains exact.
EMPTY_PARAGRAPH_RANGE_LIMIT = 100

# Limit returned text matches; the count remains exact.
TEXT_MATCH_LIMIT = 50

# Characters of surrounding text shown with each match.
TEXT_MATCH_CONTEXT_CHARS = 40

# Pending suggestions (the default view for an editor) keep old and new text side
# by side; context marks them so a caller knows what is only proposed.
SUGGESTION_MARKERS = {
    "insertion": ("{++", "++}"),
    "deletion": ("{--", "--}"),
}


def truncate_preview(text: str, preview_chars: Optional[int]) -> str:
    """Truncate preview text; None or a non-positive limit disables truncation."""
    return (
        text[:preview_chars]
        if preview_chars is not None and preview_chars > 0
        else text
    )


def parse_document_structure(
    doc_data: dict[str, Any], preview_chars: Optional[int] = 100
) -> dict[str, Any]:
    """
    Parse the full document structure into a navigable format.

    Args:
        doc_data: Raw document data from Google Docs API
        preview_chars: Maximum characters of header/footer text preview.
            None, zero or negative means no truncation.

    Returns:
        Dictionary containing parsed structure with elements and their positions
    """
    structure = {
        "title": doc_data.get("title", ""),
        "body": [],
        "tables": [],
        "section_breaks": [],
        "headers": {},
        "footers": {},
        "named_ranges": {},
        "total_length": 0,
    }

    body = doc_data.get("body", {})
    content = body.get("content", [])

    for element in content:
        element_info = _parse_element(element)
        if element_info:
            structure["body"].append(element_info)
            if element_info["type"] == "table":
                structure["tables"].append(element_info)
            elif element_info["type"] == "section_break":
                structure["section_breaks"].append(element_info)

    # Calculate total document length
    if structure["body"]:
        last_element = structure["body"][-1]
        structure["total_length"] = last_element.get("end_index", 0)

    # Parse headers and footers
    for header_id, header_data in doc_data.get("headers", {}).items():
        structure["headers"][header_id] = _parse_segment(header_data, preview_chars)

    for footer_id, footer_data in doc_data.get("footers", {}).items():
        structure["footers"][footer_id] = _parse_segment(footer_data, preview_chars)

    for range_name, named_ranges in doc_data.get("namedRanges", {}).items():
        ranges = []
        for named_range in named_ranges.get("namedRanges", []):
            for named_range_range in named_range.get("ranges", []):
                ranges.append(
                    {
                        "named_range_id": named_range.get("namedRangeId"),
                        "start_index": named_range_range.get("startIndex"),
                        "end_index": named_range_range.get("endIndex"),
                        "segment_id": named_range_range.get("segmentId"),
                        "tab_id": named_range_range.get("tabId"),
                    }
                )
        structure["named_ranges"][range_name] = ranges

    return structure


def _parse_element(element: dict[str, Any]) -> Optional[dict[str, Any]]:
    """
    Parse a single document element.

    Args:
        element: Element data from document

    Returns:
        Parsed element information or None
    """
    element_info = {
        "start_index": element.get("startIndex", 0),
        "end_index": element.get("endIndex", 0),
    }

    if "paragraph" in element:
        paragraph = element["paragraph"]
        element_info["type"] = "paragraph"
        element_info["text"] = _extract_paragraph_text(paragraph)
        element_info["style"] = paragraph.get("paragraphStyle", {})
        element_info["is_list_item"] = "bullet" in paragraph
        for api_key, key in (
            ("positionedObjectIds", "positioned_object_ids"),
            ("suggestedPositionedObjectIds", "suggested_positioned_object_ids"),
        ):
            if paragraph.get(api_key):
                element_info[key] = paragraph[api_key]

    elif "table" in element:
        table = element["table"]
        element_info["type"] = "table"
        element_info["rows"] = len(table.get("tableRows", []))
        element_info["columns"] = len(
            table.get("tableRows", [{}])[0].get("tableCells", [])
        )
        element_info["cells"] = _parse_table_cells(table)
        element_info["table_style"] = table.get("tableStyle", {})

    elif "sectionBreak" in element:
        element_info["type"] = "section_break"
        element_info["section_style"] = element["sectionBreak"].get("sectionStyle", {})

    elif "tableOfContents" in element:
        element_info["type"] = "table_of_contents"

    else:
        return None

    return element_info


def _parse_table_cells(table: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """
    Parse table cells with their positions and content.

    Args:
        table: Table element data

    Returns:
        2D list of cell information
    """
    cells = []
    for row_idx, row in enumerate(table.get("tableRows", [])):
        row_cells = []
        for col_idx, cell in enumerate(row.get("tableCells", [])):
            # Find the first paragraph in the cell for insertion
            insertion_index = cell.get("startIndex", 0) + 1  # Default fallback

            # Look for the first paragraph in cell content
            content_elements = cell.get("content", [])
            for element in content_elements:
                if "paragraph" in element:
                    paragraph = element["paragraph"]
                    # Get the first element in the paragraph
                    para_elements = paragraph.get("elements", [])
                    if para_elements:
                        first_element = para_elements[0]
                        if "startIndex" in first_element:
                            insertion_index = first_element["startIndex"]
                            break

            cell_info = {
                "row": row_idx,
                "column": col_idx,
                "start_index": cell.get("startIndex", 0),
                "end_index": cell.get("endIndex", 0),
                "insertion_index": insertion_index,  # Where to insert text in this cell
                "content": _extract_cell_text(cell),
                "content_elements": content_elements,
            }
            row_cells.append(cell_info)
        cells.append(row_cells)
    return cells


def _extract_paragraph_text(paragraph: dict[str, Any]) -> str:
    """Extract text from a paragraph element."""
    text_parts = []
    for element in paragraph.get("elements", []):
        if "textRun" in element:
            text_parts.append(element["textRun"].get("content", ""))
    return "".join(text_parts)


def _extract_cell_text(cell: dict[str, Any]) -> str:
    """Extract text content from a table cell."""
    text_parts = []
    for element in cell.get("content", []):
        if "paragraph" in element:
            text_parts.append(_extract_paragraph_text(element["paragraph"]))
    return "".join(text_parts)


def _parse_segment(
    segment_data: dict[str, Any], preview_chars: Optional[int] = 100
) -> dict[str, Any]:
    """Parse a document segment (header/footer)."""
    content = segment_data.get("content", [])
    text_parts = []
    for element in content:
        if "paragraph" in element:
            text_parts.append(_extract_paragraph_text(element["paragraph"]))

    return {
        "content": content,
        "start_index": content[0].get("startIndex", 0) if content else 0,
        "end_index": content[-1].get("endIndex", 0) if content else 0,
        "text_preview": truncate_preview("".join(text_parts), preview_chars),
        "element_count": len(content),
    }


def _iter_body_paragraphs(
    body: dict[str, Any], in_table: bool = False
) -> Iterator[tuple[dict[str, Any], bool]]:
    """Yield body paragraphs, including table cells and tables of contents."""
    for element in body.get("content", []):
        if "paragraph" in element:
            yield element, in_table
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    yield from _iter_body_paragraphs(cell, in_table=True)
        elif "tableOfContents" in element:
            yield from _iter_body_paragraphs(element["tableOfContents"], in_table)


def validate_find_text(find_text: Optional[str]) -> Optional[str]:
    """Return an error message when find_text is given but blank, else None."""
    if find_text is not None and not find_text.strip():
        return "Error: find_text must not be empty."
    return None


def _paragraph_segments(paragraph: dict[str, Any]) -> list[tuple[str, Optional[str]]]:
    """Index-aligned text of a paragraph as (text, suggestion kind) segments.

    The kind is "insertion" or "deletion" for a text run that belongs to a
    pending suggestion, "mixed" for a run that is both (inserted by one
    suggestion and deleted by another), else None. Inline objects contribute
    placeholder characters with no kind.
    """
    segments: list[tuple[str, Optional[str]]] = []
    for part in paragraph.get("elements", []):
        text_run = part.get("textRun")
        kind = None
        if text_run:
            inserted = bool(text_run.get("suggestedInsertionIds"))
            deleted = bool(text_run.get("suggestedDeletionIds"))
            if inserted and deleted:
                kind = "mixed"
            elif inserted:
                kind = "insertion"
            elif deleted:
                kind = "deletion"
        segments.append((_paragraph_element_text(part), kind))
    return segments


def _suggestion_state(
    segments: list[tuple[str, Optional[str]]], start: int, end: int
) -> Optional[str]:
    """How the characters [start, end) relate to pending suggestions.

    None when none of them is suggested text; "insertion" or "deletion" when all
    of them are; "mixed" when the span crosses suggested and settled text.
    """
    kinds: set[Optional[str]] = set()
    offset = 0
    for text, kind in segments:
        seg_end = offset + len(text)
        if seg_end > start and offset < end and text:
            kinds.add(kind)
        offset = seg_end
        if offset >= end:
            break
    if not kinds or kinds == {None}:
        return None
    if len(kinds) == 1:
        return kinds.pop()
    return "mixed"


def _render_context(
    segments: list[tuple[str, Optional[str]]], start: int, end: int
) -> str:
    """Text of [start, end) with suggested spans wrapped in their markers."""
    parts: list[str] = []
    offset = 0
    for text, kind in segments:
        seg_end = offset + len(text)
        if seg_end > start and offset < end:
            piece = text[max(start - offset, 0) : min(end - offset, len(text))]
            if piece and kind is not None:
                piece = piece.rstrip(chr(10))
                kinds = ("insertion", "deletion") if kind == "mixed" else (kind,)
                for marked in kinds:
                    opening, closing = SUGGESTION_MARKERS[marked]
                    piece = f"{opening}{piece}{closing}"
            parts.append(piece)
        offset = seg_end
        if offset >= end:
            break
    return "".join(parts).rstrip("\n")


def find_text_ranges(
    doc_data: dict[str, Any],
    needle: str,
    limit: int = TEXT_MATCH_LIMIT,
    context_chars: int = TEXT_MATCH_CONTEXT_CHARS,
) -> dict[str, Any]:
    """
    Locate needle in body paragraphs, table cells and tables of contents.

    Offsets are converted to UTF-16 code units, and inline objects are counted
    by their index span, so the ranges can be used directly in format_text,
    update_paragraph_style or comment requests without further arithmetic. The
    search runs per paragraph, so text spanning a paragraph break does not match.
    Text that belongs to a pending suggestion is still searched (the document's
    indices include it); each match says so in in_suggestion, and the context
    marks it as {++inserted++} or {--deleted--}.
    """
    matches: list[dict[str, Any]] = []
    count = 0
    for element, in_table in _iter_body_paragraphs(doc_data.get("body", {})):
        segments = _paragraph_segments(element["paragraph"])
        text = "".join(segment_text for segment_text, _ in segments)
        paragraph_start = element.get("startIndex", 0)
        offset = text.find(needle)
        while offset != -1:
            count += 1
            if len(matches) < limit:
                start = paragraph_start + utf16_length(text[:offset])
                match_end = offset + len(needle)
                context_from = max(0, offset - context_chars)
                context_to = match_end + context_chars
                matches.append(
                    {
                        "start_index": start,
                        "end_index": start + utf16_length(needle),
                        "paragraph_start_index": paragraph_start,
                        "paragraph_end_index": element.get("endIndex", 0),
                        "in_table": in_table,
                        "in_suggestion": _suggestion_state(segments, offset, match_end),
                        "context": _render_context(segments, context_from, context_to),
                    }
                )
            offset = text.find(needle, offset + 1)
    return {
        "query": needle,
        "count": count,
        "matches": matches,
        "truncated": count > len(matches),
    }


def find_tables(doc_data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Find all tables in the document with their positions and dimensions.

    Args:
        doc_data: Raw document data from Google Docs API

    Returns:
        List of table information dictionaries
    """
    tables = []
    structure = parse_document_structure(doc_data)

    for idx, table_info in enumerate(structure["tables"]):
        tables.append(
            {
                "index": idx,
                "start_index": table_info["start_index"],
                "end_index": table_info["end_index"],
                "rows": table_info["rows"],
                "columns": table_info["columns"],
                "cells": table_info["cells"],
            }
        )

    return tables


def get_table_cell_indices(
    doc_data: dict[str, Any], table_index: int = 0
) -> Optional[list[list[tuple[int, int]]]]:
    """
    Get content indices for all cells in a specific table.

    Args:
        doc_data: Raw document data from Google Docs API
        table_index: Index of the table (0-based)

    Returns:
        2D list of (start_index, end_index) tuples for each cell, or None if table not found
    """
    tables = find_tables(doc_data)

    if table_index >= len(tables):
        logger.warning(
            f"Table index {table_index} not found. Document has {len(tables)} tables."
        )
        return None

    table = tables[table_index]
    cell_indices = []

    for row in table["cells"]:
        row_indices = []
        for cell in row:
            # Each cell contains at least one paragraph
            # Find the first paragraph in the cell for content insertion
            cell_content = cell.get("content_elements", [])
            if cell_content:
                # Look for the first paragraph in cell content
                first_para = None
                for element in cell_content:
                    if "paragraph" in element:
                        first_para = element["paragraph"]
                        break

                if first_para and "elements" in first_para and first_para["elements"]:
                    # Insert at the start of the first text run in the paragraph
                    first_text_element = first_para["elements"][0]
                    if "textRun" in first_text_element:
                        start_idx = first_text_element.get(
                            "startIndex", cell["start_index"] + 1
                        )
                        end_idx = first_text_element.get("endIndex", start_idx + 1)
                        row_indices.append((start_idx, end_idx))
                        continue

            # Fallback: use cell boundaries with safe margins
            content_start = cell["start_index"] + 1
            content_end = cell["end_index"] - 1
            row_indices.append((content_start, content_end))
        cell_indices.append(row_indices)

    return cell_indices


def find_element_at_index(
    doc_data: dict[str, Any], index: int
) -> Optional[dict[str, Any]]:
    """
    Find what element exists at a given index in the document.

    Args:
        doc_data: Raw document data from Google Docs API
        index: Position in the document

    Returns:
        Information about the element at that position, or None
    """
    structure = parse_document_structure(doc_data)

    for element in structure["body"]:
        if element["start_index"] <= index < element["end_index"]:
            element_copy = element.copy()

            # If it's a table, find which cell contains the index
            if element["type"] == "table" and "cells" in element:
                for row_idx, row in enumerate(element["cells"]):
                    for col_idx, cell in enumerate(row):
                        if cell["start_index"] <= index < cell["end_index"]:
                            element_copy["containing_cell"] = {
                                "row": row_idx,
                                "column": col_idx,
                                "cell_start": cell["start_index"],
                                "cell_end": cell["end_index"],
                            }
                            break

            return element_copy

    return None


def get_next_paragraph_index(doc_data: dict[str, Any], after_index: int = 0) -> int:
    """
    Find the next safe position to insert content after a given index.

    Args:
        doc_data: Raw document data from Google Docs API
        after_index: Index after which to find insertion point

    Returns:
        Safe index for insertion
    """
    structure = parse_document_structure(doc_data)

    # Find the first paragraph element after the given index
    for element in structure["body"]:
        if element["type"] == "paragraph" and element["start_index"] > after_index:
            # Insert at the end of the previous element or start of this paragraph
            return element["start_index"]

    # If no paragraph found, return the end of document
    return structure["total_length"] - 1 if structure["total_length"] > 0 else 1


def _is_empty_paragraph(paragraph: dict[str, Any]) -> bool:
    """A newline-only paragraph with no existing or suggested object anchors."""
    return (
        paragraph["end_index"] - paragraph["start_index"] == 1
        and not paragraph.get("positioned_object_ids")
        and not paragraph.get("suggested_positioned_object_ids")
    )


def summarize_paragraph_layout(structure: dict[str, Any]) -> dict[str, Any]:
    """Count empty body paragraphs and report the last paragraph's state.

    Only top-level paragraphs are included; object anchors do not count as empty.
    Ranges are capped extents, including protected newlines. last_paragraph is
    None if absent.
    """
    paragraphs = [e for e in structure["body"] if e.get("type") == "paragraph"]

    empty_ranges = [
        {"start": p["start_index"], "end": p["end_index"]}
        for p in paragraphs
        if _is_empty_paragraph(p)
    ]

    last_paragraph = None
    if paragraphs:
        last = paragraphs[-1]
        last_paragraph = {
            "is_list_item": bool(last.get("is_list_item")),
            "is_empty": _is_empty_paragraph(last),
        }

    return {
        "empty_paragraphs": len(empty_ranges),
        "empty_paragraph_ranges": empty_ranges[:EMPTY_PARAGRAPH_RANGE_LIMIT],
        "empty_paragraph_ranges_truncated": len(empty_ranges)
        > EMPTY_PARAGRAPH_RANGE_LIMIT,
        "last_paragraph": last_paragraph,
    }


def analyze_document_complexity(doc_data: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze document complexity and provide statistics.

    Args:
        doc_data: Raw document data from Google Docs API

    Returns:
        Dictionary with document statistics
    """
    structure = parse_document_structure(doc_data)

    stats = {
        "total_elements": len(structure["body"]),
        "tables": len(structure["tables"]),
        "paragraphs": sum(1 for e in structure["body"] if e.get("type") == "paragraph"),
        "section_breaks": sum(
            1 for e in structure["body"] if e.get("type") == "section_break"
        ),
        "total_length": structure["total_length"],
        "has_headers": bool(structure["headers"]),
        "has_footers": bool(structure["footers"]),
        **summarize_paragraph_layout(structure),
    }

    # Add table statistics
    if structure["tables"]:
        total_cells = sum(
            table["rows"] * table["columns"] for table in structure["tables"]
        )
        stats["total_table_cells"] = total_cells
        stats["largest_table"] = max(
            (t["rows"] * t["columns"] for t in structure["tables"]), default=0
        )

    return stats


def _iter_tabs(tabs: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for tab in tabs:
        yield tab
        yield from _iter_tabs(tab.get("childTabs", []))


def _body_content_for_tab(
    doc: dict[str, Any], tab_id: Optional[str]
) -> list[dict[str, Any]]:
    """Return the body content of ``tab_id``, or of the only tab when omitted."""
    tabs = list(_iter_tabs(doc.get("tabs", [])))
    if tab_id is None:
        if len(tabs) > 1:
            available = [t.get("tabProperties", {}).get("tabId") for t in tabs]
            raise UserInputError(
                f"Document has {len(tabs)} tabs; tab_id is required. "
                f"Available tab IDs: {available}"
            )
        tab = tabs[0] if tabs else {"documentTab": doc}
    else:
        tab = next(
            (t for t in tabs if t.get("tabProperties", {}).get("tabId") == tab_id),
            None,
        )
        if tab is None:
            raise UserInputError(f"tab_id {tab_id!r} not found in document.")
    return tab.get("documentTab", {}).get("body", {}).get("content", [])


def _paragraph_heading_level(paragraph: dict[str, Any]) -> Optional[int]:
    """Return N for a ``HEADING_N`` paragraph, otherwise ``None``."""
    style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
    return int(style[len("HEADING_") :]) if style.startswith("HEADING_") else None


def find_heading_section(
    doc: dict[str, Any],
    heading: str,
    heading_level: Optional[int] = None,
    tab_id: Optional[str] = None,
) -> dict[str, Any]:
    """Locate the one body heading matching ``heading`` and the section below it.

    The section runs to the next ``TITLE`` or equal-or-shallower heading, or to
    the body's undeletable final newline. Returns ``body_start``,
    ``section_end``, ``last_paragraph`` and ``footnote_refs``.
    """
    content = _body_content_for_tab(doc, tab_id)
    wanted = heading.strip().casefold()
    matches = []
    for idx, element in enumerate(content):
        paragraph = element.get("paragraph", {})
        level = _paragraph_heading_level(paragraph)
        if level is None or heading_level not in (None, level):
            continue
        if _extract_paragraph_text(paragraph).strip().casefold() == wanted:
            matches.append(idx)
    if not matches:
        raise UserInputError(
            f"No heading matching {heading!r} found in the document body "
            f"(heading_level={heading_level!r}, tab_id={tab_id!r}). Headings inside "
            "tables, footnotes, headers and footers are not addressable by heading; "
            "use index-based editing for those."
        )
    if len(matches) > 1:
        starts = ", ".join(str(content[idx].get("startIndex")) for idx in matches)
        raise UserInputError(
            f"Heading {heading!r} matches {len(matches)} headings (at indices "
            f"{starts}). Pass heading_level to narrow it, or edit by index."
        )

    match_idx = matches[0]
    matched_level = _paragraph_heading_level(content[match_idx]["paragraph"])
    body_start = content[match_idx]["endIndex"]
    section_end = None
    last_paragraph = content[match_idx]
    for element in content[match_idx + 1 :]:
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        level = _paragraph_heading_level(paragraph)
        style = paragraph.get("paragraphStyle", {}).get("namedStyleType")
        if style == "TITLE" or (level is not None and level <= matched_level):
            section_end = element["startIndex"]
            break
        last_paragraph = element
    if section_end is None:
        # The body's final newline cannot be deleted.
        section_end = last_paragraph["endIndex"] - 1

    footnote_refs = sum(
        1
        for element in content[match_idx + 1 :]
        for run in element.get("paragraph", {}).get("elements", [])
        if "footnoteReference" in run
        and body_start <= run.get("startIndex", -1) < section_end
    )
    return {
        "body_start": body_start,
        "section_end": section_end,
        "last_paragraph": last_paragraph,
        "footnote_refs": footnote_refs,
    }
