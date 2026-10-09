# AI OS Multi-Repo Workspace Routing Model

Operational guide for **which repository owns which work** when using the multi-root workspace (`AI_OS.code-workspace`). This is **not** ratified constitutional law; it prevents misfiling tasks (e.g. runtime code in governing-docs).

---

## 1. Executive summary

Three repos split **policy & specs**, **product runtime**, and **Workspace MCP adapter**. **Governing-docs** holds norms and artifacts; it does **not** ship session state, shell UI, or MCP servers. **Voice-interface** owns the app, session, and agent UX. **Google-workspace-mcp** owns OAuth-adjacent transport, MCP tools, and server config—not product chrome or governance ratification.

---

## 2. Repo ownership table

| Concern | **aios-governing-docs** | **ai-os-voice-interface** | **google-workspace-mcp** |
|--------|-------------------------|---------------------------|---------------------------|
| **Governance specs, amendments, peer review, ratification** | **Yes** — source of truth | No — consumes or displays governed *content*, does not author law | No |
| **DOCUMENT_REGISTRY, STYLE_GUIDE, prompts for governance automation** | **Yes** | No | No |
| **Runtime session state, `effectiveIntent`, conversation memory** | No | **Yes** | No |
| **Shell, canvas, command rail, overlays, startup copy** | No | **Yes** | No |
| **Agent prompts, voice/LLM wiring, “system intent” behavior in product** | No | **Yes** (implementation) | No — server may receive *resolved* context from client, not define product intent |
| **MCP tool schemas, handlers, Google API calls, adapter process** | No (reference only) | Client/router side only | **Yes** |
| **OAuth token handling, MCP `Authorization`, server env for Google** | Documented in specs (e.g. SPEC-007) | Browser/client session + router | **Yes** — server |
| **Transport: JSON-RPC, tool naming, rate limits** | No | Client transport to MCP | **Yes** — server |
| **CI for governance scripts (pytest, registry)** | **Yes** | Separate CI for app | Separate CI for MCP |

### What belongs / does not belong (explicit)

#### aios-governing-docs

- **Belongs:** Specs, amendments, `core/`, `specifications/`, `reviews/`, `scripts/` for registry, peer review, governance MCP **as a document server**, `CHANGELOG`, `DEVELOPER.md`, PR templates for *this* repo.
- **Does not belong:** React/TS app code, session stores, voice client, Workspace MCP server implementation, OAuth redirect handlers for the product, or “implement `effectiveIntent` in session” tasks.

#### ai-os-voice-interface

- **Belongs:** UI, shell, routing to governed canvases, intent resolution **product-side**, agent instructions, session node, feature flags, integration **clients** to MCP (calls, headers, UX around errors).
- **Does not belong:** Ratifying amendments, editing `AIOS-SPEC-*.md` as authority (may **mirror** or **implement** specs), running the Google Workspace **MCP server process** (that’s the MCP repo), or hosting the canonical governance **document corpus** (that’s governing-docs).

#### google-workspace-mcp (e.g. `aios_google_workspace_mcp`)

- **Belongs:** MCP server, tool implementations (Calendar, Gmail, Drive), server-side secrets, OAuth/session **for the MCP service**, CORS/host config for MCP, PSE keys **on server** per SPEC-007.
- **Does not belong:** AI OS shell UI, voice pipeline, governing markdown corpus, amendment workflow, or **defining** user-facing “system intent” copy (voice-interface defines what to send; MCP executes tools).

---

## 3. Task routing table

| If the task is about… | Work in repo… |
|------------------------|---------------|
| Amendment, SPEC text, AMD, peer review thresholds, ratification processor | **aios-governing-docs** |
| Registry generator, governance MCP **script**, `DOCUMENT_REGISTRY` | **aios-governing-docs** |
| Session node, `effectiveIntent`, shell copy, command rail, canvas, voice/Gemini client | **ai-os-voice-interface** |
| Workspace **UI surface** (hub, modals), intent router **client** | **ai-os-voice-interface** |
| MCP **tool** behavior, Google API usage, server crash, adapter auth | **google-workspace-mcp** |
| **Policy** wording for Workspace (layer 1/2, scope classes) | **aios-governing-docs** (then implement in voice + validate MCP) |
| “Header must send Bearer” — **where** to set header in app vs server | **Voice-interface** (client) / **MCP** (validates) per layer |
| Bug: “governance integrity pytest fails” in governing-docs CI | **aios-governing-docs** |
| Bug: “MCP tool returns 401” | **google-workspace-mcp** (+ voice-interface if wrong token passed) |

---

## 4. Cross-repo workflow rules

1. **Normative change (law):** Edit **aios-governing-docs** first (or in parallel with a draft PR). **Do not** treat voice-interface as the source of truth for specs.
2. **Product behavior:** Implement in **ai-os-voice-interface** to match ratified/draft specs; link commits to governing PRs when behavior tracks a SPEC/AMD.
3. **Tool/transport change:** Implement in **google-workspace-mcp**; update **governing-docs** only if SPEC/policy text must change (e.g. new scope class).
4. **Order when spanning all three:**  
   - **Governance** (what must be true) → **MCP** (can the server comply?) → **Voice** (UX and routing).  
   - If urgency is “ship fix,” **MCP + voice** can land with a **follow-up** governance PR to align wording—**do not** put runtime code in governing-docs to “fix” the app.
5. **Avoid:** Opening governing-docs to add TypeScript; opening voice-interface to edit ratified SPEC files as the canonical copy; opening MCP to change amendment workflow.

---

## 5. Example: system intent / effectiveIntent

| Layer | Repo | What |
|-------|------|------|
| **Governance portion** | **aios-governing-docs** | Normative definitions: **Intent** primitive, **primary vs operator** UI, transparency (AMD-2026-007 §11, SPEC-009). Optional: short `reference/` note linking product types to spec—**not** the runtime struct implementation. |
| **Runtime portion** | **ai-os-voice-interface** | `EffectiveIntent` type, session fields, prompt assembly, shell strings, next-action ranking, canvas routing. |
| **MCP portion** | **google-workspace-mcp** | **Nothing** for defining system intent. Only ensure tools and auth support **capabilities** that voice resolves from intent (no duplicate “intent engine” on server). |

---

## 6. Maintenance

- When adding a fourth repo, extend this table.
- Cursor rules: point agents at this file for **workspace-level** routing; each repo may still have `.cursor/rules` for local scope.
