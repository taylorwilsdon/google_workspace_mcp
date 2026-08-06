# Security Fixes Summary — Google Workspace MCP

This document summarizes the vulnerability fixes applied. Public APIs, request/response
schemas, and auth architecture are preserved unless a change was required for security.

| ID | Issue | Status |
|----|-------|--------|
| V1 | `OAUTHLIB_INSECURE_TRANSPORT` process-wide | Fixed |
| V2 | JWT `id_token` without signature verify | Fixed |
| V3 | `client_secret` in user credential files | Fixed |
| V4 | Secret manifests / git hygiene | Fixed (templates + gitignore + docs) |
| V5 | Unauthenticated attachment downloads | Fixed (HMAC tokens) |
| V6 | OAuth state validation bypass | Fixed |
| V7 | Default bind `0.0.0.0` | Fixed → `127.0.0.1` |
| V8 | Blanket localhost Origin trust | Fixed (allowlist + same-origin) |
| V9 | Health info disclosure | Fixed |
| V10 | Weak JWT signing key | Fixed (reject &lt; 32 bytes) |
| V11 | No rate limiting | Fixed |
| V12 | GitLab token in clone URL | N/A (not present in this repo) |
| V13 | Shell-form Docker ENTRYPOINT | Fixed (exec-form + wrapper) |

## Per-vulnerability notes

### V1 — OAUTHLIB_INSECURE_TRANSPORT
- **Root cause:** Code set `os.environ["OAUTHLIB_INSECURE_TRANSPORT"]=1` permanently.
- **Change:** `auth/oauthlib_transport.py` context manager scopes insecure HTTP to loopback token exchange only; auto-set removed from `start_auth_flow` / callback.
- **Regression:** Explicit operator env still honored; localhost OAuth still works via temporary scope.

### V2 — id_token verification
- **Root cause:** `jwt.decode(..., verify_signature=False)`.
- **Change:** `_email_from_verified_id_token()` uses `google.oauth2.id_token.verify_oauth2_token()` (aud/iss/exp/signature).
- **Regression:** Same email extraction path; fails closed if client_id missing or token invalid.

### V3 — Client secrets in credential files
- **Root cause:** Store wrote `client_id`/`client_secret` into per-user JSON/GCS objects.
- **Change:** Persist tokens/scopes/expiry only; reconstruct client creds from `GOOGLE_OAUTH_*` config; still read old files for backward compatibility.
- **Regression:** Existing credential files continue to load; new writes omit secrets.

### V4 — Secret manifests
- **Root cause:** Risk of committing real Helm secret values.
- **Change:** `values-secrets.yaml.example`, gitignore entries, SECURITY.md CI/gitleaks guidance. Helm `secret.yaml` template unchanged (still values-driven).
- **Regression:** Deploy via `-f values-secrets.yaml` as documented.

### V5 — Attachment auth
- **Root cause:** `/attachments/{id}` served anyone who guessed/leaked the UUID.
- **Change:** HMAC download tokens (`core/attachment_tokens.py`); URLs from `get_attachment_url` include `?token=`; routes require valid token.
- **Regression:** Local disk short-circuit in Gmail tools still works; HTTP downloads need the signed URL.

### V6 — OAuth state
- **Root cause:** Single-user mode could consume “latest” state without `state` param.
- **Change:** Missing `state` always raises; callback no longer passes fallback flag.
- **Regression:** Normal OAuth redirects that include `state` unchanged.

### V7 — Bind address
- **Root cause:** Default host `0.0.0.0`.
- **Change:** Default `127.0.0.1` in `main.py` / `port_resolver` / `fastmcp.json`. Docker sets `WORKSPACE_MCP_HOST=0.0.0.0` explicitly for containers.
- **Regression:** Explicit `WORKSPACE_MCP_HOST` still wins.

### V8 — Origin validation
- **Root cause:** Any `localhost` Origin accepted.
- **Change:** Loopback Origins must be allowlisted (or same-origin-as-Host). `vscode-webview://` scheme trust retained (IDE GUID hosts).
- **Regression:** Server’s own `base_url` remains allowlisted; add `OAUTH_ALLOWED_ORIGINS` for extra local ports.

### V9 — Health disclosure
- **Root cause:** `/health` returned version/transport.
- **Change:** Public `/` and `/health` return `{"status":"ok"}` only; `/health/details` requires health token or Bearer.
- **Regression:** K8s probes using `/health` still succeed (status ok).

### V10 — Weak JWT key
- **Root cause:** Keys &lt; 12 chars only warned.
- **Change:** Startup raises if signing key &lt; 32 bytes.
- **Regression:** Strong keys unchanged; short keys must be rotated (intentional break).

### V11 — Rate limiting
- **Root cause:** No app-level HTTP rate limits.
- **Change:** `RateLimitMiddleware` (sliding window, per-IP, path-class budgets). Configurable via `WORKSPACE_RATE_LIMIT_*`; disable with `WORKSPACE_RATE_LIMIT_ENABLED=false`.
- **Regression:** Normal traffic well under defaults.

### V12 — GitLab token URL
- Not applicable — no GitLab clone-URL token pattern in this repository.

### V13 — Docker ENTRYPOINT
- **Root cause:** `ENTRYPOINT ["/bin/sh","-c"]` + interpolated `TOOLS`.
- **Change:** `docker-entrypoint.sh` + exec-form `ENTRYPOINT`/`CMD`.
- **Regression:** Same CLI flags via CMD/env; container explicitly sets bind host.

## Suggested tests (implemented where existing suites covered)
- Attachment: missing token → 401; valid token → 200
- Origin: `localhost:5173` rejected without allowlist; allowlisted/same-origin OK
- Bind host defaults to `127.0.0.1` for OAuth 2.1 without env override
- Missing OAuth state rejected even with `MCP_SINGLE_USER_MODE=1`
- Unit: attachment token mint/verify; oauthlib transport scope restore
