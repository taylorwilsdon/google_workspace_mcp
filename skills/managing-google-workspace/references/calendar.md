# Google Calendar Tools Reference

MCP tools for Google Calendar event management, Out of Office and Focus Time blocks, calendar creation, and availability queries. All tools require `user_google_email` (string, required).

---

## Calendars & Events

### list_calendars
List a single page of calendars accessible to the authenticated user. Returns summary, ID, and primary status; use the `Next page token` from the response as `page_token` to retrieve additional pages.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| max_results | integer | no | | Max calendars per page. Omit for the API default |
| page_token | string | no | | Token from a previous response's `Next page token` |

### create_calendar
Create a new secondary calendar. Returns its ID for use as `calendar_id` elsewhere.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| summary | string | yes | | Calendar name |
| description | string | no | | Calendar description |
| timezone | string | no | | IANA timezone, e.g. `America/New_York` |

### get_events
Retrieve events from a calendar. Fetch a single event by ID, list events in a time range, or search by keyword.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| calendar_id | string | no | primary | Calendar ID from `list_calendars` |
| event_id | string | no | | Retrieve a single event; ignores time filters when set |
| time_min | string | no | now | Start of range, RFC 3339 (e.g. `2026-03-19T09:00:00Z` or `2026-03-19`) |
| time_max | string | no | | End of range, RFC 3339 (exclusive) |
| max_results | integer | no | 25 | Max events per page |
| query | string | no | | Keyword search across summary, description, location |
| detailed | boolean | no | false | Include description, location, attendees with response status |
| include_attachments | boolean | no | false | Show attachment details (fileId, fileUrl, mimeType, title). Only applies when `detailed=true` |
| single_events | boolean | no | true | Expand recurring events into instances. When false, an omitted `time_min` leaves the lower bound unset |
| page_token | string | no | | Token from a previous response's `Next page token`. Requires `time_min` when `single_events=true`. Ignored when `event_id` is set |

Both tools append `Next page token: <token>` to the response when more pages remain. Pass it
back as `page_token` to continue, keeping all other query parameters unchanged.

`get_events` also returns `Pagination time_min: <timestamp>` when a lower bound is used
and more pages remain, including on empty pages. Pass that timestamp as `time_min` on
the next call, even if you omitted it on the first call. This preserves the original
range instead of letting the default current time move between pages. With
`single_events=false` and no initial `time_min`, continue leaving `time_min` unset.

### manage_event
Create, update, delete, or RSVP to a calendar event.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `update`, `delete`, or `rsvp` |
| summary | string | for create | | Event title |
| start_time | string | for create | | RFC 3339. An explicit offset fixes the instant; without one, pass `start_timezone` or `timezone`. Date-only creates an all-day event |
| end_time | string | for create | | RFC 3339, same rules as `start_time`. All-day end dates are exclusive |
| event_id | string | for update/delete/rsvp | | Event ID |
| calendar_id | string | no | primary | |
| description | string | no | | Event description |
| location | string | no | | Event location |
| attendees | array | no | | Email strings or attendee objects |
| timezone | string | no | | IANA zone for both boundaries, e.g. `Australia/Melbourne`. Overridden per boundary by `start_timezone`/`end_timezone` |
| start_timezone | string | no | | IANA zone for the start only (e.g. a flight departing in another zone) |
| end_timezone | string | no | | IANA zone for the end only |
| attachments | array of strings | no | | Google Drive file URLs or IDs. On update, replaces existing attachments |
| add_google_meet | boolean | no | | Add or remove Google Meet link. Not combinable with a third-party conference |
| conference_data | object | no | | Raw Calendar `conferenceData` payload for a third-party add-on. Exclusive with the `conference_*` helpers and `add_google_meet` (create/update) |
| conference_provider | string | no | | Third-party provider, e.g. `zoom`, `webex`, `teams`. Requires `conference_uri` (create/update) |
| conference_uri | string | no | | Join URL. Required with `conference_provider` |
| conference_passcode | string | no | | Passcode for the third-party conference |
| conference_id | string | no | | Provider-side meeting ID |
| reminders | array | no | | Custom reminder objects (see below) |
| use_default_reminders | boolean | no | true on create | Use account default reminders |
| transparency | string | no | | `opaque` (busy) or `transparent` (free) |
| visibility | string | no | | `default`, `public`, `private`, or `confidential` |
| color_id | string | no | | Event color by name or ID: `lavender` (1), `sage` (2), `grape` (3), `flamingo` (4), `banana` (5), `tangerine` (6), `peacock` (7), `graphite` (8), `blueberry` (9), `basil` (10), `tomato` (11) |
| recurrence | array of strings | no | | RFC 5545 rules, e.g. `["RRULE:FREQ=WEEKLY;COUNT=10"]` |
| guests_can_modify | boolean | no | | Attendees can edit the event |
| guests_can_invite_others | boolean | no | | Attendees can invite others |
| guests_can_see_other_guests | boolean | no | | Attendees can see other attendees |
| response | string | for rsvp | | `accepted`, `declined`, `tentative`, or `needsAction` |
| rsvp_comment | string | no | | Message sent with the RSVP (rsvp only) |
| send_updates | string | no | all | `all`, `externalOnly`, or `none`. Applies to every action |
| only_if_no_attendees | boolean | no | false | Delete only if the event has no attendees besides the calendar owner |
| this_and_following | boolean | no | false | Update only. With a recurring instance `event_id`, splits the series there so the change applies to that instance and all later ones while earlier instances keep the original schedule. Updating the series ID moves every instance and can drop past ones |

**Reminder format** (each item is a dict):
- `{"method": "email", "minutes": 30}` -- email reminder 30 minutes before
- `{"method": "popup", "minutes": 10}` -- popup reminder 10 minutes before

---

## Status Events

Out of Office and Focus Time are special event types that auto-decline conflicting invitations. They live on primary calendars only and require `dateTime` boundaries: date-only values become midnight, and a `timezone` is required unless timestamps carry an explicit UTC offset. `list` expands recurring series and paginates like `get_events` (pass back both `page_token` and the returned `Pagination time_min`).

### manage_out_of_office
Create, list, update, or delete Out of Office events, which set the user's status to "Out of office".

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `list`, `update`, or `delete` |
| start_time | string | for create | | `YYYY-MM-DD` (full day) or RFC 3339 |
| end_time | string | for create | | Exclusive. One full day on 5 April: `2026-04-05` to `2026-04-06` |
| summary | string | no | Out of Office | Display text |
| auto_decline_mode | string | no | declineAllConflictingInvitations | `declineAllConflictingInvitations`, `declineOnlyNewConflictingInvitations`, or `declineNone` |
| decline_message | string | no | | Message sent with auto-declines |
| recurrence | array of strings | no | | RFC 5545 rules, e.g. `["RRULE:FREQ=WEEKLY;COUNT=10"]` |
| timezone | string | no | | IANA zone. Required for date-only or offset-free times |
| time_min | string | no | now | Start of range (list only) |
| time_max | string | no | | End of range (list only) |
| max_results | integer | no | 10 | Page size (list only) |
| event_id | string | for update/delete | | Event ID |
| calendar_id | string | no | primary | Must be a primary calendar |
| page_token | string | no | | Next-page token (list only) |

### manage_focus_time
Create, list, update, or delete Focus Time events, which protect work blocks and by default set Chat to Do Not Disturb.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| action | string | yes | | `create`, `list`, `update`, or `delete` |
| start_time | string | for create | | `YYYY-MM-DD` (full day) or RFC 3339 |
| end_time | string | for create | | Exclusive, same format as `start_time` |
| summary | string | no | Focus Time | Display text |
| description | string | no | | Event description |
| auto_decline_mode | string | no | declineAllConflictingInvitations | `declineAllConflictingInvitations`, `declineOnlyNewConflictingInvitations`, or `declineNone` |
| decline_message | string | no | | Message sent with auto-declines |
| chat_status | string | no | doNotDisturb | `doNotDisturb` or `available` |
| recurrence | array of strings | no | | RFC 5545 rules, e.g. `["RRULE:FREQ=WEEKLY;COUNT=10"]` |
| timezone | string | no | | IANA zone. Required for date-only or offset-free times |
| time_min | string | no | now | Start of range (list only) |
| time_max | string | no | | End of range (list only) |
| max_results | integer | no | 10 | Page size (list only) |
| event_id | string | for update/delete | | Event ID |
| calendar_id | string | no | primary | Must be a primary calendar |
| page_token | string | no | | Next-page token (list only) |

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

**Time format**: Time parameters use RFC 3339. What a date-only value (e.g. `2026-03-19`) means depends on the parameter: date-only `manage_event` boundaries create an all-day event, Out of Office and Focus Time boundaries need a `timezone` (see Status Events), and query bounds such as `get_events` and `query_freebusy` `time_min`/`time_max` are read as midnight UTC.

**All-day events**: Set `start_time` and `end_time` to date-only strings (e.g. `2026-03-20` and `2026-03-21` for a single all-day event on 20 March).

**Attendees**: Can be simple email strings (`["alice@example.com"]`) or attendee objects with additional fields (`[{"email": "alice@example.com", "optional": true}]`).
