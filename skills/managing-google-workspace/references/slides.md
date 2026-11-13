# Google Slides Tools Reference

MCP tools for reading, creating, and updating Google Slides presentations. All tools require `user_google_email` (string, required).

## Contents
- Reading Presentations: get_presentation, render_page_to_image
- Creating & Updating: create_presentation, batch_update_presentation
- Speaker Notes
- Comments: list_presentation_comments, manage_presentation_comment
- Tips

---

## Reading Presentations

### get_presentation
Read a deck through one of four actions, from cheapest to most detailed: `deck` (slides and layouts), `page` (one slide's element IDs and all its text), `markup` (one slide or chosen elements as compact JSON with geometry and styles), and `layouts` (every layout, for decks with more than 25).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| action | string | no | deck | `deck`, `layouts`, `page`, or `markup` |
| page_id | string | for page/markup | | Slide (or any page) object ID, from `deck` |
| targets | list of strings | no | | `markup` only: element IDs to return instead of the whole slide, plus `"notes"` for the speaker notes |
| raw | boolean | no | false | `markup` only: return the Slides API Page resource itself (EMU, transforms, text indices), limited to `targets`, for writing raw `batch_update_presentation` requests |
| offset | integer | no | 0 | `deck` only: first slide to list. `deck` lists 100 slides per call and ends with the offset to read on from |
| after | string | no | | `layouts` only: a slide ID, or `"start"`. Lists only the layouts a new slide inserted there can use |

**`deck`** gives the title, URL and revision, then one entry per slide: its ID, title, layout ID, element counts and notes length. Then the deck's layouts, each with its placeholder types and the slides using it. Decks with more than 25 layouts list only the layouts their slides use, and point to `layouts` for the rest.

**`page`** lists every element in z-order, with groups nested: shapes with their type, placeholder and text (bullets indented by level), table cells as `[row,col] text`, images with their source URL, charts with their spreadsheet and chart IDs, and the speaker notes shape ID and text. It shows no geometry or styling, and it says when the layout or master draws content that has no element ID on the slide. Use `render_page_to_image` to see that, and to see images.

**`markup`** returns compact JSON:

```json
{"page_id":"g1_0","page_size":{"w":720,"h":405},"styles":{"s1":{"size":32}},"elements":[{"id":"g1_1","v":"2877e57c","box":{"x":33,"y":75,"w":344,"h":194},"placeholder":"TITLE","text":[{"text":"Q4 readout","s":"s1"}]},{"id":"g1_2","v":"ff3e2f10","box":{"x":39,"y":36,"w":179,"h":33},"shape":"ROUND_RECTANGLE","fill":"DARK2","outline":"none","text":["Commerce",{"text":"Platform","bullet":0}]}]}
```

- Lengths are points: a 16:9 slide is 720 x 405. `box` is the element's unrotated bounding box on the page, plus `rot` (degrees) and `flip` when set. Geometry is rounded to whole points.
- `text` is a list of paragraphs. A paragraph is a string, or an object with `text` or `runs` and its paragraph props (`align`, `line_spacing`, `space_above`, `bullet`, ...). A run is a string, or `{"text", "s"}` where `s` names an entry in `styles`.
- Only values set on the element itself appear; anything absent is inherited from the placeholder, layout or theme. On a shape with no placeholder, the editor's defaults are left out too: a missing `shape` means `TEXT_BOX`; a text box has no fill or outline, and its content and paragraphs are top- and start-aligned; other shapes are middle- and center-aligned; line spacing is 100.
- A link run omits the underline and `HYPERLINK` colour the link implies, and a bold run omits weight 700.
- `"bullet": 1` is a level in the element's only list; with several lists, a bullet is `{"list": "L1", "level": 1}`. `lists` shows each list's rendered glyphs.
- `autofit` is `"grow"` or `{"type": "shrink", "scale": 0.8}`. In a shrunk shape, sizes are as displayed.
- `v` is a short hash of the element's content.

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

Each slide has a notes page, and the notes text lives in the single shape on it identified by `speakerNotesObjectId`. Only that shape's text is writable; the rest of the notes page and the notes master are read-only. The notes *page* ID is **not** a valid `insertText` target.

**Read**: call `get_presentation` with `action="page"`. The slide's digest ends with `notes shape <ID>:` and the notes text, or `(empty)`.

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

**Workflow**: Call `get_presentation` with `action="deck"` to get slide object IDs, then `action="page"` to read a slide's elements and text. Use `action="markup"` when you need positions or styles, for example to place a new element against an existing deck's margins. Use `batch_update_presentation` for all modifications, with `action="markup"` and `raw=true` when a request needs text indices or transforms.

**Object IDs**: Every slide and element has a unique `objectId`. `action="deck"` lists slide IDs and `action="page"` lists element IDs within a slide. You can assign custom object IDs when creating elements (useful for referencing them in subsequent requests within the same batch).

**Adding text**: `insertText.objectId` must be a text-capable shape or table object ID, not the slide/page ID. To place new text on a slide, create a text box or shape first with `createShape` and `elementProperties.pageObjectId` set to the slide ID, then call `insertText` using the new shape `objectId`.

**Keep slides skimmable**: put short headlines and bullets on the slide itself and move the narration -- full sentences, context, talking points -- into speaker notes (see above). Never create a separate document for notes.

**Coordinate system**: `batch_update_presentation` and raw markup use EMU (English Metric Units); `action="markup"` converts to points (12700 EMU per point). 1 inch = 914400 EMU. Standard slide dimensions are 10 inches wide (9144000 EMU) by 5.625 inches tall (5143500 EMU).

**Batch ordering**: Requests in a batch execute sequentially. Earlier requests can create elements that later requests reference by object ID. If a request fails, subsequent requests in the batch are skipped.

**Rendered images**: The image URL in the text block returned by `render_page_to_image` expires after about 30 minutes and is tagged with the requesting account, so anyone holding it can open the image until then. Use it promptly and don't share it. The image itself is already in the result, so you rarely need the URL.
