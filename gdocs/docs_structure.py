"""
Google Docs Document Structure Parsing and Analysis

This module provides utilities for parsing and analyzing the structure
of Google Docs documents, including finding tables, cells, and other elements.
"""

import logging
from collections.abc import Iterator
from typing import Any, Optional

from core.utils import UserInputError

logger = logging.getLogger(__name__)

# Limit returned ranges; the count remains exact.
EMPTY_PARAGRAPH_RANGE_LIMIT = 100
HEADING_LIST_LIMIT = 50


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


def _document_tab(doc: dict[str, Any], tab_id: Optional[str]) -> dict[str, Any]:
    """Return the document tab ``tab_id``, or the only tab when omitted."""
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
    return tab.get("documentTab", {})


def _paragraph_heading_level(paragraph: dict[str, Any]) -> Optional[int]:
    """Return N for a ``HEADING_N`` paragraph, otherwise ``None``."""
    style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
    return int(style[len("HEADING_") :]) if style.startswith("HEADING_") else None


def _resolve_heading_section(
    content: list[dict[str, Any]],
    heading: str,
    heading_level: Optional[int],
    tab_id: Optional[str],
) -> tuple[int, int]:
    """Return the content positions of the one matching heading and its section end.

    The section ends at the next ``TITLE`` or equal-or-shallower heading, or at
    ``len(content)``.
    """
    if not heading.strip():
        raise UserInputError("'heading' must not be empty.")
    if heading_level is not None and not 1 <= heading_level <= 6:
        raise UserInputError("'heading_level' must be between 1 and 6.")
    wanted = heading.strip().casefold()
    headings = []
    for idx, element in enumerate(content):
        paragraph = element.get("paragraph", {})
        level = _paragraph_heading_level(paragraph)
        if level is not None:
            headings.append((idx, level, _extract_paragraph_text(paragraph).strip()))
    matches = [
        (idx, level)
        for idx, level, text in headings
        if text.casefold() == wanted and heading_level in (None, level)
    ]
    if not matches:
        named = [text for _, _, text in headings if text]
        available = ", ".join(map(repr, named[:HEADING_LIST_LIMIT]))
        if len(named) > HEADING_LIST_LIMIT:
            available += f" and {len(named) - HEADING_LIST_LIMIT} more"
        raise UserInputError(
            f"No heading matching {heading!r} found in the document body "
            f"(heading_level={heading_level!r}, tab_id={tab_id!r}). "
            + (
                f"Available headings: {available}. "
                if available
                else "The document body has no headings. "
            )
            + "Headings inside tables, footnotes, headers and footers are not "
            "addressable by heading; use index-based editing for those."
        )
    if len(matches) > 1:
        starts = ", ".join(str(content[idx].get("startIndex")) for idx, _ in matches)
        raise UserInputError(
            f"Heading {heading!r} matches {len(matches)} headings (at indices "
            f"{starts}). Pass heading_level to narrow it, or use document indices."
        )

    match_idx, matched_level = matches[0]
    for idx in range(match_idx + 1, len(content)):
        paragraph = content[idx].get("paragraph")
        if not paragraph:
            continue
        level = _paragraph_heading_level(paragraph)
        style = paragraph.get("paragraphStyle", {}).get("namedStyleType")
        if style == "TITLE" or (level is not None and level <= matched_level):
            return match_idx, idx
    return match_idx, len(content)


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
    content = _document_tab(doc, tab_id).get("body", {}).get("content", [])
    match_idx, end_idx = _resolve_heading_section(
        content, heading, heading_level, tab_id
    )
    body_start = content[match_idx]["endIndex"]
    last_paragraph = [e for e in content[match_idx:end_idx] if e.get("paragraph")][-1]
    if end_idx < len(content):
        section_end = content[end_idx]["startIndex"]
    else:
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


def heading_section_document(
    doc: dict[str, Any],
    heading: str,
    heading_level: Optional[int] = None,
    tab_id: Optional[str] = None,
) -> dict[str, Any]:
    """Return the tab cut down to the section under ``heading``, heading included.

    Headers, footers and positioned objects anchored outside the section are
    dropped. An empty ``tab_id`` is treated as omitted.
    """
    tab_id = tab_id or None
    document_tab = _document_tab(doc, tab_id)
    content = document_tab.get("body", {}).get("content", [])
    match_idx, end_idx = _resolve_heading_section(
        content, heading, heading_level, tab_id
    )
    section = content[match_idx:end_idx]
    anchored = {
        object_id
        for element in section
        for object_id in element.get("paragraph", {}).get("positionedObjectIds", [])
    }
    return {
        **document_tab,
        "body": {"content": section},
        "headers": {},
        "footers": {},
        "positionedObjects": {
            object_id: obj
            for object_id, obj in document_tab.get("positionedObjects", {}).items()
            if object_id in anchored
        },
    }
