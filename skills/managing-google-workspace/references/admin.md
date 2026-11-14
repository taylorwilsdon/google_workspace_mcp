# Admin Directory (opt-in)

Requires explicit `--tools admin` and Google-authorized domain-admin access.
Requests only `admin.directory.user.readonly`. An existing `admin.directory.user`
grant also satisfies these read operations; no full scope is requested. Selecting
the service grants no privileges. No writes or delegation setup are performed.

### get_admin_users

Read one user by primary email, alias or ID, or list/search one page. Returns
JSON with selected user fields, or `users` and optional `nextPageToken`.

| Parameter | Type | Description |
|-----------|------|-------------|
| `user_google_email` | string | Authenticated domain admin; omit when schema supplies identity. |
| `user_key` | string | Optional primary email, alias or ID; do not combine with list parameters. |
| `query` | string | Optional Directory users query, e.g. `isSuspended=true`. |
| `customer` | string | Customer ID; defaults to `my_customer` (all domains in admin's organization). |
| `max_results` | integer | List page size 1–500; default 100. |
| `page_token` | string | Previous `nextPageToken`; preserve other list/search parameters. |
