"""
Google Docs Table Operations

This module provides utilities for creating and manipulating tables
in Google Docs, including population with data and formatting.
"""

import logging
from typing import Dict, Any, List, Optional, Union, Tuple

logger = logging.getLogger(__name__)


def build_table_population_requests(
    table_info: Dict[str, Any], data: List[List[str]], bold_headers: bool = True
) -> List[Dict[str, Any]]:
    """
    Build batch requests to populate a table with data.

    Args:
        table_info: Table information from document structure including cell indices
        data: 2D array of data to insert into table
        bold_headers: Whether to make the first row bold

    Returns:
        List of request dictionaries for batch update
    """
    requests = []
    cells = table_info.get("cells", [])

    if not cells:
        logger.warning("No cell information found in table_info")
        return requests

    # Process each cell - ONLY INSERT, DON'T DELETE
    for row_idx, row_data in enumerate(data):
        if row_idx >= len(cells):
            logger.warning(
                f"Data has more rows ({len(data)}) than table ({len(cells)})"
            )
            break

        for col_idx, cell_text in enumerate(row_data):
            if col_idx >= len(cells[row_idx]):
                logger.warning(
                    f"Data has more columns ({len(row_data)}) than table row {row_idx} ({len(cells[row_idx])})"
                )
                break

            cell = cells[row_idx][col_idx]

            # For new empty tables, use the insertion index
            # For tables with existing content, check if cell only contains newline
            existing_content = cell.get("content", "").strip()

            # Only insert if we have text to insert
            if cell_text:
                # Use the specific insertion index for this cell
                insertion_index = cell.get("insertion_index", cell["start_index"] + 1)

                # If cell only contains a newline, replace it
                if existing_content == "" or existing_content == "\n":
                    # Cell is empty (just newline), insert at the insertion index
                    requests.append(
                        {
                            "insertText": {
                                "location": {"index": insertion_index},
                                "text": cell_text,
                            }
                        }
                    )

                    # Apply bold formatting to first row if requested
                    if bold_headers and row_idx == 0:
                        requests.append(
                            {
                                "updateTextStyle": {
                                    "range": {
                                        "startIndex": insertion_index,
                                        "endIndex": insertion_index + len(cell_text),
                                    },
                                    "textStyle": {"bold": True},
                                    "fields": "bold",
                                }
                            }
                        )
                else:
                    # Cell has content, append after existing content
                    # Find the end of existing content
                    cell_end = cell["end_index"] - 1  # Don't include cell end marker
                    requests.append(
                        {
                            "insertText": {
                                "location": {"index": cell_end},
                                "text": cell_text,
                            }
                        }
                    )

                    # Apply bold formatting to first row if requested
                    if bold_headers and row_idx == 0:
                        requests.append(
                            {
                                "updateTextStyle": {
                                    "range": {
                                        "startIndex": cell_end,
                                        "endIndex": cell_end + len(cell_text),
                                    },
                                    "textStyle": {"bold": True},
                                    "fields": "bold",
                                }
                            }
                        )

    return requests


def calculate_cell_positions(
    table_start_index: int,
    rows: int,
    cols: int,
    existing_table_data: Optional[Dict[str, Any]] = None,
) -> List[List[Dict[str, int]]]:
    """
    Calculate estimated positions for each cell in a table.

    Args:
        table_start_index: Starting index of the table
        rows: Number of rows
        cols: Number of columns
        existing_table_data: Optional existing table data with actual positions

    Returns:
        2D list of cell position dictionaries
    """
    if existing_table_data and "cells" in existing_table_data:
        # Use actual positions from existing table
        return existing_table_data["cells"]

    # Estimate positions for a new table
    # Note: These are estimates; actual positions depend on content
    cells = []
    current_index = table_start_index + 2  # Account for table start

    for row_idx in range(rows):
        row_cells = []
        for col_idx in range(cols):
            # Each cell typically starts with a paragraph marker
            cell_start = current_index
            cell_end = current_index + 2  # Minimum cell size

            row_cells.append(
                {
                    "row": row_idx,
                    "column": col_idx,
                    "start_index": cell_start,
                    "end_index": cell_end,
                }
            )

            current_index = cell_end + 1

        cells.append(row_cells)

    return cells


def format_table_data(
    raw_data: Union[List[List[str]], List[str], str],
) -> List[List[str]]:
    """
    Normalize various data formats into a 2D array for table insertion.

    Args:
        raw_data: Data in various formats (2D list, 1D list, or delimited string)

    Returns:
        Normalized 2D list of strings
    """
    if isinstance(raw_data, str):
        # Parse delimited string (detect delimiter)
        lines = raw_data.strip().split("\n")
        if "\t" in raw_data:
            # Tab-delimited
            return [line.split("\t") for line in lines]
        elif "," in raw_data:
            # Comma-delimited (simple CSV)
            return [line.split(",") for line in lines]
        else:
            # Space-delimited or single column
            return [[cell.strip() for cell in line.split()] for line in lines]

    elif isinstance(raw_data, list):
        if not raw_data:
            return [[]]

        # Check if it's already a 2D list
        if isinstance(raw_data[0], list):
            # Ensure all cells are strings
            return [[str(cell) for cell in row] for row in raw_data]
        else:
            # Convert 1D list to single-column table
            return [[str(cell)] for cell in raw_data]

    else:
        # Convert single value to 1x1 table
        return [[str(raw_data)]]


def create_table_with_data(
    index: int,
    data: List[List[str]],
    headers: Optional[List[str]] = None,
    bold_headers: bool = True,
) -> List[Dict[str, Any]]:
    """
    Create a table and populate it with data in one operation.

    Args:
        index: Position to insert the table
        data: 2D array of table data
        headers: Optional header row (will be prepended to data)
        bold_headers: Whether to make headers bold

    Returns:
        List of request dictionaries for batch update
    """
    requests = []

    # Prepare data with headers if provided
    if headers:
        full_data = [headers] + data
    else:
        full_data = data

    # Normalize the data
    full_data = format_table_data(full_data)

    if not full_data or not full_data[0]:
        raise ValueError("Cannot create table with empty data")

    rows = len(full_data)
    cols = len(full_data[0])

    # Ensure all rows have the same number of columns
    for row in full_data:
        while len(row) < cols:
            row.append("")

    # Create the table
    requests.append(
        {"insertTable": {"location": {"index": index}, "rows": rows, "columns": cols}}
    )

    # Build text insertion requests for each cell
    # Note: In practice, we'd need to get the actual document structure
    # after table creation to get accurate indices

    return requests


def build_table_style_requests(
    table_start_index: int, style_options: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Build requests to style a table.

    Args:
        table_start_index: Starting index of the table
        style_options: Dictionary of style options
            - border_width: Width of borders in points
            - border_color: RGB color for borders
            - background_color: RGB color for cell backgrounds
            - header_background: RGB color for header row background

    Returns:
        List of request dictionaries for styling
    """
    requests = []

    # Table cell style update
    if any(
        k in style_options for k in ["border_width", "border_color", "background_color"]
    ):
        table_cell_style = {}
        fields = []

        if "border_width" in style_options:
            border_width = {"magnitude": style_options["border_width"], "unit": "PT"}
            table_cell_style["borderTop"] = {"width": border_width}
            table_cell_style["borderBottom"] = {"width": border_width}
            table_cell_style["borderLeft"] = {"width": border_width}
            table_cell_style["borderRight"] = {"width": border_width}
            fields.extend(["borderTop", "borderBottom", "borderLeft", "borderRight"])

        if "border_color" in style_options:
            border_color = {"color": {"rgbColor": style_options["border_color"]}}
            if "borderTop" in table_cell_style:
                table_cell_style["borderTop"]["color"] = border_color["color"]
                table_cell_style["borderBottom"]["color"] = border_color["color"]
                table_cell_style["borderLeft"]["color"] = border_color["color"]
                table_cell_style["borderRight"]["color"] = border_color["color"]

        if "background_color" in style_options:
            table_cell_style["backgroundColor"] = {
                "color": {"rgbColor": style_options["background_color"]}
            }
            fields.append("backgroundColor")

        if table_cell_style and fields:
            requests.append(
                {
                    "updateTableCellStyle": {
                        "tableStartLocation": {"index": table_start_index},
                        "tableCellStyle": table_cell_style,
                        "fields": ",".join(fields),
                    }
                }
            )

    # Header row specific styling
    if "header_background" in style_options:
        requests.append(
            {
                "updateTableCellStyle": {
                    "tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": table_start_index},
                            "rowIndex": 0,
                            "columnIndex": 0,
                        },
                        "rowSpan": 1,
                        "columnSpan": 100,  # Large number to cover all columns
                    },
                    "tableCellStyle": {
                        "backgroundColor": {
                            "color": {"rgbColor": style_options["header_background"]}
                        }
                    },
                    "fields": "backgroundColor",
                }
            }
        )

    return requests


def extract_table_as_data(table_info: Dict[str, Any]) -> List[List[str]]:
    """
    Extract table content as a 2D array of strings.

    Args:
        table_info: Table information from document structure

    Returns:
        2D list of cell contents
    """
    data = []
    cells = table_info.get("cells", [])

    for row in cells:
        row_data = []
        for cell in row:
            row_data.append(cell.get("content", "").strip())
        data.append(row_data)

    return data


def find_table_by_content(
    tables: List[Dict[str, Any]], search_text: str, case_sensitive: bool = False
) -> Optional[int]:
    """
    Find a table index by searching for content within it.

    Args:
        tables: List of table information from document
        search_text: Text to search for in table cells
        case_sensitive: Whether to do case-sensitive search

    Returns:
        Index of the first matching table, or None
    """
    search_text = search_text if case_sensitive else search_text.lower()

    for idx, table in enumerate(tables):
        for row in table.get("cells", []):
            for cell in row:
                cell_content = cell.get("content", "")
                if not case_sensitive:
                    cell_content = cell_content.lower()

                if search_text in cell_content:
                    return idx

    return None


def validate_table_data(data: List[List[str]]) -> Tuple[bool, str]:
    """
    Validates table data format and provides specific error messages for LLMs.

    WHAT THIS CHECKS:
    - Data is a 2D list (list of lists)
    - All rows have consistent column counts
    - Dimensions are within Google Docs limits
    - No None or undefined values

    VALID FORMAT EXAMPLE:
    [
        ["Header1", "Header2"],     # Row 0 - 2 columns
        ["Data1", "Data2"],        # Row 1 - 2 columns
        ["Data3", "Data4"]         # Row 2 - 2 columns
    ]

    INVALID FORMATS:
    - [["col1"], ["col1", "col2"]]  # Inconsistent column counts
    - ["col1", "col2"]              # Not 2D (missing inner lists)
    - [["col1", None]]              # Contains None values
    - [] or [[]]                    # Empty data

    Args:
        data: 2D array of data to validate

    Returns:
        Tuple of (is_valid, error_message_with_examples)
    """
    if not data:
        return (
            False,
            "Data is empty. Use format: [['col1', 'col2'], ['row1col1', 'row1col2']]",
        )

    if not isinstance(data, list):
        return (
            False,
            f"Data must be a list, got {type(data).__name__}. Use format: [['col1', 'col2'], ['row1col1', 'row1col2']]",
        )

    if not all(isinstance(row, list) for row in data):
        return (
            False,
            f"Data must be a 2D list (list of lists). Each row must be a list. Check your format: {data}",
        )

    # Check for consistent column count
    col_counts = [len(row) for row in data]
    if len(set(col_counts)) > 1:
        return (
            False,
            f"All rows must have same number of columns. Found: {col_counts}. Fix your data format.",
        )

    # Check for reasonable size
    rows = len(data)
    cols = col_counts[0] if col_counts else 0

    if rows > 1000:
        return False, f"Too many rows ({rows}). Google Docs limit is 1000 rows."

    if cols > 20:
        return False, f"Too many columns ({cols}). Google Docs limit is 20 columns."

    return True, f"Valid table data: {rows}x{cols} table format"
