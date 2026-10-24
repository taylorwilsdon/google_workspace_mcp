"""
Google Docs to Markdown Converter

Converts Google Docs API JSON responses to clean Markdown, preserving:
- Headings (H1-H6, Title, Subtitle)
- Bold, italic, strikethrough, code, links
- Ordered and unordered lists with nesting
- Checklists with checked/unchecked state
- Tables with header row separators
- Smart chips: person (@mentions), rich links, dates, inline images,
  footnotes, horizontal rules, auto-text (page numbers), equations
- Document tabs (multi-tab and nested child tabs)
- Explicit RGB text color as a trailing ``[color: #rrggbb]`` note (opt-in)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gdocs.docs_links import resolve_link_target

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


# Pending suggestions, as the Docs API returns them in the SUGGESTIONS_INLINE
# view: the old and new text sit side by side, each run labelled with the
# suggestion it belongs to. Rendered as CriticMarkup so a reader can tell them
# apart; the legend precedes the document whenever any appear.
SUGGESTION_INSERT_MARKERS = ("{++", "++}")
SUGGESTION_DELETE_MARKERS = ("{--", "--}")
SUGGESTION_LEGEND = (
    "> Pending suggestions are shown inline: {++inserted text++}, "
    "{--deleted text--}. Suggested formatting is shown as if accepted.\n\n"
)
_SUGGESTION_KEYS = (
    "suggestedInsertionIds",
    "suggestedDeletionIds",
    "suggestedTextStyleChanges",
    "suggestedDateElementPropertiesChanges",
)


def convert_doc_to_markdown(doc: dict[str, Any], include_colors: bool = False) -> str:
    """Convert a Google Docs API document response to markdown.

    Supports both legacy (top-level body) and tab-aware responses
    (includeTabsContent=True). For multi-tab docs, each tab gets a
    heading separator. Single-tab docs render without a tab heading.
    Pending suggestions (SUGGESTIONS_INLINE view) are marked as
    {++inserted++} and {--deleted--}, with a legend at the top.

    Args:
        doc: The document JSON from docs.documents.get()
        include_colors: Mark text with an explicit RGB color as ``[color: #rrggbb]``

    Returns:
        Markdown string
    """
    tabs = doc.get("tabs", [])

    if tabs:
        markdown = _convert_tabs_to_markdown(tabs, include_colors=include_colors)
    else:
        # Legacy: no tabs, use top-level body/lists/footnotes/inlineObjects
        markdown = _convert_body_to_markdown(doc, include_colors=include_colors)

    if _has_pending_suggestions(doc):
        return SUGGESTION_LEGEND + markdown
    return markdown


def _has_pending_suggestions(node: Any) -> bool:
    """Whether any element in the response carries a pending suggestion.

    Covers inserted and deleted elements as well as style-only suggestions,
    which the Docs API exposes without insertion or deletion ids.
    """
    if isinstance(node, dict):
        if any(node.get(key) for key in _SUGGESTION_KEYS):
            return True
        return any(_has_pending_suggestions(value) for value in node.values())
    if isinstance(node, list):
        return any(_has_pending_suggestions(item) for item in node)
    return False


def _convert_tabs_to_markdown(
    tabs: list[dict[str, Any]], include_colors: bool = False
) -> str:
    """Convert a list of document tabs to markdown, recursing into child tabs."""
    all_tab_docs: list[tuple[str, dict[str, Any]]] = []
    _collect_tabs(tabs, all_tab_docs)

    if len(all_tab_docs) == 1:
        _, tab_doc = all_tab_docs[0]
        return _convert_body_to_markdown(tab_doc, include_colors=include_colors)

    sections: list[str] = []
    for title, tab_doc in all_tab_docs:
        tab_md = _convert_body_to_markdown(tab_doc, include_colors=include_colors)
        sections.append(f"# {title}\n\n{tab_md}")

    return "\n".join(sections).rstrip("\n") + "\n"


def _collect_tabs(
    tabs: list[dict[str, Any]],
    result: list[tuple[str, dict[str, Any]]],
) -> None:
    """Flatten tab hierarchy into (title, documentTab) pairs."""
    for tab in tabs:
        props = tab.get("tabProperties", {})
        title = props.get("title", "Untitled Tab")
        doc_tab = tab.get("documentTab", {})
        if doc_tab:
            result.append((title, doc_tab))
        for child in tab.get("childTabs", []):
            _collect_tabs([child], result)


def _convert_body_to_markdown(doc: dict[str, Any], include_colors: bool = False) -> str:
    """Convert a single document body (or documentTab) to markdown."""
    body = doc.get("body", {})
    content = body.get("content", [])
    lists_meta = doc.get("lists", {})
    footnotes_meta = doc.get("footnotes", {})
    inline_objects = doc.get("inlineObjects", {})

    lines: list[str] = []
    ordered_counters: dict[tuple[str, int], int] = {}
    prev_was_list = False
    footnote_defs: list[tuple[str, str]] = []

    for position, element in enumerate(content):
        if "paragraph" in element:
            para = element["paragraph"]
            text = _convert_paragraph_text(
                para,
                footnotes_meta=footnotes_meta,
                inline_objects=inline_objects,
                footnote_defs=footnote_defs,
                include_colors=include_colors,
            )

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
                    cb_text = (
                        _convert_paragraph_text(
                            para,
                            skip_strikethrough=True,
                            footnotes_meta=footnotes_meta,
                            inline_objects=inline_objects,
                            footnote_defs=footnote_defs,
                            include_colors=include_colors,
                        )
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
            table_md = _convert_table(
                element["table"],
                footnotes_meta=footnotes_meta,
                inline_objects=inline_objects,
                footnote_defs=footnote_defs,
                include_colors=include_colors,
            )
            lines.append(table_md)
            lines.append("")
        # The Docs API opens every body with a section break; only later ones
        # reflect a break the author inserted.
        elif "sectionBreak" in element and position > 0:
            lines.append("[Section Break]")
            lines.append("")

    if footnote_defs:
        lines.append("")
        for fn_id, fn_text in footnote_defs:
            lines.append(f"[^{fn_id}]: {fn_text}")

    result = "\n".join(lines).rstrip("\n") + "\n"
    return result


def _convert_paragraph_text(
    para: dict[str, Any],
    skip_strikethrough: bool = False,
    footnotes_meta: dict[str, Any] | None = None,
    inline_objects: dict[str, Any] | None = None,
    footnote_defs: list[tuple[str, str]] | None = None,
    active_footnotes: set[str] | None = None,
    include_colors: bool = False,
) -> str:
    """Convert paragraph elements to inline markdown text."""
    parts: list[str] = []
    elements = para.get("elements", [])
    link_start = 0
    color_visible = False
    for position, elem in enumerate(elements):
        if "textRun" in elem:
            link = _effective_text_run_link(elements, position)
            suggestion_ids = _text_run_suggestion_ids(elements, position)
            if (
                _effective_text_run_link(elements, position - 1) != link
                or _text_run_suggestion_ids(elements, position - 1) != suggestion_ids
            ):
                link_start = len(parts)
            parts.append(_convert_text_run(elem["textRun"], skip_strikethrough))
            color = _text_run_color(elements, position) if include_colors else None
            if color:
                if _text_run_color(elements, position - 1) != color:
                    color_visible = False
                color_visible |= bool(elem["textRun"].get("content", "").strip())
            color_ends = (
                color is not None
                and color_visible
                and _text_run_color(elements, position + 1) != color
            )
            # Build the whole link before marking it, keeping ordinary text
            # and different suggestions in separate links.
            if link and (
                color_ends
                or _effective_text_run_link(elements, position + 1) != link
                or _text_run_suggestion_ids(elements, position + 1) != suggestion_ids
            ):
                parts[link_start:] = [
                    _mark_suggested(
                        _apply_link("".join(parts[link_start:]), link), elem["textRun"]
                    )
                ]
                link_start = len(parts)
            elif not link:
                parts[-1] = _mark_suggested(parts[-1], elem["textRun"])
            if color_ends:
                label = parts[-1].rstrip()
                parts[-1] = f"{label} [color: {color}]{parts[-1][len(label) :]}"
            continue
        if "person" in elem:
            carrier = elem["person"]
            part = _convert_person_chip(carrier)
        elif "richLink" in elem:
            carrier = elem["richLink"]
            part = _convert_rich_link_chip(carrier)
        elif "dateElement" in elem:
            carrier = elem["dateElement"]
            part = _convert_date_chip(carrier)
        elif "inlineObjectElement" in elem:
            carrier = elem["inlineObjectElement"]
            part = _convert_inline_object(carrier, inline_objects)
        elif "footnoteReference" in elem:
            carrier = elem["footnoteReference"]
            part = _convert_footnote_reference(
                carrier,
                footnotes_meta,
                inline_objects,
                footnote_defs,
                active_footnotes,
                include_colors=include_colors,
            )
        elif "horizontalRule" in elem:
            carrier = elem["horizontalRule"]
            part = "\n---\n"
        elif "autoText" in elem:
            carrier = elem["autoText"]
            part = _convert_auto_text(carrier)
        elif "pageBreak" in elem:
            carrier = elem["pageBreak"]
            part = "[Page Break]"
        elif "columnBreak" in elem:
            carrier = elem["columnBreak"]
            part = "[Column Break]"
        elif "equation" in elem:
            carrier = elem["equation"]
            part = _convert_equation(carrier)
        else:
            continue
        # Suggestion IDs belong to the typed payload, as they do for textRun.
        parts.append(_mark_suggested(part, carrier))
    return "".join(parts).strip()


def _mark_suggested(text: str, carrier: dict[str, Any]) -> str:
    """Wrap rendered text in markers when its carrier is a pending suggestion."""
    if not text:
        return text
    if carrier.get("suggestedDeletionIds"):
        text = f"{SUGGESTION_DELETE_MARKERS[0]}{text}{SUGGESTION_DELETE_MARKERS[1]}"
    if carrier.get("suggestedInsertionIds"):
        text = f"{SUGGESTION_INSERT_MARKERS[0]}{text}{SUGGESTION_INSERT_MARKERS[1]}"
    return text


def _effective_text_run_link(
    elements: list[dict[str, Any]], index: int
) -> dict[str, Any] | None:
    """Link of the text run at ``index`` with pending link suggestions applied.

    Mirrors docs_links.text_run_link, which reads the base style only; grouping
    on the effective link keeps a suggested link from being dropped or rendered
    with its old target.
    """
    if 0 <= index < len(elements):
        text_run = elements[index].get("textRun")
        if text_run is not None:
            return _effective_text_style(text_run).get("link")
    return None


def _convert_text_run(
    text_run: dict[str, Any], skip_strikethrough: bool = False
) -> str:
    """Convert a single text run to markdown.

    Suggested style changes are applied as if accepted. Paragraph conversion
    adds insertion/deletion markers after assembling any complete links.
    """
    content = text_run.get("content", "")
    style = _effective_text_style(text_run)

    text = content.rstrip("\n")
    # Replace Google Docs Private Use Area chip placeholders (e.g. \ue907)
    # that appear for unsupported chip types like vote, stopwatch, timer
    text = text.replace("\ue907", "[Smart Chip]")
    if not text:
        return ""

    return _apply_text_style(text, style, skip_strikethrough)


def _text_run_suggestion_ids(
    elements: list[dict[str, Any]], index: int
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Identify suggestion boundaries while grouping a linked label's runs."""
    if 0 <= index < len(elements) and "textRun" in elements[index]:
        run = elements[index]["textRun"]
        return (
            tuple(run.get("suggestedInsertionIds", [])),
            tuple(run.get("suggestedDeletionIds", [])),
        )
    return None


def _effective_text_style(text_run: dict[str, Any]) -> dict[str, Any]:
    """The run's text style with pending style suggestions applied.

    suggestedTextStyleChanges maps a suggestion id to the proposed textStyle and
    a textStyleSuggestionState whose <field>Suggested flags say which fields the
    suggestion changes; only those fields are taken from the proposed style.
    """
    style = dict(text_run.get("textStyle", {}))
    for change in text_run.get("suggestedTextStyleChanges", {}).values():
        suggested = change.get("textStyle", {})
        for key, flagged in change.get("textStyleSuggestionState", {}).items():
            if not flagged or not key.endswith("Suggested"):
                continue
            field = key[: -len("Suggested")]
            if field in suggested:
                style[field] = suggested[field]
            else:
                style.pop(field, None)
    return style


def _convert_person_chip(person: dict[str, Any]) -> str:
    """Convert a person smart chip to a mailto link."""
    props = person.get("personProperties", {})
    name = props.get("name", "")
    email = props.get("email", "")
    if email:
        label = name or email
        return f"[{label}](mailto:{email})"
    return name or ""


def _convert_rich_link_chip(rich_link: dict[str, Any]) -> str:
    """Convert a rich link smart chip to markdown.

    Rich link chips contain a richLinkProperties dict with title and uri.
    """
    props = rich_link.get("richLinkProperties", {})
    title = props.get("title", "")
    uri = props.get("uri", "")
    if title and uri:
        return f"[{title}]({uri})"
    if uri:
        return uri
    return title or ""


def _convert_date_chip(date_elem: dict[str, Any]) -> str:
    """Convert a date smart chip (dateElement) to markdown.

    Apply flagged property suggestions before using Google's displayText.
    A changed date without fresh displayText uses an ISO date/time fallback;
    this fallback does not reproduce locale-specific date formatting.
    """
    props = dict(date_elem.get("dateElementProperties", {}))
    for change in date_elem.get("suggestedDateElementPropertiesChanges", {}).values():
        suggested = change.get("dateElementProperties", {})
        state = change.get("dateElementPropertiesSuggestionState", {})
        changed = False
        for field in ("timestamp", "timeZoneId", "locale", "dateFormat", "timeFormat"):
            if state.get(f"{field}Suggested"):
                changed = True
                if field in suggested:
                    props[field] = suggested[field]
                else:
                    props.pop(field, None)
        if changed:
            # The base displayText was derived from the old properties.
            props.pop("displayText", None)
            if suggested.get("displayText"):
                props["displayText"] = suggested["displayText"]
    display_text = props.get("displayText", "")
    if display_text:
        return display_text
    # Fallback: parse the timestamp into a date string
    timestamp = props.get("timestamp", "")
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=dt.tzinfo or ZoneInfo("UTC"))
            dt = dt.astimezone(ZoneInfo(props.get("timeZoneId") or "UTC"))
            text = dt.strftime("%Y-%m-%d")
            time_format = props.get("timeFormat")
            if time_format in (
                "TIME_FORMAT_HOUR_MINUTE",
                "TIME_FORMAT_HOUR_MINUTE_TIMEZONE",
            ):
                text += dt.strftime(" %H:%M")
                if time_format == "TIME_FORMAT_HOUR_MINUTE_TIMEZONE":
                    text += f" {dt.tzname()}"
            return text
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            return timestamp
    return ""


def _convert_inline_object(
    element: dict[str, Any],
    inline_objects: dict[str, Any] | None,
) -> str:
    """Convert an inline object (image/drawing) to markdown."""
    obj_id = element.get("inlineObjectId", "")
    if not inline_objects or obj_id not in inline_objects:
        return ""
    obj = inline_objects[obj_id]
    props = obj.get("inlineObjectProperties", {}).get("embeddedObject", {})
    title = props.get("title", "") or props.get("description", "")
    uri = props.get("imageProperties", {}).get("contentUri", "")
    if uri:
        return f"![{title}]({uri})"
    return f"[Image: {title}]" if title else "[Image]"


def _convert_footnote_reference(
    ref: dict[str, Any],
    footnotes_meta: dict[str, Any] | None,
    inline_objects: dict[str, Any] | None,
    footnote_defs: list[tuple[str, str]] | None,
    active_footnotes: set[str] | None = None,
    include_colors: bool = False,
) -> str:
    """Convert a footnote reference to a markdown footnote marker.

    Also collects the definition text with the reference's suggestion markers
    for appending at the end.
    """
    fn_id = ref.get("footnoteId", "")
    if not fn_id:
        return ""
    if footnotes_meta and footnote_defs is not None and fn_id in footnotes_meta:
        # Avoid duplicates if the same footnote is referenced multiple times.
        existing_ids = {fid for fid, _ in footnote_defs}
        if fn_id not in existing_ids:
            next_active = set(active_footnotes or ())
            if fn_id not in next_active:
                next_active.add(fn_id)
                fn_content = footnotes_meta[fn_id].get("content", [])
                fn_text = _convert_footnote_content(
                    fn_content,
                    footnotes_meta=footnotes_meta,
                    inline_objects=inline_objects,
                    footnote_defs=footnote_defs,
                    active_footnotes=next_active,
                    include_colors=include_colors,
                )
                footnote_defs.append((fn_id, _mark_suggested(fn_text, ref)))
    return f"[^{fn_id}]"


def _convert_footnote_content(
    content: list[dict[str, Any]],
    *,
    footnotes_meta: dict[str, Any] | None,
    inline_objects: dict[str, Any] | None,
    footnote_defs: list[tuple[str, str]] | None,
    active_footnotes: set[str] | None = None,
    include_colors: bool = False,
) -> str:
    """Convert footnote content with the same inline handling as body paragraphs."""
    parts: list[str] = []
    for element in content:
        if "paragraph" in element:
            text = _convert_paragraph_text(
                element["paragraph"],
                footnotes_meta=footnotes_meta,
                inline_objects=inline_objects,
                footnote_defs=footnote_defs,
                active_footnotes=active_footnotes,
                include_colors=include_colors,
            )
            if text.strip():
                parts.append(text.strip())
        elif "table" in element:
            table_text = _convert_table(
                element["table"],
                footnotes_meta=footnotes_meta,
                inline_objects=inline_objects,
                footnote_defs=footnote_defs,
                active_footnotes=active_footnotes,
                include_colors=include_colors,
            )
            if table_text.strip():
                parts.append(table_text.replace("\n", " "))
    return " ".join(parts)


def _convert_auto_text(auto_text: dict[str, Any]) -> str:
    """Convert an autoText element (e.g. page number) to a placeholder."""
    text_type = auto_text.get("type", "")
    if text_type == "PAGE_NUMBER":
        return "[Page #]"
    if text_type == "PAGE_COUNT":
        return "[Page Count]"
    return ""


def _convert_equation(equation: dict[str, Any]) -> str:
    """Convert an equation element to markdown."""
    # The Docs API does not expose equation content as text — only the
    # suggestedInsertionIds / suggestedDeletionIds are available.
    return "[Equation]"


def _apply_text_style(
    text: str, style: dict[str, Any], skip_strikethrough: bool = False
) -> str:
    """Apply markdown formatting based on text style, excluding links."""
    font_family = style.get("weightedFontFamily", {}).get("fontFamily", "")
    if font_family in MONO_FONTS:
        delimiter = "`" * (
            max((len(run) for run in re.findall(r"`+", text)), default=0) + 1
        )
        # CommonMark removes one surrounding space pair except for all-space
        # content. Padding also keeps edge backticks separate from delimiters.
        if (
            text.startswith("`")
            or text.endswith("`")
            or (text.startswith(" ") and text.endswith(" ") and text.strip(" "))
        ):
            text = f" {text} "
        text = f"{delimiter}{text}{delimiter}"

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

    return text


def _text_run_color(elements: list[dict[str, Any]], index: int) -> str | None:
    """Return the explicit RGB foreground color of the run at ``index`` as ``#rrggbb``.

    Theme colors have no fixed hex value and return None.
    """
    if not 0 <= index < len(elements):
        return None
    style = _effective_text_style(elements[index].get("textRun", {}))
    rgb = style.get("foregroundColor", {}).get("color", {}).get("rgbColor")
    if rgb is None:
        return None
    channels = (rgb.get(key, 0.0) for key in ("red", "green", "blue"))
    return "#" + "".join(f"{min(255, max(0, round(c * 255))):02x}" for c in channels)


def _apply_link(text: str, link: dict[str, Any]) -> str:
    """Render a link around already-styled label text."""
    link_target = resolve_link_target(link)
    if not text or not link_target:
        return text
    if link_target.kind == "url":
        destination = link_target.value
        if re.search(r"[\s()<>\\]", destination):
            # Angle brackets allow unbalanced parentheses. Encode whitespace
            # and characters that could escape or close the destination.
            destination = quote(destination, safe="/:?#[]@!$&'()*+,;=%")
            destination = f"<{destination}>"
        return f"[{text}]({destination})"
    if link_target.kind in ("heading", "bookmark"):
        tab_part = f", tab: {link_target.tab_id}" if link_target.tab_id else ""
        return f"{text} [{link_target.kind}: {link_target.value}{tab_part}]"
    if link_target.kind == "tab":
        return f"{text} [tab: {link_target.value}]"
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


def _convert_table(
    table: dict[str, Any],
    *,
    footnotes_meta: dict[str, Any] | None = None,
    inline_objects: dict[str, Any] | None = None,
    footnote_defs: list[tuple[str, str]] | None = None,
    active_footnotes: set[str] | None = None,
    include_colors: bool = False,
) -> str:
    """Convert a table element to markdown."""
    rows = table.get("tableRows", [])
    if not rows:
        return ""

    md_rows: list[str] = []
    for i, row in enumerate(rows):
        cells: list[str] = []
        for cell in row.get("tableCells", []):
            cell_text = _extract_cell_text(
                cell,
                footnotes_meta=footnotes_meta,
                inline_objects=inline_objects,
                footnote_defs=footnote_defs,
                active_footnotes=active_footnotes,
                include_colors=include_colors,
            )
            cells.append(cell_text)
        md_rows.append("| " + " | ".join(cells) + " |")

        if i == 0:
            sep = "| " + " | ".join("---" for _ in cells) + " |"
            md_rows.append(sep)

    return "\n".join(md_rows)


def _extract_cell_text(
    cell: dict[str, Any],
    *,
    footnotes_meta: dict[str, Any] | None = None,
    inline_objects: dict[str, Any] | None = None,
    footnote_defs: list[tuple[str, str]] | None = None,
    active_footnotes: set[str] | None = None,
    include_colors: bool = False,
) -> str:
    """Extract text from a table cell."""
    parts: list[str] = []
    for content_elem in cell.get("content", []):
        if "paragraph" in content_elem:
            text = _convert_paragraph_text(
                content_elem["paragraph"],
                footnotes_meta=footnotes_meta,
                inline_objects=inline_objects,
                footnote_defs=footnote_defs,
                active_footnotes=active_footnotes,
                include_colors=include_colors,
            )
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


def format_comments_appendix(
    comments: list[dict[str, Any]], title: str = "Comments"
) -> str:
    """Format comments as an appendix section with blockquoted anchors."""
    if not comments:
        return ""

    lines = [f"## {title}", ""]
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
