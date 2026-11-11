# Google Slides Tools Reference

MCP tools for reading, creating, and updating Google Slides presentations. All tools require `user_google_email` (string, required).

## Contents
- Reading Presentations: get_presentation, get_page, render_page_to_image
- Creating & Updating: create_presentation, batch_update_presentation
- Speaker Notes
- Comments: list_presentation_comments, manage_presentation_comment
- Tips

---

## Reading Presentations

### get_presentation
Get presentation metadata: title, slide count, and slide object IDs.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| include_speaker_notes | boolean | no | false | Also report each slide's speaker notes shape ID and current notes text |
| include_geometry | boolean | no | false | Also list each slide's elements with transform and size in EMU, to match an existing deck's margins |
| include_styles | boolean | no | false | Also list each slide's elements with text styling (see `get_page`) |
| raw | boolean | no | false | Return the full Presentation resource as JSON (can be large). Overrides the other flags |

### get_page
Get details about a specific slide, including its elements and layout.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| page_object_id | string | yes | | |
| include_geometry | boolean | no | false | Report each element's transform and intrinsic size in EMU, plus table column widths, row heights, and cell text. The only way to discover a deck's margins and content width |
| include_styles | boolean | no | false | Report explicit text styling per paragraph and run, placeholder linkage, autofit, fill, and outline. Useful for proofing |
| raw | boolean | no | false | Return the full Page resource as JSON. Overrides the other flags |

### render_page_to_image
Render a slide (or any page, including a notes page) to a PNG and return it as image content, so you can look at the slide's layout and visuals.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| page_object_id | string | yes | | |
| size | string | no | MEDIUM | `SMALL`, `MEDIUM`, `LARGE`, or `WIDTH2000_PX` |

`size` sets the image width:

| Size | Width | Approx. Claude image tokens | Use for |
|------|-------|-----------------------------|---------|
| `SMALL` | 200px | ~30 | layout only; text is unreadable |
| `MEDIUM` | 800px | ~460 | titles and body text |
| `LARGE` | 1600px | ~1,850 | small text: tables, chart labels, footnotes |
| `WIDTH2000_PX` | 2000px | ~2,900 | rarely reads more than `LARGE` |

Token cost and the resolution the model sees depend on the model. The figures above are for Claude models with high-resolution vision, which read every size as rendered. Other models may downscale `LARGE` and `WIDTH2000_PX` and count image tokens differently, so a larger size may not show more detail.

Start at `MEDIUM`, and re-render at `LARGE` only for a slide whose small text you need to read. Any other value is rejected before the API is called.

The result is a short text block (presentation ID, page ID, size with the rendered pixel dimensions, and the image URL) followed by the image. If the image cannot be fetched, only the text block comes back, with a note saying so.

---

## Creating & Updating

### create_presentation
Create a new Google Slides presentation.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| title | string | no | Untitled Presentation | |

### batch_update_presentation
Apply batch update requests to a presentation. This is the primary tool for modifying slides -- adding slides, inserting text, images, shapes, tables, and applying formatting.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| requests | array | yes | | List of Slides API request objects |

Each item in `requests` is a dict with exactly one key corresponding to a Slides API request type. Common request types:

| Request type | Purpose |
|--------------|---------|
| `createSlide` | Add a new slide |
| `deleteObject` | Delete a slide or element |
| `createShape` | Add a shape (rectangle, text box, etc.) |
| `insertText` | Insert text into a shape or table cell (including the speaker notes shape) |
| `deleteText` | Delete text from an element |
| `updateTextStyle` | Apply text formatting (bold, color, font, size) |
| `updateParagraphStyle` | Set alignment, spacing, bullet style |
| `createImage` | Insert an image from a URL |
| `createTable` | Add a table to a slide |
| `insertTableRows` | Add rows to an existing table |
| `insertTableColumns` | Add columns to an existing table |
| `updateTableCellProperties` | Format table cells |
| `replaceAllText` | Find and replace text across the presentation |
| `updatePageProperties` | Set slide background color or image |
| `updateShapeProperties` | Modify shape fill, outline, size, position |
| `duplicateObject` | Duplicate a slide or element |

See the [Slides API reference](https://developers.google.com/slides/api/reference/rest/v1/presentations/batchUpdate) for full request schemas.

---

## Speaker Notes

Speaker notes (presenter notes) are editable -- there are no dedicated tools, they go through the two tools above.

Each slide has a notes page, and the notes text lives in the single shape on it identified by `speakerNotesObjectId`. Only that shape's text is writable; the rest of the notes page and the notes master are read-only. That shape ID is **not** returned by `get_page`, and the notes *page* ID is **not** a valid `insertText` target.

**Read**: call `get_presentation` with `include_speaker_notes=true`. Each slide then reports its `Speaker Notes Shape ID` and current notes text. This costs no extra API call.

**Write**: pass that shape ID to `batch_update_presentation`. To replace notes, clear then insert:

```json
[
  {"deleteText": {"objectId": "<speaker notes shape ID>", "textRange": {"type": "ALL"}}},
  {"insertText": {"objectId": "<speaker notes shape ID>", "insertionIndex": 0, "text": "Full narration here."}}
]
```

Omit the `deleteText` request when the notes are already empty -- deleting an empty range is an API error. To clear notes, send `deleteText` alone.

Appending uses the same clear-then-insert pair: read the existing notes, then insert `existing + new` as one `insertText` at index 0. Inserting only the new text without the `deleteText` duplicates the existing notes, and the notes text reported by `get_presentation` is normalized (blank lines dropped, trailing whitespace stripped), so its length cannot be used as an `insertionIndex` -- `insertionIndex` counts Unicode code units in the shape's actual text.

If an `insertText` accidentally targets the notes page instead of the notes shape, the error response names the correct shape ID to retry with.

---

## Comments

### list_presentation_comments
List all comments on a presentation.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| max_comments | integer | no | 100 | Cap on comments returned; must be at least 1. Default comes from `WORKSPACE_MCP_COMMENTS_MAX`; unset, invalid, or non-positive values fall back to 100 |

### manage_presentation_comment
Create, reply to, resolve, edit, or delete a comment.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| action | string | yes | | `create`, `reply`, `resolve`, `update`, or `delete` |
| comment_content | string | for create/reply/update | | Comment text |
| comment_id | string | for reply/resolve/update/delete | | Target comment ID |

---

## Tips

**Workflow**: Call `get_presentation` first to get slide object IDs, then `get_page` to inspect individual slide elements. Use `batch_update_presentation` for all modifications.

**Object IDs**: Every slide and element has a unique `objectId`. Use `get_presentation` to discover slide IDs and `get_page` to discover element IDs within a slide. You can assign custom object IDs when creating elements (useful for referencing them in subsequent requests within the same batch).

**Adding text**: `insertText.objectId` must be a text-capable shape or table object ID, not the slide/page ID. To place new text on a slide, create a text box or shape first with `createShape` and `elementProperties.pageObjectId` set to the slide ID, then call `insertText` using the new shape `objectId`.

**Keep slides skimmable**: put short headlines and bullets on the slide itself and move the narration -- full sentences, context, talking points -- into speaker notes (see above). Never create a separate document for notes.

**Coordinate system**: Positions and sizes use EMU (English Metric Units). 1 inch = 914400 EMU. Standard slide dimensions are 10 inches wide (9144000 EMU) by 5.625 inches tall (5143500 EMU).

**Batch ordering**: Requests in a batch execute sequentially. Earlier requests can create elements that later requests reference by object ID. If a request fails, subsequent requests in the batch are skipped.

**Rendered images**: The image URL in the text block returned by `render_page_to_image` expires after about 30 minutes and is tagged with the requesting account, so anyone holding it can open the image until then. Use it promptly and don't share it. The image itself is already in the result, so you rarely need the URL.
