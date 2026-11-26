# Exact tool selection: `--only-tools`

This server already ships three ways to decide which tools (and which OAuth
scopes) a running instance exposes, plus a per-tool block list:

- **`--tool-tier core|extended|complete`** — fixed, *cumulative* tiers. Each tier
  is a curated superset of the previous one across every service.
- **`--permissions service:level ...`** — *cumulative* permission **levels** per
  service (e.g. `gmail:readonly` ⊂ `gmail:organize` ⊂ `gmail:drafts` ⊂
  `gmail:send` ⊂ `gmail:full`). A level maps to a set of scopes, and any tool
  whose required scopes aren't all covered is dropped.
- **`--tools gmail drive ...`** — whole **services**. All or nothing per service.
- **`--disabled-tools tool_name ...`** — subtractive per-tool block list that
  composes with all of the above; scopes are untouched.

These are the right tool most of the time. But they share one structural limit,
and `--only-tools` exists to fill exactly that gap.

## The gap this flag fills

None of the built-in selectors can **expose an arbitrary, disjoint subset of
tools with a minimal grant**. Tiers and permission levels are cumulative
ladders; `--tools` is whole services; `--disabled-tools` subtracts tools but
leaves the scope grant defined by the other selectors. There is no built-in way
to say "expose *exactly* these four tools, drawn from three different services,
and request *only* the scopes those four tools need." You always end up
over-granting scopes or over-exposing tools.

## What `--only-tools` does

```bash
--only-tools send_gmail_message manage_drive_access
```

`--only-tools` takes an explicit list of **tool names** and does two things:

1. **Allowlist the tools.** Only the named tools are registered; everything else
   is removed. The list can be an arbitrary, disjoint subset that crosses
   service boundaries.
2. **Derive the minimal scope union.** The server inspects exactly those tools'
   declared Google scopes (`_required_google_scopes`) and requests **only** that
   union plus the base identity scopes (`userinfo.email`, `userinfo.profile`,
   `openid`). It bypasses the service-granular scope maps entirely.

So `--only-tools` **narrows both layers at once**: the tool surface *and* the
OAuth grant. The token you mint can do nothing beyond what those specific tools
require. This is the tightest possible grant for a given set of tools.

Unknown tool names are rejected with an error at startup, so a typo fails loud
instead of silently shipping a smaller surface than intended.

`--only-tools` is **mutually exclusive** with `--tools`, `--tool-tier`,
`--permissions`, `--read-only`, and `--disabled-tools` (CLI flag or their
`WORKSPACE_MCP_*` env vars) — it is a self-contained selector that picks both
tools and scopes on its own, so combining it with another selector is
contradictory and rejected with an error. In particular, disabling one of its
tools with `--disabled-tools` would drop that tool from the surface while still
requesting its scope, defeating the minimal grant; just omit the tool from the
`--only-tools` list instead.

**When to use it:** you want a purpose-built endpoint that does a few specific
things and nothing else, with the smallest OAuth consent screen possible. An
agent that only sends email and shares Drive files, for example, should never be
granted read access to your whole mailbox.

## `--only-tools` vs `--disabled-tools`

The two flags work at different layers, and the difference is the OAuth grant:

| | `--only-tools` | `--disabled-tools` |
| --- | --- | --- |
| Direction | allowlist (exact set) | blocklist (subtract from any selection) |
| Combines with other selectors | no — self-contained | yes — that's the point |
| Effect on requested scopes | **derives the minimal union** from the named tools | **none** — the grant is whatever the other selectors chose |
| Enforcement layer | scope layer *and* tool layer | tool layer only |

A tool removed by `--disabled-tools` is gone from the MCP surface, but if its
scope is shared with a surviving tool the token can still perform the underlying
action through any path outside this server. Only `--only-tools` (or a
permission level that genuinely excludes the scope) narrows what the *token*
can do.
