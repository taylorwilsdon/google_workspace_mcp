"""
Google Sheets Helper Functions

Shared utilities for Google Sheets operations including A1 parsing and
conditional formatting helpers.
"""

import asyncio
import json
import re
from typing import List, Optional, Union

from core.utils import UserInputError


A1_PART_REGEX = re.compile(r"^([A-Za-z]*)(\d*)$")
SHEET_TITLE_SAFE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _column_to_index(column: str) -> Optional[int]:
    """Convert column letters (A, B, AA) to zero-based index."""
    if not column:
        return None
    result = 0
    for char in column.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


def _parse_a1_part(
    part: str, pattern: re.Pattern[str] = A1_PART_REGEX
) -> tuple[Optional[int], Optional[int]]:
    """
    Parse a single A1 part like 'B2' or 'C' into zero-based column/row indexes.
    Supports anchors like '$A$1' by stripping the dollar signs.
    """
    clean_part = part.replace("$", "")
    match = pattern.match(clean_part)
    if not match:
        raise UserInputError(f"Invalid A1 range part: '{part}'.")
    col_letters, row_digits = match.groups()
    col_idx = _column_to_index(col_letters) if col_letters else None
    row_idx = int(row_digits) - 1 if row_digits else None
    return col_idx, row_idx


def _split_sheet_and_range(range_name: str) -> tuple[Optional[str], str]:
    """
    Split an A1 notation into (sheet_name, range_part), handling quoted sheet names.

    Examples:
    - "Sheet1!A1:B2" -> ("Sheet1", "A1:B2")
    - "'My Sheet'!$A$1:$B$10" -> ("My Sheet", "$A$1:$B$10")
    - "A1:B2" -> (None, "A1:B2")
    """
    if "!" not in range_name:
        return None, range_name

    if range_name.startswith("'"):
        closing = range_name.find("'!")
        if closing != -1:
            sheet_name = range_name[1:closing].replace("''", "'")
            a1_range = range_name[closing + 2 :]
            return sheet_name, a1_range

    sheet_name, a1_range = range_name.split("!", 1)
    return sheet_name.strip().strip("'"), a1_range


def _parse_a1_range(range_name: str, sheets: List[dict]) -> dict:
    """
    Convert an A1-style range (with optional sheet name) into a GridRange.

    Falls back to the first sheet if none is provided.
    """
    sheet_name, a1_range = _split_sheet_and_range(range_name)

    if not sheets:
        raise UserInputError("Spreadsheet has no sheets.")

    target_sheet = None
    if sheet_name:
        for sheet in sheets:
            if sheet.get("properties", {}).get("title") == sheet_name:
                target_sheet = sheet
                break
        if target_sheet is None:
            available_titles = [
                sheet.get("properties", {}).get("title", "Untitled") for sheet in sheets
            ]
            available_list = ", ".join(available_titles) if available_titles else "none"
            raise UserInputError(
                f"Sheet '{sheet_name}' not found in spreadsheet. Available sheets: {available_list}."
            )
    else:
        target_sheet = sheets[0]

    props = target_sheet.get("properties", {})
    sheet_id = props.get("sheetId")

    if not a1_range:
        raise UserInputError("A1-style range must not be empty (e.g., 'A1', 'A1:B10').")

    if ":" in a1_range:
        start, end = a1_range.split(":", 1)
    else:
        start = end = a1_range

    start_col, start_row = _parse_a1_part(start)
    end_col, end_row = _parse_a1_part(end)

    grid_range = {"sheetId": sheet_id}
    if start_row is not None:
        grid_range["startRowIndex"] = start_row
    if start_col is not None:
        grid_range["startColumnIndex"] = start_col
    if end_row is not None:
        grid_range["endRowIndex"] = end_row + 1
    if end_col is not None:
        grid_range["endColumnIndex"] = end_col + 1

    return grid_range


def _parse_hex_color(color: Optional[str]) -> Optional[dict]:
    """
    Convert a hex color like '#RRGGBB' to Sheets API color (0-1 floats).
    """
    if not color:
        return None

    trimmed = color.strip()
    if trimmed.startswith("#"):
        trimmed = trimmed[1:]

    if len(trimmed) != 6:
        raise UserInputError(f"Color '{color}' must be in format #RRGGBB or RRGGBB.")

    try:
        red = int(trimmed[0:2], 16) / 255
        green = int(trimmed[2:4], 16) / 255
        blue = int(trimmed[4:6], 16) / 255
    except ValueError as exc:
        raise UserInputError(f"Color '{color}' is not valid hex.") from exc

    return {"red": red, "green": green, "blue": blue}


def _index_to_column(index: int) -> str:
    """
    Convert a zero-based column index to column letters (0 -> A, 25 -> Z, 26 -> AA).
    """
    if index < 0:
        raise UserInputError(f"Column index must be non-negative, got {index}.")

    result = []
    index += 1  # Convert to 1-based for calculation
    while index:
        index, remainder = divmod(index - 1, 26)
        result.append(chr(ord("A") + remainder))
    return "".join(reversed(result))


def _quote_sheet_title_for_a1(sheet_title: str) -> str:
    """
    Quote a sheet title for use in A1 notation if necessary.

    If the sheet title contains special characters or spaces, it is wrapped in single quotes.
    Any single quotes in the title are escaped by doubling them, as required by Google Sheets.
    """
    if SHEET_TITLE_SAFE_RE.match(sheet_title or ""):
        return sheet_title
    escaped = (sheet_title or "").replace("'", "''")
    return f"'{escaped}'"


def _format_a1_cell(sheet_title: str, row_index: int, col_index: int) -> str:
    """
    Format a cell reference in A1 notation given a sheet title and zero-based row/column indices.

    Args:
        sheet_title: The title of the sheet.
        row_index: Zero-based row index (0 for first row).
        col_index: Zero-based column index (0 for column A).

    Returns:
        A string representing the cell reference in A1 notation, e.g., 'Sheet1!B2'.
    """
    return f"{_quote_sheet_title_for_a1(sheet_title)}!{_index_to_column(col_index)}{row_index + 1}"


def _coerce_int(value: object, default: int = 0) -> int:
    """
    Safely convert a value to an integer, returning a default value if conversion fails.

    Args:
        value: The value to convert to int.
        default: The value to return if conversion fails (default is 0).

    Returns:
        The integer value of `value`, or `default` if conversion fails.
    """
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _is_sheets_error_token(value: object) -> bool:
    """
    Detect whether a cell value represents a Google Sheets error token (e.g., #ERROR!, #NAME?, #REF!, #N/A).

    Returns True if the value is a string that starts with '#' and ends with '!' or '?', or is exactly '#N/A'.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate.startswith("#"):
        return False
    upper_candidate = candidate.upper()
    if upper_candidate == "#N/A":
        return True
    return upper_candidate.endswith(("!", "?"))


def _values_contain_sheets_errors(values: List[List[object]]) -> bool:
    """
    Check whether a 2D array of cell values contains any Google Sheets error tokens.

    Args:
        values: A 2D list of cell values (as returned from the Sheets API).

    Returns:
        True if any cell contains a Google Sheets error token, False otherwise.
    """
    for row in values:
        for cell in row:
            if _is_sheets_error_token(cell):
                return True
    return False


def _a1_range_for_values(a1_range: str, values: List[List[object]]) -> Optional[str]:
    """
    Compute a tight A1 range for a returned values matrix.

    This helps keep follow-up includeGridData payloads small vs. using a wide requested range.
    Only applies when the A1 range has an explicit starting cell (e.g., 'Sheet1!B2:D10').
    """
    sheet_name, range_part = _split_sheet_and_range(a1_range)
    if not range_part:
        return None

    start_part = range_part.split(":", 1)[0]
    start_col, start_row = _parse_a1_part(start_part)
    if start_col is None or start_row is None:
        return None

    height = len(values)
    width = max((len(row) for row in values), default=0)
    if height <= 0 or width <= 0:
        return None

    end_row = start_row + height - 1
    end_col = start_col + width - 1

    start_label = f"{_index_to_column(start_col)}{start_row + 1}"
    end_label = f"{_index_to_column(end_col)}{end_row + 1}"
    range_ref = (
        start_label if start_label == end_label else f"{start_label}:{end_label}"
    )

    if sheet_name:
        return f"{_quote_sheet_title_for_a1(sheet_name)}!{range_ref}"
    return range_ref


def _a1_range_cell_count(a1_range: str) -> Optional[int]:
    """
    Return cell count for an explicit rectangular A1 range (e.g. A1:C10).

    Returns None when the range is open-ended or otherwise does not include
    both row and column bounds.
    """
    _, range_part = _split_sheet_and_range(a1_range)
    if not range_part:
        return None

    if ":" in range_part:
        start_part, end_part = range_part.split(":", 1)
    else:
        start_part = end_part = range_part

    try:
        start_col, start_row = _parse_a1_part(start_part)
        end_col, end_row = _parse_a1_part(end_part)
    except UserInputError:
        return None

    if None in (start_col, start_row, end_col, end_row):
        return None
    if end_col < start_col or end_row < start_row:
        return None

    return (end_col - start_col + 1) * (end_row - start_row + 1)


def _extract_cell_errors_from_grid(spreadsheet: dict) -> list[dict[str, Optional[str]]]:
    """
    Extracts error information from spreadsheet grid data.

    Iterates through the sheets and their grid data in the provided spreadsheet dictionary,
    collecting all cell errors. Returns a list of dictionaries, each containing:
        - "cell": the A1 notation of the cell with the error,
        - "type": the error type (e.g., "ERROR", "N/A"),
        - "message": the error message, if available.

    Args:
        spreadsheet (dict): The spreadsheet data as returned by the Sheets API with grid data included.

    Returns:
        list[dict[str, Optional[str]]]: List of error details for each cell with an error.
    """
    errors: list[dict[str, Optional[str]]] = []
    for sheet in spreadsheet.get("sheets", []) or []:
        sheet_title = sheet.get("properties", {}).get("title") or "Unknown"
        for grid in sheet.get("data", []) or []:
            start_row = _coerce_int(grid.get("startRow"), default=0)
            start_col = _coerce_int(grid.get("startColumn"), default=0)
            for row_offset, row_data in enumerate(grid.get("rowData", []) or []):
                if not row_data:
                    continue
                for col_offset, cell_data in enumerate(
                    row_data.get("values", []) or []
                ):
                    if not cell_data:
                        continue
                    error_value = (cell_data.get("effectiveValue") or {}).get(
                        "errorValue"
                    ) or None
                    if not error_value:
                        continue
                    errors.append(
                        {
                            "cell": _format_a1_cell(
                                sheet_title,
                                start_row + row_offset,
                                start_col + col_offset,
                            ),
                            "type": error_value.get("type"),
                            "message": error_value.get("message"),
                        }
                    )
    return errors


def _extract_cell_hyperlinks_from_grid(spreadsheet: dict) -> list[dict[str, str]]:
    """
    Extract hyperlink URLs from spreadsheet grid data.

    Returns a list of dictionaries with:
        - "cell": cell A1 reference
        - "url": hyperlink URL

    For rich text cells, this includes URLs from both `CellData.hyperlink`
    and `textFormatRuns[].format.link.uri`.
    """
    hyperlinks: list[dict[str, str]] = []
    for sheet in spreadsheet.get("sheets", []) or []:
        sheet_title = sheet.get("properties", {}).get("title") or "Unknown"
        for grid in sheet.get("data", []) or []:
            start_row = _coerce_int(grid.get("startRow"), default=0)
            start_col = _coerce_int(grid.get("startColumn"), default=0)
            for row_offset, row_data in enumerate(grid.get("rowData", []) or []):
                if not row_data:
                    continue
                for col_offset, cell_data in enumerate(
                    row_data.get("values", []) or []
                ):
                    if not cell_data:
                        continue
                    cell_urls: list[str] = []
                    seen_urls: set[str] = set()

                    hyperlink = cell_data.get("hyperlink")
                    if (
                        isinstance(hyperlink, str)
                        and hyperlink
                        and hyperlink not in seen_urls
                    ):
                        seen_urls.add(hyperlink)
                        cell_urls.append(hyperlink)

                    for text_run in cell_data.get("textFormatRuns", []) or []:
                        if not isinstance(text_run, dict):
                            continue
                        link_uri = (
                            (text_run.get("format") or {}).get("link") or {}
                        ).get("uri")
                        if not isinstance(link_uri, str) or not link_uri:
                            continue
                        if link_uri in seen_urls:
                            continue
                        seen_urls.add(link_uri)
                        cell_urls.append(link_uri)

                    if not cell_urls:
                        continue
                    cell_ref = _format_a1_cell(
                        sheet_title, start_row + row_offset, start_col + col_offset
                    )
                    for url in cell_urls:
                        hyperlinks.append({"cell": cell_ref, "url": url})
    return hyperlinks


async def _fetch_detailed_sheet_errors(
    service, spreadsheet_id: str, a1_range: str
) -> list[dict[str, Optional[str]]]:
    response = await asyncio.to_thread(
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            ranges=[a1_range],
            includeGridData=True,
            fields="sheets(properties(title),data(startRow,startColumn,rowData(values(effectiveValue(errorValue(type,message))))))",
        )
        .execute
    )
    return _extract_cell_errors_from_grid(response)


async def _fetch_sheet_hyperlinks(
    service, spreadsheet_id: str, a1_range: str
) -> list[dict[str, str]]:
    response = await asyncio.to_thread(
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            ranges=[a1_range],
            includeGridData=True,
            fields="sheets(properties(title),data(startRow,startColumn,rowData(values(hyperlink,textFormatRuns(format(link(uri)))))))",
        )
        .execute
    )
    return _extract_cell_hyperlinks_from_grid(response)


def _format_sheet_error_section(
    *, errors: list[dict[str, Optional[str]]], range_label: str, max_details: int = 25
) -> str:
    """
    Format a list of cell error information into a human-readable section.

    Args:
        errors: A list of dictionaries, each containing details about a cell error,
            including the cell location, error type, and message.
        range_label: A string label for the range in which the errors occurred.
        max_details: The maximum number of error details to include in the output.
            If the number of errors exceeds this value, the output will be truncated
            and a summary line will indicate how many additional errors were omitted.

    Returns:
        A formatted string listing the cell errors in a human-readable format.
        If there are no errors, returns an empty string.
    """
    # Limit the number of error details to 25 for performance and readability.
    if not errors:
        return ""

    lines = []
    for item in errors[:max_details]:
        cell = item.get("cell") or "(unknown cell)"
        error_type = item.get("type")
        message = item.get("message")
        if error_type and message:
            lines.append(f"- {cell}: {error_type} — {message}")
        elif message:
            lines.append(f"- {cell}: {message}")
        elif error_type:
            lines.append(f"- {cell}: {error_type}")
        else:
            lines.append(f"- {cell}: (unknown error)")

    suffix = (
        f"\n... and {len(errors) - max_details} more errors"
        if len(errors) > max_details
        else ""
    )
    return (
        f"\n\nDetailed cell errors in range '{range_label}':\n"
        + "\n".join(lines)
        + suffix
    )


def _format_sheet_hyperlink_section(
    *, hyperlinks: list[dict[str, str]], range_label: str, max_details: int = 25
) -> str:
    """
    Format a list of cell hyperlinks into a human-readable section.
    """
    if not hyperlinks:
        return ""

    lines = []
    for item in hyperlinks[:max_details]:
        cell = item.get("cell") or "(unknown cell)"
        url = item.get("url") or "(missing url)"
        lines.append(f"- {cell}: {url}")

    suffix = (
        f"\n... and {len(hyperlinks) - max_details} more hyperlinks"
        if len(hyperlinks) > max_details
        else ""
    )
    return f"\n\nHyperlinks in range '{range_label}':\n" + "\n".join(lines) + suffix


def _color_to_hex(color: Optional[dict]) -> Optional[str]:
    """
    Convert a Sheets color object back to #RRGGBB hex string for display.
    """
    if not color:
        return None

    def _component(value: Optional[float]) -> int:
        try:
            # Clamp and round to nearest integer in 0-255
            return max(0, min(255, int(round(float(value or 0) * 255))))
        except (TypeError, ValueError):
            return 0

    red = _component(color.get("red"))
    green = _component(color.get("green"))
    blue = _component(color.get("blue"))
    return f"#{red:02X}{green:02X}{blue:02X}"


def _grid_range_to_a1(grid_range: dict, sheet_titles: dict[int, str]) -> str:
    """
    Convert a GridRange to an A1-like string using known sheet titles.
    Falls back to the sheet ID if the title is unknown.
    """
    sheet_id = grid_range.get("sheetId")
    sheet_title = sheet_titles.get(sheet_id, f"Sheet {sheet_id}")

    start_row = grid_range.get("startRowIndex")
    end_row = grid_range.get("endRowIndex")
    start_col = grid_range.get("startColumnIndex")
    end_col = grid_range.get("endColumnIndex")

    # If nothing is specified, treat as the whole sheet.
    if start_row is None and end_row is None and start_col is None and end_col is None:
        return sheet_title

    def row_label(idx: Optional[int]) -> str:
        return str(idx + 1) if idx is not None else ""

    def col_label(idx: Optional[int]) -> str:
        return _index_to_column(idx) if idx is not None else ""

    start_label = f"{col_label(start_col)}{row_label(start_row)}"
    # end indices in GridRange are exclusive; subtract 1 for display
    end_label = f"{col_label(end_col - 1 if end_col is not None else None)}{row_label(end_row - 1 if end_row is not None else None)}"

    if start_label and end_label:
        range_ref = (
            start_label if start_label == end_label else f"{start_label}:{end_label}"
        )
    elif start_label:
        range_ref = start_label
    elif end_label:
        range_ref = end_label
    else:
        range_ref = ""

    return f"{sheet_title}!{range_ref}" if range_ref else sheet_title


def _summarize_conditional_rule(
    rule: dict, index: int, sheet_titles: dict[int, str]
) -> str:
    """
    Produce a concise human-readable summary of a conditional formatting rule.
    """
    ranges = rule.get("ranges", [])
    range_labels = [_grid_range_to_a1(rng, sheet_titles) for rng in ranges] or [
        "(no range)"
    ]

    if "booleanRule" in rule:
        boolean_rule = rule["booleanRule"]
        condition = boolean_rule.get("condition", {})
        cond_type = condition.get("type", "UNKNOWN")
        cond_values = [
            val.get("userEnteredValue")
            for val in condition.get("values", [])
            if isinstance(val, dict) and "userEnteredValue" in val
        ]
        value_desc = f" values={cond_values}" if cond_values else ""

        fmt = boolean_rule.get("format", {})
        fmt_parts = []
        bg_hex = _color_to_hex(fmt.get("backgroundColor"))
        if bg_hex:
            fmt_parts.append(f"bg {bg_hex}")
        fg_hex = _color_to_hex(fmt.get("textFormat", {}).get("foregroundColor"))
        if fg_hex:
            fmt_parts.append(f"text {fg_hex}")
        fmt_desc = ", ".join(fmt_parts) if fmt_parts else "no format"

        return f"[{index}] {cond_type}{value_desc} -> {fmt_desc} on {', '.join(range_labels)}"

    if "gradientRule" in rule:
        gradient_rule = rule["gradientRule"]
        points = []
        for point_name in ("minpoint", "midpoint", "maxpoint"):
            point = gradient_rule.get(point_name)
            if not point:
                continue
            color_hex = _color_to_hex(point.get("color"))
            type_desc = point.get("type", point_name)
            value_desc = point.get("value")
            point_desc = type_desc
            if value_desc:
                point_desc += f":{value_desc}"
            if color_hex:
                point_desc += f" {color_hex}"
            points.append(point_desc)
        gradient_desc = " | ".join(points) if points else "gradient"
        return f"[{index}] gradient -> {gradient_desc} on {', '.join(range_labels)}"

    return f"[{index}] (unknown rule) on {', '.join(range_labels)}"


def _format_conditional_rules_section(
    sheet_title: str,
    rules: List[dict],
    sheet_titles: dict[int, str],
    indent: str = "  ",
) -> str:
    """
    Build a multi-line string describing conditional formatting rules for a sheet.
    """
    if not rules:
        return f'{indent}Conditional formats for "{sheet_title}": none.'

    lines = [f'{indent}Conditional formats for "{sheet_title}" ({len(rules)}):']
    for idx, rule in enumerate(rules):
        lines.append(
            f"{indent}  {_summarize_conditional_rule(rule, idx, sheet_titles)}"
        )
    return "\n".join(lines)


CONDITION_TYPES = {
    "NUMBER_GREATER",
    "NUMBER_GREATER_THAN_EQ",
    "NUMBER_LESS",
    "NUMBER_LESS_THAN_EQ",
    "NUMBER_EQ",
    "NUMBER_NOT_EQ",
    "TEXT_CONTAINS",
    "TEXT_NOT_CONTAINS",
    "TEXT_STARTS_WITH",
    "TEXT_ENDS_WITH",
    "TEXT_EQ",
    "DATE_BEFORE",
    "DATE_ON_OR_BEFORE",
    "DATE_AFTER",
    "DATE_ON_OR_AFTER",
    "DATE_EQ",
    "DATE_NOT_EQ",
    "DATE_BETWEEN",
    "DATE_NOT_BETWEEN",
    "NOT_BLANK",
    "BLANK",
    "CUSTOM_FORMULA",
    "ONE_OF_RANGE",
}

GRADIENT_POINT_TYPES = {"MIN", "MAX", "NUMBER", "PERCENT", "PERCENTILE"}


async def _fetch_sheets_with_rules(
    service, spreadsheet_id: str
) -> tuple[List[dict], dict[int, str]]:
    """
    Fetch sheets with titles and conditional format rules in a single request.
    """
    response = await asyncio.to_thread(
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title),conditionalFormats)",
        )
        .execute
    )
    sheets = response.get("sheets", []) or []
    sheet_titles: dict[int, str] = {}
    for sheet in sheets:
        props = sheet.get("properties", {})
        sid = props.get("sheetId")
        if sid is not None:
            sheet_titles[sid] = props.get("title", f"Sheet {sid}")
    return sheets, sheet_titles


def _select_sheet(sheets: List[dict], sheet_name: Optional[str]) -> dict:
    """
    Select a sheet by name, or default to the first sheet if name is not provided.
    """
    if not sheets:
        raise UserInputError("Spreadsheet has no sheets.")

    if sheet_name is None:
        return sheets[0]

    for sheet in sheets:
        if sheet.get("properties", {}).get("title") == sheet_name:
            return sheet

    available_titles = [
        sheet.get("properties", {}).get("title", "Untitled") for sheet in sheets
    ]
    raise UserInputError(
        f"Sheet '{sheet_name}' not found. Available sheets: {', '.join(available_titles)}."
    )


def _parse_condition_values(
    condition_values: Optional[Union[str, List[Union[str, int, float]]]],
) -> Optional[List[Union[str, int, float]]]:
    """
    Normalize and validate condition_values into a list of strings/numbers.
    """
    parsed = condition_values
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise UserInputError(
                "condition_values must be a list or a JSON-encoded list (e.g., '[\"=$B2>1000\"]')."
            ) from exc

    if parsed is not None and not isinstance(parsed, list):
        parsed = [parsed]

    if parsed:
        for idx, val in enumerate(parsed):
            if not isinstance(val, (str, int, float)):
                raise UserInputError(
                    f"condition_values[{idx}] must be a string or number, got {type(val).__name__}."
                )

    return parsed


def _parse_gradient_points(
    gradient_points: Optional[Union[str, List[dict]]],
) -> Optional[List[dict]]:
    """
    Normalize gradient points into a list of dicts with type/value/color.
    Each point must have a 'type' (MIN, MAX, NUMBER, PERCENT, PERCENTILE) and a color.
    """
    if gradient_points is None:
        return None

    parsed = gradient_points
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except json.JSONDecodeError as exc:
            raise UserInputError(
                "gradient_points must be a list or JSON-encoded list of points "
                '(e.g., \'[{"type":"MIN","color":"#ffffff"}, {"type":"MAX","color":"#ff0000"}]\').'
            ) from exc

    if not isinstance(parsed, list):
        raise UserInputError("gradient_points must be a list of point objects.")

    if len(parsed) < 2 or len(parsed) > 3:
        raise UserInputError("Provide 2 or 3 gradient points (min/max or min/mid/max).")

    normalized_points: List[dict] = []
    for idx, point in enumerate(parsed):
        if not isinstance(point, dict):
            raise UserInputError(
                f"gradient_points[{idx}] must be an object with type/color."
            )

        point_type = point.get("type")
        if not point_type or point_type.upper() not in GRADIENT_POINT_TYPES:
            raise UserInputError(
                f"gradient_points[{idx}].type must be one of {sorted(GRADIENT_POINT_TYPES)}."
            )
        color_raw = point.get("color")
        color_dict = (
            _parse_hex_color(color_raw)
            if not isinstance(color_raw, dict)
            else color_raw
        )
        if not color_dict:
            raise UserInputError(f"gradient_points[{idx}].color is required.")

        normalized = {"type": point_type.upper(), "color": color_dict}
        if "value" in point and point["value"] is not None:
            normalized["value"] = str(point["value"])
        normalized_points.append(normalized)

    return normalized_points


def _build_boolean_rule(
    ranges: List[dict],
    condition_type: str,
    condition_values: Optional[List[Union[str, int, float]]],
    background_color: Optional[str],
    text_color: Optional[str],
) -> tuple[dict, str]:
    """
    Build a Sheets boolean conditional formatting rule payload.
    Returns the rule and the normalized condition type.
    """
    if not background_color and not text_color:
        raise UserInputError(
            "Provide at least one of background_color or text_color for the rule format."
        )

    cond_type_normalized = condition_type.upper()
    if cond_type_normalized not in CONDITION_TYPES:
        raise UserInputError(
            f"condition_type must be one of {sorted(CONDITION_TYPES)}."
        )

    condition = {"type": cond_type_normalized}
    if condition_values:
        condition["values"] = [
            {"userEnteredValue": str(value)} for value in condition_values
        ]

    bg_color_parsed = _parse_hex_color(background_color)
    text_color_parsed = _parse_hex_color(text_color)

    format_obj = {}
    if bg_color_parsed:
        format_obj["backgroundColor"] = bg_color_parsed
    if text_color_parsed:
        format_obj["textFormat"] = {"foregroundColor": text_color_parsed}

    return (
        {
            "ranges": ranges,
            "booleanRule": {
                "condition": condition,
                "format": format_obj,
            },
        },
        cond_type_normalized,
    )


def _build_gradient_rule(
    ranges: List[dict],
    gradient_points: List[dict],
) -> dict:
    """
    Build a Sheets gradient conditional formatting rule payload.
    """
    rule_body: dict = {"ranges": ranges, "gradientRule": {}}
    if len(gradient_points) == 2:
        rule_body["gradientRule"]["minpoint"] = gradient_points[0]
        rule_body["gradientRule"]["maxpoint"] = gradient_points[1]
    else:
        rule_body["gradientRule"]["minpoint"] = gradient_points[0]
        rule_body["gradientRule"]["midpoint"] = gradient_points[1]
        rule_body["gradientRule"]["maxpoint"] = gradient_points[2]
    return rule_body
