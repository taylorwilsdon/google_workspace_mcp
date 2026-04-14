# Google Slides Tools Reference

MCP tools for reading, creating, and updating Google Slides presentations. All tools require `user_google_email` (string, required).

## Contents
- Reading Presentations: get_presentation, get_page, get_page_thumbnail
- Creating & Updating: create_presentation, batch_update_presentation
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

### get_page
Get details about a specific slide, including its elements and layout.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| page_object_id | string | yes | | |

### get_page_thumbnail
Generate a thumbnail URL for a slide.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| page_object_id | string | yes | | |
| thumbnail_size | string | no | MEDIUM | |

`thumbnail_size` accepts `LARGE`, `MEDIUM`, or `SMALL`.

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
| `insertText` | Insert text into a shape or table cell |
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

## Comments

### list_presentation_comments
List all comments on a presentation.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |

### manage_presentation_comment
Create, reply to, or resolve a comment.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| presentation_id | string | yes | | |
| action | string | yes | | `create`, `reply`, or `resolve` |
| comment_content | string | for create/reply | | Comment text |
| comment_id | string | for reply/resolve | | Target comment ID |

---

## Tips

**Workflow**: Call `get_presentation` first to get slide object IDs, then `get_page` to inspect individual slide elements. Use `batch_update_presentation` for all modifications.

**Object IDs**: Every slide and element has a unique `objectId`. Use `get_presentation` to discover slide IDs and `get_page` to discover element IDs within a slide. You can assign custom object IDs when creating elements (useful for referencing them in subsequent requests within the same batch).

**Coordinate system**: Positions and sizes use EMU (English Metric Units). 1 inch = 914400 EMU. Standard slide dimensions are 10 inches wide (9144000 EMU) by 5.625 inches tall (5143500 EMU).

**Batch ordering**: Requests in a batch execute sequentially. Earlier requests can create elements that later requests reference by object ID. If a request fails, subsequent requests in the batch are skipped.

**Thumbnails**: Thumbnail URLs returned by `get_page_thumbnail` are temporary. Fetch them promptly after generation.
