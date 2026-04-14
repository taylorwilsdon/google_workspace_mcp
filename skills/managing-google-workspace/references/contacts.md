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

### get_contact
Get detailed information about a specific contact.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| contact_id | string | yes | | e.g. `c1234567890` or `people/c1234567890` |
| user_google_email | string | yes | | |

### manage_contact
Create, update, or delete a contact.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, or `delete` |
| user_google_email | string | yes | | |
| contact_id | string | conditional | | Required for `update` and `delete` |
| given_name | string | no | | First name (`create`, `update`) |
| family_name | string | no | | Last name (`create`, `update`) |
| email | string | no | | Email address (`create`, `update`) |
| phone | string | no | | Phone number (`create`, `update`) |
| organization | string | no | | Company/organization (`create`, `update`) |
| job_title | string | no | | Job title (`create`, `update`) |
| notes | string | no | | Additional notes (`create`, `update`) |

### manage_contacts_batch
Batch create, update, or delete contacts.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| action | string | yes | | `create`, `update`, or `delete` |
| user_google_email | string | yes | | |
| contacts | array | conditional | | For `create`: list of dicts with `given_name`, `family_name`, `email`, `phone`, `organization`, `job_title` |
| updates | array | conditional | | For `update`: list of dicts, each must include `contact_id` plus fields to update |
| contact_ids | array | conditional | | For `delete`: list of contact ID strings |

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
