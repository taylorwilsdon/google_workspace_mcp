"""
Google Sheets MCP Tools

This module provides MCP tools for interacting with Google Sheets API.
"""

import logging
import asyncio
import json
from typing import List, Optional, Union


from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors
from core.comments import create_comment_tools

# Configure module logger
logger = logging.getLogger(__name__)


@server.tool()
@handle_http_errors("list_spreadsheets", is_read_only=True, service_type="sheets")
@require_google_service("drive", "drive_read")
async def list_spreadsheets(
    service,
    user_google_email: str,
    max_results: int = 25,
) -> str:
    """
    Lists spreadsheets from Google Drive that the user has access to.

    Args:
        user_google_email (str): The user's Google email address. Required.
        max_results (int): Maximum number of spreadsheets to return. Defaults to 25.

    Returns:
        str: A formatted list of spreadsheet files (name, ID, modified time).
    """
    logger.info(f"[list_spreadsheets] Invoked. Email: '{user_google_email}'")

    files_response = await asyncio.to_thread(
        service.files()
        .list(
            q="mimeType='application/vnd.google-apps.spreadsheet'",
            pageSize=max_results,
            fields="files(id,name,modifiedTime,webViewLink)",
            orderBy="modifiedTime desc",
        )
        .execute
    )

    files = files_response.get("files", [])
    if not files:
        return f"No spreadsheets found for {user_google_email}."

    spreadsheets_list = [
        f"- \"{file['name']}\" (ID: {file['id']}) | Modified: {file.get('modifiedTime', 'Unknown')} | Link: {file.get('webViewLink', 'No link')}"
        for file in files
    ]

    text_output = (
        f"Successfully listed {len(files)} spreadsheets for {user_google_email}:\n"
        + "\n".join(spreadsheets_list)
    )

    logger.info(f"Successfully listed {len(files)} spreadsheets for {user_google_email}.")
    return text_output


@server.tool()
@handle_http_errors("get_spreadsheet_info", is_read_only=True, service_type="sheets")
@require_google_service("sheets", "sheets_read")
async def get_spreadsheet_info(
    service,
    user_google_email: str,
    spreadsheet_id: str,
) -> str:
    """
    Gets information about a specific spreadsheet including its sheets.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet to get info for. Required.

    Returns:
        str: Formatted spreadsheet information including title and sheets list.
    """
    logger.info(f"[get_spreadsheet_info] Invoked. Email: '{user_google_email}', Spreadsheet ID: {spreadsheet_id}")

    spreadsheet = await asyncio.to_thread(
        service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
    )

    title = spreadsheet.get("properties", {}).get("title", "Unknown")
    sheets = spreadsheet.get("sheets", [])

    sheets_info = []
    for sheet in sheets:
        sheet_props = sheet.get("properties", {})
        sheet_name = sheet_props.get("title", "Unknown")
        sheet_id = sheet_props.get("sheetId", "Unknown")
        grid_props = sheet_props.get("gridProperties", {})
        rows = grid_props.get("rowCount", "Unknown")
        cols = grid_props.get("columnCount", "Unknown")

        sheets_info.append(
            f"  - \"{sheet_name}\" (ID: {sheet_id}) | Size: {rows}x{cols}"
        )

    text_output = (
        f"Spreadsheet: \"{title}\" (ID: {spreadsheet_id})\n"
        f"Sheets ({len(sheets)}):\n"
        + "\n".join(sheets_info) if sheets_info else "  No sheets found"
    )

    logger.info(f"Successfully retrieved info for spreadsheet {spreadsheet_id} for {user_google_email}.")
    return text_output


@server.tool()
@handle_http_errors("read_sheet_values", is_read_only=True, service_type="sheets")
@require_google_service("sheets", "sheets_read")
async def read_sheet_values(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str = "A1:Z1000",
) -> str:
    """
    Reads values from a specific range in a Google Sheet.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (str): The range to read (e.g., "Sheet1!A1:D10", "A1:D10"). Defaults to "A1:Z1000".

    Returns:
        str: The formatted values from the specified range.
    """
    logger.info(f"[read_sheet_values] Invoked. Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Range: {range_name}")

    result = await asyncio.to_thread(
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_name)
        .execute
    )

    values = result.get("values", [])
    if not values:
        return f"No data found in range '{range_name}' for {user_google_email}."

    # Format the output as a readable table
    formatted_rows = []
    for i, row in enumerate(values, 1):
        # Pad row with empty strings to show structure
        padded_row = row + [""] * max(0, len(values[0]) - len(row)) if values else row
        formatted_rows.append(f"Row {i:2d}: {padded_row}")

    text_output = (
        f"Successfully read {len(values)} rows from range '{range_name}' in spreadsheet {spreadsheet_id} for {user_google_email}:\n"
        + "\n".join(formatted_rows[:50])  # Limit to first 50 rows for readability
        + (f"\n... and {len(values) - 50} more rows" if len(values) > 50 else "")
    )

    logger.info(f"Successfully read {len(values)} rows for {user_google_email}.")
    return text_output


@server.tool()
@handle_http_errors("modify_sheet_values", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def modify_sheet_values(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str,
    values: Optional[Union[str, List[List[str]]]] = None,
    value_input_option: str = "USER_ENTERED",
    clear_values: bool = False,
) -> str:
    """
    Modifies values in a specific range of a Google Sheet - can write, update, or clear values.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (str): The range to modify (e.g., "Sheet1!A1:D10", "A1:D10"). Required.
        values (Optional[Union[str, List[List[str]]]]): 2D array of values to write/update. Can be a JSON string or Python list. Required unless clear_values=True.
        value_input_option (str): How to interpret input values ("RAW" or "USER_ENTERED"). Defaults to "USER_ENTERED".
        clear_values (bool): If True, clears the range instead of writing values. Defaults to False.

    Returns:
        str: Confirmation message of the successful modification operation.
    """
    operation = "clear" if clear_values else "write"
    logger.info(f"[modify_sheet_values] Invoked. Operation: {operation}, Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Range: {range_name}")

    # Parse values if it's a JSON string (MCP passes parameters as JSON strings)
    if values is not None and isinstance(values, str):
        try:
            parsed_values = json.loads(values)
            if not isinstance(parsed_values, list):
                raise ValueError(f"Values must be a list, got {type(parsed_values).__name__}")
            # Validate it's a list of lists
            for i, row in enumerate(parsed_values):
                if not isinstance(row, list):
                    raise ValueError(f"Row {i} must be a list, got {type(row).__name__}")
            values = parsed_values
            logger.info(f"[modify_sheet_values] Parsed JSON string to Python list with {len(values)} rows")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON format for values: {e}")
        except ValueError as e:
            raise Exception(f"Invalid values structure: {e}")

    if not clear_values and not values:
        raise Exception("Either 'values' must be provided or 'clear_values' must be True.")

    if clear_values:
        result = await asyncio.to_thread(
            service.spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_name)
            .execute
        )

        cleared_range = result.get("clearedRange", range_name)
        text_output = f"Successfully cleared range '{cleared_range}' in spreadsheet {spreadsheet_id} for {user_google_email}."
        logger.info(f"Successfully cleared range '{cleared_range}' for {user_google_email}.")
    else:
        body = {"values": values}

        result = await asyncio.to_thread(
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption=value_input_option,
                body=body,
            )
            .execute
        )

        updated_cells = result.get("updatedCells", 0)
        updated_rows = result.get("updatedRows", 0)
        updated_columns = result.get("updatedColumns", 0)

        text_output = (
            f"Successfully updated range '{range_name}' in spreadsheet {spreadsheet_id} for {user_google_email}. "
            f"Updated: {updated_cells} cells, {updated_rows} rows, {updated_columns} columns."
        )
        logger.info(f"Successfully updated {updated_cells} cells for {user_google_email}.")

    return text_output


@server.tool()
@handle_http_errors("create_spreadsheet", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def create_spreadsheet(
    service,
    user_google_email: str,
    title: str,
    sheet_names: Optional[List[str]] = None,
) -> str:
    """
    Creates a new Google Spreadsheet.

    Args:
        user_google_email (str): The user's Google email address. Required.
        title (str): The title of the new spreadsheet. Required.
        sheet_names (Optional[List[str]]): List of sheet names to create. If not provided, creates one sheet with default name.

    Returns:
        str: Information about the newly created spreadsheet including ID and URL.
    """
    logger.info(f"[create_spreadsheet] Invoked. Email: '{user_google_email}', Title: {title}")

    spreadsheet_body = {
        "properties": {
            "title": title
        }
    }

    if sheet_names:
        spreadsheet_body["sheets"] = [
            {"properties": {"title": sheet_name}} for sheet_name in sheet_names
        ]

    spreadsheet = await asyncio.to_thread(
        service.spreadsheets().create(body=spreadsheet_body).execute
    )

    spreadsheet_id = spreadsheet.get("spreadsheetId")
    spreadsheet_url = spreadsheet.get("spreadsheetUrl")

    text_output = (
        f"Successfully created spreadsheet '{title}' for {user_google_email}. "
        f"ID: {spreadsheet_id} | URL: {spreadsheet_url}"
    )

    logger.info(f"Successfully created spreadsheet for {user_google_email}. ID: {spreadsheet_id}")
    return text_output


@server.tool()
@handle_http_errors("create_sheet", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def create_sheet(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    sheet_name: str,
) -> str:
    """
    Creates a new sheet within an existing spreadsheet.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        sheet_name (str): The name of the new sheet. Required.

    Returns:
        str: Confirmation message of the successful sheet creation.
    """
    logger.info(f"[create_sheet] Invoked. Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Sheet: {sheet_name}")

    request_body = {
        "requests": [
            {
                "addSheet": {
                    "properties": {
                        "title": sheet_name
                    }
                }
            }
        ]
    }

    response = await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    text_output = (
        f"Successfully created sheet '{sheet_name}' (ID: {sheet_id}) in spreadsheet {spreadsheet_id} for {user_google_email}."
    )

    logger.info(f"Successfully created sheet for {user_google_email}. Sheet ID: {sheet_id}")
    return text_output


@server.tool()
@require_google_service("sheets", "sheets_write")
@handle_http_errors("format_cell_style")
async def format_cell_style(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str,
    bold: bool = None,
    italic: bool = None,
    underline: bool = None,
    strikethrough: bool = None,
    font_size: int = None,
    font_family: str = None,
    text_color: str = None,
    background_color: str = None,
    horizontal_alignment: str = None,
    vertical_alignment: str = None,
) -> str:
    """
    Apply formatting to a range of cells in a Google Sheet.

    Args:
        service: Google Sheets service
        user_google_email: User's email
        spreadsheet_id: Spreadsheet ID to modify
        range_name: Range to format (e.g., "A1:C3", "Sheet1!A1:C3")
        bold: Set bold formatting (True/False)
        italic: Set italic formatting (True/False)
        underline: Set underline formatting (True/False)
        strikethrough: Set strikethrough formatting (True/False)
        font_size: Font size in points
        font_family: Font family name (e.g., 'Arial', 'Calibri')
        text_color: Text color in hex format (e.g., '#FF0000' for red)
        background_color: Background color in hex format
        horizontal_alignment: Horizontal alignment ('LEFT', 'CENTER', 'RIGHT')
        vertical_alignment: Vertical alignment ('TOP', 'MIDDLE', 'BOTTOM')

    Returns:
        str: Confirmation message
    """
    logger.info(f"[format_cell_style] Invoked. Spreadsheet ID: '{spreadsheet_id}', Range: '{range_name}', User: '{user_google_email}'")

    # Parse range to get sheet ID if needed
    sheet_id = 0  # Default to first sheet
    if '!' in range_name:
        sheet_name, cell_range = range_name.split('!', 1)
        # Get sheet ID from sheet name
        spreadsheet = await asyncio.to_thread(
            service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
        )
        for sheet in spreadsheet.get('sheets', []):
            if sheet['properties']['title'] == sheet_name:
                sheet_id = sheet['properties']['sheetId']
                break
    else:
        cell_range = range_name

    # Build cell format object
    cell_format = {}
    text_format = {}
    
    if bold is not None:
        text_format['bold'] = bold
    if italic is not None:
        text_format['italic'] = italic
    if underline is not None:
        text_format['underline'] = underline
    if strikethrough is not None:
        text_format['strikethrough'] = strikethrough
    if font_size is not None:
        text_format['fontSize'] = font_size
    if font_family is not None:
        text_format['fontFamily'] = font_family
    if text_color is not None:
        text_format['foregroundColor'] = _hex_to_rgb_sheets(text_color)
    
    if text_format:
        cell_format['textFormat'] = text_format
    
    if background_color is not None:
        cell_format['backgroundColor'] = _hex_to_rgb_sheets(background_color)
    
    if horizontal_alignment is not None or vertical_alignment is not None:
        cell_format['horizontalAlignment'] = horizontal_alignment
        cell_format['verticalAlignment'] = vertical_alignment

    if not cell_format:
        return f"No formatting changes specified for range {range_name}"

    # Create batch update request
    requests = [{
        'repeatCell': {
            'range': _parse_range_to_grid_range(cell_range, sheet_id),
            'cell': {
                'userEnteredFormat': cell_format
            },
            'fields': 'userEnteredFormat(' + ','.join(_get_format_fields(cell_format)) + ')'
        }
    }]

    await asyncio.to_thread(
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[format_cell_style] Successfully applied cell formatting to range {range_name} in spreadsheet {spreadsheet_id}")
    return f"Cell formatting applied to range {range_name} in spreadsheet {spreadsheet_id} for {user_google_email}"


@server.tool()
@require_google_service("sheets", "sheets_write")
@handle_http_errors("format_cell_borders")
async def format_cell_borders(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str,
    border_style: str = "SOLID",
    border_color: str = "#000000",
    border_width: int = 1,
    top: bool = True,
    bottom: bool = True,
    left: bool = True,
    right: bool = True,
    inner_horizontal: bool = False,
    inner_vertical: bool = False,
) -> str:
    """
    Apply borders to a range of cells in a Google Sheet.

    Args:
        service: Google Sheets service
        user_google_email: User's email
        spreadsheet_id: Spreadsheet ID to modify
        range_name: Range to format (e.g., "A1:C3", "Sheet1!A1:C3")
        border_style: Border style ('SOLID', 'DOTTED', 'DASHED', 'SOLID_MEDIUM', 'SOLID_THICK', 'DOUBLE')
        border_color: Border color in hex format (e.g., '#000000' for black)
        border_width: Border width in pixels
        top: Apply top border
        bottom: Apply bottom border
        left: Apply left border
        right: Apply right border
        inner_horizontal: Apply inner horizontal borders
        inner_vertical: Apply inner vertical borders

    Returns:
        str: Confirmation message
    """
    logger.info(f"[format_cell_borders] Invoked. Spreadsheet ID: '{spreadsheet_id}', Range: '{range_name}', User: '{user_google_email}'")

    # Parse range to get sheet ID
    sheet_id = 0
    if '!' in range_name:
        sheet_name, cell_range = range_name.split('!', 1)
        spreadsheet = await asyncio.to_thread(
            service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
        )
        for sheet in spreadsheet.get('sheets', []):
            if sheet['properties']['title'] == sheet_name:
                sheet_id = sheet['properties']['sheetId']
                break
    else:
        cell_range = range_name

    # Create border object
    border = {
        'style': border_style,
        'color': _hex_to_rgb_sheets(border_color),
        'width': border_width
    }

    # Build requests for each border position
    requests = []
    grid_range = _parse_range_to_grid_range(cell_range, sheet_id)
    
    if top:
        requests.append({
            'updateBorders': {
                'range': grid_range,
                'top': border
            }
        })
    
    if bottom:
        requests.append({
            'updateBorders': {
                'range': grid_range,
                'bottom': border
            }
        })
    
    if left:
        requests.append({
            'updateBorders': {
                'range': grid_range,
                'left': border
            }
        })
    
    if right:
        requests.append({
            'updateBorders': {
                'range': grid_range,
                'right': border
            }
        })
    
    if inner_horizontal:
        requests.append({
            'updateBorders': {
                'range': grid_range,
                'innerHorizontal': border
            }
        })
    
    if inner_vertical:
        requests.append({
            'updateBorders': {
                'range': grid_range,
                'innerVertical': border
            }
        })

    if not requests:
        return f"No border changes specified for range {range_name}"

    await asyncio.to_thread(
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[format_cell_borders] Successfully applied borders to range {range_name} in spreadsheet {spreadsheet_id}")
    return f"Borders applied to range {range_name} in spreadsheet {spreadsheet_id} for {user_google_email}"


@server.tool()
@require_google_service("sheets", "sheets_write")
@handle_http_errors("format_number_display")
async def format_number_display(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str,
    number_format: str,
) -> str:
    """
    Apply number formatting to a range of cells in a Google Sheet.

    Args:
        service: Google Sheets service
        user_google_email: User's email
        spreadsheet_id: Spreadsheet ID to modify
        range_name: Range to format (e.g., "A1:C3", "Sheet1!A1:C3")
        number_format: Number format pattern:
            - 'CURRENCY': '$#,##0.00'
            - 'PERCENT': '0.00%'
            - 'DATE': 'M/d/yyyy'
            - 'TIME': 'h:mm:ss AM/PM'
            - 'SCIENTIFIC': '0.00E+00'
            - Custom pattern like '#,##0.00', '0.00%', 'M/d/yyyy', etc.

    Returns:
        str: Confirmation message
    """
    logger.info(f"[format_number_display] Invoked. Spreadsheet ID: '{spreadsheet_id}', Range: '{range_name}', Format: '{number_format}', User: '{user_google_email}'")

    # Parse range to get sheet ID
    sheet_id = 0
    if '!' in range_name:
        sheet_name, cell_range = range_name.split('!', 1)
        spreadsheet = await asyncio.to_thread(
            service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute
        )
        for sheet in spreadsheet.get('sheets', []):
            if sheet['properties']['title'] == sheet_name:
                sheet_id = sheet['properties']['sheetId']
                break
    else:
        cell_range = range_name

    # Convert common format names to patterns
    format_patterns = {
        'CURRENCY': '$#,##0.00',
        'PERCENT': '0.00%',
        'DATE': 'M/d/yyyy',
        'TIME': 'h:mm:ss AM/PM',
        'SCIENTIFIC': '0.00E+00',
        'NUMBER': '#,##0.00',
        'INTEGER': '#,##0'
    }
    
    pattern = format_patterns.get(number_format.upper(), number_format)

    # Create batch update request
    requests = [{
        'repeatCell': {
            'range': _parse_range_to_grid_range(cell_range, sheet_id),
            'cell': {
                'userEnteredFormat': {
                    'numberFormat': {
                        'type': 'NUMBER',
                        'pattern': pattern
                    }
                }
            },
            'fields': 'userEnteredFormat.numberFormat'
        }
    }]

    await asyncio.to_thread(
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={'requests': requests}
        ).execute
    )

    logger.info(f"[format_number_display] Successfully applied number formatting to range {range_name} in spreadsheet {spreadsheet_id}")
    return f"Number formatting '{pattern}' applied to range {range_name} in spreadsheet {spreadsheet_id} for {user_google_email}"


def _hex_to_rgb_sheets(hex_color: str) -> dict:
    """Convert hex color to RGB dict for Google Sheets API."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        raise ValueError("Invalid hex color format. Use #RRGGBB format")
    
    return {
        'red': int(hex_color[0:2], 16) / 255.0,
        'green': int(hex_color[2:4], 16) / 255.0,
        'blue': int(hex_color[4:6], 16) / 255.0
    }


def _parse_range_to_grid_range(range_name: str, sheet_id: int) -> dict:
    """Parse A1 notation to GridRange object."""
    # Simple A1 notation parser - handles cases like "A1:C3"
    if ':' in range_name:
        start_cell, end_cell = range_name.split(':')
    else:
        start_cell = end_cell = range_name
    
    def _a1_to_coords(cell: str):
        """Convert A1 notation to row/col coordinates."""
        col = 0
        row = 0
        i = 0
        
        # Extract column letters
        while i < len(cell) and cell[i].isalpha():
            col = col * 26 + (ord(cell[i].upper()) - ord('A') + 1)
            i += 1
        
        # Extract row numbers
        if i < len(cell):
            row = int(cell[i:])
        
        return row - 1, col - 1  # Convert to 0-based
    
    start_row, start_col = _a1_to_coords(start_cell)
    end_row, end_col = _a1_to_coords(end_cell)
    
    return {
        'sheetId': sheet_id,
        'startRowIndex': start_row,
        'endRowIndex': end_row + 1,
        'startColumnIndex': start_col,
        'endColumnIndex': end_col + 1
    }


def _get_format_fields(cell_format: dict) -> List[str]:
    """Get list of format fields for API request."""
    fields = []
    for key in cell_format.keys():
        if key == 'textFormat':
            for text_key in cell_format[key].keys():
                fields.append(f'textFormat.{text_key}')
        else:
            fields.append(key)
    return fields


# Create comment management tools for sheets
_comment_tools = create_comment_tools("spreadsheet", "spreadsheet_id")

# Extract and register the functions
read_sheet_comments = _comment_tools['read_comments']
create_sheet_comment = _comment_tools['create_comment']
reply_to_sheet_comment = _comment_tools['reply_to_comment']
resolve_sheet_comment = _comment_tools['resolve_comment']


