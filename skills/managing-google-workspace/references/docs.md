# Google Docs Tools Reference

MCP tools for reading, creating, editing, and managing Google Docs. All tools require `user_google_email` (string, required). The `document_id` parameter accepts a doc ID or a full Google Docs URL.

## Contents
- Reading & Search: get_doc_as_markdown, get_doc_content, search_docs, list_docs_in_folder
- Creating Documents: create_doc
- Text Editing: modify_doc_text, find_and_replace_doc
- Paragraph & List Styling: update_paragraph_style
- Structural Elements: insert_doc_elements, create_table_with_data, insert_doc_image
- Headers, Footers & Export: update_doc_headers_footers, export_doc_to_pdf
- Tabs: insert_doc_tab, update_doc_tab, delete_doc_tab
- Comments: list_document_comments, manage_document_comment
- Inspection & Debugging: inspect_doc_structure, debug_table_structure
- Batch Operations: batch_update_doc
- Tips

---

## Reading & Search

### get_doc_as_markdown
Reads a Google Doc and returns it as Markdown, preserving headings, bold/italic/strikethrough, links, code spans, lists with nesting, and tables. Comments are included by default with anchor text context.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | Doc ID or full URL |
| include_comments | boolean | no | true | Include comments in output |
| comment_mode | string | no | inline | `inline` (footnote-style at anchor), `appendix` (grouped at bottom), or `none` |
| include_resolved | boolean | no | false | Include resolved comments |

### get_doc_content
Retrieves plain text content of a Google Doc or a Drive file (.docx, etc.). Native Google Docs use the Docs API; Office files are downloaded and text-extracted via Drive API.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | Doc ID or file ID |

### search_docs
Searches for Google Docs by name using Drive API (mimeType filter).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| query | string | yes | | Search query for doc names |
| page_size | integer | no | 10 | Max results to return |

### list_docs_in_folder
Lists Google Docs within a specific Drive folder.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| folder_id | string | no | root | Drive folder ID |
| page_size | integer | no | 100 | Max items to return |

---

## Creating Documents

### create_doc
Creates a new Google Doc with optional initial content.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| title | string | yes | | Document title |
| content | string | no | (empty) | Initial text content |

---

## Text Editing

### modify_doc_text
Insert or replace text and/or apply character formatting in a single operation. If `end_index` is not provided with text, text is inserted at `start_index`; provide both to replace a range. Formatting options can be applied with or without changing the text.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| start_index | integer | yes | | Docs API start position from `inspect_doc_structure`; `0` is also accepted as an alias for the first writable body position |
| end_index | integer | no | | End position; omit to insert at start_index |
| text | string | no | | Text to insert or replace with |
| bold | boolean | no | | |
| italic | boolean | no | | |
| underline | boolean | no | | |
| strikethrough | boolean | no | | |
| font_size | integer | no | | Size in points |
| font_family | string | no | | e.g. "Arial", "Times New Roman" |
| text_color | string | no | | Hex color `#RRGGBB` |
| background_color | string | no | | Highlight color `#RRGGBB` |
| link_url | string | no | | Hyperlink URL (http/https) |

### find_and_replace_doc
Find and replace text throughout a document (or a specific tab).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| find_text | string | yes | | Text to search for |
| replace_text | string | yes | | Replacement text |
| match_case | boolean | no | false | Case-sensitive matching |
| tab_id | string | no | | Target a specific tab |

---

## Paragraph & List Styling

### update_paragraph_style
Apply paragraph-level formatting: heading styles (H1-H6), lists (bulleted/numbered with nesting), alignment, spacing, and indentation. All options can be combined in a single call.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| start_index | integer | yes | | Docs API start position from `inspect_doc_structure`; `0` is also accepted as an alias for the first writable body position |
| end_index | integer | yes | | Exclusive end; should cover the entire paragraph |
| heading_level | integer | no | | 0 = NORMAL_TEXT, 1-6 = H1-H6 |
| alignment | string | no | | `START`, `CENTER`, `END`, `JUSTIFIED` |
| line_spacing | number | no | | Multiplier: 1.0 = single, 1.5, 2.0 = double |
| indent_first_line | number | no | | Points (36 = 0.5 inch) |
| indent_start | number | no | | Left indent in points |
| indent_end | number | no | | Right indent in points |
| space_above | number | no | | Points above paragraph |
| space_below | number | no | | Points below paragraph |
| named_style_type | string | no | | `NORMAL_TEXT`, `TITLE`, `SUBTITLE`, `HEADING_1`-`HEADING_6`. Mutually exclusive with heading_level |
| list_type | string | no | | `UNORDERED` (bullets), `ORDERED` (numbers) |
| list_nesting_level | integer | no | 0 | Nesting depth 0-8 |

---

## Structural Elements

### insert_doc_elements
Insert a table, list, or page break.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| element_type | string | yes | | `table`, `list`, or `page_break` |
| index | integer | yes | | 0-based insertion position |
| rows | integer | no | | Required for `table` |
| columns | integer | no | | Required for `table` |
| list_type | string | no | | Required for `list`: `UNORDERED` or `ORDERED` |
| text | string | no | | Initial text content for list items |

### create_table_with_data
Creates a table pre-populated with data in one operation. Always call `inspect_doc_structure` first to get a safe insertion index (use the `total_length` value).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| table_data | array | yes | | 2D list of strings: `[["H1","H2"],["r1c1","r1c2"]]`. All rows must have the same column count. Use `""` for empty cells, never null |
| index | integer | yes | | Get from `inspect_doc_structure` `total_length` |
| bold_headers | boolean | no | true | Bold the first row |
| tab_id | string | no | | Target a specific tab |

### insert_doc_image
Insert an image from Drive or a public URL.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| image_source | string | yes | | Drive file ID or public image URL |
| index | integer | yes | | 0-based insertion position |
| width | integer | no | 0 | Image width in points (0 = auto) |
| height | integer | no | 0 | Image height in points (0 = auto) |

---

## Headers, Footers & Export

### update_doc_headers_footers
Set or update header/footer content.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| section_type | string | yes | | `header` or `footer` |
| content | string | yes | | Text content |
| header_footer_type | string | no | DEFAULT | `DEFAULT`, `FIRST_PAGE_ONLY`, or `EVEN_PAGE` |

### export_doc_to_pdf
Export a Google Doc to PDF and save it to Drive.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| pdf_filename | string | no | (original name + "_PDF") | PDF file name |
| folder_id | string | no | (root) | Destination folder ID |

---

## Tabs

### insert_doc_tab
Add a new tab to a document.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| title | string | yes | | Tab title |
| index | integer | yes | | 0-based position among sibling tabs |
| parent_tab_id | string | no | | Nest under this parent tab |

### update_doc_tab
Rename an existing tab.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| tab_id | string | yes | | Get from `inspect_doc_structure` |
| title | string | yes | | New tab title |

### delete_doc_tab
Delete a tab by ID.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| tab_id | string | yes | | Get from `inspect_doc_structure` |

---

## Comments

### list_document_comments
List all comments on a document.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |

### manage_document_comment
Create, reply to, or resolve a comment.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| action | string | yes | | `create`, `reply`, or `resolve` |
| comment_content | string | no | | Required for `create` and `reply` |
| comment_id | string | no | | Required for `reply` and `resolve` |

---

## Inspection & Debugging

### inspect_doc_structure
Returns document structure metadata: element count, total length, table positions and dimensions, and available tabs. Essential before any index-based operation.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| detailed | boolean | no | false | Return detailed structure info |
| tab_id | string | no | | Inspect a specific tab (default: main document) |

Key output fields:
- `total_elements` -- number of document elements
- `total_length` -- maximum safe insertion index
- `tables` -- count of existing tables
- `table_details` -- position and dimensions per table
- `tabs` -- list of tabs with IDs (when no tab_id specified)

### debug_table_structure
Detailed view of a single table's layout: dimensions, cell positions, current content, and insertion indices per cell. Use after creating or populating a table to verify results.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| table_index | integer | no | 0 | 0-based table number (0 = first table) |

---

## Batch Operations

### batch_update_doc
Execute multiple operations atomically in a single API call. Each operation is a dict with a `type` field. All operations accept an optional `tab_id`.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| document_id | string | yes | | |
| operations | array | yes | | List of operation dicts (see types below) |

**Operation types:**

| Type | Required fields | Optional fields |
|------|----------------|-----------------|
| `insert_text` | `index`, `text` | |
| `delete_text` | `start_index`, `end_index` | |
| `replace_text` | `start_index`, `end_index`, `text` | |
| `format_text` | `start_index`, `end_index` | `bold`, `italic`, `underline`, `strikethrough`, `font_size`, `font_family`, `text_color`, `background_color`, `link_url` |
| `update_paragraph_style` | `start_index`, `end_index` | `heading_level` (0-6), `alignment`, `line_spacing`, `indent_first_line`, `indent_start`, `indent_end`, `space_above`, `space_below` |
| `insert_table` | `index`, `rows`, `columns` | |
| `insert_page_break` | `index` | |
| `find_replace` | `find_text`, `replace_text` | `match_case` |
| `create_bullet_list` | `start_index`, `end_index` | `list_type` (`UNORDERED`/`ORDERED`/`NONE`), `nesting_level` (0-8), `paragraph_start_indices` |
| `insert_doc_tab` | `title`, `index` | `parent_tab_id` |
| `delete_doc_tab` | `tab_id` | |
| `update_doc_tab` | `tab_id`, `title` | |

Use `list_type='NONE'` in `create_bullet_list` to remove existing list formatting.

---

## Tips

**Index-based editing**: Google Docs uses a flat character index. Index 0 is reserved for the leading section break, so `inspect_doc_structure` reports body content starting at index 1. Use those raw Docs API indices for edits and formatting. For convenience, `modify_doc_text` and `update_paragraph_style` also accept `start_index=0` when you mean "the first writable body position." After any edit that adds or removes content, indices shift -- re-inspect before the next operation, or work from the end of the document backward.

**Batch operations and index ordering**: When using `batch_update_doc` with multiple operations that change document length (inserts, deletes), process them from highest index to lowest. This prevents earlier operations from invalidating the indices of later ones. Alternatively, use `find_replace` operations which do not depend on indices.

**Structural inspection workflow**: For tables: (1) call `inspect_doc_structure` to get `total_length`, (2) use that as the insertion index for `create_table_with_data`, (3) call `debug_table_structure` to verify the result. Never guess insertion indices.

**Formatting without changing text**: Call `modify_doc_text` with `start_index` and `end_index` but omit `text` to apply formatting (bold, italic, color, etc.) to existing content.

**Prefer get_doc_as_markdown for reading**: It preserves formatting structure (headings, lists, bold, links) while `get_doc_content` returns plain text only. Use `get_doc_content` when you need raw text or are reading non-Google-Docs files.

**Tab operations**: Use `inspect_doc_structure` (without `tab_id`) to discover available tabs and their IDs. Then pass `tab_id` to editing tools or to `inspect_doc_structure` again to get structure within a specific tab.

**Comments**: `get_doc_as_markdown` with `comment_mode: "inline"` gives the most useful view of comments in context. Use `list_document_comments` for structured comment data, and `manage_document_comment` to create, reply, or resolve.
