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
Retrieves one page of messages from a Google Chat space; use the `Next page token` from the response as `page_token` to retrieve more.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| space_id | string | yes | | |
| page_size | integer | no | 50 | |
| order_by | string | no | "createTime desc" | |
| message_filter | string | no | | Chat API filter on `createTime` and `thread.name`, e.g. `createTime > "2026-03-18T00:00:00Z"` or `thread.name = spaces/X/threads/Y` |
| page_token | string | no | | Token from a previous response's `Next page token`. Keep all other parameters unchanged between pages |

### search_messages
Searches for messages in Google Chat spaces by text content and/or time range.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| query | string | no | | Text to search for (case-insensitive substring, matched client-side); omit to apply only time_filter |
| time_filter | string | no | | Chat API `createTime` filter, e.g. `createTime > "2026-03-18T00:00:00Z"`; combine bounds with `AND` |
| space_id | string | no | | Filter to a specific space |
| page_size | integer | no | 25 | Max messages fetched per space |
| max_spaces | integer | no | 10 | Spaces searched when space_id is omitted; pages through the space list until this many are found |

### send_message
Sends a message to a Google Chat space. Can reply to existing threads, or edit a message already sent.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| space_id | string | yes | | |
| message_text | string | yes | | |
| thread_key | string | no | | App-defined key; creates thread if not found |
| thread_name | string | no | | Resource name, e.g. `spaces/X/threads/Y` |
| message_name | string | no | | Edit this message in place, e.g. `spaces/X/messages/Y`; must sit in `space_id`, own messages only, cannot be combined with a thread parameter |

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

**Unnamed spaces**: Direct messages and group chats have no name of their own, so `list_spaces`, `get_messages` and `search_messages` label them after up to three other members (e.g. `Alice Smith, Bob Jones and 2 others`). Naming needs the `chat.memberships.readonly` and `contacts.readonly` scopes (requested with the Chat scopes) and the People API enabled in the Google Cloud project. Tokens granted before `chat.memberships.readonly` was added keep working without re-consent. If the member lookup fails, for example because that scope is missing, the label falls back to `Direct message` or `Group chat`; if only a member's name cannot be resolved, their user ID (e.g. `users/123`) is shown in its place.

**Message resource names**: Messages are identified by their full resource name in the format `spaces/SPACE_ID/messages/MESSAGE_ID`. This is the value expected by `create_reaction` and `download_chat_attachment`.

**Reactions**: The `emoji_unicode` parameter takes a literal Unicode emoji character (e.g. a thumbs-up character), not an emoji shortcode.
