---
name: create-google-doc-with-layout
description: Use when creating a Google Doc with proper layout — headings, lists, tables, normal text. Prevents style cascade, heading-styled body text, and other formatting pitfalls of the Google Docs API.
---

# Create Google Doc with Proper Layout

## What is this?

This is a [Claude Code skill](https://docs.anthropic.com/en/docs/claude-code/skills) — a reusable prompt that guides AI agents through complex multi-step workflows.

### Why does this exist?

The Google Docs API has well-documented formatting pitfalls that are difficult for AI agents to navigate without guidance:

- **Style cascade**: inserting a heading and body text in the same block causes both to inherit the heading style
- **Index shifting**: every insertion changes character positions, making subsequent style operations target the wrong text
- **List merging**: inserting list items one at a time creates separate lists instead of one continuous list
- **Table cell indexing**: filling cells top-to-bottom corrupts positions; bottom-right-to-top-left is required

These issues affect any consumer of this MCP's document creation tools (`modify_doc_text`, `update_paragraph_style`, `insert_doc_elements`). This skill encodes the correct insertion patterns so agents produce well-formatted documents on the first attempt.

### Why here and not in the tools themselves?

Ideally these patterns would be handled internally by the MCP tools (e.g., a single `create_formatted_doc` call that accepts structured content and handles ordering/style-resets). This skill is a pragmatic complement — it works with the existing tool surface today. If the tools evolve to handle these patterns natively, this skill becomes unnecessary.

### How to use

This file is included automatically as part of the `managing-google-workspace` skill. Install that skill to use this guidance:

```bash
mkdir -p ~/.claude/skills/managing-google-workspace/references
cp skills/managing-google-workspace/references/docs-layout-workflow.md ~/.claude/skills/managing-google-workspace/references/
```

Claude Code will automatically discover and apply it when creating formatted Google Docs.

---

## Core Principle

**Build incrementally.** Never insert all content at once. Insert small batches, apply styles immediately, then insert the next batch. Re-read the document after every change to get fresh character positions.

---

## Step 1 — Start from a template (if available)

If a branded template exists, copy it with `copy_drive_file`. If not, create an empty doc with `create_doc`.

**Important:** The template must be **single-tab** unless you explicitly pass `tab_id` to each `update_paragraph_style` call. Without `tab_id`, the tool targets the default tab — which may not be the one you're editing in a multi-tab document.

---

## Step 2 — Replace title placeholders

Use `find_and_replace_doc` to replace any template placeholders (company name, project name, author, date). This preserves the template's title/subtitle styling.

---

## Step 3 — Build content in batches

Insert content at the end of the document using `modify_doc_text` with `end_of_segment=true` or by reading the doc and finding the end position.

### The batch pattern

Repeat this cycle for each content group:

1. **Insert heading text** (one paragraph only, ending with `\n`)
2. **Apply heading style** with `update_paragraph_style` (heading_level=1, 2, or 3)
3. **Insert body text** (one or more paragraphs, each ending with `\n`)
4. **Apply NORMAL_TEXT** to body paragraphs with `update_paragraph_style` (named_style_type="NORMAL_TEXT")
5. **Re-read doc** with `get_doc_content` to get fresh positions

### Critical rules

| Rule | Why |
|------|-----|
| **Never insert heading + body in same text block** | Both paragraphs get the heading style |
| **Always apply NORMAL_TEXT after a heading's body** | Prevents the next insertion from inheriting the heading style |
| **Re-read after every insertion** | Character positions shift after every insert; stale positions cause wrong styling |
| **For lists: insert ALL items in ONE block** | Separate insertions create separate lists instead of one continuous list |

---

## Step 4 — Insert lists

### Unordered (bullet) lists

1. Insert all list items as ONE text block: `"Item 1\nItem 2\nItem 3\n"`
2. Apply `list_type="UNORDERED"` to the entire range in ONE call
3. Immediately insert the next paragraph and set it to `named_style_type="NORMAL_TEXT"` to break the list

### Ordered (numbered) lists

Same approach but with `list_type="ORDERED"`.

### Separating lists from surrounding content

After a list, the next paragraph will inherit list formatting unless you explicitly set it to NORMAL_TEXT. Always insert the next paragraph and reset its style before continuing.

---

## Step 5 — Insert tables

1. Find the current end position by reading the doc
2. Insert table with `insert_doc_elements(element_type="table", index=<position>, rows=N, columns=M)`
3. **Fill cells from BOTTOM-RIGHT to TOP-LEFT** — this prevents index shifting as content is inserted into cells
4. Re-read the doc to find the position after the table before inserting more content

### Finding position after a table

The table inserts invisible structural characters. To find where to continue:
- Try inserting at a very high index (e.g., 99999) — the error message reveals the actual document end
- Or re-read the doc and count characters

---

## Step 6 — Verify

**ALWAYS export to PDF and visually verify layout before claiming done.**

```python
mcp__google-workspace__export_doc_to_pdf(document_id="<doc_id>")
```

Then download and read the PDF to check:
- No body paragraphs styled as headings (they appear in heading font/size/color)
- Lists render as proper bullets/numbers
- Tables have all cells filled
- Heading hierarchy is correct (H1 > H2 > H3)
- No stray empty paragraphs or list items

---

## Common Pitfalls & Fixes

| Problem | Cause | Fix |
|---------|-------|-----|
| Body text appears as heading (large/colored font) | Inserted heading + body in same block, or didn't reset to NORMAL_TEXT | Insert heading alone, apply style, then insert body with NORMAL_TEXT |
| All paragraphs become bullets | list_type applied to too-wide range | Apply list_type only to actual list item paragraphs |
| Numbered list shows "1. 1. MAP" | Text already contains "1." prefix AND list_type="ORDERED" adds numbering | Remove number prefixes from text when using ORDERED lists |
| Euro sign shows as \u20ac | Unicode escape not resolved | Use actual € character in text |
| Table cells empty | Index calculation wrong after table insert | Fill cells bottom-right to top-left; re-read doc for fresh positions |
| Wrong tab styled in multi-tab doc | `tab_id` not passed to `update_paragraph_style` | Pass `tab_id` to each `update_paragraph_style` call, or use single-tab documents |
| Style cascade across batches | Didn't re-read doc between batches | Always re-read after each insertion |

---

## Quick Reference: Style Application

```python
# Heading 1
update_paragraph_style(start_index=X, end_index=Y, heading_level=1)

# Heading 2
update_paragraph_style(start_index=X, end_index=Y, heading_level=2)

# Normal text (reset from heading/list)
update_paragraph_style(start_index=X, end_index=Y, named_style_type="NORMAL_TEXT")

# Bullet list
update_paragraph_style(start_index=X, end_index=Y, list_type="UNORDERED")

# Numbered list
update_paragraph_style(start_index=X, end_index=Y, list_type="ORDERED")
```

**Index rules:**
- start_index = first character of the paragraph from `inspect_doc_structure` (Docs API body content typically starts at 1; `0` is also accepted as a start-of-body alias)
- end_index = character AFTER the `\n` that ends the paragraph
- For multi-paragraph ranges (lists), span from first char of first item to after `\n` of last item
