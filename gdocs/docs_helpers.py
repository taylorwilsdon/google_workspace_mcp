"""
Google Docs Helper Functions

This module provides utility functions for common Google Docs operations
to simplify the implementation of document editing tools.
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

VALID_NAMED_STYLE_TYPES = (
    "NORMAL_TEXT",
    "TITLE",
    "SUBTITLE",
    "HEADING_1",
    "HEADING_2",
    "HEADING_3",
    "HEADING_4",
    "HEADING_5",
    "HEADING_6",
)

VALID_SUGGESTIONS_VIEW_MODES = (
    "DEFAULT_FOR_CURRENT_ACCESS",
    "SUGGESTIONS_INLINE",
    "PREVIEW_SUGGESTIONS_ACCEPTED",
    "PREVIEW_WITHOUT_SUGGESTIONS",
)

VALID_TEXT_BASELINE_OFFSETS = (
    "NONE",
    "SUPERSCRIPT",
    "SUBSCRIPT",
)

VALID_PARAGRAPH_DIRECTIONS = (
    "LEFT_TO_RIGHT",
    "RIGHT_TO_LEFT",
)

VALID_PARAGRAPH_SPACING_MODES = (
    "NEVER_COLLAPSE",
    "COLLAPSE_LISTS",
)

VALID_DASH_STYLES = (
    "SOLID",
    "DOT",
    "DASH",
)

VALID_SECTION_TYPES = (
    "CONTINUOUS",
    "NEXT_PAGE",
)

VALID_CONTENT_DIRECTIONS = (
    "LEFT_TO_RIGHT",
    "RIGHT_TO_LEFT",
)

VALID_COLUMN_SEPARATOR_STYLES = (
    "NONE",
    "BETWEEN_EACH_COLUMN",
)

VALID_DOCUMENT_MODES = (
    "PAGES",
    "PAGELESS",
)

VALID_BULLET_PRESETS = (
    "BULLET_DISC_CIRCLE_SQUARE",
    "BULLET_DIAMONDX_ARROW3D_SQUARE",
    "BULLET_CHECKBOX",
    "BULLET_ARROW_DIAMOND_DISC",
    "BULLET_STAR_CIRCLE_SQUARE",
    "BULLET_ARROW3D_CIRCLE_SQUARE",
    "BULLET_LEFTTRIANGLE_DIAMOND_DISC",
    "BULLET_DIAMONDX_HOLLOWDIAMOND_SQUARE",
    "BULLET_DIAMOND_CIRCLE_SQUARE",
    "NUMBERED_DECIMAL_ALPHA_ROMAN",
    "NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS",
    "NUMBERED_DECIMAL_NESTED",
    "NUMBERED_UPPERALPHA_ALPHA_ROMAN",
    "NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL",
    "NUMBERED_ZERODECIMAL_ALPHA_ROMAN",
)


def validate_suggestions_view_mode(suggestions_view_mode: str) -> Optional[str]:
    """Return an error message when suggestions_view_mode is invalid."""
    if suggestions_view_mode in VALID_SUGGESTIONS_VIEW_MODES:
        return None

    return (
        "Error: suggestions_view_mode must be one of "
        f"{', '.join(VALID_SUGGESTIONS_VIEW_MODES)}, got '{suggestions_view_mode}'"
    )


def _build_dimension(value: float, unit: str = "PT") -> Dict[str, Any]:
    """Build a Google Docs Dimension object."""
    return {"magnitude": value, "unit": unit}


def _build_optional_color(color: Optional[str], param_name: str) -> Dict[str, Any]:
    """Build a Google Docs OptionalColor object."""
    rgb = _normalize_color(color, param_name)
    return {"color": {"rgbColor": rgb}}


def _build_location(
    index: Optional[int] = None,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
    end_of_segment: bool = False,
) -> Dict[str, Any]:
    """Build a location or endOfSegmentLocation object."""
    if end_of_segment:
        location: Dict[str, Any] = {}
        if segment_id:
            location["segmentId"] = segment_id
        if tab_id:
            location["tabId"] = tab_id
        return {"endOfSegmentLocation": location}

    if index is None:
        raise ValueError("index is required unless end_of_segment=True")

    location = {"index": index}
    if segment_id:
        location["segmentId"] = segment_id
    if tab_id:
        location["tabId"] = tab_id
    return {"location": location}


def _build_range(
    start_index: int,
    end_index: int,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a Google Docs Range object."""
    range_obj = {"startIndex": start_index, "endIndex": end_index}
    if segment_id:
        range_obj["segmentId"] = segment_id
    if tab_id:
        range_obj["tabId"] = tab_id
    return range_obj


def _build_tabs_criteria(tab_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Build Docs tabsCriteria for operations that support tab-scoped selection."""
    if not tab_id:
        return None
    return {"tabIds": [tab_id]}


def _normalize_color(
    color: Optional[str], param_name: str
) -> Optional[Dict[str, float]]:
    """
    Normalize a user-supplied color into Docs API rgbColor format.

    Supports only hex strings in the form "#RRGGBB".
    """
    if color is None:
        return None

    if not isinstance(color, str):
        raise ValueError(f"{param_name} must be a hex string like '#RRGGBB'")

    if len(color) != 7 or not color.startswith("#"):
        raise ValueError(f"{param_name} must be a hex string like '#RRGGBB'")

    hex_color = color[1:]
    if any(c not in "0123456789abcdefABCDEF" for c in hex_color):
        raise ValueError(f"{param_name} must be a hex string like '#RRGGBB'")

    r = int(hex_color[0:2], 16) / 255
    g = int(hex_color[2:4], 16) / 255
    b = int(hex_color[4:6], 16) / 255
    return {"red": r, "green": g, "blue": b}


def build_text_style(
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    strikethrough: bool = None,
    font_size: int = None,
    font_family: str = None,
    font_weight: int = None,
    text_color: str = None,
    background_color: str = None,
    link_url: str = None,
    clear_link: bool = None,
    baseline_offset: str = None,
    small_caps: bool = None,
) -> tuple[Dict[str, Any], list[str]]:
    """
    Build text style object for Google Docs API requests.

    Args:
        bold: Whether text should be bold
        italic: Whether text should be italic
        underline: Whether text should be underlined
        strikethrough: Whether text should be struck through
        font_size: Font size in points
        font_family: Font family name
        font_weight: Font weight (100-900 in steps of 100)
        text_color: Text color as hex string "#RRGGBB"
        background_color: Background (highlight) color as hex string "#RRGGBB"
        link_url: Hyperlink URL (http/https)
        clear_link: Remove hyperlink from the range when True
        baseline_offset: One of NONE, SUPERSCRIPT, SUBSCRIPT
        small_caps: Whether text should use small caps

    Returns:
        Tuple of (text_style_dict, list_of_field_names)
    """
    text_style = {}
    fields = []

    if bold is not None:
        text_style["bold"] = bold
        fields.append("bold")

    if italic is not None:
        text_style["italic"] = italic
        fields.append("italic")

    if underline is not None:
        text_style["underline"] = underline
        fields.append("underline")

    if strikethrough is not None:
        text_style["strikethrough"] = strikethrough
        fields.append("strikethrough")

    if font_size is not None:
        text_style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
        fields.append("fontSize")

    if font_family is not None or font_weight is not None:
        weighted_font_family: Dict[str, Any] = {}
        if font_family is not None:
            weighted_font_family["fontFamily"] = font_family
        if font_weight is not None:
            weighted_font_family["weight"] = font_weight
        text_style["weightedFontFamily"] = weighted_font_family
        fields.append("weightedFontFamily")

    if text_color is not None:
        rgb = _normalize_color(text_color, "text_color")
        text_style["foregroundColor"] = {"color": {"rgbColor": rgb}}
        fields.append("foregroundColor")

    if background_color is not None:
        rgb = _normalize_color(background_color, "background_color")
        text_style["backgroundColor"] = {"color": {"rgbColor": rgb}}
        fields.append("backgroundColor")

    if link_url is not None and clear_link:
        raise ValueError("link_url and clear_link cannot both be provided")

    if link_url is not None:
        text_style["link"] = {"url": link_url}
        fields.append("link")
    elif clear_link is True:
        fields.append("link")

    if baseline_offset is not None:
        baseline_offset_upper = baseline_offset.upper()
        if baseline_offset_upper not in VALID_TEXT_BASELINE_OFFSETS:
            raise ValueError(
                f"baseline_offset must be one of: {', '.join(VALID_TEXT_BASELINE_OFFSETS)}"
            )
        text_style["baselineOffset"] = baseline_offset_upper
        fields.append("baselineOffset")

    if small_caps is not None:
        text_style["smallCaps"] = small_caps
        fields.append("smallCaps")

    return text_style, fields


def build_paragraph_style(
    heading_level: int = None,
    alignment: str = None,
    line_spacing: float = None,
    indent_first_line: float = None,
    indent_start: float = None,
    indent_end: float = None,
    space_above: float = None,
    space_below: float = None,
    named_style_type: Optional[str] = None,
    direction: Optional[str] = None,
    keep_lines_together: Optional[bool] = None,
    keep_with_next: Optional[bool] = None,
    avoid_widow_and_orphan: Optional[bool] = None,
    page_break_before: Optional[bool] = None,
    spacing_mode: Optional[str] = None,
    shading_color: Optional[str] = None,
) -> tuple[Dict[str, Any], list[str]]:
    """
    Build paragraph style object for Google Docs API requests.

    Args:
        heading_level: Heading level 0-6 (0 = NORMAL_TEXT, 1-6 = HEADING_N)
        alignment: Text alignment - 'START', 'CENTER', 'END', or 'JUSTIFIED'
        line_spacing: Line spacing multiplier (1.0 = single, 2.0 = double)
        indent_first_line: First line indent in points
        indent_start: Left/start indent in points
        indent_end: Right/end indent in points
        space_above: Space above paragraph in points
        space_below: Space below paragraph in points
        named_style_type: Direct named style (TITLE, SUBTITLE, HEADING_1..6, NORMAL_TEXT).
                          Takes precedence over heading_level when both are provided.
        direction: Paragraph content direction - LEFT_TO_RIGHT or RIGHT_TO_LEFT
        keep_lines_together: Keep all lines of the paragraph together if possible
        keep_with_next: Keep paragraph with the next paragraph if possible
        avoid_widow_and_orphan: Avoid widows/orphans for the paragraph
        page_break_before: Always start paragraph on a new page
        spacing_mode: Paragraph spacing mode - NEVER_COLLAPSE or COLLAPSE_LISTS
        shading_color: Paragraph shading/background color as hex string "#RRGGBB"

    Returns:
        Tuple of (paragraph_style_dict, list_of_field_names)
    """
    paragraph_style = {}
    fields = []

    if named_style_type is not None:
        if named_style_type not in VALID_NAMED_STYLE_TYPES:
            raise ValueError(
                f"Invalid named_style_type '{named_style_type}'. "
                f"Must be one of: {', '.join(VALID_NAMED_STYLE_TYPES)}"
            )
        paragraph_style["namedStyleType"] = named_style_type
        fields.append("namedStyleType")
    elif heading_level is not None:
        if heading_level < 0 or heading_level > 6:
            raise ValueError("heading_level must be between 0 (normal text) and 6")
        if heading_level == 0:
            paragraph_style["namedStyleType"] = "NORMAL_TEXT"
        else:
            paragraph_style["namedStyleType"] = f"HEADING_{heading_level}"
        fields.append("namedStyleType")

    if alignment is not None:
        valid_alignments = ["START", "CENTER", "END", "JUSTIFIED"]
        alignment_upper = alignment.upper()
        if alignment_upper not in valid_alignments:
            raise ValueError(
                f"Invalid alignment '{alignment}'. Must be one of: {valid_alignments}"
            )
        paragraph_style["alignment"] = alignment_upper
        fields.append("alignment")

    if line_spacing is not None:
        if line_spacing <= 0:
            raise ValueError("line_spacing must be positive")
        paragraph_style["lineSpacing"] = line_spacing * 100
        fields.append("lineSpacing")

    if indent_first_line is not None:
        paragraph_style["indentFirstLine"] = {
            "magnitude": indent_first_line,
            "unit": "PT",
        }
        fields.append("indentFirstLine")

    if indent_start is not None:
        paragraph_style["indentStart"] = {"magnitude": indent_start, "unit": "PT"}
        fields.append("indentStart")

    if indent_end is not None:
        paragraph_style["indentEnd"] = {"magnitude": indent_end, "unit": "PT"}
        fields.append("indentEnd")

    if space_above is not None:
        paragraph_style["spaceAbove"] = {"magnitude": space_above, "unit": "PT"}
        fields.append("spaceAbove")

    if space_below is not None:
        paragraph_style["spaceBelow"] = {"magnitude": space_below, "unit": "PT"}
        fields.append("spaceBelow")

    if direction is not None:
        direction_upper = direction.upper()
        if direction_upper not in VALID_PARAGRAPH_DIRECTIONS:
            raise ValueError(
                f"direction must be one of: {', '.join(VALID_PARAGRAPH_DIRECTIONS)}"
            )
        paragraph_style["direction"] = direction_upper
        fields.append("direction")

    if keep_lines_together is not None:
        paragraph_style["keepLinesTogether"] = keep_lines_together
        fields.append("keepLinesTogether")

    if keep_with_next is not None:
        paragraph_style["keepWithNext"] = keep_with_next
        fields.append("keepWithNext")

    if avoid_widow_and_orphan is not None:
        paragraph_style["avoidWidowAndOrphan"] = avoid_widow_and_orphan
        fields.append("avoidWidowAndOrphan")

    if page_break_before is not None:
        paragraph_style["pageBreakBefore"] = page_break_before
        fields.append("pageBreakBefore")

    if spacing_mode is not None:
        spacing_mode_upper = spacing_mode.upper()
        if spacing_mode_upper not in VALID_PARAGRAPH_SPACING_MODES:
            raise ValueError(
                f"spacing_mode must be one of: {', '.join(VALID_PARAGRAPH_SPACING_MODES)}"
            )
        paragraph_style["spacingMode"] = spacing_mode_upper
        fields.append("spacingMode")

    if shading_color is not None:
        paragraph_style["shading"] = {
            "backgroundColor": _build_optional_color(shading_color, "shading_color")
        }
        fields.append("shading")

    return paragraph_style, fields


def build_document_style(
    background_color: Optional[str] = None,
    margin_top: Optional[float] = None,
    margin_bottom: Optional[float] = None,
    margin_left: Optional[float] = None,
    margin_right: Optional[float] = None,
    margin_header: Optional[float] = None,
    margin_footer: Optional[float] = None,
    page_width: Optional[float] = None,
    page_height: Optional[float] = None,
    page_number_start: Optional[int] = None,
    use_even_page_header_footer: Optional[bool] = None,
    use_first_page_header_footer: Optional[bool] = None,
    flip_page_orientation: Optional[bool] = None,
    document_mode: Optional[str] = None,
) -> tuple[Dict[str, Any], list[str]]:
    """Build a documentStyle object and explicit field mask."""
    document_style: Dict[str, Any] = {}
    fields: List[str] = []

    if background_color is not None:
        document_style["background"] = _build_optional_color(
            background_color, "background_color"
        )
        fields.append("background")

    for value, field_name in (
        (margin_top, "marginTop"),
        (margin_bottom, "marginBottom"),
        (margin_left, "marginLeft"),
        (margin_right, "marginRight"),
        (margin_header, "marginHeader"),
        (margin_footer, "marginFooter"),
    ):
        if value is not None:
            document_style[field_name] = _build_dimension(value)
            fields.append(field_name)

    if page_width is not None or page_height is not None:
        size: Dict[str, Any] = {}
        if page_width is not None:
            size["width"] = _build_dimension(page_width)
        if page_height is not None:
            size["height"] = _build_dimension(page_height)
        document_style["pageSize"] = size
        fields.append("pageSize")

    if page_number_start is not None:
        document_style["pageNumberStart"] = page_number_start
        fields.append("pageNumberStart")

    if use_even_page_header_footer is not None:
        document_style["useEvenPageHeaderFooter"] = use_even_page_header_footer
        fields.append("useEvenPageHeaderFooter")

    if use_first_page_header_footer is not None:
        document_style["useFirstPageHeaderFooter"] = use_first_page_header_footer
        fields.append("useFirstPageHeaderFooter")

    if flip_page_orientation is not None:
        document_style["flipPageOrientation"] = flip_page_orientation
        fields.append("flipPageOrientation")

    if document_mode is not None:
        document_mode_upper = document_mode.upper()
        if document_mode_upper not in VALID_DOCUMENT_MODES:
            raise ValueError(
                f"document_mode must be one of: {', '.join(VALID_DOCUMENT_MODES)}"
            )
        document_style["documentFormat"] = {"documentMode": document_mode_upper}
        fields.append("documentFormat")

    return document_style, fields


def build_section_style(
    margin_top: Optional[float] = None,
    margin_bottom: Optional[float] = None,
    margin_left: Optional[float] = None,
    margin_right: Optional[float] = None,
    margin_header: Optional[float] = None,
    margin_footer: Optional[float] = None,
    page_number_start: Optional[int] = None,
    use_first_page_header_footer: Optional[bool] = None,
    flip_page_orientation: Optional[bool] = None,
    content_direction: Optional[str] = None,
    column_count: Optional[int] = None,
    column_spacing: Optional[float] = None,
    column_separator_style: Optional[str] = None,
) -> tuple[Dict[str, Any], list[str]]:
    """Build a sectionStyle object and explicit field mask."""
    section_style: Dict[str, Any] = {}
    fields: List[str] = []

    for value, field_name in (
        (margin_top, "marginTop"),
        (margin_bottom, "marginBottom"),
        (margin_left, "marginLeft"),
        (margin_right, "marginRight"),
        (margin_header, "marginHeader"),
        (margin_footer, "marginFooter"),
    ):
        if value is not None:
            section_style[field_name] = _build_dimension(value)
            fields.append(field_name)

    if page_number_start is not None:
        section_style["pageNumberStart"] = page_number_start
        fields.append("pageNumberStart")

    if use_first_page_header_footer is not None:
        section_style["useFirstPageHeaderFooter"] = use_first_page_header_footer
        fields.append("useFirstPageHeaderFooter")

    if flip_page_orientation is not None:
        section_style["flipPageOrientation"] = flip_page_orientation
        fields.append("flipPageOrientation")

    if content_direction is not None:
        content_direction_upper = content_direction.upper()
        if content_direction_upper not in VALID_CONTENT_DIRECTIONS:
            raise ValueError(
                "content_direction must be one of: "
                f"{', '.join(VALID_CONTENT_DIRECTIONS)}"
            )
        section_style["contentDirection"] = content_direction_upper
        fields.append("contentDirection")

    if column_separator_style is not None:
        column_separator_style_upper = column_separator_style.upper()
        if column_separator_style_upper not in VALID_COLUMN_SEPARATOR_STYLES:
            raise ValueError(
                "column_separator_style must be one of: "
                f"{', '.join(VALID_COLUMN_SEPARATOR_STYLES)}"
            )
        section_style["columnSeparatorStyle"] = column_separator_style_upper
        fields.append("columnSeparatorStyle")

    if column_count is not None or column_spacing is not None:
        if column_count is None:
            raise ValueError("column_count is required when specifying section columns")
        if column_count < 1 or column_count > 3:
            raise ValueError("column_count must be between 1 and 3")

        columns = []
        for _ in range(column_count):
            column: Dict[str, Any] = {}
            if column_spacing is not None:
                column["paddingEnd"] = _build_dimension(column_spacing)
            columns.append(column)
        section_style["columnProperties"] = columns
        fields.append("columnProperties")

    return section_style, fields


def build_table_cell_style(
    background_color: str = None,
    border_color: str = None,
    border_width: float = None,
    padding_top: float = None,
    padding_bottom: float = None,
    padding_left: float = None,
    padding_right: float = None,
    content_alignment: str = None,
) -> tuple[Dict[str, Any], list[str]]:
    """
    Build a table cell style object for Google Docs API requests.

    Args:
        background_color: Cell background color as hex string "#RRGGBB"
        border_color: Cell border color as hex string "#RRGGBB"
        border_width: Cell border width in points
        padding_top: Top padding in points
        padding_bottom: Bottom padding in points
        padding_left: Left padding in points
        padding_right: Right padding in points
        content_alignment: Vertical content alignment ("TOP", "MIDDLE", "BOTTOM")

    Returns:
        Tuple of (table_cell_style_dict, list_of_field_names)
    """
    table_cell_style = {}
    fields = []

    if border_color is not None or border_width is not None:
        border_style = {}

        if border_width is not None:
            border_style["width"] = {"magnitude": border_width, "unit": "PT"}

        if border_color is not None:
            rgb = _normalize_color(border_color, "border_color")
            border_style["color"] = {"color": {"rgbColor": rgb}}

        for border_name in ("borderTop", "borderBottom", "borderLeft", "borderRight"):
            table_cell_style[border_name] = border_style.copy()
            fields.append(border_name)

    if background_color is not None:
        rgb = _normalize_color(background_color, "background_color")
        table_cell_style["backgroundColor"] = {"color": {"rgbColor": rgb}}
        fields.append("backgroundColor")

    for padding_value, api_key in (
        (padding_top, "paddingTop"),
        (padding_bottom, "paddingBottom"),
        (padding_left, "paddingLeft"),
        (padding_right, "paddingRight"),
    ):
        if padding_value is not None:
            table_cell_style[api_key] = {"magnitude": padding_value, "unit": "PT"}
            fields.append(api_key)

    if content_alignment is not None:
        table_cell_style["contentAlignment"] = content_alignment.upper()
        fields.append("contentAlignment")

    return table_cell_style, fields


def create_insert_text_request(
    index: Optional[int],
    text: str,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
    end_of_segment: bool = False,
) -> Dict[str, Any]:
    """
    Create an insertText request for Google Docs API.

    Args:
        index: Position to insert text
        text: Text to insert
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the insertText request
    """
    request = {"insertText": {"text": text}}
    request["insertText"].update(
        _build_location(
            index=index,
            tab_id=tab_id,
            segment_id=segment_id,
            end_of_segment=end_of_segment,
        )
    )
    return request


def create_insert_text_segment_request(
    index: int, text: str, segment_id: str, tab_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create an insertText request for Google Docs API with segmentId (for headers/footers).

    Args:
        index: Position to insert text
        text: Text to insert
        segment_id: Segment ID (for targeting headers/footers)
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the insertText request with segmentId and optional tabId
    """
    return create_insert_text_request(
        index=index,
        text=text,
        tab_id=tab_id,
        segment_id=segment_id,
    )


def create_delete_range_request(
    start_index: int,
    end_index: int,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a deleteContentRange request for Google Docs API.

    Args:
        start_index: Start position of content to delete
        end_index: End position of content to delete
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the deleteContentRange request
    """
    return {
        "deleteContentRange": {
            "range": _build_range(start_index, end_index, tab_id, segment_id)
        }
    }


def create_format_text_request(
    start_index: int,
    end_index: int,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    strikethrough: bool = None,
    font_size: int = None,
    font_family: str = None,
    font_weight: int = None,
    text_color: str = None,
    background_color: str = None,
    link_url: str = None,
    clear_link: bool = None,
    baseline_offset: str = None,
    small_caps: bool = None,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Create an updateTextStyle request for Google Docs API.

    Args:
        start_index: Start position of text to format
        end_index: End position of text to format
        bold: Whether text should be bold
        italic: Whether text should be italic
        underline: Whether text should be underlined
        strikethrough: Whether text should be struck through
        font_size: Font size in points
        font_family: Font family name
        text_color: Text color as hex string "#RRGGBB"
        background_color: Background (highlight) color as hex string "#RRGGBB"
        link_url: Hyperlink URL (http/https)
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the updateTextStyle request, or None if no styles provided
    """
    text_style, fields = build_text_style(
        bold,
        italic,
        underline,
        strikethrough,
        font_size,
        font_family,
        font_weight,
        text_color,
        background_color,
        link_url,
        clear_link,
        baseline_offset,
        small_caps,
    )

    if not text_style:
        return None

    return {
        "updateTextStyle": {
            "range": _build_range(start_index, end_index, tab_id, segment_id),
            "textStyle": text_style,
            "fields": ",".join(fields),
        }
    }


def create_update_paragraph_style_request(
    start_index: int,
    end_index: int,
    heading_level: int = None,
    alignment: str = None,
    line_spacing: float = None,
    indent_first_line: float = None,
    indent_start: float = None,
    indent_end: float = None,
    space_above: float = None,
    space_below: float = None,
    tab_id: Optional[str] = None,
    named_style_type: Optional[str] = None,
    segment_id: Optional[str] = None,
    direction: Optional[str] = None,
    keep_lines_together: Optional[bool] = None,
    keep_with_next: Optional[bool] = None,
    avoid_widow_and_orphan: Optional[bool] = None,
    page_break_before: Optional[bool] = None,
    spacing_mode: Optional[str] = None,
    shading_color: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Create an updateParagraphStyle request for Google Docs API.

    Args:
        start_index: Start position of paragraph range
        end_index: End position of paragraph range
        heading_level: Heading level 0-6 (0 = NORMAL_TEXT, 1-6 = HEADING_N)
        alignment: Text alignment - 'START', 'CENTER', 'END', or 'JUSTIFIED'
        line_spacing: Line spacing multiplier (1.0 = single, 2.0 = double)
        indent_first_line: First line indent in points
        indent_start: Left/start indent in points
        indent_end: Right/end indent in points
        space_above: Space above paragraph in points
        space_below: Space below paragraph in points
        tab_id: Optional ID of the tab to target
        named_style_type: Direct named style (TITLE, SUBTITLE, HEADING_1..6, NORMAL_TEXT)

    Returns:
        Dictionary representing the updateParagraphStyle request, or None if no styles provided
    """
    paragraph_style, fields = build_paragraph_style(
        heading_level=heading_level,
        alignment=alignment,
        line_spacing=line_spacing,
        indent_first_line=indent_first_line,
        indent_start=indent_start,
        indent_end=indent_end,
        space_above=space_above,
        space_below=space_below,
        named_style_type=named_style_type,
        direction=direction,
        keep_lines_together=keep_lines_together,
        keep_with_next=keep_with_next,
        avoid_widow_and_orphan=avoid_widow_and_orphan,
        page_break_before=page_break_before,
        spacing_mode=spacing_mode,
        shading_color=shading_color,
    )

    if not paragraph_style:
        return None

    return {
        "updateParagraphStyle": {
            "range": _build_range(start_index, end_index, tab_id, segment_id),
            "paragraphStyle": paragraph_style,
            "fields": ",".join(fields),
        }
    }


def create_find_replace_request(
    find_text: str,
    replace_text: str,
    match_case: bool = False,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a replaceAllText request for Google Docs API.

    Args:
        find_text: Text to find
        replace_text: Text to replace with
        match_case: Whether to match case exactly
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the replaceAllText request
    """
    request = {
        "replaceAllText": {
            "containsText": {"text": find_text, "matchCase": match_case},
            "replaceText": replace_text,
        }
    }
    tabs_criteria = _build_tabs_criteria(tab_id)
    if tabs_criteria:
        request["replaceAllText"]["tabsCriteria"] = tabs_criteria
    return request


def create_insert_table_request(
    index: Optional[int],
    rows: int,
    columns: int,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
    end_of_segment: bool = False,
) -> Dict[str, Any]:
    """
    Create an insertTable request for Google Docs API.

    Args:
        index: Position to insert table
        rows: Number of rows
        columns: Number of columns
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the insertTable request
    """
    request = {"insertTable": {"rows": rows, "columns": columns}}
    request["insertTable"].update(
        _build_location(
            index=index,
            tab_id=tab_id,
            segment_id=segment_id,
            end_of_segment=end_of_segment,
        )
    )
    return request


def create_update_table_cell_style_request(
    table_start_index: int,
    background_color: str = None,
    border_color: str = None,
    border_width: float = None,
    padding_top: float = None,
    padding_bottom: float = None,
    padding_left: float = None,
    padding_right: float = None,
    content_alignment: str = None,
    row_index: int = None,
    column_index: int = None,
    row_span: int = None,
    column_span: int = None,
    tab_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Create an updateTableCellStyle request for Google Docs API.

    Args:
        table_start_index: Start index of the target table
        background_color: Cell background color as hex string "#RRGGBB"
        border_color: Cell border color as hex string "#RRGGBB"
        border_width: Cell border width in points
        padding_top: Top padding in points
        padding_bottom: Bottom padding in points
        padding_left: Left padding in points
        padding_right: Right padding in points
        content_alignment: Vertical content alignment ("TOP", "MIDDLE", "BOTTOM")
        row_index: Optional starting row index for a sub-range
        column_index: Optional starting column index for a sub-range
        row_span: Optional row span for a sub-range (defaults to 1)
        column_span: Optional column span for a sub-range (defaults to 1)
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the updateTableCellStyle request, or None if no
        style fields were provided
    """
    table_cell_style, fields = build_table_cell_style(
        background_color=background_color,
        border_color=border_color,
        border_width=border_width,
        padding_top=padding_top,
        padding_bottom=padding_bottom,
        padding_left=padding_left,
        padding_right=padding_right,
        content_alignment=content_alignment,
    )
    if not table_cell_style:
        return None

    location = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id

    request: Dict[str, Any] = {
        "tableCellStyle": table_cell_style,
        "fields": ",".join(fields),
    }

    uses_table_range = any(
        value is not None for value in (row_index, column_index, row_span, column_span)
    )
    if uses_table_range:
        if row_index is None or column_index is None:
            raise ValueError(
                "row_index and column_index are required when targeting a table cell range"
            )

        request["tableRange"] = {
            "tableCellLocation": {
                "tableStartLocation": location,
                "rowIndex": row_index,
                "columnIndex": column_index,
            },
            "rowSpan": 1 if row_span is None else row_span,
            "columnSpan": 1 if column_span is None else column_span,
        }
    else:
        request["tableStartLocation"] = location

    return {"updateTableCellStyle": request}


def create_insert_page_break_request(
    index: Optional[int],
    tab_id: Optional[str] = None,
    end_of_segment: bool = False,
) -> Dict[str, Any]:
    """
    Create an insertPageBreak request for Google Docs API.

    Args:
        index: Position to insert page break
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the insertPageBreak request
    """
    request = {"insertPageBreak": {}}
    request["insertPageBreak"].update(
        _build_location(index=index, tab_id=tab_id, end_of_segment=end_of_segment)
    )
    return request


def create_insert_doc_tab_request(
    title: str, index: int, parent_tab_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create an addDocumentTab request for Google Docs API.

    Args:
        title: Title of the new tab
        index: Position to insert the tab
        parent_tab_id: Optional ID of the parent tab to nest under

    Returns:
        Dictionary representing the addDocumentTab request
    """
    tab_properties: Dict[str, Any] = {
        "title": title,
        "index": index,
    }
    if parent_tab_id:
        tab_properties["parentTabId"] = parent_tab_id
    return {
        "addDocumentTab": {
            "tabProperties": tab_properties,
        }
    }


def create_delete_doc_tab_request(tab_id: str) -> Dict[str, Any]:
    """
    Create a deleteDocumentTab request for Google Docs API.

    Args:
        tab_id: ID of the tab to delete

    Returns:
        Dictionary representing the deleteDocumentTab request
    """
    return {"deleteTab": {"tabId": tab_id}}


def create_update_doc_tab_request(tab_id: str, title: str) -> Dict[str, Any]:
    """
    Create an updateDocumentTab request for Google Docs API.

    Args:
        tab_id: ID of the tab to update
        title: New title for the tab

    Returns:
        Dictionary representing the updateDocumentTab request
    """
    return {
        "updateDocumentTabProperties": {
            "tabProperties": {
                "tabId": tab_id,
                "title": title,
            },
            "fields": "title",
        }
    }


def create_insert_image_request(
    index: Optional[int],
    image_uri: str,
    width: int = None,
    height: int = None,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
    end_of_segment: bool = False,
) -> Dict[str, Any]:
    """
    Create an insertInlineImage request for Google Docs API.

    Args:
        index: Position to insert image
        image_uri: URI of the image (Drive URL or public URL)
        width: Image width in points
        height: Image height in points
        tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the insertInlineImage request
    """
    request = {"insertInlineImage": {"uri": image_uri}}
    request["insertInlineImage"].update(
        _build_location(
            index=index,
            tab_id=tab_id,
            segment_id=segment_id,
            end_of_segment=end_of_segment,
        )
    )

    # Add size properties if specified
    object_size = {}
    if width is not None:
        object_size["width"] = {"magnitude": width, "unit": "PT"}
    if height is not None:
        object_size["height"] = {"magnitude": height, "unit": "PT"}

    if object_size:
        request["insertInlineImage"]["objectSize"] = object_size

    return request


def create_bullet_list_request(
    start_index: int,
    end_index: int,
    list_type: str = "UNORDERED",
    nesting_level: int = None,
    paragraph_start_indices: Optional[list[int]] = None,
    doc_tab_id: Optional[str] = None,
    bullet_preset: Optional[str] = None,
    segment_id: Optional[str] = None,
) -> list[Dict[str, Any]]:
    """
    Create requests to apply bullet list formatting with optional nesting.

    Google Docs infers list nesting from leading tab characters. To set a nested
    level, this helper inserts literal tab characters before each targeted
    paragraph, then calls createParagraphBullets. This is a Docs API workaround
    and does temporarily mutate content/index positions while the batch executes.

    Args:
        start_index: Start of text range to convert to list
        end_index: End of text range to convert to list
        list_type: Type of list ("UNORDERED" or "ORDERED")
        nesting_level: Nesting level (0-8, where 0 is top level). If None or 0, no tabs added.
        paragraph_start_indices: Optional paragraph start positions for ranges with
            multiple paragraphs. If omitted, only start_index is tab-prefixed.
        doc_tab_id: Optional ID of the tab to target

    Returns:
        List of request dictionaries (insertText for nesting tabs if needed,
        then createParagraphBullets)
    """
    if bullet_preset is None:
        if list_type == "UNORDERED":
            bullet_preset = "BULLET_DISC_CIRCLE_SQUARE"
        elif list_type == "CHECKBOX":
            bullet_preset = "BULLET_CHECKBOX"
        else:
            bullet_preset = "NUMBERED_DECIMAL_ALPHA_ROMAN"
    elif bullet_preset not in VALID_BULLET_PRESETS:
        raise ValueError(
            f"bullet_preset must be one of: {', '.join(VALID_BULLET_PRESETS)}"
        )

    # Validate nesting level
    if nesting_level is not None:
        if not isinstance(nesting_level, int):
            raise ValueError("nesting_level must be an integer between 0 and 8")
        if nesting_level < 0 or nesting_level > 8:
            raise ValueError("nesting_level must be between 0 and 8")

    requests = []

    # Insert tabs for nesting if needed (nesting_level > 0).
    # For multi-paragraph ranges, callers should provide paragraph_start_indices.
    if nesting_level and nesting_level > 0:
        tabs = "\t" * nesting_level
        paragraph_starts = paragraph_start_indices or [start_index]
        paragraph_starts = sorted(set(paragraph_starts))

        if any(not isinstance(idx, int) for idx in paragraph_starts):
            raise ValueError("paragraph_start_indices must contain only integers")

        original_start = start_index
        original_end = end_index
        inserted_char_count = 0

        for paragraph_start in paragraph_starts:
            adjusted_start = paragraph_start + inserted_char_count
            requests.append(
                create_insert_text_request(
                    adjusted_start,
                    tabs,
                    doc_tab_id,
                    segment_id=segment_id,
                )
            )
            inserted_char_count += nesting_level

        # Keep createParagraphBullets range aligned to the same logical content.
        start_index += (
            sum(1 for idx in paragraph_starts if idx < original_start) * nesting_level
        )
        end_index += (
            sum(1 for idx in paragraph_starts if idx < original_end) * nesting_level
        )

    # Create the bullet list
    range_obj = _build_range(start_index, end_index, doc_tab_id, segment_id)

    requests.append(
        {
            "createParagraphBullets": {
                "range": range_obj,
                "bulletPreset": bullet_preset,
            }
        }
    )

    return requests


def create_delete_bullet_list_request(
    start_index: int,
    end_index: int,
    doc_tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a deleteParagraphBullets request to remove bullet/list formatting.

    Args:
        start_index: Start of the paragraph range
        end_index: End of the paragraph range
        doc_tab_id: Optional ID of the tab to target

    Returns:
        Dictionary representing the deleteParagraphBullets request
    """
    return {
        "deleteParagraphBullets": {
            "range": _build_range(start_index, end_index, doc_tab_id, segment_id),
        }
    }


def create_named_range_request(
    name: str,
    start_index: int,
    end_index: int,
    tab_id: Optional[str] = None,
    segment_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a createNamedRange request."""
    return {
        "createNamedRange": {
            "name": name,
            "range": _build_range(start_index, end_index, tab_id, segment_id),
        }
    }


def create_delete_named_range_request(
    named_range_id: Optional[str] = None,
    named_range_name: Optional[str] = None,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a deleteNamedRange request."""
    request: Dict[str, Any] = {}
    if named_range_id is not None:
        request["namedRangeId"] = named_range_id
    if named_range_name is not None:
        request["name"] = named_range_name
    tabs_criteria = _build_tabs_criteria(tab_id)
    if tabs_criteria:
        request["tabsCriteria"] = tabs_criteria
    return {"deleteNamedRange": request}


def create_replace_named_range_content_request(
    text: str,
    named_range_id: Optional[str] = None,
    named_range_name: Optional[str] = None,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a replaceNamedRangeContent request."""
    request: Dict[str, Any] = {"text": text}
    if named_range_id is not None:
        request["namedRangeId"] = named_range_id
    if named_range_name is not None:
        request["namedRangeName"] = named_range_name
    tabs_criteria = _build_tabs_criteria(tab_id)
    if tabs_criteria:
        request["tabsCriteria"] = tabs_criteria
    return {"replaceNamedRangeContent": request}


def create_insert_section_break_request(
    index: Optional[int] = None,
    section_type: str = "NEXT_PAGE",
    end_of_segment: bool = False,
) -> Dict[str, Any]:
    """Create an insertSectionBreak request."""
    section_type_upper = section_type.upper()
    if section_type_upper not in VALID_SECTION_TYPES:
        raise ValueError(
            f"section_type must be one of: {', '.join(VALID_SECTION_TYPES)}"
        )
    request = {"insertSectionBreak": {"sectionType": section_type_upper}}
    request["insertSectionBreak"].update(
        _build_location(index=index, end_of_segment=end_of_segment)
    )
    return request


def create_update_document_style_request(
    *,
    tab_id: Optional[str] = None,
    background_color: Optional[str] = None,
    margin_top: Optional[float] = None,
    margin_bottom: Optional[float] = None,
    margin_left: Optional[float] = None,
    margin_right: Optional[float] = None,
    margin_header: Optional[float] = None,
    margin_footer: Optional[float] = None,
    page_width: Optional[float] = None,
    page_height: Optional[float] = None,
    page_number_start: Optional[int] = None,
    use_even_page_header_footer: Optional[bool] = None,
    use_first_page_header_footer: Optional[bool] = None,
    flip_page_orientation: Optional[bool] = None,
    document_mode: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create an updateDocumentStyle request."""
    document_style, fields = build_document_style(
        background_color=background_color,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        margin_left=margin_left,
        margin_right=margin_right,
        margin_header=margin_header,
        margin_footer=margin_footer,
        page_width=page_width,
        page_height=page_height,
        page_number_start=page_number_start,
        use_even_page_header_footer=use_even_page_header_footer,
        use_first_page_header_footer=use_first_page_header_footer,
        flip_page_orientation=flip_page_orientation,
        document_mode=document_mode,
    )
    if not document_style:
        return None

    request: Dict[str, Any] = {
        "updateDocumentStyle": {
            "documentStyle": document_style,
            "fields": ",".join(fields),
        }
    }
    if tab_id:
        request["updateDocumentStyle"]["tabId"] = tab_id
    return request


def create_update_section_style_request(
    start_index: int,
    end_index: int,
    *,
    margin_top: Optional[float] = None,
    margin_bottom: Optional[float] = None,
    margin_left: Optional[float] = None,
    margin_right: Optional[float] = None,
    margin_header: Optional[float] = None,
    margin_footer: Optional[float] = None,
    page_number_start: Optional[int] = None,
    use_first_page_header_footer: Optional[bool] = None,
    flip_page_orientation: Optional[bool] = None,
    content_direction: Optional[str] = None,
    column_count: Optional[int] = None,
    column_spacing: Optional[float] = None,
    column_separator_style: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create an updateSectionStyle request."""
    section_style, fields = build_section_style(
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        margin_left=margin_left,
        margin_right=margin_right,
        margin_header=margin_header,
        margin_footer=margin_footer,
        page_number_start=page_number_start,
        use_first_page_header_footer=use_first_page_header_footer,
        flip_page_orientation=flip_page_orientation,
        content_direction=content_direction,
        column_count=column_count,
        column_spacing=column_spacing,
        column_separator_style=column_separator_style,
    )
    if not section_style:
        return None

    return {
        "updateSectionStyle": {
            "range": _build_range(start_index, end_index),
            "sectionStyle": section_style,
            "fields": ",".join(fields),
        }
    }


def create_create_header_footer_request(
    section_type: str,
    header_footer_type: str = "DEFAULT",
    section_break_index: Optional[int] = None,
) -> Dict[str, Any]:
    """Create a createHeader/createFooter request."""
    header_footer_type_upper = header_footer_type.upper()
    if header_footer_type_upper == "FIRST_PAGE_ONLY":
        header_footer_type_upper = "DEFAULT"

    request: Dict[str, Any] = {"type": header_footer_type_upper}
    if section_break_index is not None:
        request["sectionBreakLocation"] = {"index": section_break_index}

    if section_type == "header":
        return {"createHeader": request}
    if section_type == "footer":
        return {"createFooter": request}
    raise ValueError("section_type must be 'header' or 'footer'")


def create_insert_table_row_request(
    table_start_index: int,
    row_index: int,
    insert_below: bool = True,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an insertTableRow request."""
    location: Dict[str, Any] = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id
    return {
        "insertTableRow": {
            "tableCellLocation": {
                "tableStartLocation": location,
                "rowIndex": row_index,
                "columnIndex": 0,
            },
            "insertBelow": insert_below,
        }
    }


def create_delete_table_row_request(
    table_start_index: int,
    row_index: int,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a deleteTableRow request."""
    location: Dict[str, Any] = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id
    return {
        "deleteTableRow": {
            "tableCellLocation": {
                "tableStartLocation": location,
                "rowIndex": row_index,
                "columnIndex": 0,
            }
        }
    }


def create_insert_table_column_request(
    table_start_index: int,
    column_index: int,
    insert_right: bool = True,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an insertTableColumn request."""
    location: Dict[str, Any] = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id
    return {
        "insertTableColumn": {
            "tableCellLocation": {
                "tableStartLocation": location,
                "rowIndex": 0,
                "columnIndex": column_index,
            },
            "insertRight": insert_right,
        }
    }


def create_delete_table_column_request(
    table_start_index: int,
    column_index: int,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a deleteTableColumn request."""
    location: Dict[str, Any] = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id
    return {
        "deleteTableColumn": {
            "tableCellLocation": {
                "tableStartLocation": location,
                "rowIndex": 0,
                "columnIndex": column_index,
            }
        }
    }


def _build_table_range(
    table_start_index: int,
    row_index: int,
    column_index: int,
    row_span: int,
    column_span: int,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a tableRange object used by merge/unmerge requests."""
    location: Dict[str, Any] = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id
    return {
        "tableRange": {
            "tableCellLocation": {
                "tableStartLocation": location,
                "rowIndex": row_index,
                "columnIndex": column_index,
            },
            "rowSpan": row_span,
            "columnSpan": column_span,
        }
    }


def create_merge_table_cells_request(
    table_start_index: int,
    row_index: int,
    column_index: int,
    row_span: int,
    column_span: int,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a mergeTableCells request."""
    return {
        "mergeTableCells": _build_table_range(
            table_start_index, row_index, column_index, row_span, column_span, tab_id
        )
    }


def create_unmerge_table_cells_request(
    table_start_index: int,
    row_index: int,
    column_index: int,
    row_span: int,
    column_span: int,
    tab_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build an unmergeTableCells request."""
    return {
        "unmergeTableCells": _build_table_range(
            table_start_index, row_index, column_index, row_span, column_span, tab_id
        )
    }


def create_update_table_column_properties_request(
    table_start_index: int,
    column_indices: list,
    width: float = None,
    width_type: str = None,
    tab_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Build an updateTableColumnProperties request. Returns None if no properties set."""
    location: Dict[str, Any] = {"index": table_start_index}
    if tab_id:
        location["tabId"] = tab_id

    properties: Dict[str, Any] = {}
    fields = []

    if width is not None:
        properties["width"] = {"magnitude": width, "unit": "PT"}
        fields.append("width")

    if width_type is not None:
        properties["widthType"] = width_type
        fields.append("widthType")

    if not fields:
        return None

    return {
        "updateTableColumnProperties": {
            "tableStartLocation": location,
            "columnIndices": column_indices,
            "tableColumnProperties": properties,
            "fields": ",".join(fields),
        }
    }


def validate_operation(operation: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a batch operation dictionary.

    Args:
        operation: Operation dictionary to validate

    Returns:
        Tuple of (is_valid, error_message)
    """
    op_type = operation.get("type")
    if not op_type:
        return False, "Missing 'type' field"

    # Validate required fields for each operation type
    required_fields = {
        "insert_text": ["text"],
        "delete_text": ["start_index", "end_index"],
        "replace_text": ["start_index", "end_index", "text"],
        "format_text": ["start_index", "end_index"],
        "update_paragraph_style": ["start_index", "end_index"],
        "update_table_cell_style": ["table_start_index"],
        "insert_table": ["rows", "columns"],
        "insert_page_break": [],
        "insert_section_break": [],
        "find_replace": ["find_text", "replace_text"],
        "create_bullet_list": ["start_index", "end_index"],
        "create_named_range": ["name", "start_index", "end_index"],
        "replace_named_range_content": ["text"],
        "delete_named_range": [],
        "update_document_style": [],
        "update_section_style": ["start_index", "end_index"],
        "create_header_footer": ["section_type"],
        "insert_image": ["image_uri"],
        "insert_doc_tab": ["title", "index"],
        "delete_doc_tab": ["tab_id"],
        "update_doc_tab": ["tab_id", "title"],
        "insert_table_row": ["table_start_index", "row_index"],
        "delete_table_row": ["table_start_index", "row_index"],
        "insert_table_column": ["table_start_index", "column_index"],
        "delete_table_column": ["table_start_index", "column_index"],
        "merge_table_cells": [
            "table_start_index",
            "row_index",
            "column_index",
            "row_span",
            "column_span",
        ],
        "unmerge_table_cells": [
            "table_start_index",
            "row_index",
            "column_index",
            "row_span",
            "column_span",
        ],
        "update_table_column_properties": ["table_start_index", "column_indices"],
    }

    if op_type not in required_fields:
        return False, f"Unsupported operation type: {op_type or 'None'}"

    for field in required_fields[op_type]:
        if field not in operation:
            return False, f"Missing required field: {field}"

    if op_type in {
        "insert_text",
        "insert_table",
        "insert_page_break",
        "insert_section_break",
        "insert_image",
    }:
        end_of_segment = operation.get("end_of_segment", False)
        if end_of_segment and "index" in operation:
            return (
                False,
                "Cannot specify both 'index' and 'end_of_segment=true'. Use one or the other.",
            )
        if (
            not end_of_segment
            and "index" not in operation
            and op_type != "insert_image"
        ):
            return (
                False,
                "Missing required field: index (or set end_of_segment=true to append)",
            )
        if (
            op_type == "insert_image"
            and not end_of_segment
            and "index" not in operation
        ):
            return (
                False,
                "Missing required field: index (or set end_of_segment=true to append)",
            )

    return True, ""
