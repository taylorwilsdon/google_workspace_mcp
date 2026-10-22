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
Sends a message to a Google Chat space. Can reply to existing threads, edit a message already sent, create a new named space to post into, and pin the sent or edited message.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| message_text | string | yes | | |
| space_id | string | no | | Required unless `create_space_name` is set |
| thread_key | any | no | | App-defined key; creates thread if not found |
| thread_name | any | no | | Resource name, e.g. `spaces/X/threads/Y` |
| message_name | any | no | | Edit this message in place, e.g. `spaces/X/messages/Y`; must sit in `space_id`, own messages only, cannot be combined with a thread parameter |
| create_space_name | string | no | | Create a named space with this display name and send into it; cannot be combined with `space_id` or `message_name`. Display names must be unique in the Workspace org. Needs the `chat.spaces` scope (in the default Chat scopes); if the first message then fails, the error names the new space so the retry can use `space_id` |
| member_emails | list[string] | no | | With `create_space_name`: people to add to the new space (the caller is added automatically) |
| pin | boolean | no | false | Pin the sent or edited message; needs the `chat.spaces` scope; a pin failure is reported in the result without undoing the send |

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

**Space IDs**: Call `list_spaces` first to discover available spaces and their IDs. You need the space ID for all message operations on existing spaces; the exception is `send_message` with `create_space_name`, which creates the space and returns its ID.

**Unnamed spaces**: Direct messages and group chats have no name of their own, so `list_spaces`, `get_messages` and `search_messages` label them after up to three other members (e.g. `Alice Smith, Bob Jones and 2 others`). Naming needs the `chat.memberships.readonly` and `contacts.readonly` scopes (requested with the Chat scopes) and the People API enabled in the Google Cloud project. Tokens granted before `chat.memberships.readonly` was added keep working without re-consent. If the member lookup fails, for example because that scope is missing, the label falls back to `Direct message` or `Group chat`; if only a member's name cannot be resolved, their user ID (e.g. `users/123`) is shown in its place.

**Message resource names**: Messages are identified by their full resource name in the format `spaces/SPACE_ID/messages/MESSAGE_ID`. This is the value expected by `create_reaction` and `download_chat_attachment`.

**Reactions**: The `emoji_unicode` parameter takes a literal Unicode emoji character (e.g. a thumbs-up character), not an emoji shortcode.
