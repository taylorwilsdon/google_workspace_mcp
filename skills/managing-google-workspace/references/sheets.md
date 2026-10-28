# Google Sheets Tools Reference

MCP tools for reading, writing, formatting, and managing Google Sheets. All tools require `user_google_email` (string, required). The `spreadsheet_id` parameter accepts a spreadsheet ID or a full Google Sheets URL.

## Contents
- Search & Info: list_spreadsheets, get_spreadsheet_info
- Read & Write: read_sheet_values, modify_sheet_values
- Create: create_spreadsheet, create_sheet, move_sheet_rows
- Tabs & Dimensions: manage_sheet_tab, resize_sheet_dimensions
- Tables: list_sheet_tables, append_table_rows
- Formatting: format_sheet_range, manage_conditional_formatting
- Named Ranges: manage_named_range
- Comments: list_spreadsheet_comments, manage_spreadsheet_comment
- Tips

---

## Search & Info

### list_spreadsheets
List spreadsheets the user has access to (via Drive).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| max_results | integer | no | 25 | |
| page_token | string | no | | `nextPageToken` from a previous response |
| corpora | string | no | | `user`, `domain`, `drive`, or `allDrives`. Defaults to `drive` when `drive_id` is set, else `allDrives` |
| drive_id | string | no | | Shared drive ID to search |

### get_spreadsheet_info
Get spreadsheet metadata: title, locale, and list of sheets.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |

---

## Read & Write

### read_sheet_values
Read values from a range in a spreadsheet.

The response has one line per row: the sheet row number, then the row's cells
separated by tabs, starting at the range's first column (the first line says
which). Row numbers are the sheet's own, so reading `A41:G60` starts at row 41.
Trailing empty cells are dropped. Tabs, line breaks and backslashes inside a
cell are shown as `\t`, `\n`, `\r` and `\\`, so `\\n` is a backslash
followed by `n`, not a line break.

Open-ended or oversized A1 ranges are clamped to at most **1000 rows** before
the Sheets API request (same budget as the default `A1:Z1000`). When clamped,
the response includes a note with the rewritten range so you can request the
next window (e.g. `A1001:A2000`). Quoted whole-sheet references such as
`'My Sheet'` are clamped as well. Bare identifiers are left unchanged because
Google Sheets can resolve them as named ranges.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| range_name | string | no | A1:Z1000 | A1 notation, e.g. `Sheet1!A1:D10`. Caps at 1000 rows |
| include_hyperlinks | boolean | no | false | Fetch hyperlink metadata (slower) |
| include_notes | boolean | no | false | Fetch cell notes (slower) |
| include_formulas | boolean | no | false | Fetch raw formula strings |
| include_smart_chips | boolean | no | false | Fetch smart chips metadata (Drive files/folders & People) |

### modify_sheet_values
Write, update, or clear values in a range, or insert Smart Chips (Drive files/folders or People). Chip writes are chunked into batches of <=8 chips to respect Google Sheets API limits.

When providing `chips`, omit `values` and leave `clear_values=false`. `value_input_option` applies only to ordinary value writes.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| range_name | string | yes | | A1 notation |
| values | array or string | conditional | | 2D array of values. Required unless `clear_values=true` or `chips` is set. Accepts a JSON string or a list |
| value_input_option | string | no | USER_ENTERED | `RAW` or `USER_ENTERED` |
| clear_values | boolean | no | false | Clear the range instead of writing |
| chips | array, object, or string | conditional | | Smart chips instead of `values`: single URL/email, 1D/2D array of URLs/emails, or dict(s) with `type`, `uri`/`email` |
| chip_type | string | no | null | Optional override for `chips`: `drive` or `person` (auto-detected if omitted) |

---

## Create

### create_spreadsheet
Create a new Google Spreadsheet.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| title | string | yes | | Spreadsheet title |
| sheet_names | array of strings | no | | Sheet names to create. Defaults to one sheet with the default name |
| folder_id | string | no | root | Parent folder. Accepts a folder ID or shortcut; for shared drives, a folder inside that drive. If the move fails, the spreadsheet is still created in My Drive root and the reply reports why |

### create_sheet
Add a new sheet (tab) to an existing spreadsheet, or duplicate an existing tab when `source_sheet_name` is set.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| sheet_name | string | no | | Title of the new tab. Google picks a default name if omitted |
| source_sheet_name | string | no | | Existing tab to duplicate instead of adding a blank one |
| insert_sheet_index | integer | no | end | 0-based position for the new tab |

### move_sheet_rows
Move rows from one sheet to another within the same spreadsheet. Preserves formulas, data types, and formatting.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| source_sheet | string | yes | | Name of the sheet to move rows from |
| start_row | integer | yes | | First row to move (1-based, inclusive) |
| end_row | integer | yes | | Last row to move (1-based, inclusive) |
| destination_sheet | string | yes | | Name of the sheet to move rows to |

---

## Tabs & Dimensions

### manage_sheet_tab
Act on an existing tab: rename, delete, hide, unhide, reorder, or style it. Use `create_sheet` to add a tab and `resize_sheet_dimensions` for row and column changes.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| sheet_name | string | yes | | Exact title of the tab to act on |
| action | string | yes | | `rename`, `delete`, `hide`, `unhide`, `reorder`, or `style`. `delete` refuses to remove the last tab |
| new_name | string | for rename | | New tab title |
| new_index | integer | for reorder | | New 0-based position; must be less than the tab count |
| tab_color | string | no | | `style` only: hex `#RRGGBB`, or `none` to remove the color |
| show_gridlines | boolean | no | | `style` only: show or hide gridlines (grid sheets only). `style` needs this and/or `tab_color` |

### resize_sheet_dimensions
Change rows and columns on one tab in a single batch: pixel sizes, auto-fit, freezing, hiding, inserting, and deleting. Provide at least one operation. Rows are 1-based numbers and columns are letters; list and dict params accept JSON strings. Every row number and column letter refers to the tab as it was before the call: deletes apply before inserts, so an insert position is not shifted by deletions in the same call, and one inside a deleted span lands where that span was.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| sheet_name | string | no | first sheet | Exact tab title |
| column_sizes | object or string | no | | Column letter to pixel width, e.g. `{"A": 200, "C": 300}` |
| row_sizes | object or string | no | | Row number to pixel height, e.g. `{"1": 40}` |
| auto_resize_columns | array or string | no | | Column letters to fit to content, e.g. `["A", "B"]` |
| auto_resize_rows | array or string | no | | Row numbers to fit to content |
| frozen_row_count | integer | no | | Rows to freeze from the top; `0` unfreezes |
| frozen_column_count | integer | no | | Columns to freeze from the left; `0` unfreezes |
| hide_columns | array or string | no | | Column letters to hide |
| unhide_columns | array or string | no | | Column letters to unhide |
| hide_rows | array or string | no | | Row numbers to hide |
| unhide_rows | array or string | no | | Row numbers to unhide |
| insert_rows | integer | no | | Number of rows to insert |
| insert_rows_at | integer | no | end | Row number to insert before. Appends to the sheet end if omitted |
| insert_columns | integer | no | | Number of columns to insert |
| insert_columns_at | string | no | end | Column letter to insert before, e.g. `C`. Appends if omitted |
| delete_rows | array or string | no | | Row numbers to delete, best for scattered rows. Not with `delete_row_range` |
| delete_row_range | string | no | | Contiguous rows as `start:end` (inclusive), e.g. `5:10`. Not with `delete_rows` |
| delete_columns | array or string | no | | Column letters to delete |

---

## Tables

### list_sheet_tables
List structured tables in a spreadsheet with their IDs, names, sheets, ranges, and column names. Use it to find the `table_id` for `append_table_rows`.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |

### append_table_rows
Append rows to the end of a structured table, extending its range automatically.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| table_id | string | yes | | From `list_sheet_tables` |
| values | array or string | yes | | 2D array, one inner list per row. JSON string or list. Strings starting with `=` are written as formulas |

---

## Formatting

### format_sheet_range
Apply visual formatting to a range: colors, number formats, text wrapping, alignment, text styling, cell merging, borders, and dropdown lists.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| range_name | string | yes | | A1 notation (with optional sheet name) |
| background_color | string | no | | Hex `#RRGGBB` |
| text_color | string | no | | Hex `#RRGGBB` |
| number_format_type | string | no | | `NUMBER`, `CURRENCY`, `DATE`, `PERCENT`, etc. |
| number_format_pattern | string | no | | Custom pattern for the number format |
| wrap_strategy | string | no | | `WRAP`, `CLIP`, or `OVERFLOW_CELL` |
| horizontal_alignment | string | no | | `LEFT`, `CENTER`, or `RIGHT` |
| vertical_alignment | string | no | | `TOP`, `MIDDLE`, or `BOTTOM` |
| bold | boolean | no | | |
| italic | boolean | no | | |
| font_size | integer | no | | Size in points |
| merge_cells | boolean | no | | `true` to merge, `false` to unmerge; merging keeps only the top-left value |
| merge_type | string | no | | `MERGE_ALL` (default when merging), `MERGE_COLUMNS`, or `MERGE_ROWS`; implies a merge |
| borders | string | no | | Sides to draw, comma-separated: `all`, `outer`, `inner`, `top`, `bottom`, `left`, `right`, `inner_horizontal`, `inner_vertical`. `none` removes all borders |
| border_style | string | no | `SOLID` | `SOLID`, `SOLID_MEDIUM`, `SOLID_THICK`, `DASHED`, `DOTTED`, `DOUBLE`. Requires `borders` |
| border_color | string | no | `#000000` | Hex `#RRGGBB`. Requires `borders` |
| dropdown_values | string[] | no | | Turns each cell into a dropdown with these values |
| dropdown_strict | boolean | no | `true` | `false` only warns on other input. Requires `dropdown_values` |
| clear_dropdown | boolean | no | | Removes all data validation from the range, including checkboxes |

### manage_conditional_formatting
Add, update, or delete conditional formatting rules.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| action | string | yes | | `add`, `update`, or `delete` |
| range_name | string | for add | | A1 notation. Optional for update (preserves existing ranges if omitted) |
| condition_type | string | for add | | e.g. `NUMBER_GREATER`, `TEXT_CONTAINS`, `DATE_BEFORE`, `CUSTOM_FORMULA` |
| condition_values | array or string | conditional | | Values for the condition. Depends on `condition_type` |
| background_color | string | no | | Hex `#RRGGBB` applied when condition matches |
| text_color | string | no | | Hex `#RRGGBB` applied when condition matches |
| rule_index | integer | for update/delete | | 0-based index of the rule |
| gradient_points | array or string | no | | List of gradient point dicts for color-scale rules. Overrides boolean rule parameters |
| sheet_name | string | no | first sheet | Sheet name for locating the rule (used by update/delete) |

---

## Named Ranges

### manage_named_range
List, create, update, or delete named ranges in a spreadsheet.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | Spreadsheet ID or URL |
| action | string | yes | | `list`, `create`, `update`, or `delete` |
| name | string | for create | | Name of the named range (required for `create`; optional identifier for `update`/`delete`) |
| range_name | string | for create | | Target cell or range in A1 notation, e.g. `Sheet1!A1:D10`, `A1:B5` (required for `create`) |
| named_range_id | string | no | | ID of the named range (optional identifier for `update`/`delete`) |
| new_name | string | no | | New name for the named range (action `update`) |
| new_range | string | no | | New A1-style range for the named range (action `update`) |

---

## Comments

### list_spreadsheet_comments
List all comments on a spreadsheet.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| max_comments | integer | no | 100 | Cap on comments returned; must be at least 1. Default comes from `WORKSPACE_MCP_COMMENTS_MAX`; unset, invalid, or non-positive values fall back to 100 |

### manage_spreadsheet_comment
Create, reply to, resolve, edit, or delete a comment.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| spreadsheet_id | string | yes | | |
| action | string | yes | | `create`, `reply`, `resolve`, `update`, or `delete` |
| comment_content | string | for create/reply/update | | Comment text |
| comment_id | string | for reply/resolve/update/delete | | Target comment ID |
| cell | string | no | | Single cell in A1 notation (e.g. `Sheet1!B2`) to anchor a `create` comment to. Omit for a file-level comment |

---

## Tips

**Range notation**: Use A1 notation throughout. Include the sheet name for multi-sheet spreadsheets (e.g. `Sheet2!A1:C10`). If no sheet name is given, the first sheet is used.

**USER_ENTERED vs RAW**: `USER_ENTERED` (default) parses values as if typed into the Sheets UI -- formulas are evaluated, dates parsed, numbers formatted. `RAW` stores exact strings without interpretation.

**Conditional formatting condition types**: Common values include `NUMBER_GREATER`, `NUMBER_LESS`, `NUMBER_BETWEEN`, `TEXT_CONTAINS`, `TEXT_NOT_CONTAINS`, `DATE_BEFORE`, `DATE_AFTER`, `CUSTOM_FORMULA`, `BLANK`, `NOT_BLANK`.

**Gradient rules**: Provide `gradient_points` as a list of dicts, each with `type` (`MIN`, `MAX`, `NUMBER`, `PERCENT`, `PERCENTILE`), `value` (string, optional for `MIN`/`MAX`), and `color` (hex `#RRGGBB`). When gradient points are set, boolean formatting parameters are ignored.
