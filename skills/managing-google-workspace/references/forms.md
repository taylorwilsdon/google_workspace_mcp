# Google Forms Tools Reference

MCP tools for creating, reading, and updating Google Forms and their responses. All tools require `user_google_email` (string, required).

---

## Read

### get_form
Get a form's details including title, description, questions, and URLs.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| form_id | string | yes | | |
| user_google_email | string | yes | | |

### list_form_responses
List responses submitted to a form.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| form_id | string | yes | | |
| user_google_email | string | yes | | |
| page_size | integer | no | 10 | Max responses per page |
| page_token | any | no | | Pagination token |

### get_form_response
Get a single response from a form.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| form_id | string | yes | | |
| response_id | string | yes | | |
| user_google_email | string | yes | | |

---

## Create & Modify

### create_form
Create a new Google Form.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| title | string | yes | | The form title |
| user_google_email | string | yes | | |
| description | string | no | | Form description |
| document_title | string | no | | Title shown in browser tab |

### batch_update_form
Apply batch updates to a form. Primary method for modifying form content after creation.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| form_id | string | yes | | |
| requests | array | yes | | List of update request objects (see below) |
| user_google_email | string | yes | | |

Supported request types in the `requests` array:
- **createItem** -- add a new question or content item
- **updateItem** -- modify an existing item
- **deleteItem** -- remove an item
- **moveItem** -- reorder an item
- **updateFormInfo** -- update form title/description
- **updateSettings** -- modify form settings (e.g. quiz mode)

### set_publish_settings
Update the publish settings of a form.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| form_id | string | yes | | |
| user_google_email | string | yes | | |
| publish_as_template | boolean | no | false | |
| require_authentication | boolean | no | false | |

---

## Tips

**Building forms**: Use `create_form` to create the form first, then call `batch_update_form` with `createItem` requests to add questions and content items. Forms cannot be created with questions in a single step.

**Updating existing questions**: Call `get_form` before modifying a form -- it returns the item IDs you need to target specific questions in `updateItem` and `deleteItem` requests within `batch_update_form`.

**Checking responses**: Use `list_form_responses` to retrieve submissions. Each response includes answers keyed by question ID, so cross-reference with `get_form` to map answers back to questions.
