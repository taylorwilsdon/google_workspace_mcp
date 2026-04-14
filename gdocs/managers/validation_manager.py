"""
Validation Manager

This module provides centralized validation logic for Google Docs operations,
extracting validation patterns from individual tool functions.
"""

import logging
from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import urlparse

from gdocs.docs_helpers import (
    validate_operation,
    VALID_NAMED_STYLE_TYPES,
    VALID_TEXT_BASELINE_OFFSETS,
    VALID_PARAGRAPH_DIRECTIONS,
    VALID_PARAGRAPH_SPACING_MODES,
    VALID_SECTION_TYPES,
    VALID_CONTENT_DIRECTIONS,
    VALID_COLUMN_SEPARATOR_STYLES,
    VALID_DOCUMENT_MODES,
    VALID_BULLET_PRESETS,
)

logger = logging.getLogger(__name__)


class ValidationManager:
    """
    Centralized validation manager for Google Docs operations.

    Provides consistent validation patterns and error messages across
    all document operations, reducing code duplication and improving
    error message quality.
    """

    def __init__(self):
        """Initialize the validation manager."""
        self.validation_rules = self._setup_validation_rules()

    def _setup_validation_rules(self) -> Dict[str, Any]:
        """Setup validation rules and constraints."""
        return {
            "table_max_rows": 1000,
            "table_max_columns": 20,
            "document_id_pattern": r"^[a-zA-Z0-9-_]+$",
            "max_text_length": 1000000,  # 1MB text limit
            "font_size_range": (1, 400),  # Google Docs font size limits
            "valid_header_footer_types": ["DEFAULT", "FIRST_PAGE_ONLY", "EVEN_PAGE"],
            "valid_section_types": ["header", "footer"],
            "valid_list_types": ["UNORDERED", "ORDERED", "CHECKBOX"],
            "valid_element_types": ["table", "list", "page_break"],
            "valid_alignments": ["START", "CENTER", "END", "JUSTIFIED"],
            "heading_level_range": (0, 6),
            "font_weight_range": (100, 900),
        }

    def validate_document_id(self, document_id: str) -> Tuple[bool, str]:
        """
        Validate Google Docs document ID format.

        Args:
            document_id: Document ID to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not document_id:
            return False, "Document ID cannot be empty"

        if not isinstance(document_id, str):
            return (
                False,
                f"Document ID must be a string, got {type(document_id).__name__}",
            )

        # Basic length check (Google Docs IDs are typically 40+ characters)
        if len(document_id) < 20:
            return False, "Document ID appears too short to be valid"

        return True, ""

    def validate_table_data(self, table_data: List[List[str]]) -> Tuple[bool, str]:
        """
        Comprehensive validation for table data format.

        This extracts and centralizes table validation logic from multiple functions.

        Args:
            table_data: 2D array of data to validate

        Returns:
            Tuple of (is_valid, detailed_error_message)
        """
        if not table_data:
            return (
                False,
                "Table data cannot be empty. Required format: [['col1', 'col2'], ['row1col1', 'row1col2']]",
            )

        if not isinstance(table_data, list):
            return (
                False,
                f"Table data must be a list, got {type(table_data).__name__}. Required format: [['col1', 'col2'], ['row1col1', 'row1col2']]",
            )

        # Check if it's a 2D list
        if not all(isinstance(row, list) for row in table_data):
            non_list_rows = [
                i for i, row in enumerate(table_data) if not isinstance(row, list)
            ]
            return (
                False,
                f"All rows must be lists. Rows {non_list_rows} are not lists. Required format: [['col1', 'col2'], ['row1col1', 'row1col2']]",
            )

        # Check for empty rows
        if any(len(row) == 0 for row in table_data):
            empty_rows = [i for i, row in enumerate(table_data) if len(row) == 0]
            return (
                False,
                f"Rows cannot be empty. Empty rows found at indices: {empty_rows}",
            )

        # Check column consistency
        col_counts = [len(row) for row in table_data]
        if len(set(col_counts)) > 1:
            return (
                False,
                f"All rows must have the same number of columns. Found column counts: {col_counts}. Fix your data structure.",
            )

        rows = len(table_data)
        cols = col_counts[0]

        # Check dimension limits
        if rows > self.validation_rules["table_max_rows"]:
            return (
                False,
                f"Too many rows ({rows}). Maximum allowed: {self.validation_rules['table_max_rows']}",
            )

        if cols > self.validation_rules["table_max_columns"]:
            return (
                False,
                f"Too many columns ({cols}). Maximum allowed: {self.validation_rules['table_max_columns']}",
            )

        # Check cell content types
        for row_idx, row in enumerate(table_data):
            for col_idx, cell in enumerate(row):
                if cell is None:
                    return (
                        False,
                        f"Cell ({row_idx},{col_idx}) is None. All cells must be strings, use empty string '' for empty cells.",
                    )

                if not isinstance(cell, str):
                    return (
                        False,
                        f"Cell ({row_idx},{col_idx}) is {type(cell).__name__}, not string. All cells must be strings. Value: {repr(cell)}",
                    )

        return True, f"Valid table data: {rows}×{cols} table format"

    def validate_text_formatting_params(
        self,
        bold: Optional[bool] = None,
        italic: Optional[bool] = None,
        underline: Optional[bool] = None,
        strikethrough: Optional[bool] = None,
        font_size: Optional[int] = None,
        font_family: Optional[str] = None,
        font_weight: Optional[int] = None,
        text_color: Optional[str] = None,
        background_color: Optional[str] = None,
        link_url: Optional[str] = None,
        clear_link: Optional[bool] = None,
        baseline_offset: Optional[str] = None,
        small_caps: Optional[bool] = None,
    ) -> Tuple[bool, str]:
        """
        Validate text formatting parameters.

        Args:
            bold: Bold setting
            italic: Italic setting
            underline: Underline setting
            strikethrough: Strikethrough setting
            font_size: Font size in points
            font_family: Font family name
            text_color: Text color in "#RRGGBB" format
            background_color: Background color in "#RRGGBB" format
            link_url: Hyperlink URL (http/https)

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check if at least one formatting option is provided
        formatting_params = [
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
        ]
        if all(param is None for param in formatting_params):
            return (
                False,
                "At least one formatting parameter must be provided (bold, italic, underline, strikethrough, font_size, font_family, font_weight, text_color, background_color, link_url, clear_link, baseline_offset, or small_caps)",
            )

        # Validate boolean parameters
        for param, name in [
            (bold, "bold"),
            (italic, "italic"),
            (underline, "underline"),
            (strikethrough, "strikethrough"),
        ]:
            if param is not None and not isinstance(param, bool):
                return (
                    False,
                    f"{name} parameter must be boolean (True/False), got {type(param).__name__}",
                )

        for param, name in [
            (clear_link, "clear_link"),
            (small_caps, "small_caps"),
        ]:
            if param is not None and not isinstance(param, bool):
                return (
                    False,
                    f"{name} parameter must be boolean (True/False), got {type(param).__name__}",
                )

        # Validate font size
        if font_size is not None:
            if not isinstance(font_size, int):
                return (
                    False,
                    f"font_size must be an integer, got {type(font_size).__name__}",
                )

            min_size, max_size = self.validation_rules["font_size_range"]
            if not (min_size <= font_size <= max_size):
                return (
                    False,
                    f"font_size must be between {min_size} and {max_size} points, got {font_size}",
                )

        # Validate font family
        if font_family is not None:
            if not isinstance(font_family, str):
                return (
                    False,
                    f"font_family must be a string, got {type(font_family).__name__}",
                )

            if not font_family.strip():
                return False, "font_family cannot be empty"

        if font_weight is not None:
            if not isinstance(font_weight, int):
                return (
                    False,
                    f"font_weight must be an integer, got {type(font_weight).__name__}",
                )
            min_weight, max_weight = self.validation_rules["font_weight_range"]
            if not (min_weight <= font_weight <= max_weight) or font_weight % 100 != 0:
                return (
                    False,
                    "font_weight must be a multiple of 100 between 100 and 900",
                )
            if font_family is None:
                return False, "font_weight requires font_family to also be provided"

        if clear_link and link_url is not None:
            return False, "clear_link cannot be combined with link_url"

        if baseline_offset is not None:
            if not isinstance(baseline_offset, str):
                return (
                    False,
                    "baseline_offset must be a string "
                    f"({', '.join(VALID_TEXT_BASELINE_OFFSETS)})",
                )
            if baseline_offset.upper() not in VALID_TEXT_BASELINE_OFFSETS:
                return (
                    False,
                    "baseline_offset must be one of: "
                    f"{', '.join(VALID_TEXT_BASELINE_OFFSETS)}",
                )

        # Validate colors
        is_valid, error_msg = self.validate_color_param(text_color, "text_color")
        if not is_valid:
            return False, error_msg

        is_valid, error_msg = self.validate_color_param(
            background_color, "background_color"
        )
        if not is_valid:
            return False, error_msg

        is_valid, error_msg = self.validate_link_url(link_url)
        if not is_valid:
            return False, error_msg

        return True, ""

    def validate_link_url(self, link_url: Optional[str]) -> Tuple[bool, str]:
        """Validate hyperlink URL parameters."""
        if link_url is None:
            return True, ""

        if not isinstance(link_url, str):
            return False, f"link_url must be a string, got {type(link_url).__name__}"

        if not link_url.strip():
            return False, "link_url cannot be empty"

        parsed = urlparse(link_url)
        if parsed.scheme not in ("http", "https"):
            return False, "link_url must start with http:// or https://"

        if not parsed.netloc:
            return False, "link_url must include a valid host"

        return True, ""

    def validate_paragraph_style_params(
        self,
        heading_level: Optional[int] = None,
        alignment: Optional[str] = None,
        line_spacing: Optional[float] = None,
        indent_first_line: Optional[float] = None,
        indent_start: Optional[float] = None,
        indent_end: Optional[float] = None,
        space_above: Optional[float] = None,
        space_below: Optional[float] = None,
        named_style_type: Optional[str] = None,
        direction: Optional[str] = None,
        keep_lines_together: Optional[bool] = None,
        keep_with_next: Optional[bool] = None,
        avoid_widow_and_orphan: Optional[bool] = None,
        page_break_before: Optional[bool] = None,
        spacing_mode: Optional[str] = None,
        shading_color: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Validate paragraph style parameters.

        Args:
            heading_level: Heading level 0-6 (0 = NORMAL_TEXT, 1-6 = HEADING_N)
            alignment: Text alignment - 'START', 'CENTER', 'END', or 'JUSTIFIED'
            line_spacing: Line spacing multiplier (must be positive)
            indent_first_line: First line indent in points
            indent_start: Left/start indent in points
            indent_end: Right/end indent in points
            space_above: Space above paragraph in points
            space_below: Space below paragraph in points
            named_style_type: Direct named style (TITLE, SUBTITLE, HEADING_1..6, NORMAL_TEXT)

        Returns:
            Tuple of (is_valid, error_message)
        """
        style_params = [
            heading_level,
            alignment,
            line_spacing,
            indent_first_line,
            indent_start,
            indent_end,
            space_above,
            space_below,
            named_style_type,
            direction,
            keep_lines_together,
            keep_with_next,
            avoid_widow_and_orphan,
            page_break_before,
            spacing_mode,
            shading_color,
        ]
        if all(param is None for param in style_params):
            return (
                False,
                "At least one paragraph style parameter must be provided (heading_level, alignment, line_spacing, indent_first_line, indent_start, indent_end, space_above, space_below, named_style_type, direction, keep_lines_together, keep_with_next, avoid_widow_and_orphan, page_break_before, spacing_mode, or shading_color)",
            )

        if heading_level is not None and named_style_type is not None:
            return (
                False,
                "heading_level and named_style_type are mutually exclusive; provide only one",
            )

        if named_style_type is not None:
            if named_style_type not in VALID_NAMED_STYLE_TYPES:
                return (
                    False,
                    f"Invalid named_style_type '{named_style_type}'. Must be one of: {', '.join(VALID_NAMED_STYLE_TYPES)}",
                )

        if heading_level is not None and named_style_type is None:
            if not isinstance(heading_level, int):
                return (
                    False,
                    f"heading_level must be an integer, got {type(heading_level).__name__}",
                )
            min_level, max_level = self.validation_rules["heading_level_range"]
            if not (min_level <= heading_level <= max_level):
                return (
                    False,
                    f"heading_level must be between {min_level} and {max_level}, got {heading_level}",
                )

        if alignment is not None:
            if not isinstance(alignment, str):
                return (
                    False,
                    f"alignment must be a string, got {type(alignment).__name__}",
                )
            valid = self.validation_rules["valid_alignments"]
            if alignment.upper() not in valid:
                return (
                    False,
                    f"alignment must be one of: {', '.join(valid)}, got '{alignment}'",
                )

        if line_spacing is not None:
            if not isinstance(line_spacing, (int, float)):
                return (
                    False,
                    f"line_spacing must be a number, got {type(line_spacing).__name__}",
                )
            if line_spacing <= 0:
                return False, "line_spacing must be positive"

        for param, name in [
            (indent_first_line, "indent_first_line"),
            (indent_start, "indent_start"),
            (indent_end, "indent_end"),
            (space_above, "space_above"),
            (space_below, "space_below"),
        ]:
            if param is not None:
                if not isinstance(param, (int, float)):
                    return (
                        False,
                        f"{name} must be a number, got {type(param).__name__}",
                    )
                # indent_first_line may be negative (hanging indent)
                if name != "indent_first_line" and param < 0:
                    return False, f"{name} must be non-negative, got {param}"

        if direction is not None:
            if not isinstance(direction, str):
                return (
                    False,
                    f"direction must be a string, got {type(direction).__name__}",
                )
            if direction.upper() not in VALID_PARAGRAPH_DIRECTIONS:
                return (
                    False,
                    "direction must be one of: "
                    f"{', '.join(VALID_PARAGRAPH_DIRECTIONS)}, got '{direction}'",
                )

        for param, name in [
            (keep_lines_together, "keep_lines_together"),
            (keep_with_next, "keep_with_next"),
            (avoid_widow_and_orphan, "avoid_widow_and_orphan"),
            (page_break_before, "page_break_before"),
        ]:
            if param is not None and not isinstance(param, bool):
                return (
                    False,
                    f"{name} must be boolean (True/False), got {type(param).__name__}",
                )

        if spacing_mode is not None:
            if not isinstance(spacing_mode, str):
                return (
                    False,
                    f"spacing_mode must be a string, got {type(spacing_mode).__name__}",
                )
            if spacing_mode.upper() not in VALID_PARAGRAPH_SPACING_MODES:
                return (
                    False,
                    "spacing_mode must be one of: "
                    f"{', '.join(VALID_PARAGRAPH_SPACING_MODES)}, got '{spacing_mode}'",
                )

        is_valid, error_msg = self.validate_color_param(shading_color, "shading_color")
        if not is_valid:
            return False, error_msg

        return True, ""

    def validate_named_range_operation(
        self,
        name: Optional[str] = None,
        start_index: Optional[int] = None,
        end_index: Optional[int] = None,
        named_range_id: Optional[str] = None,
        named_range_name: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Validate named range create/delete/replace inputs."""
        if name is not None:
            if not isinstance(name, str):
                return False, f"name must be a string, got {type(name).__name__}"
            if not name.strip():
                return False, "name cannot be empty"
            if len(name) > 256:
                return False, "name cannot exceed 256 UTF-16 code units"

        if start_index is not None or end_index is not None:
            is_valid, error_msg = self.validate_index_range(start_index, end_index)
            if not is_valid:
                return False, error_msg

        if named_range_id is not None and not isinstance(named_range_id, str):
            return (
                False,
                f"named_range_id must be a string, got {type(named_range_id).__name__}",
            )

        if named_range_name is not None and not isinstance(named_range_name, str):
            return (
                False,
                f"named_range_name must be a string, got {type(named_range_name).__name__}",
            )

        return True, ""

    def validate_document_style_params(
        self,
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
    ) -> Tuple[bool, str]:
        """Validate updateDocumentStyle parameters."""
        params = [
            background_color,
            margin_top,
            margin_bottom,
            margin_left,
            margin_right,
            margin_header,
            margin_footer,
            page_width,
            page_height,
            page_number_start,
            use_even_page_header_footer,
            use_first_page_header_footer,
            flip_page_orientation,
            document_mode,
        ]
        if all(param is None for param in params):
            return False, "At least one document style parameter must be provided"

        for value, name in [
            (margin_top, "margin_top"),
            (margin_bottom, "margin_bottom"),
            (margin_left, "margin_left"),
            (margin_right, "margin_right"),
            (margin_header, "margin_header"),
            (margin_footer, "margin_footer"),
            (page_width, "page_width"),
            (page_height, "page_height"),
        ]:
            if value is not None:
                if not isinstance(value, (int, float)):
                    return False, f"{name} must be a number, got {type(value).__name__}"
                if value <= 0:
                    return False, f"{name} must be positive, got {value}"

        is_valid, error_msg = self.validate_color_param(
            background_color, "background_color"
        )
        if not is_valid:
            return False, error_msg

        if page_number_start is not None:
            if not isinstance(page_number_start, int):
                return (
                    False,
                    f"page_number_start must be an integer, got {type(page_number_start).__name__}",
                )
            if page_number_start < 1:
                return False, "page_number_start must be >= 1"

        for value, name in [
            (use_even_page_header_footer, "use_even_page_header_footer"),
            (use_first_page_header_footer, "use_first_page_header_footer"),
            (flip_page_orientation, "flip_page_orientation"),
        ]:
            if value is not None and not isinstance(value, bool):
                return (
                    False,
                    f"{name} must be boolean (True/False), got {type(value).__name__}",
                )

        if document_mode is not None:
            if not isinstance(document_mode, str):
                return (
                    False,
                    f"document_mode must be a string, got {type(document_mode).__name__}",
                )
            if document_mode.upper() not in VALID_DOCUMENT_MODES:
                return (
                    False,
                    f"document_mode must be one of: {', '.join(VALID_DOCUMENT_MODES)}",
                )

        return True, ""

    def validate_section_style_params(
        self,
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
    ) -> Tuple[bool, str]:
        """Validate updateSectionStyle parameters."""
        params = [
            margin_top,
            margin_bottom,
            margin_left,
            margin_right,
            margin_header,
            margin_footer,
            page_number_start,
            use_first_page_header_footer,
            flip_page_orientation,
            content_direction,
            column_count,
            column_spacing,
            column_separator_style,
        ]
        if all(param is None for param in params):
            return False, "At least one section style parameter must be provided"

        is_valid, error_msg = self.validate_document_style_params(
            margin_top=margin_top,
            margin_bottom=margin_bottom,
            margin_left=margin_left,
            margin_right=margin_right,
            margin_header=margin_header,
            margin_footer=margin_footer,
            page_number_start=page_number_start,
            flip_page_orientation=flip_page_orientation,
        )
        if not is_valid and "At least one document style parameter" not in error_msg:
            return False, error_msg

        if use_first_page_header_footer is not None and not isinstance(
            use_first_page_header_footer, bool
        ):
            return (
                False,
                "use_first_page_header_footer must be boolean (True/False)",
            )

        if content_direction is not None:
            if not isinstance(content_direction, str):
                return (
                    False,
                    f"content_direction must be a string, got {type(content_direction).__name__}",
                )
            if content_direction.upper() not in VALID_CONTENT_DIRECTIONS:
                return (
                    False,
                    "content_direction must be one of: "
                    f"{', '.join(VALID_CONTENT_DIRECTIONS)}",
                )

        if column_count is not None:
            if not isinstance(column_count, int):
                return (
                    False,
                    f"column_count must be an integer, got {type(column_count).__name__}",
                )
            if column_count < 1 or column_count > 3:
                return False, "column_count must be between 1 and 3"

        if column_spacing is not None:
            if not isinstance(column_spacing, (int, float)):
                return (
                    False,
                    f"column_spacing must be a number, got {type(column_spacing).__name__}",
                )
            if column_spacing < 0:
                return False, "column_spacing must be non-negative"
            if column_count is None:
                return False, "column_spacing requires column_count to be provided"

        if column_separator_style is not None:
            if not isinstance(column_separator_style, str):
                return (
                    False,
                    "column_separator_style must be a string, "
                    f"got {type(column_separator_style).__name__}",
                )
            if column_separator_style.upper() not in VALID_COLUMN_SEPARATOR_STYLES:
                return (
                    False,
                    "column_separator_style must be one of: "
                    f"{', '.join(VALID_COLUMN_SEPARATOR_STYLES)}",
                )

        return True, ""

    VALID_CONTENT_ALIGNMENTS = ("TOP", "MIDDLE", "BOTTOM")

    def validate_table_cell_style_params(
        self,
        background_color: Optional[str] = None,
        border_color: Optional[str] = None,
        border_width: Optional[float] = None,
        padding_top: Optional[float] = None,
        padding_bottom: Optional[float] = None,
        padding_left: Optional[float] = None,
        padding_right: Optional[float] = None,
        content_alignment: Optional[str] = None,
        row_index: Optional[int] = None,
        column_index: Optional[int] = None,
        row_span: Optional[int] = None,
        column_span: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Validate table cell style parameters for updateTableCellStyle requests.

        Args:
            background_color: Cell background color in "#RRGGBB" format
            border_color: Border color in "#RRGGBB" format
            border_width: Border width in points
            padding_top: Top padding in points (non-negative)
            padding_bottom: Bottom padding in points (non-negative)
            padding_left: Left padding in points (non-negative)
            padding_right: Right padding in points (non-negative)
            content_alignment: Vertical alignment ("TOP", "MIDDLE", "BOTTOM")
            row_index: Optional starting row index for a targeted cell range
            column_index: Optional starting column index for a targeted cell range
            row_span: Optional row span for a targeted cell range
            column_span: Optional column span for a targeted cell range

        Returns:
            Tuple of (is_valid, error_message)
        """
        if all(
            param is None
            for param in (
                background_color,
                border_color,
                border_width,
                padding_top,
                padding_bottom,
                padding_left,
                padding_right,
                content_alignment,
            )
        ):
            return (
                False,
                "At least one table cell style parameter must be provided "
                "(background_color, border_color, border_width, padding_top, "
                "padding_bottom, padding_left, padding_right, or content_alignment)",
            )

        is_valid, error_msg = self.validate_color_param(
            background_color, "background_color"
        )
        if not is_valid:
            return False, error_msg

        is_valid, error_msg = self.validate_color_param(border_color, "border_color")
        if not is_valid:
            return False, error_msg

        if border_width is not None:
            if not isinstance(border_width, (int, float)):
                return (
                    False,
                    f"border_width must be a number, got {type(border_width).__name__}",
                )
            if border_width <= 0:
                return False, f"border_width must be positive, got {border_width}"

        for padding_value, padding_name in (
            (padding_top, "padding_top"),
            (padding_bottom, "padding_bottom"),
            (padding_left, "padding_left"),
            (padding_right, "padding_right"),
        ):
            if padding_value is not None:
                if isinstance(padding_value, bool) or not isinstance(
                    padding_value, (int, float)
                ):
                    return (
                        False,
                        f"{padding_name} must be a number, got {type(padding_value).__name__}",
                    )
                if padding_value < 0:
                    return (
                        False,
                        f"{padding_name} must be non-negative, got {padding_value}",
                    )

        if content_alignment is not None:
            if not isinstance(content_alignment, str):
                return (
                    False,
                    f"content_alignment must be a string, got {type(content_alignment).__name__}",
                )
            if content_alignment.upper() not in self.VALID_CONTENT_ALIGNMENTS:
                return (
                    False,
                    f"content_alignment must be one of: {', '.join(self.VALID_CONTENT_ALIGNMENTS)}",
                )

        has_range_start = row_index is not None or column_index is not None
        has_range_span = row_span is not None or column_span is not None
        if has_range_start or has_range_span:
            if row_index is None or column_index is None:
                return (
                    False,
                    "row_index and column_index must both be provided when targeting a table range",
                )

            for value, name in (
                (row_index, "row_index"),
                (column_index, "column_index"),
            ):
                if not isinstance(value, int):
                    return (
                        False,
                        f"{name} must be an integer, got {type(value).__name__}",
                    )
                if value < 0:
                    return False, f"{name} must be non-negative, got {value}"

            for value, name in (
                (row_span, "row_span"),
                (column_span, "column_span"),
            ):
                if value is not None:
                    if not isinstance(value, int):
                        return (
                            False,
                            f"{name} must be an integer, got {type(value).__name__}",
                        )
                    if value <= 0:
                        return False, f"{name} must be positive, got {value}"

        return True, ""

    def validate_color_param(
        self, color: Optional[str], param_name: str
    ) -> Tuple[bool, str]:
        """Validate color parameters (hex string "#RRGGBB")."""
        if color is None:
            return True, ""

        if not isinstance(color, str):
            return False, f"{param_name} must be a hex string like '#RRGGBB'"

        if len(color) != 7 or not color.startswith("#"):
            return False, f"{param_name} must be a hex string like '#RRGGBB'"

        hex_color = color[1:]
        if any(c not in "0123456789abcdefABCDEF" for c in hex_color):
            return False, f"{param_name} must be a hex string like '#RRGGBB'"

        return True, ""

    def validate_index(self, index: int, context: str = "Index") -> Tuple[bool, str]:
        """
        Validate a single document index.

        Args:
            index: Index to validate
            context: Context description for error messages

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(index, int):
            return False, f"{context} must be an integer, got {type(index).__name__}"

        if index < 0:
            return (
                False,
                f"{context} {index} is negative. You MUST call inspect_doc_structure first to get the proper insertion index.",
            )

        return True, ""

    def validate_index_range(
        self,
        start_index: int,
        end_index: Optional[int] = None,
        document_length: Optional[int] = None,
    ) -> Tuple[bool, str]:
        """
        Validate document index ranges.

        Args:
            start_index: Starting index
            end_index: Ending index (optional)
            document_length: Total document length for bounds checking

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate start_index
        if not isinstance(start_index, int):
            return (
                False,
                f"start_index must be an integer, got {type(start_index).__name__}",
            )

        if start_index < 0:
            return False, f"start_index cannot be negative, got {start_index}"

        # Validate end_index if provided
        if end_index is not None:
            if not isinstance(end_index, int):
                return (
                    False,
                    f"end_index must be an integer, got {type(end_index).__name__}",
                )

            if end_index <= start_index:
                return (
                    False,
                    f"end_index ({end_index}) must be greater than start_index ({start_index})",
                )

        # Validate against document length if provided
        if document_length is not None:
            if start_index >= document_length:
                return (
                    False,
                    f"start_index ({start_index}) exceeds document length ({document_length})",
                )

            if end_index is not None and end_index > document_length:
                return (
                    False,
                    f"end_index ({end_index}) exceeds document length ({document_length})",
                )

        return True, ""

    def validate_element_insertion_params(
        self, element_type: str, index: int, **kwargs
    ) -> Tuple[bool, str]:
        """
        Validate parameters for element insertion.

        Args:
            element_type: Type of element to insert
            index: Insertion index
            **kwargs: Additional parameters specific to element type

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Validate element type
        if element_type not in self.validation_rules["valid_element_types"]:
            valid_types = ", ".join(self.validation_rules["valid_element_types"])
            return (
                False,
                f"Invalid element_type '{element_type}'. Must be one of: {valid_types}",
            )

        # Validate index
        if not isinstance(index, int) or index < 0:
            return False, f"index must be a non-negative integer, got {index}"

        # Validate element-specific parameters
        if element_type == "table":
            rows = kwargs.get("rows")
            columns = kwargs.get("columns")

            if not rows or not columns:
                return False, "Table insertion requires 'rows' and 'columns' parameters"

            if not isinstance(rows, int) or not isinstance(columns, int):
                return False, "Table rows and columns must be integers"

            if rows <= 0 or columns <= 0:
                return False, "Table rows and columns must be positive integers"

            if rows > self.validation_rules["table_max_rows"]:
                return (
                    False,
                    f"Too many rows ({rows}). Maximum: {self.validation_rules['table_max_rows']}",
                )

            if columns > self.validation_rules["table_max_columns"]:
                return (
                    False,
                    f"Too many columns ({columns}). Maximum: {self.validation_rules['table_max_columns']}",
                )

        elif element_type == "list":
            list_type = kwargs.get("list_type")

            if not list_type:
                return False, "List insertion requires 'list_type' parameter"

            if list_type not in self.validation_rules["valid_list_types"]:
                valid_types = ", ".join(self.validation_rules["valid_list_types"])
                return (
                    False,
                    f"Invalid list_type '{list_type}'. Must be one of: {valid_types}",
                )

        return True, ""

    def validate_header_footer_params(
        self, section_type: str, header_footer_type: str = "DEFAULT"
    ) -> Tuple[bool, str]:
        """
        Validate header/footer operation parameters.

        Args:
            section_type: Type of section ("header" or "footer")
            header_footer_type: Specific header/footer type

        Returns:
            Tuple of (is_valid, error_message)
        """
        if section_type not in self.validation_rules["valid_section_types"]:
            valid_types = ", ".join(self.validation_rules["valid_section_types"])
            return (
                False,
                f"section_type must be one of: {valid_types}, got '{section_type}'",
            )

        if header_footer_type not in self.validation_rules["valid_header_footer_types"]:
            valid_types = ", ".join(self.validation_rules["valid_header_footer_types"])
            return (
                False,
                f"header_footer_type must be one of: {valid_types}, got '{header_footer_type}'",
            )

        return True, ""

    def validate_batch_operations(
        self, operations: List[Dict[str, Any]]
    ) -> Tuple[bool, str]:
        """
        Validate a list of batch operations.

        Args:
            operations: List of operation dictionaries

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not operations:
            return False, "Operations list cannot be empty"

        if not isinstance(operations, list):
            return False, f"Operations must be a list, got {type(operations).__name__}"

        # Validate each operation
        for i, op in enumerate(operations):
            if not isinstance(op, dict):
                return (
                    False,
                    f"Operation {i + 1} must be a dictionary, got {type(op).__name__}",
                )

            if "type" not in op:
                return False, f"Operation {i + 1} missing required 'type' field"

            # Validate required fields for the operation type
            is_valid, error_msg = validate_operation(op)
            if not is_valid:
                return False, f"Operation {i + 1}: {error_msg}"

            op_type = op["type"]

            if op_type == "format_text":
                is_valid, error_msg = self.validate_text_formatting_params(
                    op.get("bold"),
                    op.get("italic"),
                    op.get("underline"),
                    op.get("strikethrough"),
                    op.get("font_size"),
                    op.get("font_family"),
                    op.get("font_weight"),
                    op.get("text_color"),
                    op.get("background_color"),
                    op.get("link_url"),
                    op.get("clear_link"),
                    op.get("baseline_offset"),
                    op.get("small_caps"),
                )
                if not is_valid:
                    return False, f"Operation {i + 1} (format_text): {error_msg}"

                is_valid, error_msg = self.validate_index_range(
                    op["start_index"], op["end_index"]
                )
                if not is_valid:
                    return False, f"Operation {i + 1} (format_text): {error_msg}"

            elif op_type == "update_paragraph_style":
                is_valid, error_msg = self.validate_paragraph_style_params(
                    op.get("heading_level"),
                    op.get("alignment"),
                    op.get("line_spacing"),
                    op.get("indent_first_line"),
                    op.get("indent_start"),
                    op.get("indent_end"),
                    op.get("space_above"),
                    op.get("space_below"),
                    op.get("named_style_type"),
                    op.get("direction"),
                    op.get("keep_lines_together"),
                    op.get("keep_with_next"),
                    op.get("avoid_widow_and_orphan"),
                    op.get("page_break_before"),
                    op.get("spacing_mode"),
                    op.get("shading_color"),
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_paragraph_style): {error_msg}",
                    )

                is_valid, error_msg = self.validate_index_range(
                    op["start_index"], op["end_index"]
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_paragraph_style): {error_msg}",
                    )

            elif op_type == "update_table_cell_style":
                is_valid, error_msg = self.validate_index(
                    op["table_start_index"], "table_start_index"
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_table_cell_style): {error_msg}",
                    )

                is_valid, error_msg = self.validate_table_cell_style_params(
                    op.get("background_color"),
                    op.get("border_color"),
                    op.get("border_width"),
                    op.get("padding_top"),
                    op.get("padding_bottom"),
                    op.get("padding_left"),
                    op.get("padding_right"),
                    op.get("content_alignment"),
                    op.get("row_index"),
                    op.get("column_index"),
                    op.get("row_span"),
                    op.get("column_span"),
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_table_cell_style): {error_msg}",
                    )

            elif op_type == "create_named_range":
                is_valid, error_msg = self.validate_named_range_operation(
                    name=op.get("name"),
                    start_index=op.get("start_index"),
                    end_index=op.get("end_index"),
                )
                if not is_valid:
                    return False, f"Operation {i + 1} (create_named_range): {error_msg}"

            elif op_type == "replace_named_range_content":
                is_valid, error_msg = self.validate_named_range_operation(
                    named_range_id=op.get("named_range_id"),
                    named_range_name=op.get("named_range_name"),
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (replace_named_range_content): {error_msg}",
                    )
                if not op.get("named_range_id") and not op.get("named_range_name"):
                    return (
                        False,
                        f"Operation {i + 1} (replace_named_range_content): named_range_id or named_range_name is required",
                    )

            elif op_type == "delete_named_range":
                is_valid, error_msg = self.validate_named_range_operation(
                    named_range_id=op.get("named_range_id"),
                    named_range_name=op.get("named_range_name"),
                )
                if not is_valid:
                    return False, f"Operation {i + 1} (delete_named_range): {error_msg}"
                if not op.get("named_range_id") and not op.get("named_range_name"):
                    return (
                        False,
                        f"Operation {i + 1} (delete_named_range): named_range_id or named_range_name is required",
                    )

            elif op_type == "insert_section_break":
                if op.get("index") is not None:
                    is_valid, error_msg = self.validate_index(op["index"], "index")
                    if not is_valid:
                        return (
                            False,
                            f"Operation {i + 1} (insert_section_break): {error_msg}",
                        )
                if op.get("section_type") is not None:
                    section_type = op["section_type"]
                    if (
                        not isinstance(section_type, str)
                        or section_type.upper() not in VALID_SECTION_TYPES
                    ):
                        return (
                            False,
                            f"Operation {i + 1} (insert_section_break): section_type must be one of {', '.join(VALID_SECTION_TYPES)}",
                        )

            elif op_type == "update_document_style":
                is_valid, error_msg = self.validate_document_style_params(
                    background_color=op.get("background_color"),
                    margin_top=op.get("margin_top"),
                    margin_bottom=op.get("margin_bottom"),
                    margin_left=op.get("margin_left"),
                    margin_right=op.get("margin_right"),
                    margin_header=op.get("margin_header"),
                    margin_footer=op.get("margin_footer"),
                    page_width=op.get("page_width"),
                    page_height=op.get("page_height"),
                    page_number_start=op.get("page_number_start"),
                    use_even_page_header_footer=op.get("use_even_page_header_footer"),
                    use_first_page_header_footer=op.get("use_first_page_header_footer"),
                    flip_page_orientation=op.get("flip_page_orientation"),
                    document_mode=op.get("document_mode"),
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_document_style): {error_msg}",
                    )

            elif op_type == "update_section_style":
                is_valid, error_msg = self.validate_index_range(
                    op["start_index"], op["end_index"]
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_section_style): {error_msg}",
                    )

                is_valid, error_msg = self.validate_section_style_params(
                    margin_top=op.get("margin_top"),
                    margin_bottom=op.get("margin_bottom"),
                    margin_left=op.get("margin_left"),
                    margin_right=op.get("margin_right"),
                    margin_header=op.get("margin_header"),
                    margin_footer=op.get("margin_footer"),
                    page_number_start=op.get("page_number_start"),
                    use_first_page_header_footer=op.get("use_first_page_header_footer"),
                    flip_page_orientation=op.get("flip_page_orientation"),
                    content_direction=op.get("content_direction"),
                    column_count=op.get("column_count"),
                    column_spacing=op.get("column_spacing"),
                    column_separator_style=op.get("column_separator_style"),
                )
                if not is_valid:
                    return (
                        False,
                        f"Operation {i + 1} (update_section_style): {error_msg}",
                    )

            elif op_type == "create_bullet_list":
                list_type = op.get("list_type", "UNORDERED")
                if list_type not in ("UNORDERED", "ORDERED", "CHECKBOX", "NONE"):
                    return (
                        False,
                        f"Operation {i + 1} (create_bullet_list): list_type must be UNORDERED, ORDERED, CHECKBOX, or NONE",
                    )
                bullet_preset = op.get("bullet_preset")
                if (
                    bullet_preset is not None
                    and bullet_preset not in VALID_BULLET_PRESETS
                ):
                    return (
                        False,
                        f"Operation {i + 1} (create_bullet_list): bullet_preset must be one of {', '.join(VALID_BULLET_PRESETS)}",
                    )

        return True, ""

    def validate_text_content(
        self, text: str, max_length: Optional[int] = None
    ) -> Tuple[bool, str]:
        """
        Validate text content for insertion.

        Args:
            text: Text to validate
            max_length: Maximum allowed length

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not isinstance(text, str):
            return False, f"Text must be a string, got {type(text).__name__}"

        max_len = max_length or self.validation_rules["max_text_length"]
        if len(text) > max_len:
            return False, f"Text too long ({len(text)} characters). Maximum: {max_len}"

        return True, ""

    def get_validation_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all validation rules and constraints.

        Returns:
            Dictionary containing validation rules
        """
        return {
            "constraints": self.validation_rules.copy(),
            "supported_operations": {
                "table_operations": ["create_table", "populate_table"],
                "text_operations": [
                    "insert_text",
                    "format_text",
                    "find_replace",
                    "update_paragraph_style",
                    "update_table_cell_style",
                ],
                "element_operations": [
                    "insert_table",
                    "insert_list",
                    "insert_page_break",
                ],
                "header_footer_operations": ["update_header", "update_footer"],
            },
            "data_formats": {
                "table_data": "2D list of strings: [['col1', 'col2'], ['row1col1', 'row1col2']]",
                "text_formatting": "Optional boolean/integer parameters for styling",
                "document_indices": "Non-negative integers for position specification",
            },
        }
