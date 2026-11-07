# Google Gmail Tools Reference

MCP tools for Gmail message search, sending, drafting, labels, and filters. All tools require `user_google_email` (string, required).

## Contents
- Search & Read: search_gmail_messages, get_gmail_message_content, get_gmail_messages_content_batch, get_gmail_thread_content, get_gmail_threads_content_batch, get_gmail_attachment_content
- Send & Draft: send_gmail_message, draft_gmail_message
- Label Management: list_gmail_labels, manage_gmail_label, modify_gmail_message_labels, batch_modify_gmail_message_labels
- Filter Management: list_gmail_filters, manage_gmail_filter
- Tips

---

## Search & Read

### search_gmail_messages
Search messages by query. Returns message IDs, thread IDs, and Gmail web links.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| query | string | yes | | Gmail search query (see operators below) |
| user_google_email | string | yes | | |
| page_size | integer | no | 10 | Max results per page |
| page_token | any | no | | Pagination token |
| include_headers | boolean | no | false | Also return Subject, From, Date and label IDs per result; costs one metadata read per result |

### get_gmail_message_content
Get full content of a single message (subject, sender, recipients, date, Message-ID, body).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_id | string | yes | | |
| user_google_email | string | yes | | |
| format | string | no | "full" | "full" (with body and attachments) or "metadata" (headers only) |
| body_format | string | no | "text" | "text" (plaintext, HTML converted as fallback), "html" (raw HTML body) or "raw" (decoded MIME); requires format="full" |
| full | boolean | no | false | Return the complete untruncated message as a file path (stdio) or download URL (HTTP) instead of the inline body, which is truncated at 20,000 chars. body_format picks the export type ("raw" gives a byte-exact .eml). Stateless mode without GCS staging returns it inline. Requires format="full" |
| include_headers | boolean | no | true | false omits the Subject/From/To/Date summary (attached messages' too) and, with full=true, names the export after the message ID. Requires format="full"; body_format="raw" only when exported to a file |
| include_links | boolean | no | false | Append a LINKS section listing the http(s) URLs from the HTML body that the text output omits; only applies to body_format="text" |
| page_offset | integer | no | null | Start at 0 to return JSON body pages; follow next_offset until complete=true. Character offsets, not bytes. Requires format="full", full=false, body_format="text" or "html" |
| page_size | integer | no | 20000 | Body characters per page, 1 to 20,000; requires page_offset |
| expected_sha256 | string | no | null | Previous body_sha256; rejects a changed representation. Requires page_offset |

Keep body_format and include_links unchanged while paging. The JSON includes body,
page_offset, next_offset, total_chars, complete, and body_sha256, plus a headers object
when include_headers=true. Attachments and attached messages are excluded; use the
attachment tools or full=true for exports. Every page checks current Gmail access, then
reuses a process-local body cache (five minutes, at most 128 entries and 64 MiB) keyed by
account, message, body_format, and include_links. A cache miss re-fetches the message, and
expected_sha256 detects a changed representation. Raw and rendered messages are capped at
64 MiB, or WORKSPACE_MCP_MAX_FILE_BYTES when smaller.

### get_gmail_messages_content_batch
Get content of multiple messages in one request. Auto-batches in chunks of 25.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_ids | array of strings | yes | | |
| user_google_email | string | yes | | |
| format | string | no | "full" | "full" (with body) or "metadata" (headers only) |
| body_format | string | no | "text" | "text" (plaintext, HTML converted as fallback), "html" (raw HTML body) or "raw" (decoded MIME); requires format="full" |
| include_links | boolean | no | false | Append a LINKS section listing the http(s) URLs from the HTML body that the text output omits; only applies to body_format="text" |

### get_gmail_thread_content
Get all messages in a conversation thread.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| thread_id | string | yes | | |
| user_google_email | string | yes | | |
| body_format | string | no | "text" | "text" (plaintext, HTML converted as fallback), "html" (raw HTML body) or "raw" (decoded MIME) |
| include_analysis | boolean | no | false | Return a dict with the thread content plus ownership analysis (last sender, ball-in-court verdict, per-sender counts, participants) instead of a string |
| include_links | boolean | no | false | Append a LINKS section listing the http(s) URLs from the HTML body that the text output omits; only applies to body_format="text" |

### get_gmail_threads_content_batch
Get content of multiple threads in one request. Auto-batches in chunks of 25.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| thread_ids | array of strings | yes | | |
| user_google_email | string | yes | | |
| body_format | string | no | "text" | "text" (plaintext, HTML converted as fallback), "html" (raw HTML body) or "raw" (decoded MIME) |
| include_links | boolean | no | false | Append a LINKS section listing the http(s) URLs from the HTML body that the text output omits; only applies to body_format="text" |

### get_gmail_attachment_content
Download an attachment to local disk (stdio mode) or get a temporary URL (HTTP mode, 1-hour expiry).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_id | string | yes | | |
| attachment_id | string | yes | | |
| user_google_email | string | yes | | |
| attachment_index | integer | no | | Zero-based position from the message-content listing; lets the server re-resolve Gmail's refreshed attachment IDs before downloading |
| return_base64 | boolean | no | false | Also include the attachment as standard base64 in the response, for clients that cannot reach the file path or URL |

---

## Send & Draft

### send_gmail_message
Send an email. Supports new messages, replies, forwards, HTML, attachments, CC/BCC, and Send As aliases. Sends immediately; Gmail's API cannot schedule a send.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| to | string | conditional | | Recipient address. Required unless reply_all derives it; always required when forwarding |
| subject | string | conditional | | Required for a new message. Optional when replying with thread_id (inherited, "Re:" added if absent) or forwarding (defaults to "Fwd: <original subject>") |
| body | string | conditional | | Plain text or HTML content. Required unless forwarding, where it is an optional note prepended above the original |
| body_format | string | no | "plain" | "plain" or "html" |
| direction | string | no | "auto" | Base text direction: "auto" (from the first strong character), "ltr" or "rtl". On a forward it orients the note |
| forward_message_id | string | no | | Gmail message ID to forward instead of composing; reply and signature options do not apply |
| include_forwarded_attachments | boolean | no | true | Carry over the original's attachments; ignored unless forward_message_id is set |
| user_google_email | string | yes | | |
| cc | string | no | | |
| bcc | string | no | | |
| from_name | string | no | | Display name, e.g. "John Doe" |
| from_email | string | no | | Send As alias (must be configured in Gmail settings) |
| thread_id | string | no | | Thread ID for replies; defaults to its latest non-draft, non-trash message with an RFC `Message-ID` |
| in_reply_to | string | no | | RFC Message-ID of a specific reply target; omit to reply to the latest eligible message |
| references | string | no | | Optional Message-ID ancestry chain; normally derived from thread_id |
| reply_all | boolean | no | false | Derive recipients from the thread: To = sender being replied to, Cc = other participants (minus you and from_email). Requires thread_id; explicit to/cc win |
| quote_original | boolean | no | false | Include the message being replied to as a quoted original (requires thread_id) |
| include_signature | boolean | no | true | Append Gmail signature if available |
| attachments | array | no | | See attachment format below |

**Attachment format** (each item is an object):
- **File path**: `{"path": "path/to/file.pdf"}` -- optionally add `"filename"` and `"mime_type"`. Use forward slashes on all platforms
- **Base64 content**: `{"content": "base64data", "filename": "doc.pdf"}` -- optionally add `"mime_type"` (must be standard base64, not urlsafe)

### draft_gmail_message
Create a draft. Same capabilities as send (including forwards and reply_all).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| subject | string | conditional | | Required for a new draft. Optional when replying with thread_id (inherited) or forwarding (defaults to "Fwd: <original subject>") |
| body | string | conditional | | Required unless forwarding, where it is an optional note prepended above the original |
| body_format | string | no | "plain" | "plain" or "html" |
| direction | string | no | "auto" | Base text direction: "auto" (from the first strong character), "ltr" or "rtl". On a forward it orients the note |
| forward_message_id | string | no | | Gmail message ID to save a forward of as a draft; reply and signature options do not apply |
| include_forwarded_attachments | boolean | no | true | Carry over the original's attachments; ignored unless forward_message_id is set |
| user_google_email | string | yes | | |
| to | string | no | | Can be empty for drafts, including forwards; a reply draft fills it from the thread |
| cc | string | no | | |
| bcc | string | no | | |
| from_name | string | no | | Display name |
| from_email | string | no | Gmail default | Send As alias; when omitted, resolves `isDefault`, then `isPrimary`, then the first Send-As entry, and finally the authenticated email if no usable entry is available |
| thread_id | string | no | | For reply drafts; defaults to its latest non-draft, non-trash message with an RFC `Message-ID` |
| in_reply_to | string | no | | RFC Message-ID of a specific reply target; omit for the latest eligible message |
| references | string | no | | Optional Message-ID ancestry chain; normally derived from thread_id |
| attachments | array | no | | Same format as send |
| include_signature | boolean | no | true | Append Gmail signature if available |
| reply_all | boolean | no | false | Derive recipients from the thread: To = sender being replied to, Cc = other participants (minus you and from_email). Requires thread_id; explicit to/cc win |
| quote_original | boolean | no | false | Include original message as quoted reply (requires thread_id) |

Operational Gmail settings errors such as rate limits abort draft creation instead of silently falling back to a potentially unintended sender.

---

## Label Management

### list_gmail_labels
List all labels with IDs, names, and types.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| prefix | string | no | | Only labels whose name starts with this exact, case-sensitive string (filtered after fetching the full list) |
| include_system | boolean | no | true | Set false to return user labels only |
| compact | boolean | no | false | Return minimal JSON `{"count", "labels": [{"id", "name"}]}` sorted by name instead of formatted text |

### manage_gmail_label
Create, update, or delete a label.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "create", "update", or "delete" |
| name | string | conditional | | Required for create, optional for update |
| label_id | string | conditional | | Required for update and delete |
| label_list_visibility | string | no | "labelShow" on create | "labelShow", "labelShowIfUnread" or "labelHide". On update, omitting it keeps the label's current setting |
| message_list_visibility | string | no | "show" on create | "show" or "hide". On update, omitting it keeps the label's current setting |
| background_color | string | no | | Hex color, e.g. "#fb4c2f". Set with text_color. Gmail accepts only its own [palette](https://developers.google.com/gmail/api/reference/rest/v1/users.labels#Label); other values are rejected before the request. User labels only |
| text_color | string | no | | Hex color, e.g. "#ffffff". Set with background_color. Same palette |
| clear_color | boolean | no | false | On update, remove the current color. Cannot be combined with background_color or text_color |

Colors are optional, but Gmail requires both when one is given, so passing only
one is rejected. On update, passing neither keeps the color the label already had;
set clear_color to remove it. Color parameters are ignored for delete actions.

### modify_gmail_message_labels
Add or remove labels on a single message.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_id | string | yes | | |
| add_label_ids | array of strings | no | | Label IDs to add |
| remove_label_ids | array of strings | no | | Label IDs to remove |

### batch_modify_gmail_message_labels
Add or remove labels on multiple messages, or on every message of whole threads, at once.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_ids | array of strings | one of | | Message IDs |
| thread_ids | array of strings | one of | | Thread IDs; changes every message in each thread |
| add_label_ids | array of strings | no | | Label IDs to add |
| remove_label_ids | array of strings | no | | Label IDs to remove |
| verify | boolean | no | true | Read messages back and report which ids actually changed (Gmail silently ignores unknown ids). Set false for very large sweeps. Not needed for thread_ids |

**Common label operations:**
- Archive: remove `"INBOX"`
- Mark read: remove `"UNREAD"`
- Mark unread: add `"UNREAD"`
- Star: add `"STARRED"`
- Trash: add `"TRASH"`

---

## Filter Management

### list_gmail_filters
List all filters with their criteria and actions.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |

### manage_gmail_filter
Create, delete, update, or apply a filter. Update recreates the filter (Gmail has no update API), so its ID changes. Apply runs a filter's label actions on mail already in the mailbox (never forwarding) and also needs the gmail.modify scope.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "create", "delete", "update" or "apply" |
| criteria | object | for create | | Filter criteria (see below). On update, replaces the whole object; omit to keep the old one |
| filter_action | object | for create | | Actions to apply (see below). On update, replaces the whole object; omit to keep the old one |
| filter_id | string | for delete/update | | ID of the filter. For apply, pass it or criteria + filter_action for an ad-hoc run |
| dry_run | boolean | no | false | Apply only: report the search query and match count without changing anything |
| max_messages | integer | no | 5000 | Apply only: safety cap on messages changed |

**Criteria object keys:** `from`, `to`, `subject`, `query`, `negatedQuery`, `hasAttachment` (bool), `excludeChats` (bool), `size` (int), `sizeComparison` (string).

**Filter action object keys:** `addLabelIds` (array), `removeLabelIds` (array), `forward` (string).

---

## Tips

**Search syntax**: The `query` parameter uses standard Gmail search syntax (`from:`, `to:`, `subject:`, `is:unread`, `has:attachment`, `newer_than:7d`, `label:`, `category:`, `rfc822msgid:`).

### Threading and Replies
- Every search result returns both a `message_id` and a `thread_id`. Use the thread_id to read the full conversation.
- For a normal reply, pass `thread_id` and omit `in_reply_to` and `references`. The server targets the latest non-draft, non-trash message with an RFC `Message-ID` and derives both RFC reply headers through it.
- Set `in_reply_to` only when deliberately replying to a specific older message. Use that message's RFC `Message-ID` header, not its Gmail API message ID; `references` can still be omitted and derived automatically.
- Keep the subject consistent with the thread, normally prefixed with `Re: `.

### Pagination
- `search_gmail_messages` returns a `next_page_token` when more results exist. Pass it as `page_token` in the next call.
- Unpaginated search results are incomplete -- always check for and follow `next_page_token` when you need full coverage.

### Batch Operations
- `get_gmail_messages_content_batch` and `get_gmail_threads_content_batch` fetch in chunks of 25 to avoid SSL exhaustion.
- `batch_modify_gmail_message_labels` sends `message_ids` in one Gmail call, which accepts up to 1000 ids; split larger sets across calls.

### Label IDs
- System labels use uppercase IDs: `INBOX`, `SENT`, `TRASH`, `SPAM`, `DRAFT`, `UNREAD`, `STARRED`, `IMPORTANT`.
- Custom labels have generated IDs (e.g., `Label_123`). Use `list_gmail_labels` to discover them.
- Use label IDs (not names) in `modify_gmail_message_labels`, `batch_modify_gmail_message_labels`, and filter actions.

### Drafts vs Send
- Use `draft_gmail_message` when you want the user to review before sending. It supports `include_signature` (auto-appends Gmail signature) and `quote_original` (includes quoted reply text).
- Use `send_gmail_message` for immediate delivery.
- To forward, pass `forward_message_id` to either tool; the original subject, quoted body and (by default) attachments are carried over, and `body` becomes an optional note.

### Attachments
- To find attachments, read the message with `get_gmail_message_content` -- attachment IDs are listed in the response.
- Download with `get_gmail_attachment_content` using both the message_id and attachment_id. Also pass the listed `attachment_index`, since Gmail can refresh attachment IDs between reads.
- When sending/drafting, attachments can be specified as file paths (auto-encoded) or pre-encoded base64 content (standard base64, not urlsafe).
