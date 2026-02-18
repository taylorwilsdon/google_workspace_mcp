"""
Google Docs Helper Functions

This module provides utility functions for common Google Docs operations
to simplify the implementation of document editing tools.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


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
    font_size: int = None,
    font_family: str = None,
    text_color: str = None,
    background_color: str = None,
    link_url: str = None,
) -> tuple[Dict[str, Any], list[str]]:
    """
    Build text style object for Google Docs API requests.

    Args:
        bold: Whether text should be bold
        italic: Whether text should be italic
        underline: Whether text should be underlined
        font_size: Font size in points
        font_family: Font family name
        text_color: Text color as hex string "#RRGGBB"
        background_color: Background (highlight) color as hex string "#RRGGBB"
        link_url: Hyperlink URL (http/https)

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

    if font_size is not None:
        text_style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
        fields.append("fontSize")

    if font_family is not None:
        text_style["weightedFontFamily"] = {"fontFamily": font_family}
        fields.append("weightedFontFamily")

    if text_color is not None:
        rgb = _normalize_color(text_color, "text_color")
        text_style["foregroundColor"] = {"color": {"rgbColor": rgb}}
        fields.append("foregroundColor")

    if background_color is not None:
        rgb = _normalize_color(background_color, "background_color")
        text_style["backgroundColor"] = {"color": {"rgbColor": rgb}}
        fields.append("backgroundColor")

    if link_url is not None:
        text_style["link"] = {"url": link_url}
        fields.append("link")

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

    Returns:
        Tuple of (paragraph_style_dict, list_of_field_names)
    """
    paragraph_style = {}
    fields = []

    if heading_level is not None:
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

    return paragraph_style, fields


def create_insert_text_request(index: int, text: str) -> Dict[str, Any]:
    """
    Create an insertText request for Google Docs API.

    Args:
        index: Position to insert text
        text: Text to insert

    Returns:
        Dictionary representing the insertText request
    """
    return {"insertText": {"location": {"index": index}, "text": text}}


def create_insert_text_segment_request(
    index: int, text: str, segment_id: str
) -> Dict[str, Any]:
    """
    Create an insertText request for Google Docs API with segmentId (for headers/footers).

    Args:
        index: Position to insert text
        text: Text to insert
        segment_id: Segment ID (for targeting headers/footers)

    Returns:
        Dictionary representing the insertText request with segmentId
    """
    return {
        "insertText": {
            "location": {"segmentId": segment_id, "index": index},
            "text": text,
        }
    }


def create_delete_range_request(start_index: int, end_index: int) -> Dict[str, Any]:
    """
    Create a deleteContentRange request for Google Docs API.

    Args:
        start_index: Start position of content to delete
        end_index: End position of content to delete

    Returns:
        Dictionary representing the deleteContentRange request
    """
    return {
        "deleteContentRange": {
            "range": {"startIndex": start_index, "endIndex": end_index}
        }
    }


def create_format_text_request(
    start_index: int,
    end_index: int,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    font_size: int = None,
    font_family: str = None,
    text_color: str = None,
    background_color: str = None,
    link_url: str = None,
) -> Optional[Dict[str, Any]]:
    """
    Create an updateTextStyle request for Google Docs API.

    Args:
        start_index: Start position of text to format
        end_index: End position of text to format
        bold: Whether text should be bold
        italic: Whether text should be italic
        underline: Whether text should be underlined
        font_size: Font size in points
        font_family: Font family name
        text_color: Text color as hex string "#RRGGBB"
        background_color: Background (highlight) color as hex string "#RRGGBB"
        link_url: Hyperlink URL (http/https)

    Returns:
        Dictionary representing the updateTextStyle request, or None if no styles provided
    """
    text_style, fields = build_text_style(
        bold,
        italic,
        underline,
        font_size,
        font_family,
        text_color,
        background_color,
        link_url,
    )

    if not text_style:
        return None

    return {
        "updateTextStyle": {
            "range": {"startIndex": start_index, "endIndex": end_index},
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

    Returns:
        Dictionary representing the updateParagraphStyle request, or None if no styles provided
    """
    paragraph_style, fields = build_paragraph_style(
        heading_level,
        alignment,
        line_spacing,
        indent_first_line,
        indent_start,
        indent_end,
        space_above,
        space_below,
    )

    if not paragraph_style:
        return None

    return {
        "updateParagraphStyle": {
            "range": {"startIndex": start_index, "endIndex": end_index},
            "paragraphStyle": paragraph_style,
            "fields": ",".join(fields),
        }
    }


def create_find_replace_request(
    find_text: str, replace_text: str, match_case: bool = False
) -> Dict[str, Any]:
    """
    Create a replaceAllText request for Google Docs API.

    Args:
        find_text: Text to find
        replace_text: Text to replace with
        match_case: Whether to match case exactly

    Returns:
        Dictionary representing the replaceAllText request
    """
    return {
        "replaceAllText": {
            "containsText": {"text": find_text, "matchCase": match_case},
            "replaceText": replace_text,
        }
    }


def create_insert_table_request(index: int, rows: int, columns: int) -> Dict[str, Any]:
    """
    Create an insertTable request for Google Docs API.

    Args:
        index: Position to insert table
        rows: Number of rows
        columns: Number of columns

    Returns:
        Dictionary representing the insertTable request
    """
    return {
        "insertTable": {"location": {"index": index}, "rows": rows, "columns": columns}
    }


def create_insert_page_break_request(index: int) -> Dict[str, Any]:
    """
    Create an insertPageBreak request for Google Docs API.

    Args:
        index: Position to insert page break

    Returns:
        Dictionary representing the insertPageBreak request
    """
    return {"insertPageBreak": {"location": {"index": index}}}


def create_insert_image_request(
    index: int, image_uri: str, width: int = None, height: int = None
) -> Dict[str, Any]:
    """
    Create an insertInlineImage request for Google Docs API.

    Args:
        index: Position to insert image
        image_uri: URI of the image (Drive URL or public URL)
        width: Image width in points
        height: Image height in points

    Returns:
        Dictionary representing the insertInlineImage request
    """
    request = {"insertInlineImage": {"location": {"index": index}, "uri": image_uri}}

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

    Returns:
        List of request dictionaries (insertText for nesting tabs if needed,
        then createParagraphBullets)
    """
    bullet_preset = (
        "BULLET_DISC_CIRCLE_SQUARE"
        if list_type == "UNORDERED"
        else "NUMBERED_DECIMAL_ALPHA_ROMAN"
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
                {
                    "insertText": {
                        "location": {"index": adjusted_start},
                        "text": tabs,
                    }
                }
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
    requests.append(
        {
            "createParagraphBullets": {
                "range": {"startIndex": start_index, "endIndex": end_index},
                "bulletPreset": bullet_preset,
            }
        }
    )

    return requests


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
        "insert_text": ["index", "text"],
        "delete_text": ["start_index", "end_index"],
        "replace_text": ["start_index", "end_index", "text"],
        "format_text": ["start_index", "end_index"],
        "update_paragraph_style": ["start_index", "end_index"],
        "insert_table": ["index", "rows", "columns"],
        "insert_page_break": ["index"],
        "find_replace": ["find_text", "replace_text"],
    }

    if op_type not in required_fields:
        return False, f"Unsupported operation type: {op_type or 'None'}"

    for field in required_fields[op_type]:
        if field not in operation:
            return False, f"Missing required field: {field}"

    return True, ""
