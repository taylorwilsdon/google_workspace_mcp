# Design: Suggestion-Aware Doc Text Tools with Section Chunking

**Date:** 2026-03-28
**Status:** Approved

## Problem

`get_doc_content` merges original text and suggested changes into a single flat string, making it impossible to distinguish what is original vs. what is a pending suggestion. Additionally, long documents exceed what can be usefully processed in a single response.

## Solution

Two new tools backed by a shared helper module:

1. **`list_doc_sections`** — returns a table of contents based on heading structure
2. **`get_doc_text`** — returns document text filtered by suggestion mode, optionally scoped to a section or chunk

## Architecture

### New files

- `gdocs/docs_text.py` — shared text extraction helpers (suggestion-aware)

### Modified files

- `gdocs/docs_tools.py` — register the two new tools

### No changes to existing tools

`get_doc_content` is left untouched.

## Helper Module: `gdocs/docs_text.py`

### `extract_sections(body_elements) -> list[dict]`

Walks the document body elements and groups them by heading boundaries.

Returns a list of:
```python
{
    "index": int,       # 0-based section index
    "level": int,       # heading level (1, 2, 3, ...)
    "title": str,       # heading text (suggestions stripped)
    "elements": list    # raw API elements belonging to this section
}
```

Non-heading content before the first heading is grouped as section 0 with title `"(preamble)"`.

### `render_elements(elements, mode) -> str`

Walks elements (paragraphs and tables), applies per-`textRun` suggestion filter:

| mode | include textRun if |
|------|--------------------|
| `"original"` | `suggestedInsertionIds` is empty (not a pending insert); textRuns with `suggestedDeletionIds` are included (they still exist in original) |
| `"accepted"` | `suggestedDeletionIds` is empty (not pending deletion); textRuns with `suggestedInsertionIds` are included (insertions accepted) |

TextRuns with neither field set are always included.

## Tool: `list_doc_sections`

```python
list_doc_sections(
    user_google_email: str,
    document_id: str,
) -> str
```

**Returns:** Numbered list of sections with heading level and title.

**If no headings found:** Returns doc character length and recommended `chunk_size` so the caller can plan `get_doc_text` calls with `chunk_index`.

**Scope:** Main document body only (tabs not included).

**Error:** Non-native Google Doc → error directing user to `get_doc_content`.

## Tool: `get_doc_text`

```python
get_doc_text(
    user_google_email: str,
    document_id: str,
    mode: str,                # "original" or "accepted"
    section_index: int = None,  # 0-based index from list_doc_sections
    chunk_index: int = None,    # 0-based, used when no headings
    chunk_size: int = 10000,    # chars per chunk (headingless fallback)
) -> str
```

**Behavior:**
- If `section_index` provided: return text for that section (heading through next same-or-higher heading). Takes priority over `chunk_index` if both provided.
- If `chunk_index` provided (headingless doc): slice full rendered text by `chunk_size`.
- If neither provided: return full text (caller's responsibility if large).

**Response format:** Text content prefixed with metadata header:
```
[Section 2/8: "Introduction" | mode: original]
<text>
```
or for chunks:
```
[Chunk 1/5 | mode: accepted | chunk_size: 10000]
<text>
```

**Scope:** Main document body only.

## Error Handling

| Condition | Response |
|-----------|----------|
| `mode` not `"original"` or `"accepted"` | Clear error message |
| `section_index` out of range | `"Document has N sections (0 to N-1)"` |
| `chunk_index` out of range | `"Document has N chunks of size chunk_size"` |
| Non-native Google Doc | Error directing to `get_doc_content` |
| Empty section | Return heading title + empty body (not an error) |

## Out of Scope

- Multi-tab document support (can be added later)
- Accepting or rejecting individual suggestions (write operation, separate feature)
- `.docx` file support
