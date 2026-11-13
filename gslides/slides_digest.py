"""
Google Slides digests: the plain-text views ``get_presentation`` returns for
the ``deck``, ``layouts`` and ``page`` actions.

``deck`` lists slides with their titles, layouts and element counts, paged by
slide. ``page`` lists every element on one slide with its ID and all its text,
and no presentation detail. ``layouts`` lists a deck's layouts and the
placeholders each provides.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from gslides.slides_markup import element_kind, iter_elements, placeholder_key

EMU_PER_PT = 12700
LAYOUT_CAP = 25
DECK_PAGE_SLIDES = 100
_TITLE_TYPES = ("TITLE", "CENTERED_TITLE")


def _paragraphs(text: Optional[Dict[str, Any]]) -> List[Tuple[Optional[int], str]]:
    """(bullet level or None, paragraph text) for a text body, empties dropped."""
    out: List[Tuple[Optional[int], str]] = []
    level: Optional[int] = None
    buf: List[str] = []

    def flush() -> None:
        line = "".join(buf).replace("\u000b", " ").strip()
        if line:
            out.append((level, line))

    for te in (text or {}).get("textElements", []):
        if "paragraphMarker" in te:
            flush()
            buf = []
            bullet = te["paragraphMarker"].get("bullet")
            level = bullet.get("nestingLevel", 0) if bullet is not None else None
        elif "textRun" in te:
            buf.append(te["textRun"].get("content", "").rstrip("\n"))
        elif "autoText" in te:
            buf.append(te["autoText"].get("content", ""))
    flush()
    return out


def _notes(
    slide: Dict[str, Any],
) -> Tuple[Optional[str], List[Tuple[Optional[int], str]]]:
    notes_page = (slide.get("slideProperties") or {}).get("notesPage") or {}
    notes_id = (notes_page.get("notesProperties") or {}).get("speakerNotesObjectId")
    for el in notes_page.get("pageElements", []):
        if el.get("objectId") == notes_id:
            return notes_id, _paragraphs((el.get("shape") or {}).get("text"))
    return notes_id, []


def _largest_font(text: Optional[Dict[str, Any]]) -> float:
    sizes = [
        ((te["textRun"].get("style") or {}).get("fontSize") or {}).get("magnitude", 0)
        for te in (text or {}).get("textElements", [])
        if "textRun" in te and te["textRun"].get("content", "").strip()
    ]
    return max(sizes, default=0)


def slide_title(slide: Dict[str, Any]) -> Optional[str]:
    """Title placeholder text, else the text set in the largest font.

    Slides built without placeholders (HTML imports, decks made through this
    MCP) often put a small label above the headline, so the first text in
    z-order is not the title; the largest type is. Ties go to z-order.
    """
    elements = [el for el, _ in iter_elements(slide.get("pageElements", []))]
    for el in elements:
        ph = (el.get("shape") or {}).get("placeholder") or {}
        if ph.get("type") in _TITLE_TYPES:
            paras = _paragraphs(el["shape"].get("text"))
            if paras:
                return paras[0][1]
    best: Optional[str] = None
    best_size = -1.0
    for el in elements:
        text = (el.get("shape") or {}).get("text")
        paras = _paragraphs(text)
        if not paras:
            continue
        size = _largest_font(text)
        if size > best_size:
            best, best_size = paras[0][1], size
    return best


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _counts(slide: Dict[str, Any]) -> str:
    counts: Dict[str, int] = {}
    tables = []
    for el, _ in iter_elements(slide.get("pageElements", [])):
        kind = element_kind(el)
        if kind == "group":
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "table":
            tables.append(f"{el['table'].get('rows')}x{el['table'].get('columns')}")
    parts = []
    for kind, n in counts.items():
        part = _plural(n, kind)
        if kind == "table":
            part += f" ({', '.join(tables)})"
        parts.append(part)
    return ", ".join(parts) or "empty"


# --- layouts ----------------------------------------------------------------


def _layout_usage(pres: Dict[str, Any]) -> Dict[str, List[int]]:
    used: Dict[str, List[int]] = {}
    for n, slide in enumerate(pres.get("slides", []), 1):
        lid = (slide.get("slideProperties") or {}).get("layoutObjectId")
        used.setdefault(lid, []).append(n)
    return used


def _slide_list(numbers: List[int]) -> str:
    if not numbers:
        return "-"
    ranges, start, prev = [], numbers[0], numbers[0]
    for n in numbers[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        ranges.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    label = "slide" if len(numbers) == 1 else "slides"
    return f"{label} {', '.join(ranges)}"


def _layout_line(layout: Dict[str, Any], used: Dict[str, List[int]]) -> str:
    props = layout.get("layoutProperties") or {}
    kinds = [
        p for p in (placeholder_key(el) for el in layout.get("pageElements", [])) if p
    ]
    return (
        f"  {layout.get('objectId')}  {props.get('displayName', '')}  "
        f"{', '.join(kinds) or '-'}  {_slide_list(used.get(layout.get('objectId'), []))}"
    )


def _master_of_position(pres: Dict[str, Any], after: Optional[str]) -> Optional[str]:
    """The master add_slide resolves a layout against when inserting there."""
    slides = pres.get("slides", [])
    if not slides:
        masters = pres.get("masters", [])
        return masters[0].get("objectId") if masters else None
    if after == "start":
        anchor = slides[0]
    else:
        anchor = next((s for s in slides if s.get("objectId") == after), slides[-1])
    return (anchor.get("slideProperties") or {}).get("masterObjectId")


def format_layouts(
    pres: Dict[str, Any],
    after: Optional[str] = None,
    only_used: bool = False,
    used_by: Optional[Iterable[str]] = None,
) -> List[str]:
    """Layout lines grouped by master.

    ``only_used`` keeps layouts some slide uses; ``used_by`` narrows that to
    the given layout IDs (those used by one page of the deck).
    """
    used = _layout_usage(pres)
    wanted = set(used_by) if used_by is not None else set(used)
    masters = {m.get("objectId"): m for m in pres.get("masters", [])}
    only_master = _master_of_position(pres, after) if after else None
    by_master: Dict[str, List[Dict[str, Any]]] = {}
    for layout in pres.get("layouts", []):
        mid = (layout.get("layoutProperties") or {}).get("masterObjectId")
        if only_master and mid != only_master:
            continue
        if only_used and layout.get("objectId") not in wanted:
            continue
        by_master.setdefault(mid, []).append(layout)
    lines: List[str] = []
    for mid, layouts in by_master.items():
        if len(masters) > 1:
            name = (masters.get(mid, {}).get("masterProperties") or {}).get(
                "displayName", ""
            )
            lines.append(f"Master {mid}  {name}".rstrip())
        lines.extend(_layout_line(layout, used) for layout in layouts)
    return lines


# --- deck -------------------------------------------------------------------


def format_deck(
    pres: Dict[str, Any], offset: int = 0, limit: int = DECK_PAGE_SLIDES
) -> str:
    """Deck digest, ``limit`` slides from slide index ``offset``.

    Pages the way get_drive_file_content does: a note at the end gives the
    offset to call again with. The header's revision lets a reader paging
    through tell that the deck changed and start again from offset 0.
    """
    pid = pres.get("presentationId")
    size = pres.get("pageSize") or {}
    w = size.get("width", {}).get("magnitude", 0) / EMU_PER_PT
    h = size.get("height", {}).get("magnitude", 0) / EMU_PER_PT
    slides = pres.get("slides", [])
    total = len(slides)
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}.")
    if total and offset >= total:
        return f"[offset={offset:,} is past the last slide ({total:,} slides).]"
    shown = slides[offset : offset + limit]
    lines = [
        f"Presentation: {pres.get('title', '')}",
        f"ID: {pid}",
        f"URL: https://docs.google.com/presentation/d/{pid}/edit",
        f"Slides: {total}   Page size: {w:g} x {h:g} pt   Revision: {pres.get('revisionId', '-')}",
        "",
    ]
    for n, slide in enumerate(shown, offset + 1):
        _, notes = _notes(slide)
        chars = sum(len(t) for _, t in notes)
        layout = (slide.get("slideProperties") or {}).get("layoutObjectId")
        lines.append(
            f"{n}. {slide.get('objectId')}  {slide_title(slide) or '(no title)'}"
        )
        lines.append(
            f"   layout {layout}  {_counts(slide)}  notes: {f'{chars} chars' if chars else '-'}"
        )
    layouts = pres.get("layouts", [])
    paged = len(shown) < total
    lines.append("")
    if len(layouts) <= LAYOUT_CAP:
        lines.append("Layouts:")
        lines.extend(format_layouts(pres))
    else:
        # On a page of a long deck, the layouts this page's slides use: enough
        # for "another one like this", and bounded by the page size.
        page_layouts = {
            (s.get("slideProperties") or {}).get("layoutObjectId") for s in shown
        }
        listed = format_layouts(pres, only_used=True, used_by=page_layouts)
        lines.append(
            "Layouts used by these slides:" if paged else "Layouts used by slides:"
        )
        lines.extend(listed)
        count = sum(1 for line in listed if line.startswith("  "))
        masters = len(pres.get("masters", []))
        lines.append(
            f"+ {len(layouts) - count} more layouts in {_plural(masters, 'master')}: "
            'get_presentation(action="layouts")'
        )
    end = offset + len(shown)
    if paged:
        note = f"[Showing slides {offset + 1:,} to {end:,} of {total:,}"
        if end < total:
            note += f"; {total - end:,} more follow. Call again with offset={end} to read on"
        lines.extend(["", note + ".]"])
    return "\n".join(lines)


# --- page -------------------------------------------------------------------


def _element_lines(el: Dict[str, Any], indent: str) -> List[str]:
    kind = element_kind(el)
    oid = el.get("objectId")
    lines: List[str] = []
    if kind == "shape":
        shape = el["shape"]
        head = f"{indent}- shape {oid}  {shape.get('shapeType')}"
        ph = placeholder_key(el)
        if ph:
            head += f"  placeholder={ph}"
        lines.append(head)
        for level, text in _paragraphs(shape.get("text")):
            prefix = "" if level is None else "  " * level + "- "
            lines.append(f"{indent}  {prefix}{text}")
    elif kind == "table":
        table = el["table"]
        lines.append(
            f"{indent}- table {oid}  {table.get('rows')}x{table.get('columns')}"
        )
        for row in table.get("tableRows", []):
            for cell in row.get("tableCells", []):
                text = " / ".join(t for _, t in _paragraphs(cell.get("text")))
                if not text:
                    continue
                loc = cell.get("location", {})
                span = ""
                if cell.get("rowSpan", 1) > 1 or cell.get("columnSpan", 1) > 1:
                    span = f" span={cell.get('rowSpan', 1)}x{cell.get('columnSpan', 1)}"
                lines.append(
                    f"{indent}  [{loc.get('rowIndex', 0)},{loc.get('columnIndex', 0)}]{span} {text}"
                )
    elif kind == "image":
        image = el["image"]
        # contentUrl is left out: about 160 tokens each, it expires after 30
        # minutes and is tied to the caller's account. render_page_to_image
        # shows the image.
        head = f"{indent}- image {oid}"
        if image.get("sourceUrl"):
            head += f"  src={image['sourceUrl']}"
        lines.append(head)
    elif kind == "chart":
        chart = el["sheetsChart"]
        lines.append(
            f"{indent}- chart {oid}  spreadsheet={chart.get('spreadsheetId')}  chart={chart.get('chartId')}"
        )
    elif kind == "line":
        lines.append(
            f"{indent}- line {oid}  {el['line'].get('lineCategory', 'STRAIGHT')}"
        )
    elif kind == "group":
        children = el["elementGroup"].get("children", [])
        lines.append(f"{indent}- group {oid}  {_plural(len(children), 'element')}")
        for child in children:
            lines.extend(_element_lines(child, indent + "  "))
    elif kind == "wordArt":
        lines.append(
            f"{indent}- wordArt {oid}  {el['wordArt'].get('renderedText', '')}"
        )
    elif kind == "video":
        video = el["video"]
        lines.append(
            f"{indent}- video {oid}  {video.get('source')}  id={video.get('id')}"
        )
    else:
        lines.append(f"{indent}- {kind} {oid}")
    alt = " / ".join(t for t in (el.get("title"), el.get("description")) if t)
    if alt:
        lines.append(f"{indent}  alt: {alt}")
    return lines


def _inherited(pres: Optional[Dict[str, Any]], slide: Dict[str, Any]) -> Optional[str]:
    """Say what is visible but has no element ID on the slide."""
    parts = []
    background = (slide.get("pageProperties") or {}).get("pageBackgroundFill") or {}
    if background.get("propertyState") not in (None, "INHERIT", "NOT_RENDERED"):
        parts.append("the page background")
    if pres:
        props = slide.get("slideProperties") or {}
        drawn = 0
        for page in pres.get("layouts", []) + pres.get("masters", []):
            if page.get("objectId") in (
                props.get("layoutObjectId"),
                props.get("masterObjectId"),
            ):
                drawn += sum(
                    1 for el in page.get("pageElements", []) if not placeholder_key(el)
                )
        if drawn:
            parts.append(f"{_plural(drawn, 'element')} drawn by the layout and master")
    if not parts:
        return None
    return f"Also visible, with no element IDs here: {' and '.join(parts)}. render_page_to_image shows them."


def format_page(
    slide: Dict[str, Any],
    revision: Optional[str] = None,
    pres: Optional[Dict[str, Any]] = None,
) -> str:
    elements = slide.get("pageElements", [])
    lines = [
        f"page {slide.get('objectId')}  {slide.get('pageType', 'SLIDE')}  "
        f"{_plural(len(elements), 'element')}  revision {revision or slide.get('revisionId', '-')}",
        "",
    ]
    for el in elements:
        lines.extend(_element_lines(el, ""))
    notes_id, notes = _notes(slide)
    if notes_id:
        # The notes shape ID is the only valid target for writing notes, so it
        # is listed even when the notes are empty.
        lines.append("")
        lines.append(f"notes shape {notes_id}:" + ("" if notes else " (empty)"))
        lines.extend(
            f"  {'  ' * (lvl or 0)}{'- ' if lvl is not None else ''}{t}"
            for lvl, t in notes
        )
    inherited = _inherited(pres, slide)
    if inherited:
        lines.append("")
        lines.append(inherited)
    return "\n".join(lines)
