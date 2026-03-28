"""
Text extraction helpers for Google Docs.

The suggestion mode (original vs accepted) is handled at the API level via
the suggestionsViewMode parameter on documents().get(). These helpers simply
extract plain text from whatever the API returns.

Provides two public functions:
- extract_sections: groups document body elements by heading boundaries
- render_elements: renders elements to plain text
"""
from typing import Any

HEADING_STYLES = {"HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6"}

# Maps the mode parameter to the Google Docs API suggestionsViewMode value
SUGGESTIONS_VIEW_MODE = {
    "original": "PREVIEW_WITHOUT_SUGGESTIONS",
    "accepted": "PREVIEW_SUGGESTIONS_ACCEPTED",
}


def _heading_level(paragraph: dict[str, Any]) -> int | None:
    """Return the heading level (1-6) if this paragraph is a heading, else None."""
    style_name = paragraph.get("paragraphStyle", {}).get("namedStyleType", "")
    if style_name in HEADING_STYLES:
        return int(style_name[-1])
    return None


def render_elements(elements: list[dict[str, Any]], depth: int = 0) -> str:
    """
    Render a list of document body elements to plain text.

    Suggestion filtering is done by the API (suggestionsViewMode), so this
    function simply extracts whatever text the API returned.

    Args:
        elements: List of document elements (paragraphs, tables, etc.)
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
                    line += text_run.get("content", "")
            if line.strip():
                parts.append(line)
        elif "table" in element:
            table = element["table"]
            for row in table.get("tableRows", []):
                for cell in row.get("tableCells", []):
                    cell_text = render_elements(cell.get("content", []), depth + 1)
                    if cell_text.strip():
                        parts.append(cell_text)
    return "".join(parts)


def extract_sections(body_elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group document body elements into sections by heading boundaries.

    Content before the first heading is section 0 with title "(preamble)".

    Args:
        body_elements: The 'content' list from the document body.

    Returns:
        List of section dicts:
            {
                "index": int,    # 0-based
                "level": int,    # heading level (0 for preamble)
                "title": str,    # heading text
                "elements": list # raw API elements for this section
            }
    """
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] = {"index": 0, "level": 0, "title": "(preamble)", "elements": []}

    for element in body_elements:
        if "paragraph" in element:
            level = _heading_level(element["paragraph"])
            if level is not None:
                if current["elements"]:
                    sections.append(current)
                title = render_elements([element]).strip()
                current = {
                    "index": len(sections),
                    "level": level,
                    "title": title or f"(heading level {level})",
                    "elements": [element],
                }
                continue
        current["elements"].append(element)

    if current["elements"]:
        sections.append(current)

    return sections
