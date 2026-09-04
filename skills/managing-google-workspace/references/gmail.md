# Google Gmail Tools Reference

MCP tools for Gmail message search, sending, drafting, labels, and filters. All tools require `user_google_email` (string, required).

## Contents
- Search & Read: search_gmail_messages, get_gmail_message_content, get_gmail_messages_content_batch, get_gmail_thread_content, get_gmail_threads_content_batch, get_gmail_attachment_content
- Send & Draft: send_gmail_message, draft_gmail_message, delete_gmail_draft
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

### get_gmail_message_content
Get full content of a single message (subject, sender, recipients, date, Message-ID, body).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_id | string | yes | | |
| user_google_email | string | yes | | |

### get_gmail_messages_content_batch
Get content of multiple messages in one request. Max 25 per batch.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_ids | array of strings | yes | | Max 25 |
| user_google_email | string | yes | | |
| format | string | no | "full" | "full" (with body) or "metadata" (headers only) |

### get_gmail_thread_content
Get all messages in a conversation thread.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| thread_id | string | yes | | |
| user_google_email | string | yes | | |

### get_gmail_threads_content_batch
Get content of multiple threads in one request. Auto-batches in chunks of 25.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| thread_ids | array of strings | yes | | |
| user_google_email | string | yes | | |

### get_gmail_attachment_content
Download an attachment to local disk (stdio mode) or get a temporary URL (HTTP mode, 1-hour expiry).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| message_id | string | yes | | |
| attachment_id | string | yes | | |
| user_google_email | string | yes | | |

---

## Send & Draft

### send_gmail_message
Send an email. Supports new messages, replies, HTML, attachments, CC/BCC, and Send As aliases.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| to | string | yes | | Recipient address |
| subject | string | yes | | |
| body | string | yes | | Plain text or HTML content |
| body_format | string | no | "plain" | "plain" or "html" |
| user_google_email | string | yes | | |
| cc | string | no | | |
| bcc | string | no | | |
| from_name | string | no | | Display name, e.g. "John Doe" |
| from_email | string | no | | Send As alias (must be configured in Gmail settings) |
| thread_id | string | no | | Thread ID for replies; defaults to its latest non-draft, non-trash message with an RFC `Message-ID` |
| in_reply_to | string | no | | RFC Message-ID of a specific reply target; omit to reply to the latest eligible message |
| references | string | no | | Optional Message-ID ancestry chain; normally derived from thread_id |
| attachments | array | no | | See attachment format below |

**Attachment format** (each item is an object):
- **File path**: `{"path": "path/to/file.pdf"}` -- optionally add `"filename"` and `"mime_type"`. Use forward slashes on all platforms
- **Base64 content**: `{"content": "base64data", "filename": "doc.pdf"}` -- optionally add `"mime_type"` (must be standard base64, not urlsafe)

### draft_gmail_message
Create a draft. Same capabilities as send but with additional signature/quoting options.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| subject | string | yes | | |
| body | string | yes | | |
| body_format | string | no | "plain" | "plain" or "html" |
| user_google_email | string | yes | | |
| to | string | no | | Can be empty for drafts |
| cc | string | no | | |
| bcc | string | no | | |
| from_name | string | no | | Display name |
| from_email | string | no | Gmail default | Send As alias; when omitted, resolves `isDefault`, then `isPrimary`, then the first Send-As entry, and finally the authenticated email if no usable entry is available |
| thread_id | string | no | | For reply drafts; defaults to its latest non-draft, non-trash message with an RFC `Message-ID` |
| in_reply_to | string | no | | RFC Message-ID of a specific reply target; omit for the latest eligible message |
| references | string | no | | Optional Message-ID ancestry chain; normally derived from thread_id |
| attachments | array | no | | Same format as send |
| include_signature | boolean | no | true | Append Gmail signature if available |
| quote_original | boolean | no | false | Include original message as quoted reply (requires thread_id) |

Operational Gmail settings errors such as rate limits abort draft creation instead of silently falling back to a potentially unintended sender.

### delete_gmail_draft
Immediately and permanently delete one draft without sending it. This does not move the draft to Trash.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| draft_identifier | string | yes | | Either the Draft ID returned by `draft_gmail_message` or the contained Message ID returned by an `in:drafts` search |

The tool scans all draft pages, resolves either identifier form, and deletes only when exactly one draft matches. If multiple drafts share a thread, use each draft's unique Draft ID or contained Message ID rather than the shared Thread ID.

---

## Label Management

### list_gmail_labels
List all labels with IDs, names, and types.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |

### manage_gmail_label
Create, update, or delete a label.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "create", "update", or "delete" |
| name | string | conditional | | Required for create, optional for update |
| label_id | string | conditional | | Required for update and delete |
| label_list_visibility | string | no | "labelShow" | "labelShow" or "labelHide" |
| message_list_visibility | string | no | "show" | "show" or "hide" |
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
Add or remove labels on multiple messages at once.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_ids | array of strings | yes | | |
| add_label_ids | array of strings | no | | Label IDs to add |
| remove_label_ids | array of strings | no | | Label IDs to remove |

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
Create or delete a filter.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | "create" or "delete" |
| criteria | object | for create | | Filter criteria (see below) |
| filter_action | object | for create | | Actions to apply (see below) |
| filter_id | string | for delete | | ID of filter to remove |

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
- Batch tools (`get_gmail_messages_content_batch`, `get_gmail_threads_content_batch`, `batch_modify_gmail_message_labels`) max out at 25 items per call to avoid SSL exhaustion.
- For larger sets, make multiple batch calls.

### Label IDs
- System labels use uppercase IDs: `INBOX`, `SENT`, `TRASH`, `SPAM`, `DRAFT`, `UNREAD`, `STARRED`, `IMPORTANT`.
- Custom labels have generated IDs (e.g., `Label_123`). Use `list_gmail_labels` to discover them.
- Use label IDs (not names) in `modify_gmail_message_labels`, `batch_modify_gmail_message_labels`, and filter actions.

### Drafts vs Send
- Use `draft_gmail_message` when you want the user to review before sending. It supports `include_signature` (auto-appends Gmail signature) and `quote_original` (includes quoted reply text).
- Use `delete_gmail_draft` to permanently remove a draft. For a safe revision workflow, create the replacement successfully before deleting the previous draft.
- Use `send_gmail_message` for immediate delivery.

### Attachments
- To find attachments, read the message with `get_gmail_message_content` -- attachment IDs are listed in the response.
- Download with `get_gmail_attachment_content` using both the message_id and attachment_id.
- When sending/drafting, attachments can be specified as file paths (auto-encoded) or pre-encoded base64 content (standard base64, not urlsafe).
