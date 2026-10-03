"""
Google Slides MCP Tools

This module provides MCP tools for interacting with Google Slides API.
"""

import asyncio
import base64
import json
import logging
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx
from fastmcp.tools import ToolResult
from mcp.types import ImageContent, TextContent, ToolAnnotations

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors
from core.comments import create_comment_tools
from gslides.slides_helpers import (
    validate_batch_update_requests,
    validate_insert_text_targets,
)

logger = logging.getLogger(__name__)


def _extract_shape_text(shape: Optional[Dict[str, Any]]) -> str:
    """Extract the full text content from a Slides shape, sorted by text-run start index.

    Returns an empty string if the shape has no text. The Slides API stores text
    as a tree of textElements containing textRuns; this walks that tree, sorts
    runs by startIndex, and joins their content. See:
    https://googleapis.github.io/google-api-python-client/docs/dyn/slides_v1.presentations.html#get
    """
    if not shape:
        return ""
    text = shape.get("text")
    if not text:
        return ""
    runs = []
    for text_element in text.get("textElements", []):
        text_run = text_element.get("textRun")
        if text_run and text_run.get("content"):
            runs.append((text_element.get("startIndex", 0), text_run["content"]))
    if not runs:
        return ""
    runs.sort(key=lambda r: r[0])
    return "".join(r[1] for r in runs)


def _iter_text_bearing_elements(
    elements: Optional[List[Dict[str, Any]]],
) -> Iterator[str]:
    """Yield full text strings from any shape or table cell with non-empty text,
    descending recursively into elementGroup.children so grouped shapes are not
    skipped.
    """
    for element in elements or []:
        if "shape" in element:
            full_text = _extract_shape_text(element["shape"])
            if full_text:
                yield full_text
        elif "table" in element:
            for row in element["table"].get("tableRows", []):
                for cell in row.get("tableCells", []):
                    cell_text = _extract_shape_text(cell)
                    if cell_text:
                        yield cell_text
        elif "elementGroup" in element:
            children = element["elementGroup"].get("children", [])
            yield from _iter_text_bearing_elements(children)


def _describe_geometry(element: Dict[str, Any], indent: str) -> Optional[str]:
    """Render a page element's placement in raw EMU, or None if it has none.

    Slides stores placement as a `transform` (translate plus scale) applied to an
    intrinsic `size`, and `batch_update_presentation` writes in those same terms,
    so both are reported unrounded rather than pre-multiplied.
    """
    transform = element.get("transform") or {}
    size = element.get("size") or {}
    if not transform and not size:
        return None

    parts: List[str] = []
    if transform:
        unit = transform.get("unit", "EMU")
        parts.append(
            f"position: x={transform.get('translateX', 0)} "
            f"y={transform.get('translateY', 0)} {unit}"
        )
        scale_x = transform.get("scaleX", 1)
        scale_y = transform.get("scaleY", 1)
        if scale_x != 1 or scale_y != 1:
            parts.append(f"scale: x={scale_x} y={scale_y}")
        shear_x = transform.get("shearX", 0)
        shear_y = transform.get("shearY", 0)
        if shear_x or shear_y:
            parts.append(f"shear: x={shear_x} y={shear_y}")
    if size:
        width = size.get("width", {})
        height = size.get("height", {})
        unit = width.get("unit") or height.get("unit") or "EMU"
        parts.append(
            f"size: {width.get('magnitude', 'Unknown')} x "
            f"{height.get('magnitude', 'Unknown')} {unit}"
        )
    return f"{indent}  {'   '.join(parts)}"


def _describe_color(color: Optional[Dict[str, Any]]) -> Optional[str]:
    """Render an OptionalColor/OpaqueColor as #RRGGBB or a theme color name."""
    if not color:
        return None
    opaque = color.get("opaqueColor", color)
    if "themeColor" in opaque:
        return opaque["themeColor"]
    rgb = opaque.get("rgbColor")
    if rgb is None:
        return None
    return "#{:02X}{:02X}{:02X}".format(
        round(rgb.get("red", 0) * 255),
        round(rgb.get("green", 0) * 255),
        round(rgb.get("blue", 0) * 255),
    )


def _describe_run_style(style: Dict[str, Any]) -> str:
    """Compact, stable rendering of a TextStyle. Only explicitly set fields show;
    anything absent is inherited from the placeholder, layout or master."""
    parts: List[str] = []
    family = (style.get("weightedFontFamily") or {}).get("fontFamily") or style.get(
        "fontFamily"
    )
    if family:
        parts.append(family)
    weight = (style.get("weightedFontFamily") or {}).get("weight")
    if weight and weight != 400:
        parts.append(f"w{weight}")
    size = style.get("fontSize") or {}
    if "magnitude" in size:
        parts.append(f"{size['magnitude']}{size.get('unit', 'PT').lower()}")
    for flag in ("bold", "italic", "underline", "strikethrough", "smallCaps"):
        if flag in style:
            parts.append(flag if style[flag] else f"{flag}=false")
    fg = _describe_color(style.get("foregroundColor"))
    if fg:
        parts.append(f"color={fg}")
    bg = _describe_color(style.get("backgroundColor"))
    if bg:
        parts.append(f"bg={bg}")
    if style.get("baselineOffset") not in (None, "NONE", "BASELINE_OFFSET_UNSPECIFIED"):
        parts.append(style["baselineOffset"].lower())
    if style.get("link"):
        parts.append("link")
    return " ".join(parts) if parts else "inherited"


def _describe_paragraph_style(style: Dict[str, Any]) -> str:
    parts: List[str] = []
    if style.get("alignment"):
        parts.append(f"align={style['alignment']}")
    for key, label in (
        ("indentStart", "indentStart"),
        ("indentFirstLine", "indentFirst"),
        ("spaceAbove", "spaceAbove"),
        ("spaceBelow", "spaceBelow"),
    ):
        dim = style.get(key) or {}
        if "magnitude" in dim:
            parts.append(f"{label}={dim['magnitude']}{dim.get('unit', 'PT').lower()}")
    if style.get("lineSpacing") is not None:
        parts.append(f"lineSpacing={style['lineSpacing']}%")
    return " ".join(parts)


def _describe_text_styles(text: Optional[Dict[str, Any]], indent: str) -> List[str]:
    """One line per paragraph (alignment, bullet) plus one line per run of
    uniform style, with a short excerpt so findings can be tied to copy.

    Consecutive runs with identical styling are merged, so a plain paragraph
    costs two lines regardless of how Slides split its runs.
    """
    if not text:
        return []
    lines: List[str] = []
    last_style: Optional[str] = None
    buffer = ""

    def flush() -> None:
        nonlocal buffer, last_style
        if last_style is not None and buffer.strip():
            excerpt = buffer.replace("\n", " ").strip()
            if len(excerpt) > 48:
                excerpt = excerpt[:45] + "..."
            lines.append(f'{indent}    run: {last_style} | "{excerpt}"')
        buffer = ""
        last_style = None

    for te in sorted(
        text.get("textElements", []), key=lambda t: t.get("startIndex", 0)
    ):
        if "paragraphMarker" in te:
            flush()
            marker = te["paragraphMarker"]
            desc = _describe_paragraph_style(marker.get("style") or {})
            bullet = " bullet" if marker.get("bullet") else ""
            lines.append(
                f"{indent}  para @{te.get('startIndex', 0)}-{te.get('endIndex', 0)}:"
                f"{(' ' + desc) if desc else ''}{bullet}"
            )
        elif "textRun" in te:
            run = te["textRun"]
            style = _describe_run_style(run.get("style") or {})
            if style != last_style:
                flush()
                last_style = style
            buffer += run.get("content", "")
        elif "autoText" in te:
            flush()
            lines.append(
                f"{indent}    autoText: {te['autoText'].get('type', 'UNKNOWN')}"
            )
    flush()
    return lines


def _describe_fill_state(
    prop: Dict[str, Any], fill: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Render a fill or outline by its PropertyState, or None when nothing is set.

    An absent state is the API default, RENDERED. A NOT_RENDERED property may
    still carry a color for child placeholders to inherit, and an INHERIT one
    takes its state from the parent placeholder, so neither color is shown as
    drawn.
    """
    if not prop:
        return None
    state = prop.get("propertyState", "RENDERED")
    if state == "NOT_RENDERED":
        return "none"
    color = _describe_color(((fill or {}).get("solidFill") or {}).get("color"))
    if state == "INHERIT":
        return f"inherit:{color}" if color else "inherit"
    return color


def _describe_shape_frame(shape: Dict[str, Any], indent: str) -> List[str]:
    """Placeholder linkage, autofit and fill/outline for a shape. Placeholder
    parent IDs matter because unset text styles inherit from them."""
    lines: List[str] = []
    placeholder = shape.get("placeholder")
    if placeholder:
        lines.append(
            f"{indent}  placeholder: {placeholder.get('type', 'UNKNOWN')}"
            f" index={placeholder.get('index', 0)}"
            f" parent={placeholder.get('parentObjectId', 'none')}"
        )
    props = shape.get("shapeProperties") or {}
    frame: List[str] = []
    autofit = props.get("autofit") or {}
    autofit_type = autofit.get("autofitType")
    if autofit_type:
        frame.append(f"autofit={autofit_type}")
    # Slides renders TEXT_AUTOFIT runs at fontSize * fontScale.
    if autofit_type == "TEXT_AUTOFIT" and "fontScale" in autofit:
        frame.append(f"fontScale={autofit['fontScale']}")
    if props.get("contentAlignment"):
        frame.append(f"vAlign={props['contentAlignment']}")
    background = props.get("shapeBackgroundFill") or {}
    outline = props.get("outline") or {}
    for label, prop, fill in (
        ("fill", background, background),
        ("outline", outline, outline.get("outlineFill")),
    ):
        state = _describe_fill_state(prop, fill)
        if state:
            frame.append(f"{label}={state}")
    if frame:
        lines.append(f"{indent}  frame: {' '.join(frame)}")
    return lines


def _describe_table_geometry(table: Dict[str, Any], indent: str) -> Optional[str]:
    """Column widths and row heights, labeled with the unit the API reports."""
    widths = [c.get("columnWidth") or {} for c in table.get("tableColumns", [])]
    heights = [r.get("rowHeight") or {} for r in table.get("tableRows", [])]
    if not widths and not heights:
        return None
    unit = next((d["unit"] for d in widths + heights if d.get("unit")), "EMU")

    def render(dims: List[Dict[str, Any]]) -> str:
        return ", ".join(
            f"{d.get('magnitude', '?')}"
            + (f" {d['unit']}" if d.get("unit", unit) != unit else "")
            for d in dims
        )

    return f"{indent}  columns ({unit}): {render(widths)}   rows ({unit}): {render(heights)}"


def _describe_table_cells(
    table: Dict[str, Any], indent: str, include_styles: bool
) -> List[str]:
    """Cell text by [row,col], with merged-cell spans and, optionally, styles."""
    lines: List[str] = []
    for r, row in enumerate(table.get("tableRows", [])):
        for c, cell in enumerate(row.get("tableCells", [])):
            loc = cell.get("location") or {}
            ri, ci = loc.get("rowIndex", r), loc.get("columnIndex", c)
            content = _extract_shape_text(cell).strip()
            span = ""
            if cell.get("rowSpan", 1) != 1 or cell.get("columnSpan", 1) != 1:
                span = f" span={cell.get('rowSpan', 1)}x{cell.get('columnSpan', 1)}"
            if content or span:
                shown = content.replace("\n", " / ")
                lines.append(f'{indent}  cell [{ri},{ci}]{span}: "{shown}"')
            if include_styles:
                lines.extend(_describe_text_styles(cell.get("text"), indent + "  "))
    return lines


def _describe_elements(
    elements: Optional[List[Dict[str, Any]]],
    indent: str = "  ",
    include_geometry: bool = False,
    include_styles: bool = False,
) -> List[str]:
    """Build descriptive lines for page elements, including text content for shapes.

    Recurses into elementGroup.children with deeper indentation so grouped shapes
    and their text are visible. Multi-line shape text is rendered as indented
    blockquote-style lines preserving paragraph structure.

    Non-shape elements surface the identifying metadata a caller needs to act on
    them in a follow-up batch_update: a linked ``sheetsChart`` exposes its source
    ``spreadsheetId``/``chartId`` (so the source data can be edited and the chart
    refreshed via ``refreshSheetsChart``), and images/videos expose their source
    or rendered content URL when available.
    """
    info: List[str] = []
    for element in elements or []:
        element_id = element.get("objectId", "Unknown")
        header_line = len(info)
        if "shape" in element:
            shape_type = element["shape"].get("shapeType", "Unknown")
            full_text = _extract_shape_text(element["shape"])
            if full_text:
                lines = [
                    line.rstrip() for line in full_text.split("\n") if line.strip()
                ]
                if len(lines) == 1:
                    info.append(
                        f'{indent}Shape: ID {element_id}, Type: {shape_type}, Text: "{lines[0]}"'
                    )
                else:
                    info.append(
                        f"{indent}Shape: ID {element_id}, Type: {shape_type}, Text:"
                    )
                    info.extend(f"{indent}  > {line}" for line in lines)
            else:
                info.append(f"{indent}Shape: ID {element_id}, Type: {shape_type}")
            if include_styles:
                info.extend(_describe_shape_frame(element["shape"], indent))
                info.extend(_describe_text_styles(element["shape"].get("text"), indent))
        elif "table" in element:
            table = element["table"]
            rows = table.get("rows", 0)
            cols = table.get("columns", 0)
            info.append(f"{indent}Table: ID {element_id}, Size: {rows}x{cols}")
            if include_geometry:
                table_geometry = _describe_table_geometry(table, indent)
                if table_geometry:
                    info.append(table_geometry)
            if include_geometry or include_styles:
                info.extend(_describe_table_cells(table, indent, include_styles))
        elif "line" in element:
            line_type = element["line"].get("lineType", "Unknown")
            info.append(f"{indent}Line: ID {element_id}, Type: {line_type}")
        elif "sheetsChart" in element:
            chart = element["sheetsChart"]
            info.append(
                f"{indent}SheetsChart: ID {element_id}, "
                f"SpreadsheetID {chart.get('spreadsheetId', 'Unknown')}, "
                f"ChartID {chart.get('chartId', 'Unknown')}"
            )
        elif "image" in element:
            image = element["image"]
            source = image.get("sourceUrl")
            if source:
                info.append(f"{indent}Image: ID {element_id}, Source: {source}")
            else:
                content_url = image.get("contentUrl")
                if content_url:
                    info.append(
                        f"{indent}Image: ID {element_id}, ContentURL: {content_url}"
                    )
                else:
                    info.append(f"{indent}Image: ID {element_id}, Source: Unknown")
        elif "video" in element:
            video = element["video"]
            info.append(
                f"{indent}Video: ID {element_id}, "
                f"Source: {video.get('source', 'Unknown')}, VideoID: {video.get('id', 'Unknown')}"
            )
        elif "wordArt" in element:
            rendered = element["wordArt"].get("renderedText", "")
            if rendered:
                info.append(f'{indent}WordArt: ID {element_id}, Text: "{rendered}"')
            else:
                info.append(f"{indent}WordArt: ID {element_id}")
        elif "elementGroup" in element:
            children = element["elementGroup"].get("children", [])
            info.append(f"{indent}Group: ID {element_id}, Children: {len(children)}")
            info.extend(
                _describe_elements(
                    children, indent + "  ", include_geometry, include_styles
                )
            )
        else:
            info.append(f"{indent}Element: ID {element_id}, Type: Unknown")

        if include_geometry:
            geometry = _describe_geometry(element, indent)
            if geometry:
                # Sits directly under the element's own line, above any text
                # lines or nested children.
                info.insert(header_line + 1, geometry)
    return info


def _speaker_notes_shape(slide: Dict[str, Any]) -> Tuple[Optional[str], str]:
    """Return ``(speaker_notes_object_id, current_notes_text)`` for a slide resource.

    A slide's speaker notes live in the single BODY placeholder shape on its notes
    page, identified by ``notesProperties.speakerNotesObjectId``. Only that shape's
    text is writable; the rest of the notes page and the notes master are read-only.
    The shape itself is occasionally absent for slides that have never had notes, in
    which case the ID resolves but no text element exists yet. See:
    https://developers.google.com/slides/api/guides/notes
    """
    notes_page = slide.get("slideProperties", {}).get("notesPage", {})
    notes_object_id = notes_page.get("notesProperties", {}).get("speakerNotesObjectId")
    if not notes_object_id:
        return None, ""
    for element in notes_page.get("pageElements", []):
        if element.get("objectId") == notes_object_id:
            return notes_object_id, _extract_shape_text(element.get("shape"))
    return notes_object_id, ""


def _describe_speaker_notes(slide: Dict[str, Any], indent: str = "    ") -> List[str]:
    """Build lines describing a slide's speaker notes shape ID and current notes text."""
    notes_object_id, notes_text = _speaker_notes_shape(slide)
    if not notes_object_id:
        return [f"{indent}Speaker Notes: none (slide has no notes placeholder)"]
    lines = [line.rstrip() for line in notes_text.split("\n") if line.strip()]
    header = f"{indent}Speaker Notes Shape ID: {notes_object_id}"
    if not lines:
        return [f"{header}, Notes: empty"]
    return [f"{header}, Notes:"] + [f"{indent}  > {line}" for line in lines]


@server.tool(
    title="Create Presentation",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("create_presentation", service_type="slides")
@require_google_service("slides", "slides")
async def create_presentation(
    service, user_google_email: str, title: str = "Untitled Presentation"
) -> str:
    """
    Create a new Google Slides presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        title (str): The title for the new presentation. Defaults to "Untitled Presentation".

    Returns:
        str: Details about the created presentation including ID and URL.
    """
    logger.info(
        f"[create_presentation] Invoked. Email: '{user_google_email}', title_len={len(title)}"
    )

    body = {"title": title}

    result = await asyncio.to_thread(service.presentations().create(body=body).execute)

    presentation_id = result.get("presentationId")
    presentation_url = f"https://docs.google.com/presentation/d/{presentation_id}/edit"

    confirmation_message = f"""Presentation Created Successfully for {user_google_email}:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: {presentation_url}
- Slides: {len(result.get("slides", []))} slide(s) created"""

    logger.info(f"Presentation created successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Get Presentation",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_presentation", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_presentation(
    service,
    user_google_email: str,
    presentation_id: str,
    include_speaker_notes: bool = False,
    include_geometry: bool = False,
    include_styles: bool = False,
    raw: bool = False,
) -> str:
    """
    Get details about a Google Slides presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation to retrieve.
        include_speaker_notes (bool): Also report each slide's speaker (presenter)
            notes and the object ID of the shape holding them. Pass True when you
            need to read or edit notes: that shape ID is the only valid target for
            insertText/deleteText on notes, and batch_update_presentation writes
            notes by deleting the shape's existing text and inserting new text.
            Defaults to False.
        include_geometry (bool): Also list each slide's elements with their
            placement - transform (translate, and scale/shear when not identity)
            and intrinsic size, in raw EMU. Set this when adding slides to an
            existing deck so new elements can match its established margins and
            content width. Defaults to False.
        include_styles (bool): Also list each slide's elements with text styling
            (see get_page). Defaults to False.
        raw (bool): Return the complete Slides API Presentation resource as JSON
            (slides, layouts, masters, notes, every element and style) instead
            of the summary. Nothing is filtered, so it can be large; prefer
            get_page with raw=True for a single slide. Overrides the other flags.

    Returns:
        str: Details about the presentation including title, slides count, and metadata.
    """
    logger.info(
        f"[get_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}', Notes: {include_speaker_notes}"
    )

    result = await asyncio.to_thread(
        service.presentations().get(presentationId=presentation_id).execute
    )

    if raw:
        return json.dumps(result, ensure_ascii=False)

    title = result.get("title", "Untitled")
    slides = result.get("slides", [])
    page_size = result.get("pageSize", {})

    slides_info = []
    for i, slide in enumerate(slides, 1):
        slide_id = slide.get("objectId", "Unknown")
        page_elements = slide.get("pageElements", [])

        # Collect text from the slide, recursing into elementGroup.children so
        # grouped shapes (common for layout templates) are not skipped. The
        # Slides API JSON structure is documented at:
        # https://googleapis.github.io/google-api-python-client/docs/dyn/slides_v1.presentations.html#get
        slide_text = ""
        try:
            texts_from_elements = list(_iter_text_bearing_elements(page_elements))

            # cleanup text we collected
            slide_text = "\n".join(texts_from_elements)
            slide_text_rows = slide_text.split("\n")
            slide_text_rows = [row for row in slide_text_rows if len(row.strip()) > 0]
            if slide_text_rows:
                slide_text_rows = ["    > " + row for row in slide_text_rows]
                slide_text = "\n" + "\n".join(slide_text_rows)
            else:
                slide_text = ""
        except Exception as e:
            logger.warning(f"Failed to extract text from the slide {slide_id}: {e}")
            slide_text = f"<failed to extract text: {type(e)}, {e}>"

        slides_info.append(
            f"  Slide {i}: ID {slide_id}, {len(page_elements)} element(s), text: {slide_text if slide_text else 'empty'}"
        )
        if include_geometry or include_styles:
            slides_info.extend(
                _describe_elements(
                    page_elements,
                    "    ",
                    include_geometry=include_geometry,
                    include_styles=include_styles,
                )
            )
        # The unfiltered presentations.get response already carries each slide's
        # notesPage, so reporting notes costs no extra API call.
        if include_speaker_notes:
            slides_info.extend(_describe_speaker_notes(slide))

    confirmation_message = f"""Presentation Details for {user_google_email}:
- Title: {title}
- Presentation ID: {presentation_id}
- URL: https://docs.google.com/presentation/d/{presentation_id}/edit
- Total Slides: {len(slides)}
- Page Size: {page_size.get("width", {}).get("magnitude", "Unknown")} x {page_size.get("height", {}).get("magnitude", "Unknown")} {page_size.get("width", {}).get("unit", "")}

Slides Breakdown:
{chr(10).join(slides_info) if slides_info else "  No slides found"}"""

    logger.info(f"Presentation retrieved successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Batch Update Presentation",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
@handle_http_errors("batch_update_presentation", service_type="slides")
@require_google_service("slides", "slides")
async def batch_update_presentation(
    service,
    user_google_email: str,
    presentation_id: str,
    requests: List[Dict[str, Any]],
) -> str:
    """
    Apply batch updates to a Google Slides presentation.

    Important:
        Each request object must contain exactly one supported Slides request
        type, such as createSlide, createShape, insertText, updateTextStyle,
        createImage, or deleteObject.

        insertText.objectId must be a text-capable shape or table object ID,
        not a slide/page object ID. To add text to a slide, create a text box
        or shape first with createShape, set elementProperties.pageObjectId to
        the slide ID, and then insertText into that shape objectId. To edit
        existing text, call get_page and use a Shape or Table element ID.

        To write speaker (presenter) notes, call get_presentation with
        include_speaker_notes=True to get the slide's speaker notes shape ID,
        then target that ID: deleteText with textRange {"type": "ALL"} to clear
        the existing notes (omit this when they are already empty, which errors),
        followed by insertText at insertionIndex 0. The notes page ID is not a
        valid target.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation to update.
        requests (List[Dict[str, Any]]): List of update requests to apply.

    Returns:
        str: Details about the batch update operation results.
    """
    logger.info(
        f"[batch_update_presentation] Invoked. Email: '{user_google_email}', ID: '{presentation_id}', Requests: {len(requests)}"
    )

    validate_batch_update_requests(requests)
    await validate_insert_text_targets(service, presentation_id, requests)

    body = {"requests": requests}

    result = await asyncio.to_thread(
        service.presentations()
        .batchUpdate(presentationId=presentation_id, body=body)
        .execute
    )

    replies = result.get("replies", [])

    confirmation_message = f"""Batch Update Completed for {user_google_email}:
- Presentation ID: {presentation_id}
- URL: https://docs.google.com/presentation/d/{presentation_id}/edit
- Requests Applied: {len(requests)}
- Replies Received: {len(replies)}"""

    if replies:
        confirmation_message += "\n\nUpdate Results:"
        for i, reply in enumerate(replies, 1):
            if "createSlide" in reply:
                slide_id = reply["createSlide"].get("objectId", "Unknown")
                confirmation_message += (
                    f"\n  Request {i}: Created slide with ID {slide_id}"
                )
            elif "createShape" in reply:
                shape_id = reply["createShape"].get("objectId", "Unknown")
                confirmation_message += (
                    f"\n  Request {i}: Created shape with ID {shape_id}"
                )
            else:
                confirmation_message += f"\n  Request {i}: Operation completed"

    logger.info(f"Batch update completed successfully for {user_google_email}")
    return confirmation_message


@server.tool(
    title="Get Page",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_page", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_page(
    service,
    user_google_email: str,
    presentation_id: str,
    page_object_id: str,
    include_geometry: bool = False,
    include_styles: bool = False,
    raw: bool = False,
) -> str:
    """
    Get details about a specific page (slide) in a presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide to retrieve.
        include_geometry (bool): Also report each element's placement - its
            transform (translate, and scale/shear when not identity) and intrinsic
            size, in raw EMU. Set this when adding elements to an existing deck:
            it is the only way to discover the deck's margins, gutters and content
            width, which Slides exposes nowhere else, and it reports the same terms
            batch_update_presentation writes. Defaults to False to keep the
            default output's token cost unchanged. Tables also report column
            widths, row heights and cell text.
        include_styles (bool): Also report text styling: per paragraph
            (alignment, indents, spacing, bullets) and per run of uniform style
            (font family, weight, size, bold/italic, color), each with a short
            excerpt; plus placeholder linkage, autofit, fill and outline for
            shapes (fill=none / outline=none when explicitly not rendered),
            and the text styling of every table cell. Only explicitly set
            values appear; anything else is inherited from the placeholder
            named in the output. Use for proofing: font consistency, color
            use, alignment.
        raw (bool): Return the complete Slides API Page resource as JSON instead
            of the summary. Nothing is filtered. Use when the summary does not
            carry a field you need. Overrides the other flags.

    Returns:
        str: Details about the specific page including elements and layout.
    """
    logger.info(
        f"[get_page] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .get(presentationId=presentation_id, pageObjectId=page_object_id)
        .execute
    )

    if raw:
        logger.info(f"Raw page returned for {user_google_email}")
        return json.dumps(result, ensure_ascii=False)

    page_type = result.get("pageType", "Unknown")
    page_elements = result.get("pageElements", [])

    # Walk pageElements recursively, surfacing text content from shapes and
    # descending into elementGroup.children so grouped shapes are not hidden.
    # This is what makes the documented "call get_page and use a Shape or Table
    # element ID" workflow in batch_update_presentation actually viable for
    # text that lives inside a Group.
    elements_info = _describe_elements(
        page_elements,
        include_geometry=include_geometry,
        include_styles=include_styles,
    )

    confirmation_message = f"""Page Details for {user_google_email}:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Page Type: {page_type}
- Total Elements: {len(page_elements)}

Page Elements:
{chr(10).join(elements_info) if elements_info else "  No elements found"}"""

    logger.info(f"Page retrieved successfully for {user_google_email}")
    return confirmation_message


async def _fetch_thumbnail_image(url: str) -> Optional[ImageContent]:
    """Download a rendered thumbnail, or return None if it cannot be retrieved."""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # The URL grants the requester's access to anyone holding it, so it
        # stays out of the log.
        logger.warning(
            f"[get_page_thumbnail] Inline fetch failed: {type(exc).__name__}"
        )
        return None
    mime_type = response.headers.get("content-type", "").split(";")[0].strip()
    if not mime_type.startswith("image/"):
        logger.warning("[get_page_thumbnail] Inline fetch returned a non-image")
        return None
    return ImageContent(
        type="image",
        data=base64.b64encode(response.content).decode("ascii"),
        mimeType=mime_type,
    )


@server.tool(
    title="Get Page Thumbnail",
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("get_page_thumbnail", is_read_only=True, service_type="slides")
@require_google_service("slides", "slides_read")
async def get_page_thumbnail(
    service,
    user_google_email: str,
    presentation_id: str,
    page_object_id: str,
    thumbnail_size: str = "MEDIUM",
    inline: bool = False,
) -> str:
    """
    Generate a thumbnail URL for a specific page (slide) in a presentation.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        page_object_id (str): The object ID of the page/slide.
        thumbnail_size (str): Size of thumbnail ("LARGE", "MEDIUM", "SMALL"). Defaults to "MEDIUM".
        inline (bool): Also return the rendered PNG itself as image content, so
            a multimodal client can look at the slide without fetching the URL.
            The URL is a short-lived googleusercontent link that sandboxed or
            egress-restricted clients often cannot reach. If the image cannot
            be fetched, the URL-only result is returned with a note saying so.
            Defaults to False.

    Returns:
        str: URL to the generated thumbnail image.
    """
    logger.info(
        f"[get_page_thumbnail] Invoked. Email: '{user_google_email}', Presentation: '{presentation_id}', Page: '{page_object_id}', Size: '{thumbnail_size}'"
    )

    result = await asyncio.to_thread(
        service.presentations()
        .pages()
        .getThumbnail(
            presentationId=presentation_id,
            pageObjectId=page_object_id,
            thumbnailProperties_thumbnailSize=thumbnail_size,
            thumbnailProperties_mimeType="PNG",
        )
        .execute
    )

    thumbnail_url = result.get("contentUrl", "")

    confirmation_message = f"""Thumbnail Generated for {user_google_email}:
- Presentation ID: {presentation_id}
- Page ID: {page_object_id}
- Thumbnail Size: {thumbnail_size}
- Thumbnail URL: {thumbnail_url}

You can view or download the thumbnail using the provided URL."""

    logger.info(f"Thumbnail generated successfully for {user_google_email}")
    if not inline or not thumbnail_url:
        return confirmation_message

    image = await _fetch_thumbnail_image(thumbnail_url)
    if image is None:
        # The URL may still be reachable by the caller, so a failed fetch
        # degrades to the URL-only result instead of failing the call.
        return (
            f"{confirmation_message}\n\n"
            "Inline image unavailable: the thumbnail could not be fetched."
        )
    # structured_content mirrors the plain-string result so clients that
    # validate against the tool's output schema still get what they expect.
    return ToolResult(
        content=[TextContent(type="text", text=confirmation_message), image],
        structured_content={"result": confirmation_message},
    )


@server.tool(
    title="Set Speaker Notes",
    annotations=ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
@handle_http_errors("set_speaker_notes", service_type="slides")
@require_google_service("slides", "slides")
async def set_speaker_notes(
    service,
    user_google_email: str,
    presentation_id: str,
    notes_text: str,
    slide_object_id: str = "",
    slide_index: int = -1,
    mode: str = "replace",
) -> str:
    """
    Write speaker notes to a slide.

    Resolves the slide's speakerNotesObjectId internally so callers do not
    need to fetch it. Identify the slide by either slide_object_id or
    slide_index (0-based). Exactly one must be provided.

    Args:
        user_google_email (str): The user's Google email address. Required.
        presentation_id (str): The ID of the presentation.
        notes_text (str): Text to write into the speaker notes.
        slide_object_id (str): Object ID of the slide. Mutually exclusive with slide_index.
        slide_index (int): 0-based slide index. Mutually exclusive with slide_object_id.
        mode (str): "replace" (default) clears existing notes first; "append" adds to end.

    Returns:
        str: Confirmation with slide ID, notes shape ID, and char count written.
    """
    from core.utils import UserInputError

    logger.info(
        f"[set_speaker_notes] Email: '{user_google_email}', Pres: '{presentation_id}', "
        f"slide_object_id='{slide_object_id}', slide_index={slide_index}, mode='{mode}'"
    )

    has_id = bool(slide_object_id)
    has_idx = slide_index >= 0
    if has_id == has_idx:
        raise UserInputError(
            "Provide exactly one of slide_object_id or slide_index (>=0)."
        )
    if mode not in ("replace", "append"):
        raise UserInputError("mode must be 'replace' or 'append'.")

    fields = (
        "slides(objectId,"
        "slideProperties/notesPage("
        "objectId,"
        "notesProperties/speakerNotesObjectId,"
        "pageElements(objectId,shape(text(textElements(textRun(content)))))"
        "))"
    )

    pres = await asyncio.to_thread(
        service.presentations()
        .get(presentationId=presentation_id, fields=fields)
        .execute
    )
    slides = pres.get("slides", [])
    if not slides:
        raise UserInputError("Presentation has no slides.")

    target = None
    if has_id:
        for s in slides:
            if s.get("objectId") == slide_object_id:
                target = s
                break
        if target is None:
            raise UserInputError(
                f"slide_object_id '{slide_object_id}' not found in presentation."
            )
    else:
        if slide_index >= len(slides):
            raise UserInputError(
                f"slide_index {slide_index} out of range (presentation has {len(slides)} slides)."
            )
        target = slides[slide_index]

    slide_id = target.get("objectId")
    notes_page = target.get("slideProperties", {}).get("notesPage", {}) or {}
    notes_obj_id = (
        notes_page.get("notesProperties", {}).get("speakerNotesObjectId")
    )
    if not notes_obj_id:
        raise UserInputError(
            f"Could not resolve speakerNotesObjectId for slide '{slide_id}'."
        )

    existing_len = 0
    for pe in notes_page.get("pageElements", []):
        if pe.get("objectId") != notes_obj_id:
            continue
        text = pe.get("shape", {}).get("text", {}) or {}
        for te in text.get("textElements", []):
            tr = te.get("textRun") or {}
            content = tr.get("content") or ""
            existing_len += len(content)

    requests: List[Dict[str, Any]] = []
    if mode == "replace" and existing_len > 0:
        requests.append(
            {"deleteText": {"objectId": notes_obj_id, "textRange": {"type": "ALL"}}}
        )

    insertion_index = 0
    if mode == "append" and existing_len > 0:
        insertion_index = max(existing_len - 1, 0)

    requests.append(
        {
            "insertText": {
                "objectId": notes_obj_id,
                "insertionIndex": insertion_index,
                "text": notes_text,
            }
        }
    )

    await asyncio.to_thread(
        service.presentations()
        .batchUpdate(presentationId=presentation_id, body={"requests": requests})
        .execute
    )

    return (
        f"Speaker notes {mode}d for {user_google_email}:\n"
        f"- Presentation ID: {presentation_id}\n"
        f"- Slide ID: {slide_id}\n"
        f"- Notes Shape ID: {notes_obj_id}\n"
        f"- Chars written: {len(notes_text)}\n"
        f"- URL: https://docs.google.com/presentation/d/{presentation_id}/edit"
    )


# Create comment management tools for slides
_comment_tools = create_comment_tools("presentation", "presentation_id")
list_presentation_comments = _comment_tools["list_comments"]
manage_presentation_comment = _comment_tools["manage_comment"]

# Aliases for backwards compatibility and intuitive naming
list_slide_comments = list_presentation_comments
manage_slide_comment = manage_presentation_comment
