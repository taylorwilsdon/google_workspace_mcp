"""
Google Sheets MCP Tools

This module provides MCP tools for interacting with Google Sheets API.
"""

import logging
import asyncio
import json
import copy
from typing import List, Optional, Union

from auth.service_decorator import require_google_service
from core.server import server
from core.utils import handle_http_errors, UserInputError
from core.comments import create_comment_tools
from gsheets.sheets_helpers import (
    CONDITION_TYPES,
    _build_boolean_rule,
    _build_gradient_rule,
    _fetch_sheets_with_rules,
    _format_conditional_rules_section,
    _parse_a1_range,
    _parse_condition_values,
    _parse_gradient_points,
    _parse_hex_color,
    _select_sheet,
)

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
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute
    )

    files = files_response.get("files", [])
    if not files:
        return f"No spreadsheets found for {user_google_email}."

    spreadsheets_list = [
        f'- "{file["name"]}" (ID: {file["id"]}) | Modified: {file.get("modifiedTime", "Unknown")} | Link: {file.get("webViewLink", "No link")}'
        for file in files
    ]

    text_output = (
        f"Successfully listed {len(files)} spreadsheets for {user_google_email}:\n"
        + "\n".join(spreadsheets_list)
    )

    logger.info(
        f"Successfully listed {len(files)} spreadsheets for {user_google_email}."
    )
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
        str: Formatted spreadsheet information including title, locale, and sheets list.
    """
    logger.info(
        f"[get_spreadsheet_info] Invoked. Email: '{user_google_email}', Spreadsheet ID: {spreadsheet_id}"
    )

    spreadsheet = await asyncio.to_thread(
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="spreadsheetId,properties(title,locale),sheets(properties(title,sheetId,gridProperties(rowCount,columnCount)),conditionalFormats)",
        )
        .execute
    )

    properties = spreadsheet.get("properties", {})
    title = properties.get("title", "Unknown")
    locale = properties.get("locale", "Unknown")
    sheets = spreadsheet.get("sheets", [])

    sheet_titles = {}
    for sheet in sheets:
        sheet_props = sheet.get("properties", {})
        sid = sheet_props.get("sheetId")
        if sid is not None:
            sheet_titles[sid] = sheet_props.get("title", f"Sheet {sid}")

    sheets_info = []
    for sheet in sheets:
        sheet_props = sheet.get("properties", {})
        sheet_name = sheet_props.get("title", "Unknown")
        sheet_id = sheet_props.get("sheetId", "Unknown")
        grid_props = sheet_props.get("gridProperties", {})
        rows = grid_props.get("rowCount", "Unknown")
        cols = grid_props.get("columnCount", "Unknown")
        rules = sheet.get("conditionalFormats", []) or []

        sheets_info.append(
            f'  - "{sheet_name}" (ID: {sheet_id}) | Size: {rows}x{cols} | Conditional formats: {len(rules)}'
        )
        if rules:
            sheets_info.append(
                _format_conditional_rules_section(
                    sheet_name, rules, sheet_titles, indent="    "
                )
            )

    sheets_section = "\n".join(sheets_info) if sheets_info else "  No sheets found"
    text_output = "\n".join(
        [
            f'Spreadsheet: "{title}" (ID: {spreadsheet_id}) | Locale: {locale}',
            f"Sheets ({len(sheets)}):",
            sheets_section,
        ]
    )

    logger.info(
        f"Successfully retrieved info for spreadsheet {spreadsheet_id} for {user_google_email}."
    )
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
    logger.info(
        f"[read_sheet_values] Invoked. Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Range: {range_name}"
    )

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
    logger.info(
        f"[modify_sheet_values] Invoked. Operation: {operation}, Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Range: {range_name}"
    )

    # Parse values if it's a JSON string (MCP passes parameters as JSON strings)
    if values is not None and isinstance(values, str):
        try:
            parsed_values = json.loads(values)
            if not isinstance(parsed_values, list):
                raise ValueError(
                    f"Values must be a list, got {type(parsed_values).__name__}"
                )
            # Validate it's a list of lists
            for i, row in enumerate(parsed_values):
                if not isinstance(row, list):
                    raise ValueError(
                        f"Row {i} must be a list, got {type(row).__name__}"
                    )
            values = parsed_values
            logger.info(
                f"[modify_sheet_values] Parsed JSON string to Python list with {len(values)} rows"
            )
        except json.JSONDecodeError as e:
            raise UserInputError(f"Invalid JSON format for values: {e}")
        except ValueError as e:
            raise UserInputError(f"Invalid values structure: {e}")

    if not clear_values and not values:
        raise UserInputError(
            "Either 'values' must be provided or 'clear_values' must be True."
        )

    if clear_values:
        result = await asyncio.to_thread(
            service.spreadsheets()
            .values()
            .clear(spreadsheetId=spreadsheet_id, range=range_name)
            .execute
        )

        cleared_range = result.get("clearedRange", range_name)
        text_output = f"Successfully cleared range '{cleared_range}' in spreadsheet {spreadsheet_id} for {user_google_email}."
        logger.info(
            f"Successfully cleared range '{cleared_range}' for {user_google_email}."
        )
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
        logger.info(
            f"Successfully updated {updated_cells} cells for {user_google_email}."
        )

    return text_output


@server.tool()
@handle_http_errors("format_sheet_range", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def format_sheet_range(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    number_format_type: Optional[str] = None,
    number_format_pattern: Optional[str] = None,
) -> str:
    """
    Applies formatting to a range: background/text color and number/date formats.

    Colors accept hex strings (#RRGGBB). Number formats follow Sheets types
    (e.g., NUMBER, NUMBER_WITH_GROUPING, CURRENCY, DATE, TIME, DATE_TIME,
    PERCENT, TEXT, SCIENTIFIC). If no sheet name is provided, the first sheet
    is used.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (str): A1-style range (optionally with sheet name). Required.
        background_color (Optional[str]): Hex background color (e.g., "#FFEECC").
        text_color (Optional[str]): Hex text color (e.g., "#000000").
        number_format_type (Optional[str]): Sheets number format type (e.g., "DATE").
        number_format_pattern (Optional[str]): Optional custom pattern for the number format.

    Returns:
        str: Confirmation of the applied formatting.
    """
    logger.info(
        "[format_sheet_range] Invoked. Email: '%s', Spreadsheet: %s, Range: %s",
        user_google_email,
        spreadsheet_id,
        range_name,
    )

    if not any([background_color, text_color, number_format_type]):
        raise UserInputError(
            "Provide at least one of background_color, text_color, or number_format_type."
        )

    bg_color_parsed = _parse_hex_color(background_color)
    text_color_parsed = _parse_hex_color(text_color)

    number_format = None
    if number_format_type:
        allowed_number_formats = {
            "NUMBER",
            "NUMBER_WITH_GROUPING",
            "CURRENCY",
            "PERCENT",
            "SCIENTIFIC",
            "DATE",
            "TIME",
            "DATE_TIME",
            "TEXT",
        }
        normalized_type = number_format_type.upper()
        if normalized_type not in allowed_number_formats:
            raise UserInputError(
                f"number_format_type must be one of {sorted(allowed_number_formats)}."
            )
        number_format = {"type": normalized_type}
        if number_format_pattern:
            number_format["pattern"] = number_format_pattern

    metadata = await asyncio.to_thread(
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title))",
        )
        .execute
    )
    sheets = metadata.get("sheets", [])
    grid_range = _parse_a1_range(range_name, sheets)

    user_entered_format = {}
    fields = []
    if bg_color_parsed:
        user_entered_format["backgroundColor"] = bg_color_parsed
        fields.append("userEnteredFormat.backgroundColor")
    if text_color_parsed:
        user_entered_format["textFormat"] = {"foregroundColor": text_color_parsed}
        fields.append("userEnteredFormat.textFormat.foregroundColor")
    if number_format:
        user_entered_format["numberFormat"] = number_format
        fields.append("userEnteredFormat.numberFormat")

    if not user_entered_format:
        raise UserInputError(
            "No formatting applied. Verify provided colors or number format."
        )

    request_body = {
        "requests": [
            {
                "repeatCell": {
                    "range": grid_range,
                    "cell": {"userEnteredFormat": user_entered_format},
                    "fields": ",".join(fields),
                }
            }
        ]
    }

    await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    applied_parts = []
    if bg_color_parsed:
        applied_parts.append(f"background {background_color}")
    if text_color_parsed:
        applied_parts.append(f"text {text_color}")
    if number_format:
        nf_desc = number_format["type"]
        if number_format_pattern:
            nf_desc += f" (pattern: {number_format_pattern})"
        applied_parts.append(f"format {nf_desc}")

    summary = ", ".join(applied_parts)
    return (
        f"Applied formatting to range '{range_name}' in spreadsheet {spreadsheet_id} "
        f"for {user_google_email}: {summary}."
    )


@server.tool()
@handle_http_errors("add_conditional_formatting", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def add_conditional_formatting(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    range_name: str,
    condition_type: str,
    condition_values: Optional[Union[str, List[Union[str, int, float]]]] = None,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    rule_index: Optional[int] = None,
    gradient_points: Optional[Union[str, List[dict]]] = None,
) -> str:
    """
    Adds a conditional formatting rule to a range.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (str): A1-style range (optionally with sheet name). Required.
        condition_type (str): Sheets condition type (e.g., NUMBER_GREATER, TEXT_CONTAINS, DATE_BEFORE, CUSTOM_FORMULA).
        condition_values (Optional[Union[str, List[Union[str, int, float]]]]): Values for the condition; accepts a list or a JSON string representing a list. Depends on condition_type.
        background_color (Optional[str]): Hex background color to apply when condition matches.
        text_color (Optional[str]): Hex text color to apply when condition matches.
        rule_index (Optional[int]): Optional position to insert the rule (0-based) within the sheet's rules.
        gradient_points (Optional[Union[str, List[dict]]]): List (or JSON list) of gradient points for a color scale. If provided, a gradient rule is created and boolean parameters are ignored.

    Returns:
        str: Confirmation of the added rule.
    """
    logger.info(
        "[add_conditional_formatting] Invoked. Email: '%s', Spreadsheet: %s, Range: %s, Type: %s, Values: %s",
        user_google_email,
        spreadsheet_id,
        range_name,
        condition_type,
        condition_values,
    )

    if rule_index is not None and (not isinstance(rule_index, int) or rule_index < 0):
        raise UserInputError("rule_index must be a non-negative integer when provided.")

    condition_values_list = _parse_condition_values(condition_values)
    gradient_points_list = _parse_gradient_points(gradient_points)

    sheets, sheet_titles = await _fetch_sheets_with_rules(service, spreadsheet_id)
    grid_range = _parse_a1_range(range_name, sheets)

    target_sheet = None
    for sheet in sheets:
        if sheet.get("properties", {}).get("sheetId") == grid_range.get("sheetId"):
            target_sheet = sheet
            break
    if target_sheet is None:
        raise UserInputError(
            "Target sheet not found while adding conditional formatting."
        )

    current_rules = target_sheet.get("conditionalFormats", []) or []

    insert_at = rule_index if rule_index is not None else len(current_rules)
    if insert_at > len(current_rules):
        raise UserInputError(
            f"rule_index {insert_at} is out of range for sheet '{target_sheet.get('properties', {}).get('title', 'Unknown')}' "
            f"(current count: {len(current_rules)})."
        )

    if gradient_points_list:
        new_rule = _build_gradient_rule([grid_range], gradient_points_list)
        rule_desc = "gradient"
        values_desc = ""
        applied_parts = [f"gradient points {len(gradient_points_list)}"]
    else:
        rule, cond_type_normalized = _build_boolean_rule(
            [grid_range],
            condition_type,
            condition_values_list,
            background_color,
            text_color,
        )
        new_rule = rule
        rule_desc = cond_type_normalized
        values_desc = ""
        if condition_values_list:
            values_desc = f" with values {condition_values_list}"
        applied_parts = []
        if background_color:
            applied_parts.append(f"background {background_color}")
        if text_color:
            applied_parts.append(f"text {text_color}")

    new_rules_state = copy.deepcopy(current_rules)
    new_rules_state.insert(insert_at, new_rule)

    add_rule_request = {"rule": new_rule}
    if rule_index is not None:
        add_rule_request["index"] = rule_index

    request_body = {"requests": [{"addConditionalFormatRule": add_rule_request}]}

    await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    format_desc = ", ".join(applied_parts) if applied_parts else "format applied"

    sheet_title = target_sheet.get("properties", {}).get("title", "Unknown")
    state_text = _format_conditional_rules_section(
        sheet_title, new_rules_state, sheet_titles, indent=""
    )

    return "\n".join(
        [
            f"Added conditional format on '{range_name}' in spreadsheet {spreadsheet_id} "
            f"for {user_google_email}: {rule_desc}{values_desc}; format: {format_desc}.",
            state_text,
        ]
    )


@server.tool()
@handle_http_errors("update_conditional_formatting", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def update_conditional_formatting(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    rule_index: int,
    range_name: Optional[str] = None,
    condition_type: Optional[str] = None,
    condition_values: Optional[Union[str, List[Union[str, int, float]]]] = None,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    sheet_name: Optional[str] = None,
    gradient_points: Optional[Union[str, List[dict]]] = None,
) -> str:
    """
    Updates an existing conditional formatting rule by index on a sheet.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        range_name (Optional[str]): A1-style range to apply the updated rule (optionally with sheet name). If omitted, existing ranges are preserved.
        rule_index (int): Index of the rule to update (0-based).
        condition_type (Optional[str]): Sheets condition type. If omitted, the existing rule's type is preserved.
        condition_values (Optional[Union[str, List[Union[str, int, float]]]]): Values for the condition.
        background_color (Optional[str]): Hex background color when condition matches.
        text_color (Optional[str]): Hex text color when condition matches.
        sheet_name (Optional[str]): Sheet name to locate the rule when range_name is omitted. Defaults to first sheet.
        gradient_points (Optional[Union[str, List[dict]]]): If provided, updates the rule to a gradient color scale using these points.

    Returns:
        str: Confirmation of the updated rule and the current rule state.
    """
    logger.info(
        "[update_conditional_formatting] Invoked. Email: '%s', Spreadsheet: %s, Range: %s, Rule Index: %s",
        user_google_email,
        spreadsheet_id,
        range_name,
        rule_index,
    )

    if not isinstance(rule_index, int) or rule_index < 0:
        raise UserInputError("rule_index must be a non-negative integer.")

    condition_values_list = _parse_condition_values(condition_values)
    gradient_points_list = _parse_gradient_points(gradient_points)

    sheets, sheet_titles = await _fetch_sheets_with_rules(service, spreadsheet_id)

    target_sheet = None
    grid_range = None
    if range_name:
        grid_range = _parse_a1_range(range_name, sheets)
        for sheet in sheets:
            if sheet.get("properties", {}).get("sheetId") == grid_range.get("sheetId"):
                target_sheet = sheet
                break
    else:
        target_sheet = _select_sheet(sheets, sheet_name)

    if target_sheet is None:
        raise UserInputError(
            "Target sheet not found while updating conditional formatting."
        )

    sheet_props = target_sheet.get("properties", {})
    sheet_id = sheet_props.get("sheetId")
    sheet_title = sheet_props.get("title", f"Sheet {sheet_id}")

    rules = target_sheet.get("conditionalFormats", []) or []
    if rule_index >= len(rules):
        raise UserInputError(
            f"rule_index {rule_index} is out of range for sheet '{sheet_title}' (current count: {len(rules)})."
        )

    existing_rule = rules[rule_index]
    ranges_to_use = existing_rule.get("ranges", [])
    if range_name:
        ranges_to_use = [grid_range]
    if not ranges_to_use:
        ranges_to_use = [{"sheetId": sheet_id}]

    new_rule = None
    rule_desc = ""
    values_desc = ""
    format_desc = ""

    if gradient_points_list is not None:
        new_rule = _build_gradient_rule(ranges_to_use, gradient_points_list)
        rule_desc = "gradient"
        format_desc = f"gradient points {len(gradient_points_list)}"
    elif "gradientRule" in existing_rule:
        if any([background_color, text_color, condition_type, condition_values_list]):
            raise UserInputError(
                "Existing rule is a gradient rule. Provide gradient_points to update it, or omit formatting/condition parameters to keep it unchanged."
            )
        new_rule = {
            "ranges": ranges_to_use,
            "gradientRule": existing_rule.get("gradientRule", {}),
        }
        rule_desc = "gradient"
        format_desc = "gradient (unchanged)"
    else:
        existing_boolean = existing_rule.get("booleanRule", {})
        existing_condition = existing_boolean.get("condition", {})
        existing_format = copy.deepcopy(existing_boolean.get("format", {}))

        cond_type = (condition_type or existing_condition.get("type", "")).upper()
        if not cond_type:
            raise UserInputError("condition_type is required for boolean rules.")
        if cond_type not in CONDITION_TYPES:
            raise UserInputError(
                f"condition_type must be one of {sorted(CONDITION_TYPES)}."
            )

        if condition_values_list is not None:
            cond_values = [
                {"userEnteredValue": str(val)} for val in condition_values_list
            ]
        else:
            cond_values = existing_condition.get("values")

        new_format = copy.deepcopy(existing_format) if existing_format else {}
        if background_color is not None:
            bg_color_parsed = _parse_hex_color(background_color)
            if bg_color_parsed:
                new_format["backgroundColor"] = bg_color_parsed
            elif "backgroundColor" in new_format:
                del new_format["backgroundColor"]
        if text_color is not None:
            text_color_parsed = _parse_hex_color(text_color)
            text_format = copy.deepcopy(new_format.get("textFormat", {}))
            if text_color_parsed:
                text_format["foregroundColor"] = text_color_parsed
            elif "foregroundColor" in text_format:
                del text_format["foregroundColor"]
            if text_format:
                new_format["textFormat"] = text_format
            elif "textFormat" in new_format:
                del new_format["textFormat"]

        if not new_format:
            raise UserInputError("At least one format option must remain on the rule.")

        new_rule = {
            "ranges": ranges_to_use,
            "booleanRule": {
                "condition": {"type": cond_type},
                "format": new_format,
            },
        }
        if cond_values:
            new_rule["booleanRule"]["condition"]["values"] = cond_values

        rule_desc = cond_type
        if condition_values_list:
            values_desc = f" with values {condition_values_list}"
        format_parts = []
        if "backgroundColor" in new_format:
            format_parts.append("background updated")
        if "textFormat" in new_format and new_format["textFormat"].get(
            "foregroundColor"
        ):
            format_parts.append("text color updated")
        format_desc = ", ".join(format_parts) if format_parts else "format preserved"

    new_rules_state = copy.deepcopy(rules)
    new_rules_state[rule_index] = new_rule

    request_body = {
        "requests": [
            {
                "updateConditionalFormatRule": {
                    "index": rule_index,
                    "sheetId": sheet_id,
                    "rule": new_rule,
                }
            }
        ]
    }

    await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    state_text = _format_conditional_rules_section(
        sheet_title, new_rules_state, sheet_titles, indent=""
    )

    return "\n".join(
        [
            f"Updated conditional format at index {rule_index} on sheet '{sheet_title}' in spreadsheet {spreadsheet_id} "
            f"for {user_google_email}: {rule_desc}{values_desc}; format: {format_desc}.",
            state_text,
        ]
    )


@server.tool()
@handle_http_errors("delete_conditional_formatting", service_type="sheets")
@require_google_service("sheets", "sheets_write")
async def delete_conditional_formatting(
    service,
    user_google_email: str,
    spreadsheet_id: str,
    rule_index: int,
    sheet_name: Optional[str] = None,
) -> str:
    """
    Deletes an existing conditional formatting rule by index on a sheet.

    Args:
        user_google_email (str): The user's Google email address. Required.
        spreadsheet_id (str): The ID of the spreadsheet. Required.
        rule_index (int): Index of the rule to delete (0-based).
        sheet_name (Optional[str]): Name of the sheet that contains the rule. Defaults to the first sheet if not provided.

    Returns:
        str: Confirmation of the deletion and the current rule state.
    """
    logger.info(
        "[delete_conditional_formatting] Invoked. Email: '%s', Spreadsheet: %s, Sheet: %s, Rule Index: %s",
        user_google_email,
        spreadsheet_id,
        sheet_name,
        rule_index,
    )

    if not isinstance(rule_index, int) or rule_index < 0:
        raise UserInputError("rule_index must be a non-negative integer.")

    sheets, sheet_titles = await _fetch_sheets_with_rules(service, spreadsheet_id)
    target_sheet = _select_sheet(sheets, sheet_name)

    sheet_props = target_sheet.get("properties", {})
    sheet_id = sheet_props.get("sheetId")
    target_sheet_name = sheet_props.get("title", f"Sheet {sheet_id}")
    rules = target_sheet.get("conditionalFormats", []) or []
    if rule_index >= len(rules):
        raise UserInputError(
            f"rule_index {rule_index} is out of range for sheet '{target_sheet_name}' (current count: {len(rules)})."
        )

    new_rules_state = copy.deepcopy(rules)
    del new_rules_state[rule_index]

    request_body = {
        "requests": [
            {
                "deleteConditionalFormatRule": {
                    "index": rule_index,
                    "sheetId": sheet_id,
                }
            }
        ]
    }

    await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    state_text = _format_conditional_rules_section(
        target_sheet_name, new_rules_state, sheet_titles, indent=""
    )

    return "\n".join(
        [
            f"Deleted conditional format at index {rule_index} on sheet '{target_sheet_name}' in spreadsheet {spreadsheet_id} for {user_google_email}.",
            state_text,
        ]
    )


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
        str: Information about the newly created spreadsheet including ID, URL, and locale.
    """
    logger.info(
        f"[create_spreadsheet] Invoked. Email: '{user_google_email}', Title: {title}"
    )

    spreadsheet_body = {"properties": {"title": title}}

    if sheet_names:
        spreadsheet_body["sheets"] = [
            {"properties": {"title": sheet_name}} for sheet_name in sheet_names
        ]

    spreadsheet = await asyncio.to_thread(
        service.spreadsheets()
        .create(
            body=spreadsheet_body,
            fields="spreadsheetId,spreadsheetUrl,properties(title,locale)",
        )
        .execute
    )

    properties = spreadsheet.get("properties", {})
    spreadsheet_id = spreadsheet.get("spreadsheetId")
    spreadsheet_url = spreadsheet.get("spreadsheetUrl")
    locale = properties.get("locale", "Unknown")

    text_output = (
        f"Successfully created spreadsheet '{title}' for {user_google_email}. "
        f"ID: {spreadsheet_id} | URL: {spreadsheet_url} | Locale: {locale}"
    )

    logger.info(
        f"Successfully created spreadsheet for {user_google_email}. ID: {spreadsheet_id}"
    )
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
    logger.info(
        f"[create_sheet] Invoked. Email: '{user_google_email}', Spreadsheet: {spreadsheet_id}, Sheet: {sheet_name}"
    )

    request_body = {"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]}

    response = await asyncio.to_thread(
        service.spreadsheets()
        .batchUpdate(spreadsheetId=spreadsheet_id, body=request_body)
        .execute
    )

    sheet_id = response["replies"][0]["addSheet"]["properties"]["sheetId"]

    text_output = f"Successfully created sheet '{sheet_name}' (ID: {sheet_id}) in spreadsheet {spreadsheet_id} for {user_google_email}."

    logger.info(
        f"Successfully created sheet for {user_google_email}. Sheet ID: {sheet_id}"
    )
    return text_output


# Create comment management tools for sheets
_comment_tools = create_comment_tools("spreadsheet", "spreadsheet_id")

# Extract and register the functions
read_sheet_comments = _comment_tools["read_comments"]
create_sheet_comment = _comment_tools["create_comment"]
reply_to_sheet_comment = _comment_tools["reply_to_comment"]
resolve_sheet_comment = _comment_tools["resolve_comment"]
