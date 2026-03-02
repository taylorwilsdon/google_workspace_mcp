"""
Google Docs to Markdown Converter

Converts Google Docs API JSON responses to clean Markdown, preserving:
- Headings (H1-H6, Title, Subtitle)
- Bold, italic, strikethrough, code, links
- Ordered and unordered lists with nesting
- Checklists with checked/unchecked state
- Tables with header row separators
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MONO_FONTS = {"Courier New", "Consolas", "Roboto Mono", "Source Code Pro"}

HEADING_MAP = {
    "TITLE": "#",
    "SUBTITLE": "##",
    "HEADING_1": "#",
    "HEADING_2": "##",
    "HEADING_3": "###",
    "HEADING_4": "####",
    "HEADING_5": "#####",
    "HEADING_6": "######",
}


def convert_doc_to_markdown(doc: dict[str, Any]) -> str:
    """Convert a Google Docs API document response to markdown.

    Args:
        doc: The document JSON from docs.documents.get()

    Returns:
        Markdown string
    """
    body = doc.get("body", {})
    content = body.get("content", [])
    lists_meta = doc.get("lists", {})

    lines: list[str] = []
    ordered_counters: dict[tuple[str, int], int] = {}
    prev_was_list = False

    for element in content:
        if "paragraph" in element:
            para = element["paragraph"]
            text = _convert_paragraph_text(para)

            if not text.strip():
                if prev_was_list:
                    prev_was_list = False
                continue

            bullet = para.get("bullet")
            if bullet:
                list_id = bullet["listId"]
                nesting = bullet.get("nestingLevel", 0)

                if _is_checklist(lists_meta, list_id, nesting):
                    checked = _is_checked(para)
                    checkbox = "[x]" if checked else "[ ]"
                    indent = "  " * nesting
                    # Re-render text without strikethrough for checked items
                    # to avoid redundant ~~text~~ alongside [x]
                    cb_text = (
                        _convert_paragraph_text(para, skip_strikethrough=True)
                        if checked
                        else text
                    )
                    lines.append(f"{indent}- {checkbox} {cb_text}")
                elif _is_ordered_list(lists_meta, list_id, nesting):
                    key = (list_id, nesting)
                    ordered_counters[key] = ordered_counters.get(key, 0) + 1
                    counter = ordered_counters[key]
                    indent = "   " * nesting
                    lines.append(f"{indent}{counter}. {text}")
                else:
                    indent = "  " * nesting
                    lines.append(f"{indent}- {text}")
                prev_was_list = True
            else:
                if prev_was_list:
                    ordered_counters.clear()
                    lines.append("")
                    prev_was_list = False

                style = para.get("paragraphStyle", {})
                named_style = style.get("namedStyleType", "NORMAL_TEXT")
                prefix = HEADING_MAP.get(named_style, "")

                if prefix:
                    lines.append(f"{prefix} {text}")
                    lines.append("")
                else:
                    lines.append(text)
                    lines.append("")

        elif "table" in element:
            if prev_was_list:
                ordered_counters.clear()
                lines.append("")
                prev_was_list = False
            table_md = _convert_table(element["table"])
            lines.append(table_md)
            lines.append("")

    result = "\n".join(lines).rstrip("\n") + "\n"
    return result


def _convert_paragraph_text(
    para: dict[str, Any], skip_strikethrough: bool = False
) -> str:
    """Convert paragraph elements to inline markdown text."""
    parts: list[str] = []
    for elem in para.get("elements", []):
        if "textRun" in elem:
            parts.append(_convert_text_run(elem["textRun"], skip_strikethrough))
    return "".join(parts).strip()


def _convert_text_run(
    text_run: dict[str, Any], skip_strikethrough: bool = False
) -> str:
    """Convert a single text run to markdown."""
    content = text_run.get("content", "")
    style = text_run.get("textStyle", {})

    text = content.rstrip("\n")
    if not text:
        return ""

    return _apply_text_style(text, style, skip_strikethrough)


def _apply_text_style(
    text: str, style: dict[str, Any], skip_strikethrough: bool = False
) -> str:
    """Apply markdown formatting based on text style."""
    link = style.get("link", {})
    url = link.get("url")

    font_family = style.get("weightedFontFamily", {}).get("fontFamily", "")
    if font_family in MONO_FONTS:
        return f"`{text}`"

    bold = style.get("bold", False)
    italic = style.get("italic", False)
    strikethrough = style.get("strikethrough", False)

    if bold and italic:
        text = f"***{text}***"
    elif bold:
        text = f"**{text}**"
    elif italic:
        text = f"*{text}*"

    if strikethrough and not skip_strikethrough:
        text = f"~~{text}~~"

    if url:
        text = f"[{text}]({url})"

    return text


def _is_ordered_list(lists_meta: dict[str, Any], list_id: str, nesting: int) -> bool:
    """Check if a list at a given nesting level is ordered."""
    list_info = lists_meta.get(list_id, {})
    nesting_levels = list_info.get("listProperties", {}).get("nestingLevels", [])
    if nesting < len(nesting_levels):
        level = nesting_levels[nesting]
        glyph = level.get("glyphType", "")
        return glyph not in ("", "GLYPH_TYPE_UNSPECIFIED")
    return False


def _is_checklist(lists_meta: dict[str, Any], list_id: str, nesting: int) -> bool:
    """Check if a list at a given nesting level is a checklist.

    Google Docs checklists are distinguished from regular bullet lists by having
    GLYPH_TYPE_UNSPECIFIED with no glyphSymbol — the Docs UI renders interactive
    checkboxes rather than a static glyph character.
    """
    list_info = lists_meta.get(list_id, {})
    nesting_levels = list_info.get("listProperties", {}).get("nestingLevels", [])
    if nesting < len(nesting_levels):
        level = nesting_levels[nesting]
        glyph_type = level.get("glyphType", "")
        has_glyph_symbol = "glyphSymbol" in level
        return glyph_type in ("", "GLYPH_TYPE_UNSPECIFIED") and not has_glyph_symbol
    return False


def _is_checked(para: dict[str, Any]) -> bool:
    """Check if a checklist item is checked.

    Google Docs marks checked checklist items by applying strikethrough
    formatting to the paragraph text.
    """
    for elem in para.get("elements", []):
        if "textRun" in elem:
            content = elem["textRun"].get("content", "").strip()
            if content:
                return elem["textRun"].get("textStyle", {}).get("strikethrough", False)
    return False


def _convert_table(table: dict[str, Any]) -> str:
    """Convert a table element to markdown."""
    rows = table.get("tableRows", [])
    if not rows:
        return ""

    md_rows: list[str] = []
    for i, row in enumerate(rows):
        cells: list[str] = []
        for cell in row.get("tableCells", []):
            cell_text = _extract_cell_text(cell)
            cells.append(cell_text)
        md_rows.append("| " + " | ".join(cells) + " |")

        if i == 0:
            sep = "| " + " | ".join("---" for _ in cells) + " |"
            md_rows.append(sep)

    return "\n".join(md_rows)


def _extract_cell_text(cell: dict[str, Any]) -> str:
    """Extract text from a table cell."""
    parts: list[str] = []
    for content_elem in cell.get("content", []):
        if "paragraph" in content_elem:
            text = _convert_paragraph_text(content_elem["paragraph"])
            if text.strip():
                parts.append(text.strip())
    cell_text = " ".join(parts)
    return cell_text.replace("|", "\\|")


def format_comments_inline(markdown: str, comments: list[dict[str, Any]]) -> str:
    """Insert footnote-style comment annotations inline in markdown.

    For each comment, finds the anchor text in the markdown and inserts
    a footnote reference. Unmatched comments go to an appendix at the bottom.
    """
    if not comments:
        return markdown

    footnotes: list[str] = []
    unmatched: list[dict[str, Any]] = []

    for i, comment in enumerate(comments, 1):
        ref = f"[^c{i}]"
        anchor = comment.get("anchor_text", "")

        if anchor and anchor in markdown:
            markdown = markdown.replace(anchor, anchor + ref, 1)
            footnotes.append(_format_footnote(i, comment))
        else:
            unmatched.append(comment)

    if footnotes:
        markdown = markdown.rstrip("\n") + "\n\n" + "\n".join(footnotes) + "\n"

    if unmatched:
        appendix = format_comments_appendix(unmatched)
        if appendix.strip():
            markdown = markdown.rstrip("\n") + "\n\n" + appendix

    return markdown


def _format_footnote(num: int, comment: dict[str, Any]) -> str:
    """Format a single footnote."""
    lines = [f"[^c{num}]: **{comment['author']}**: {comment['content']}"]
    for reply in comment.get("replies", []):
        lines.append(f"    - **{reply['author']}**: {reply['content']}")
    return "\n".join(lines)


def format_comments_appendix(comments: list[dict[str, Any]]) -> str:
    """Format comments as an appendix section with blockquoted anchors."""
    if not comments:
        return ""

    lines = ["## Comments", ""]
    for comment in comments:
        resolved_tag = " *(Resolved)*" if comment.get("resolved") else ""
        anchor = comment.get("anchor_text", "")
        if anchor:
            lines.append(f"> {anchor}")
            lines.append("")
        lines.append(f"- **{comment['author']}**: {comment['content']}{resolved_tag}")
        for reply in comment.get("replies", []):
            lines.append(f"  - **{reply['author']}**: {reply['content']}")
        lines.append("")

    return "\n".join(lines)


def parse_drive_comments(
    response: dict[str, Any], include_resolved: bool = False
) -> list[dict[str, Any]]:
    """Parse Drive API comments response into structured dicts.

    Args:
        response: Raw JSON from drive.comments.list()
        include_resolved: Whether to include resolved comments

    Returns:
        List of comment dicts with keys: author, content, anchor_text,
        replies, resolved
    """
    results = []
    for comment in response.get("comments", []):
        if not include_resolved and comment.get("resolved", False):
            continue

        anchor_text = comment.get("quotedFileContent", {}).get("value", "")
        replies = [
            {
                "author": r.get("author", {}).get("displayName", "Unknown"),
                "content": r.get("content", ""),
            }
            for r in comment.get("replies", [])
        ]
        results.append(
            {
                "author": comment.get("author", {}).get("displayName", "Unknown"),
                "content": comment.get("content", ""),
                "anchor_text": anchor_text,
                "replies": replies,
                "resolved": comment.get("resolved", False),
            }
        )
    return results
