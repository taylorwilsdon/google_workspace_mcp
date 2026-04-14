# Google Chat Tools Reference

MCP tools for Google Chat spaces, messages, reactions, and attachments. All tools require `user_google_email` (string, required).

---

## Spaces

### list_spaces
Lists Google Chat spaces (rooms and direct messages) accessible to the user.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| page_size | integer | no | 100 | |
| space_type | string | no | "all" | |

---

## Messages

### get_messages
Retrieves messages from a Google Chat space.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| space_id | string | yes | | |
| page_size | integer | no | 50 | |
| order_by | string | no | "createTime desc" | |

### search_messages
Searches for messages in Google Chat spaces by text content.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| query | string | yes | | Text to search for |
| space_id | any | no | | Filter to a specific space |
| page_size | integer | no | 25 | |

### send_message
Sends a message to a Google Chat space. Can reply to existing threads.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| space_id | string | yes | | |
| message_text | string | yes | | |
| thread_key | any | no | | App-defined key; creates thread if not found |
| thread_name | any | no | | Resource name, e.g. `spaces/X/threads/Y` |

---

## Reactions & Attachments

### create_reaction
Adds an emoji reaction to a message.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_id | string | yes | | Resource name, e.g. `spaces/X/messages/Y` |
| emoji_unicode | string | yes | | Literal Unicode emoji character (not a shortcode) |

### download_chat_attachment
Downloads an attachment from a Chat message. Returns a local file path (stdio mode) or a temporary URL valid for 1 hour (HTTP mode).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_id | string | yes | | Resource name, e.g. `spaces/X/messages/Y` |
| attachment_index | integer | no | 0 | Zero-based index of the attachment |

---

## Tips

**Space IDs**: Call `list_spaces` first to discover available spaces and their IDs. You need the space ID for all message operations.

**Message resource names**: Messages are identified by their full resource name in the format `spaces/SPACE_ID/messages/MESSAGE_ID`. This is the value expected by `create_reaction` and `download_chat_attachment`.

**Reactions**: The `emoji_unicode` parameter takes a literal Unicode emoji character (e.g. a thumbs-up character), not an emoji shortcode.
