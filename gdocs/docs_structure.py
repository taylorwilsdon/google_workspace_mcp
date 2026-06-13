"""
Google Docs Document Structure Parsing and Analysis

This module provides utilities for parsing and analyzing the structure
of Google Docs documents, including finding tables, cells, and other elements.
"""

import logging
from typing import Any, Literal, Optional

from core.utils import UserInputError

logger = logging.getLogger(__name__)


def parse_document_structure(doc_data: dict[str, Any]) -> dict[str, Any]:
    """
    Parse the full document structure into a navigable format.

    Args:
        doc_data: Raw document data from Google Docs API

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
        structure["headers"][header_id] = _parse_segment(header_data)

    for footer_id, footer_data in doc_data.get("footers", {}).items():
        structure["footers"][footer_id] = _parse_segment(footer_data)

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


def _parse_segment(segment_data: dict[str, Any]) -> dict[str, Any]:
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
        "text_preview": "".join(text_parts)[:100],
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


# Section-addressable editing helpers (see gdocs/docs_tools.py section-edit tools).
# Walks doc structure to map heading paragraphs to (start, end) index ranges that
# can be fed to DeleteContentRangeRequest, with a pre-check for footnote
# references that would make a delete request behave undefinedly.


def _body_content_for_tab(
    doc: dict[str, Any], tab_id: Optional[str]
) -> list[dict[str, Any]]:
    """Return the body.content list for the requested scope.

    Single-tab access path:
      - if tab_id is None and doc has no `tabs` (or only one tab), returns
        body.content.
    Multi-tab access path:
      - if doc has >1 tab and tab_id is None, raises UserInputError listing
        available tab IDs.
      - if tab_id is given, walks doc.tabs[*] for a matching
        tabProperties.tabId and returns its documentTab.body.content;
        UserInputError if not found.

    `includeTabsContent=True` on documents.get is required for the tab path
    to be populated; without it, tabs[*].documentTab is omitted.
    """
    tabs = doc.get("tabs") or []
    if len(tabs) > 1:
        if tab_id is None:
            available = [t.get("tabProperties", {}).get("tabId", "?") for t in tabs]
            raise UserInputError(
                f"Document has {len(tabs)} tabs; tab_id is required. "
                f"Available tab IDs: {available}"
            )
        for tab in tabs:
            if tab.get("tabProperties", {}).get("tabId") == tab_id:
                return tab.get("documentTab", {}).get("body", {}).get("content", [])
        raise UserInputError(f"tab_id {tab_id!r} not found in document.")
    # Single-tab: prefer the top-level body for backward compat; some tab-aware
    # docs put content under tabs[0] only.
    body_content = doc.get("body", {}).get("content")
    if body_content:
        return body_content
    if tabs:
        return tabs[0].get("documentTab", {}).get("body", {}).get("content", [])
    return []


def body_protected_end_index(doc: dict[str, Any], tab_id: Optional[str] = None) -> int:
    """Largest index passable to DeleteContentRangeRequest.endIndex without
    triggering "Deleting the last newline character of a Body".

    The Docs API does NOT guarantee body.content's last element is a paragraph
    (it could be sectionBreak, table, or tableOfContents). Walks backward
    through body.content to find the last paragraph and returns its
    endIndex - 1. If no paragraph exists at all (theoretically possible per
    spec), falls back to last element's endIndex - 1 with a debug log
    (behavior is Inferred-not-Verified for the no-paragraph case).

    Returns 1 for an empty body (signals the section-edit tools to skip the
    delete request entirely).
    """
    content = _body_content_for_tab(doc, tab_id)
    if not content:
        return 1
    for element in reversed(content):
        if "paragraph" in element:
            return int(element.get("endIndex", 1)) - 1
    logger.debug(
        "body_protected_end_index: no paragraph found in body.content; "
        "falling back to last element's endIndex - 1"
    )
    return int(content[-1].get("endIndex", 1)) - 1


def count_footnote_refs_in_range(
    doc: dict[str, Any],
    tab_id: Optional[str],
    start: int,
    end: int,
) -> int:
    """Count footnoteReference structural elements whose startIndex is in
    [start, end). Used by the section-edit tools to pre-check before issuing
    a DeleteContentRangeRequest, since the Docs API does not document
    cross-footnoteRef delete behavior.
    """
    count = 0
    for element in _body_content_for_tab(doc, tab_id):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        for run in paragraph.get("elements", []):
            if "footnoteReference" not in run:
                continue
            run_start = run.get("startIndex")
            if run_start is None:
                continue
            if start <= int(run_start) < end:
                count += 1
    return count


def _paragraph_heading_level(paragraph: dict[str, Any]) -> Optional[int]:
    """Return the 1-6 heading level for a paragraph whose paragraphStyle
    declares HEADING_N; None for anything else. namedStyleType defaults to
    NORMAL_TEXT when absent.
    """
    style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
    if style.startswith("HEADING_"):
        try:
            return int(style.split("_", 1)[1])
        except (IndexError, ValueError):
            return None
    return None


def find_heading_range(
    doc: dict[str, Any],
    heading_text: str,
    heading_level: Optional[int],
    match: Literal["first", "exact"],
    tab_id: Optional[str],
) -> tuple[int, int, int]:
    """Locate a heading paragraph and compute the end of its section.

    Returns (section_start, section_end, footnote_ref_count_in_range).

    section_start = matched heading paragraph's startIndex.
    section_end = startIndex of the next paragraph whose namedStyleType is a
    heading at equal-or-shallower level (HEADING_N where N <= matched_level)
    or TITLE — or body_protected_end_index() if no such successor exists.
    SUBTITLE is NOT a terminator (it functions as a continuation of TITLE in
    Docs, not a structural divider mid-body).

    Match criteria:
      - extract textRun content from paragraph.elements[*]; rstrip("\n");
        full-string case-sensitive equality with heading_text
      - if heading_level given, paragraphStyle.namedStyleType must equal
        f"HEADING_{heading_level}"

    Body-only matching. Headings inside table cells, footnotes, headers, or
    footers are NOT addressable by this helper.

    Raises UserInputError on:
      - no match
      - match="exact" and multiple matches
      - matched heading paragraph contains inline objects (footnoteReference,
        pageBreak, equation, inlineObjectElement) — _extract_paragraph_text
        silently skips these so the matched text is unreliable
    """
    content = _body_content_for_tab(doc, tab_id)

    candidates: list[tuple[int, dict[str, Any]]] = []
    for idx, element in enumerate(content):
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        level = _paragraph_heading_level(paragraph)
        if level is None:
            continue
        if heading_level is not None and level != heading_level:
            continue
        text = _extract_paragraph_text(paragraph).rstrip("\n")
        if text == heading_text:
            candidates.append((idx, element))

    if not candidates:
        raise UserInputError(
            f"No heading matching {heading_text!r} found in body "
            f"(level={heading_level!r}, tab_id={tab_id!r}). "
            "Note: headings inside table cells, footnotes, headers, and footers "
            "are not addressable by this tool — use batch_update_doc for those."
        )
    if match == "exact" and len(candidates) > 1:
        raise UserInputError(
            f"Multiple headings match {heading_text!r} (found {len(candidates)}); "
            'pass match="first" to accept the first one or narrow with heading_level.'
        )

    chosen_idx, chosen_element = candidates[0]
    chosen_paragraph = chosen_element["paragraph"]

    # Reject if the matched heading paragraph contains inline objects that
    # _extract_paragraph_text silently skips — equality may be coincidental.
    for run in chosen_paragraph.get("elements", []):
        if any(
            k in run
            for k in (
                "footnoteReference",
                "pageBreak",
                "equation",
                "inlineObjectElement",
            )
        ):
            raise UserInputError(
                "Matched heading paragraph contains inline objects "
                "(footnoteReference / pageBreak / equation / inlineObjectElement); "
                "matched text is unreliable. Use batch_update_doc instead."
            )

    matched_level = _paragraph_heading_level(chosen_paragraph)
    section_start = int(chosen_element.get("startIndex", 0))

    # Walk forward for a terminator: HEADING_<= matched_level OR TITLE.
    section_end: Optional[int] = None
    for element in content[chosen_idx + 1 :]:
        paragraph = element.get("paragraph")
        if not paragraph:
            continue
        style = paragraph.get("paragraphStyle", {}).get("namedStyleType", "NORMAL_TEXT")
        if style == "TITLE":
            section_end = int(element.get("startIndex", 0))
            break
        level = _paragraph_heading_level(paragraph)
        if level is not None and matched_level is not None and level <= matched_level:
            section_end = int(element.get("startIndex", 0))
            break

    if section_end is None:
        section_end = body_protected_end_index(doc, tab_id)

    footnote_count = count_footnote_refs_in_range(
        doc, tab_id, section_start, section_end
    )
    return section_start, section_end, footnote_count
