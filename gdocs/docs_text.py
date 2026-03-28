"""
Suggestion-aware text extraction for Google Docs.

Provides two public functions:
- extract_sections: groups document body elements by heading boundaries
- render_elements: renders elements to plain text, filtered by suggestion mode
"""
from typing import Any

HEADING_STYLES = {"HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6"}


def _heading_level(paragraph: dict[str, Any]) -> int | None:
    """Return the heading level (1-6) if this paragraph is a heading, else None."""
    style_name = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
    if style_name in HEADING_STYLES:
        return int(style_name[-1])
    return None


def _render_text_run(text_run: dict[str, Any], mode: str) -> str:
    """
    Return the textRun content filtered by suggestion mode.

    original: skip textRuns that are pending insertions (suggestedInsertionIds non-empty)
    accepted: skip textRuns that are pending deletions (suggestedDeletionIds non-empty)
    """
    if mode == "original" and text_run.get("suggestedInsertionIds"):
        return ""
    if mode == "accepted" and text_run.get("suggestedDeletionIds"):
        return ""
    return text_run.get("content", "")


def render_elements(elements: list[dict[str, Any]], mode: str, depth: int = 0) -> str:
    """
    Render a list of document body elements to plain text.

    Args:
        elements: List of document elements (paragraphs, tables, etc.)
        mode: "original" or "accepted"
        depth: Recursion depth guard (max 5)

    Returns:
        Plain text string.
    """
    if depth > 5:
        return ""
    parts = []
    for element in elements:
        if "paragraph" in element:
            para_elements = element["paragraph"].get("elements", [])
            line = ""
            for pe in para_elements:
                text_run = pe.get("textRun", {})
                if text_run:
                    line += _render_text_run(text_run, mode)
            if line.strip():
                parts.append(line)
        elif "table" in element:
            table = element["table"]
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    cell_text = render_elements(cell.get("content", []), mode, depth + 1)
                    if cell_text.strip():
                        parts.append(cell_text)
    return "".join(parts)


def extract_sections(body_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group document body elements into sections by heading boundaries.

    Content before the first heading is section 0 with title "(preamble)".
    Each heading starts a new section; the section contains that heading element
    and all elements up to (but not including) the next heading of equal or
    higher level (lower number).

    Args:
        body_elements: The 'content' list from the document body.

    Returns:
        List of section dicts:
            {
                "index": int,       # 0-based
                "level": int,       # heading level (0 for preamble)
                "title": str,       # heading text (suggestions stripped, i.e. original mode)
                "elements": list    # raw API elements for this section
            }
    """
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"index": 0, "level": 0, "title": "(preamble)", "elements": []}

    for element in body_elements:
        if "paragraph" in element:
            level = _heading_level(element["paragraph"])
            if level is not None:
                # Save current section if it has content
                if current["elements"]:
                    sections.append(current)
                # Extract heading title using original mode (no pending insertions)
                title = render_elements([element], "original").strip()
                current = {
                    "index": len(sections),
                    "level": level,
                    "title": title or f"(heading level {level})",
                    "elements": [element],
                }
                continue
        current["elements"].append(element)

    # Append last section
    if current["elements"]:
        sections.append(current)

    return sections
