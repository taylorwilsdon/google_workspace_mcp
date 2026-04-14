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
| thread_id | string | no | | Thread ID for replies |
| in_reply_to | string | no | | RFC Message-ID being replied to, e.g. `<msg@gmail.com>` |
| references | string | no | | Space-separated chain of Message-IDs for threading |
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
| from_email | string | no | | Send As alias |
| thread_id | string | no | | For reply drafts |
| in_reply_to | string | no | | RFC Message-ID |
| references | string | no | | Message-ID chain |
| attachments | array | no | | Same format as send |
| include_signature | boolean | no | true | Append Gmail signature if available |
| quote_original | boolean | no | false | Include original message as quoted reply (requires thread_id) |

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
- To reply: pass `thread_id`, `in_reply_to` (the Message-ID header of the message you are replying to), and `references` (chain of all Message-IDs in the thread, space-separated). Prefix the subject with `Re:` followed by a space.
- The `in_reply_to` and `references` values come from the `Message-ID` header returned by `get_gmail_message_content`.
- The `in_reply_to` field in this MCP server is known to be unreliable. Always provide `thread_id` and `references` for threading -- those are the fields Gmail actually uses.

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
- Use `send_gmail_message` for immediate delivery.

### Attachments
- To find attachments, read the message with `get_gmail_message_content` -- attachment IDs are listed in the response.
- Download with `get_gmail_attachment_content` using both the message_id and attachment_id.
- When sending/drafting, attachments can be specified as file paths (auto-encoded) or pre-encoded base64 content (standard base64, not urlsafe).
