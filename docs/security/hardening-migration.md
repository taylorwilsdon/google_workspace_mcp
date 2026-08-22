# Security hardening: breaking changes and migration

This release closes the 51 findings recorded in
[`docs/security/vulnerability-remediation-plan.md`](vulnerability-remediation-plan.md).
Most fixes are invisible, but several remove behaviour that was only reachable because
a control failed open. Those are listed here with what to change.

The theme: **anything that used to be permitted "by default because nothing said
otherwise" now has to be stated.** Where a safe default did not exist, the server
refuses to start rather than run with the control disabled.

---

## 1. OAuth 2.1 requires a DCR redirect URI allowlist

**What changed.** `WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS` is now mandatory when
`MCP_ENABLE_OAUTH21=true`. Leaving it unset used to pass `None` to FastMCP, which
means "accept any client-supplied redirect URI" during Dynamic Client Registration:
anyone able to reach the registration endpoint could register their own redirect URI
and receive authorization codes (findings 23, 28, 51).

**Symptom if unmigrated.** Startup fails with:

```
OAuth 2.1 requires WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS to list the redirect
URIs that dynamically registered MCP clients may use.
```

**What to do.** List the exact callback URLs your clients use, comma-separated:

```bash
WORKSPACE_MCP_ALLOWED_CLIENT_REDIRECT_URIS="https://claude.ai/api/mcp/auth_callback,https://claude.com/api/mcp/auth_callback,http://127.0.0.1:*/callback"
```

Rules now enforced on each entry:

| Entry shape | Allowed | Why |
| :--- | :---: | :--- |
| `https://host/path` | yes | Exact match, per RFC 6749 §3.1.2 |
| `http://127.0.0.1:*/callback`, `http://localhost:*/callback` | yes | RFC 8252 §7.3 — a native client picks an ephemeral loopback port, and the code stays on the user's own machine |
| `http://host/path` (non-loopback) | no | The code would cross the network in cleartext |
| `https://*.example.com/cb` | no | A host wildcard lets anyone controlling one subdomain collect codes |
| `https://host/auth/*` | no | `fnmatch` path globs match across `/`, so `/auth/*` also covers `/auth/../steal` |
| `https://host:*/cb` (non-loopback) | no | A remote port wildcard widens the allowlist with no RFC 8252 benefit |
| `https://localhost@evil.example/cb` | no | Userinfo is the classic allowlist bypass — the real host is `evil.example` |

## 2. Origin validation no longer trusts "Origin equals Host"

**What changed.** `OriginValidationMiddleware` used to accept any request whose
`Origin` matched its own `Host` header. That is precisely the shape of a DNS rebinding
attack: the attacker's page is served from a name that resolves to loopback, so its
`Origin` and `Host` are identical and unrelated to your deployment (finding 9). Only
configured origins are accepted now.

**Symptom if unmigrated.** Browser-originated requests get `403 {"error": "Origin not
allowed"}` when served on a hostname the server does not know about.

**What to do.** Name the hostnames you serve:

```bash
WORKSPACE_EXTERNAL_URL="https://mcp.example.com"
# and/or, for additional origins:
OAUTH_ALLOWED_ORIGINS="https://mcp.example.com,https://alt.example.com"
```

Loopback origins and the `vscode-webview:` scheme are still accepted without
configuration: a page actually served from loopback is the local IDE, and a rebound
name would present its own hostname instead.

## 3. Legacy OAuth 2.0 is single-user by default

**What changed.** In legacy mode (`MCP_ENABLE_OAUTH21` off, no trusted gateway) there
is no verified per-request identity, so a `user_google_email` tool argument is an
unauthenticated claim. Honouring it let any client read any account whose grant this
server holds (findings 21, 22, 35–37). The principal now comes from
`USER_GOOGLE_EMAIL`, and a mismatched argument is rejected.

**Symptom if unmigrated.** Tool calls fail with either:

```
Cannot determine the account to act as. Legacy OAuth 2.0 mode requires
USER_GOOGLE_EMAIL to be configured, ...
```

or, when an argument disagrees with the principal:

```
Requested account b@example.com does not match the authenticated account
a@example.com. You may only act as your own account.
```

**What to do.** Pick one:

- **Single user (recommended for legacy mode).** Set `USER_GOOGLE_EMAIL`.
- **Multi-user.** Move to `MCP_ENABLE_OAUTH21=true`, or to
  `TRUST_GATEWAY_IDENTITY=true` behind an identity-aware proxy. Both give a verified
  per-request principal.
- **Restore the old behaviour (discouraged).** `ALLOW_CALLER_SUPPLIED_USER_EMAIL=true`
  accepts the caller's claim again. This provides no cross-user isolation: every
  client can act as every account the server holds a grant for. It never relaxes the
  check against a *verified* principal — when OAuth 2.1 or a gateway has established
  one, a mismatched argument is still rejected.

## 4. Per-request domain-wide delegation requires a domain allowlist

**What changed.** Domain-wide delegation can impersonate any user in the Workspace
domain, and the impersonation subject used to be taken from the caller's
`user_google_email` with a domain allowlist that was empty by default — unrestricted
impersonation (findings 2, 4, 5, 16–18, 33). The subject now comes only from an
identity the server established.

Two cases, and only the second needs new configuration:

| Deployment | Subject | `DWD_ALLOWED_DOMAINS` |
| :--- | :--- | :--- |
| Service account, single user | `USER_GOOGLE_EMAIL`, fixed | Not required (honoured if set) |
| Service account + `TRUST_GATEWAY_IDENTITY` | the gateway-verified principal, varies per request | **Required** |

The second case is validated at startup, so a misconfiguration is not discovered on
the first tool call:

```
Per-request domain-wide delegation requires DWD_ALLOWED_DOMAINS.
```

**What to do.** For the per-request case, list the Workspace domains this deployment
may impersonate:

```bash
DWD_ALLOWED_DOMAINS="corp.example.com,partner.example.com"
```

The allowlist is a bound, not the whole control: it cannot stop same-domain
impersonation on its own, so the subject is *also* required to equal the verified
principal. A caller-supplied `user_google_email` may only agree with that principal,
never widen it.

## 5. `modify_sheet_values` writes literal text by default

**What changed.** The default was `USER_ENTERED`, so any string beginning with `=`
became a live formula in the recipient's spreadsheet — a stored injection whose payload
runs for whoever opens the sheet, not for the caller (findings 14, 45). The
caller-supplied `value_input_option` parameter is gone; the default is now `RAW`.

`append_table_rows` had the same problem through `_to_extended_value`, which turned any
`=`-prefixed string into a `formulaValue` (finding 10). It is now literal by default
too.

**Symptom if unmigrated.** A call passing `value_input_option` fails with
`TypeError: unexpected keyword argument`. Values intended as formulas appear as text.

**What to do.** Pass `allow_formulas=true` on the specific calls that really mean to
write a formula:

```jsonc
// before
{"range_name": "A1", "values": [["=SUM(B:B)"]], "value_input_option": "USER_ENTERED"}
// after
{"range_name": "A1", "values": [["=SUM(B:B)"]], "allow_formulas": true}
```

Only enable it for values you trust. A formula written into a shared sheet runs for
everyone who opens it, and `HYPERLINK`/`IMPORTDATA` can exfiltrate other cells.

## 6. Attachment URLs are authenticated and owner-scoped

**What changed.** `/attachments/{file_id}` served any stored attachment to anyone who
presented the UUID, with no authentication (findings 24, 30, 39). A URL that leaked —
into logs, a chat transcript, a forwarded message — was a durable read primitive over
other users' Gmail and Drive content. The route now requires a verified principal and
serves only that principal's own attachments. Denials and misses both return `404`, so
the route cannot be used to probe which ids exist.

Attachment URLs are also always absolute now. A relative `/attachments/{id}` used to
skip the trusted-origin check that only ran when a URL had an authority component
(finding 38).

**Symptom if unmigrated.**

- `401` from the attachment route when no credential is presented. Clients must send
  the same `Authorization: Bearer` token they use for tool calls (or, in trusted-gateway
  mode, traverse the gateway).
- Tools that return an attachment URL fail if no base URL is configured, rather than
  emitting a relative URL.

**What to do.** Make sure a base URL is configured — `WORKSPACE_EXTERNAL_URL`, or
`WORKSPACE_MCP_BASE_URI` plus the port. In legacy/stdio single-user mode the principal
is `USER_GOOGLE_EMAIL`, so that must be set for the route to authorise anyone.

## 7. `manage_event` ignores other attendees' RSVP fields

**What changed.** `events.update` replaces the whole attendee list, and caller-supplied
attendee dicts used to pass through untouched. Anyone able to edit an event — the
organizer, or any guest when `guestsCanModify` is set — could write a `responseStatus`
for other people, accepting or declining on their behalf (finding 13).
`responseStatus` and `comment` are now kept only for the calling user; both are dropped
for everyone else, with a warning in the log.

**What to do.** Use `rsvp_event` to set your own RSVP. There is no supported way to set
someone else's, which matches what the Calendar API permits a client to do on its own
behalf.

## 8. Fixed resource limits

Every size ceiling lives in [`core/limits.py`](../../core/limits.py) as a constant. They
are deliberately not environment variables: a deployment that can raise a limit can also
be talked into raising it.

| Input | Limit |
| :--- | :--- |
| HTTP request body | 50 MiB |
| Stored attachment | 50 MiB |
| Gmail attachment (decoded, and per-message total) | 25 MiB |
| Google Chat attachment download | 50 MiB |
| Drive upload supplied inline as base64 | 32 MiB |
| Drive upload streamed from a URL or local path | 2 GiB |
| Google Doc content extracted into memory | 50 MiB |
| Apps Script project | 100 files / 5 MiB per file / 10 MiB total |

Requests over a limit are refused with an explicit error (`413` for HTTP bodies).
Oversized transfers are abandoned mid-stream rather than buffered and then rejected.

## 9. Restricted modes now disable tools with unknown scope requirements

**What changed.** Read-only mode and granular permissions mode both skipped any tool
that declared no `_required_google_scopes` — the opposite of what a restrictive mode is
for (finding 41). Unknown requirements are now a reason to disable.

**What to do.** Nothing, unless you maintain a fork with custom tools. A tool that
calls a Google API should carry `@require_google_service`; one that genuinely calls no
Google API should be marked `@requires_no_google_scopes` (as `start_google_auth` and
`generate_trigger_code` are). Otherwise it disappears in restricted modes, and the log
says so.

---

## Non-breaking hardening

No action required for these; listed so the change is not a surprise while reading a
diff or a log.

- **SSRF classification** no longer depends on `ipaddress.is_global`, whose results have
  changed across Python patch releases. Blocked ranges are enumerated explicitly, so
  `fc00::/7` (IPv6 unique local) and `100.64.0.0/10` (CGNAT) are refused on every
  supported interpreter, and IPv4-in-IPv6 forms such as `::ffff:10.0.0.1` are unwrapped
  before the decision (findings 6, 7). The table also refuses ranges that `is_global`
  reports as reachable on every interpreter we support, so these were open before the
  change too: `::/96` (the deprecated IPv4-compatible form, which writes an internal
  IPv4 target as `::10.0.0.1` without being recognised by `ipv4_mapped`), `fec0::/10`
  (RFC 3879 site-local, deprecated but still routed where deployed), `2001::/23`
  (Teredo, benchmarking, ORCHID, AS112), `3fff::/20` and `5f00::/16`. A test asserts the
  table stays a superset of what the interpreter treats as private, so a future edit
  cannot silently fall below that line.
- **`ssrf_safe_stream`** closes its HTTP client on task cancellation. `CancelledError`
  derives from `BaseException`, so the previous `except Exception` missed it and leaked
  the client and its connection pool (finding 49).
- **Credential files** are replaced atomically (temporary file plus `os.replace`).
  Concurrent token refreshes could previously interleave and leave a truncated file,
  losing the refresh token (finding 40).
- **OAuth state** is single-use and mandatory; the single-user "recover the most recent
  state" fallback is gone (findings 47, 48).
- **Cross-account checks** compare addresses case-insensitively, matching how the
  principal check already behaved. Six comparisons on the auth path did this
  byte-for-byte, so a case-only difference — the same Google account — was denied and
  logged as `SECURITY VIOLATION`. They now share one predicate, so they cannot disagree
  about whether two spellings are one account. The on-disk credential store is still
  keyed by the exact string; only the in-memory session map resolves leniently, because
  re-keying files would orphan credentials that already exist.
- **Sessions with no recorded scopes** are rejected instead of being treated as
  satisfying whatever the tool needed (finding 29).
- **Markdown written into Docs** drops `javascript:`, `data:` and similar link schemes,
  and computes indexes in UTF-16 code units so emoji no longer shift every subsequent
  style range (findings 26, 27).
- **Drive queries** validate `folder_id` and escape free-text values, so a quote can no
  longer close the query literal (finding 31).
- **`validate_file_path`** no longer reports whether a forbidden path exists, which was
  a filesystem existence oracle over the whole machine (finding 34).
- **Remote `Content-Type`** headers are stripped of parameters and validated before they
  can override a caller's MIME type (finding 42).
- **The publish workflow** validates `server.json` against a vendored schema instead of
  fetching the URL named inside it, and installs `mcp-publisher` from a pinned version
  verified by SHA-256 instead of piping `releases/latest` into `tar`. All actions are
  pinned to commit SHAs (findings 15, 46).
