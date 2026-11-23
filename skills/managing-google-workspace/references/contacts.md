# Google Contacts Tools Reference

MCP tools for managing Google Contacts and contact groups. All tools require `user_google_email` (string, required).

## Contents
- Contacts: search_contacts, list_contacts, get_contact, manage_contact, manage_contacts_batch
- Contact Groups: list_contact_groups, get_contact_group, manage_contact_group
- Tips

---

## Contacts

### search_contacts
Search contacts by name, email, phone number, or other fields.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| query | string | yes | | Searches names, emails, phone numbers |
| user_google_email | string | yes | | |
| page_size | integer | no | 30 | Max 30 |

### list_contacts
List contacts for the authenticated user.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| page_size | integer | no | 100 | Max 1000 |
| page_token | any | no | | Pagination token |
| sort_order | string | no | | `LAST_MODIFIED_ASCENDING`, `LAST_MODIFIED_DESCENDING`, `FIRST_NAME_ASCENDING`, or `LAST_NAME_ASCENDING` |
| request_sync_token | boolean | no | false | Start a full sync; the last page ends with `Next sync token` |
| sync_token | string | no | | List changes since that sync token; deleted contacts show `Deleted: yes` |

Keep every argument unchanged when following `page_token`. `sort_order` is
rejected when syncing. Sync tokens expire after seven days; an expired token
returns a message asking for a new full sync.

### get_contact
Get detailed information about a specific contact.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| contact_id | string | yes | | e.g. `c1234567890` or `people/c1234567890` |
| user_google_email | string | yes | | |

### manage_contact
Create, update, or delete a contact. `create` and `update` need at least one field.

On `update`, each list field has a `*_mode` companion that controls how the list combines with what the contact already has: `merge` (default) adds new entries and skips duplicates, `replace` overwrites the whole list, and `remove` deletes the entries that match the ones you pass. Modes are ignored on `create`.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, or `delete` |
| user_google_email | string | yes | | |
| contact_id | string | conditional | | Required for `update` and `delete`. `c123` or `people/c123` |
| given_name | string | no | | First name |
| family_name | string | no | | Last name |
| phones | array | no | | `[{number, type?}]`. Types include `mobile`, `work`, `home`, `main`, `workMobile`, `other`; use `internal` for PBX short numbers |
| emails | array | no | | `[{address, type?}]`, type e.g. `work`, `home`, `other` |
| organizations | array | no | | `[{name?, title?, department?, jobDescription?, type?}]` |
| nicknames | array | no | | `[{value, type?}]` or plain strings. Types: `default`, `alternate_name`, `maiden_name`, `initials`, `other` |
| urls | array | no | | `[{value, type?}]` or plain strings. Types: `homepage`, `blog`, `profile`, `work`, `ftp`, `reservations`, `other` |
| user_defined | array | no | | Custom fields `[{key, value}]`. `value` may be omitted in `remove` mode. `merge` overwrites the value of a matching key |
| relations | array | no | | `[{person, type?}]` or plain names. Types include `spouse`, `child`, `parent`, `friend`, `manager`, `assistant` |
| notes | string | no | | Free-text notes. `""` clears them |
| address | string | no | | Street address; replaces existing addresses. `""` clears them |
| birthday | string | no | | `YYYY-MM-DD`, `MM-DD` (no year), or `clear`/`""` to remove |
| phones_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| emails_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| organizations_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| nicknames_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| urls_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| user_defined_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| relations_mode | string | no | merge | `merge`, `replace`, or `remove` (`update` only) |
| email | string | no | | Deprecated, use `emails`. Ignored when `emails` is set |
| phone | string | no | | Deprecated, use `phones`. Ignored when `phones` is set |
| organization | string | no | | Deprecated, use `organizations`. Ignored when `organizations` is set |
| job_title | string | no | | Deprecated, use `organizations`. Ignored when `organizations` is set |

### manage_contacts_batch
Batch create, update, or delete up to 200 contacts. A batch `update` writes one field per call and replaces that field outright (no `*_mode` merging); only `names` keeps the name parts you leave out.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, or `delete` |
| user_google_email | string | yes | | |
| contacts | array | conditional | | For `create`: list of dicts with the same fields as `manage_contact` (`given_name`, `phones`, `emails`, `birthday`, etc.) |
| updates | array | conditional | | For `update`: list of dicts, each with `contact_id` plus the data for `field`. Entries missing that data are skipped |
| contact_ids | array | conditional | | For `delete`: list of contact ID strings |
| field | string | for update | | The single field to write: `names`, `phoneNumbers`, `emailAddresses`, `organizations`, `nicknames`, `urls`, `userDefined`, `relations`, `biographies`, `addresses`, or `birthdays` |

---

## Contact Groups

### list_contact_groups
List contact groups (labels).

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| page_size | integer | no | 100 | Max 1000 |
| page_token | any | no | | Pagination token |

### get_contact_group
Get details of a specific contact group including its members.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| group_id | string | yes | | |
| user_google_email | string | yes | | |
| max_members | integer | no | 100 | Max 1000 |

### manage_contact_group
Create, update, delete a contact group, or modify its members.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, `delete`, or `modify_members` |
| user_google_email | string | yes | | |
| group_id | string | conditional | | Required for `update`, `delete`, `modify_members` |
| name | string | conditional | | Group name; required for `create` and `update` |
| delete_contacts | boolean | no | false | If true and action is `delete`, also deletes contacts in the group |
| add_contact_ids | array | no | | Contact IDs to add (`modify_members`) |
| remove_contact_ids | array | no | | Contact IDs to remove (`modify_members`) |

---

## Tips

**Avoid duplicates**: Use `search_contacts` to check whether a contact already exists before creating a new one. Search matches on name, email, and phone number.

**Batch organisation**: Use contact groups to organise contacts into categories. Create a group with `manage_contact_group`, then add contacts to it with the `modify_members` action.

**Extra context**: The `notes` field on a contact is a free-text area useful for storing context that does not fit into structured fields (e.g. how you met, preferred contact method).
