# google_workspace_mcp

## VerdiLabs platform

This repo deploys to the VerdiLabs AKS platform (dev cluster). Follow the paved
road — the shared `verdi` plugin (Claude Code `/verdi:...`, Codex `/skills`)
provides:

- **platform-helm-conventions** — the RULES a chart must follow (secrets -> Key
  Vault via CSI, config -> ConfigMap, image -> the shared ACR (not GHCR), ingress
  -> an HTTPRoute to the platform Gateway with **free HTTPS**, Workload Identity,
  Spot toleration, PodSecurity restricted, logs -> stdout).
- **onboard-app-to-dev** — the end-to-end procedure to deploy this app.
- **debug-deployment** — when a deploy/app is broken.

App: `google_workspace_mcp` . Env: `dev` . Hostname once deployed: `google_workspace_mcp.dev.verdilabs.io`
(HTTPS is provided by the platform — do NOT set up your own cert/ingress.)

If the `verdi` plugin isn't installed, see https://github.com/VerdiLabs/verdi-skills.
