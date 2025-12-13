"""
Google Docs Helper Functions

This module provides utility functions for common Google Docs operations
to simplify the implementation of document editing tools.
"""

import logging
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)


def _normalize_color(color: Any, param_name: str) -> Optional[Dict[str, float]]:
    """
    Normalize a user-supplied color into Docs API rgbColor format.

    Supports:
    - Hex strings: "#RRGGBB" or "RRGGBB"
    - Tuple/list of 3 ints (0-255) or floats (0-1)
    """
    if color is None:
        return None

    def _to_component(value: Any) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{param_name} components cannot be boolean values")
        if isinstance(value, int):
            if value < 0 or value > 255:
                raise ValueError(
                    f"{param_name} components must be 0-255 when using integers"
                )
            return value / 255
        if isinstance(value, float):
            if value < 0 or value > 1:
                raise ValueError(
                    f"{param_name} components must be between 0 and 1 when using floats"
                )
            return value
        raise ValueError(f"{param_name} components must be int (0-255) or float (0-1)")

    if isinstance(color, str):
        hex_color = color.lstrip("#")
        if len(hex_color) != 6 or any(
            c not in "0123456789abcdefABCDEF" for c in hex_color
        ):
            raise ValueError(f"{param_name} must be a hex string like '#RRGGBB'")
        r = int(hex_color[0:2], 16) / 255
        g = int(hex_color[2:4], 16) / 255
        b = int(hex_color[4:6], 16) / 255
        return {"red": r, "green": g, "blue": b}

    if isinstance(color, (list, tuple)) and len(color) == 3:
        r = _to_component(color[0])
        g = _to_component(color[1])
        b = _to_component(color[2])
        return {"red": r, "green": g, "blue": b}

    raise ValueError(f"{param_name} must be a hex string or RGB tuple/list")


def build_text_style(
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    font_size: int = None,
    font_family: str = None,
    text_color: Any = None,
    background_color: Any = None,
) -> tuple[Dict[str, Any], list[str]]:
    """
    Build text style object for Google Docs API requests.

    Args:
        bold: Whether text should be bold
        italic: Whether text should be italic
        underline: Whether text should be underlined
        font_size: Font size in points
        font_family: Font family name
        text_color: Text color as hex string or RGB tuple/list
        background_color: Background (highlight) color as hex string or RGB tuple/list

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

    return text_style, fields


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
    text_color: Any = None,
    background_color: Any = None,
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
        text_color: Text color as hex string or RGB tuple/list
        background_color: Background (highlight) color as hex string or RGB tuple/list

    Returns:
        Dictionary representing the updateTextStyle request, or None if no styles provided
    """
    text_style, fields = build_text_style(
        bold, italic, underline, font_size, font_family, text_color, background_color
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
    start_index: int, end_index: int, list_type: str = "UNORDERED"
) -> Dict[str, Any]:
    """
    Create a createParagraphBullets request for Google Docs API.

    Args:
        start_index: Start of text range to convert to list
        end_index: End of text range to convert to list
        list_type: Type of list ("UNORDERED" or "ORDERED")

    Returns:
        Dictionary representing the createParagraphBullets request
    """
    bullet_preset = (
        "BULLET_DISC_CIRCLE_SQUARE"
        if list_type == "UNORDERED"
        else "NUMBERED_DECIMAL_ALPHA_ROMAN"
    )

    return {
        "createParagraphBullets": {
            "range": {"startIndex": start_index, "endIndex": end_index},
            "bulletPreset": bullet_preset,
        }
    }


def validate_operation(operation: Dict[str, Any]) -> Tuple[bool, str]:
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
