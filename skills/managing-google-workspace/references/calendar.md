# Google Calendar Tools Reference

MCP tools for Google Calendar event management and availability queries. All tools require `user_google_email` (string, required).

---

## Calendars & Events

### list_calendars
List all calendars accessible to the authenticated user. Returns summary, ID, and primary status.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |

### get_events
Retrieve events from a calendar. Fetch a single event by ID, list events in a time range, or search by keyword.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| calendar_id | string | no | primary | Calendar ID from `list_calendars` |
| event_id | string | no | | Retrieve a single event; ignores time filters when set |
| time_min | string | no | now | Start of range, RFC 3339 (e.g. `2026-03-19T09:00:00Z` or `2026-03-19`) |
| time_max | string | no | | End of range, RFC 3339 (exclusive) |
| max_results | integer | no | 25 | Max events to return |
| query | string | no | | Keyword search across summary, description, location |
| detailed | boolean | no | false | Include description, location, attendees with response status |
| include_attachments | boolean | no | false | Show attachment details (fileId, fileUrl, mimeType, title). Only applies when `detailed=true` |

### manage_event
Create, update, or delete a calendar event.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `update`, or `delete` |
| summary | string | for create | | Event title |
| start_time | string | for create | | RFC 3339 format |
| end_time | string | for create | | RFC 3339 format |
| event_id | string | for update/delete | | Event ID |
| calendar_id | string | no | primary | |
| description | string | no | | Event description |
| location | string | no | | Event location |
| attendees | array | no | | Email strings or attendee objects |
| timezone | string | no | | e.g. `Australia/Melbourne` |
| attachments | array of strings | no | | Google Drive file URLs or IDs |
| add_google_meet | boolean | no | | Add or remove Google Meet link |
| reminders | array | no | | Custom reminder objects (see below) |
| use_default_reminders | boolean | no | | Use account default reminders |
| transparency | string | no | | `opaque` (busy) or `transparent` (free) |
| visibility | string | no | | `default`, `public`, `private`, or `confidential` |
| color_id | string | no | | Event color 1-11 (update only) |
| guests_can_modify | boolean | no | | Attendees can edit the event |
| guests_can_invite_others | boolean | no | | Attendees can invite others |
| guests_can_see_other_guests | boolean | no | | Attendees can see other attendees |

**Reminder format** (each item is a dict):
- `{"method": "email", "minutes": 30}` -- email reminder 30 minutes before
- `{"method": "popup", "minutes": 10}` -- popup reminder 10 minutes before

---

## Availability

### query_freebusy
Check free/busy status for one or more calendars over a time interval.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| time_min | string | yes | | Start of interval, RFC 3339 |
| time_max | string | yes | | End of interval, RFC 3339 |
| calendar_ids | any | no | | Calendar IDs to query. Defaults to primary calendar if omitted |
| group_expansion_max | integer | no | | Max members per group (max 100) |
| calendar_expansion_max | integer | no | | Max calendars to query (max 50) |

---

## Tips

**Calendar IDs**: Use `list_calendars` to discover IDs. The primary calendar can always be referenced as `primary`.

**Time format**: All time parameters use RFC 3339. Date-only values (e.g. `2026-03-19`) are accepted and interpreted as midnight UTC.

**All-day events**: Set `start_time` and `end_time` to date-only strings (e.g. `2026-03-20` and `2026-03-21` for a single all-day event on 20 March).

**Attendees**: Can be simple email strings (`["alice@example.com"]`) or attendee objects with additional fields (`[{"email": "alice@example.com", "optional": true}]`).
