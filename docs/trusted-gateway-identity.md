# Trusted-gateway identity (`TRUST_GATEWAY_IDENTITY`)

Run the server behind an MCP-aware reverse proxy (Pomerium, oauth2-proxy, Cloudflare Access,
Istio/Envoy, Traefik ForwardAuth, …) that authenticates the user and injects a **signed
identity assertion** (a JWT) on every upstream request. The server verifies that assertion
against the proxy's JWKS and uses the asserted email as the **per-request principal** —
*without terminating MCP OAuth itself*, so it composes with the proxy instead of fighting it
for the single MCP auth handshake.

This gives true per-user isolation for proxy-fronted deployments: the Google credential is
keyed to the **verified principal**, not to the transport session.

## When to use it

- You have a gateway doing SSO + per-route authz, and you don't want this server to also
  terminate MCP OAuth (which would contend for the handshake).
- You want the asserted identity (not a client-supplied value) to decide which user's Google
  grant a request may use.

Keep `MCP_ENABLE_OAUTH21=false` (the proxy owns the handshake). `TRUST_GATEWAY_IDENTITY` is
mutually exclusive with `MCP_ENABLE_OAUTH21=true`.

## Configuration

| Env var | Required | Default | Notes |
| --- | --- | --- | --- |
| `TRUST_GATEWAY_IDENTITY` | yes | `false` | enable the mode |
| `GATEWAY_IDENTITY_JWKS_URL` | yes | — | proxy JWKS endpoint used to verify the assertion |
| `GATEWAY_IDENTITY_HEADER` | no | `x-pomerium-jwt-assertion` | header carrying the JWT (e.g. `cf-access-jwt-assertion` for Cloudflare Access) |
| `GATEWAY_IDENTITY_ALGORITHMS` | no | `ES256` | comma-separated allowed alg(s); pinned to block alg-confusion/`none` (e.g. `RS256` for Cloudflare Access) |
| `GATEWAY_IDENTITY_ISSUER` | no | — | if set, the assertion's `iss` must match |
| `GATEWAY_IDENTITY_AUDIENCE` | yes | — | assertion `aud` identifying this MCP deployment; always verified |

Example (Pomerium):

```bash
TRUST_GATEWAY_IDENTITY=true
GATEWAY_IDENTITY_JWKS_URL=https://authenticate.example.com/.well-known/pomerium/jwks.json
GATEWAY_IDENTITY_AUDIENCE=workspace-mcp.example.com
# header/alg defaults already target Pomerium
```

The proxy must overwrite or remove any client-supplied identity header before forwarding
its own assertion to the upstream (Pomerium: set `pass_identity_headers: true` on the route).

## How it works

1. **Verify** — `auth/gateway_identity.py` validates the assertion JWT against the JWKS
   (signature + `exp` + `aud`; optional `iss`), pinned to the configured algorithm(s).
   A missing or invalid assertion rejects the request immediately; gateway mode never falls
   back to bearer tokens or transport-session identity.
2. **Principal** — the verified `email` becomes `authenticated_user_email`
   (`authenticated_via=gateway_assertion`) in request-scoped state only.
3. **No prompt / no spoofing** — like OAuth 2.1 mode, the `user_google_email` tool parameter
   is hidden and auto-filled from the verified principal, so clients never ask for an email
   and a caller can't act on another account by passing one. Any caller-supplied
   `user_google_email` is dropped from every tool call (cached pre-gateway schemas keep
   working), and a configured `USER_GOOGLE_EMAIL` default is ignored — the verified
   principal always wins.
4. **Consent enforcement** — the per-user Google consent (side flow) is initiated for the
   principal. The OAuth state records an explicit immutable principal binding, and at
   `/oauth2callback` the Google account actually consented **must match** it; a missing
   binding or mismatch is rejected and nothing is stored.

Credentials still use the normal per-user store (keyed by email); the asserted identity just
selects/locks which user's grant a request may use.

## Security notes

- The assertion is verified **cryptographically** — an unverified/expired/wrong-alg token is
  rejected.
- Pin `GATEWAY_IDENTITY_ALGORITHMS` to your proxy's actual algorithm.
- `GATEWAY_IDENTITY_AUDIENCE` is mandatory so assertions minted for another application or
  route cannot be replayed here. Set `GATEWAY_IDENTITY_ISSUER` as well when your gateway
  provides a stable issuer.
- Configure the proxy to strip/overwrite incoming `GATEWAY_IDENTITY_HEADER` values.
- Ensure the backend is reachable **only** via the proxy (e.g. ClusterIP / no public port), so
  the assertion can't be supplied by an untrusted client.
