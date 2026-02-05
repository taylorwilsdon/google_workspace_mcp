<!--
Sync Impact Report
==================
Version change: 0.0.0 → 1.0.0 (initial ratification)
Modified principles: N/A (new document)
Added sections: Core Principles (6), Security Requirements, Development Workflow, Governance
Removed sections: N/A
Templates requiring updates:
  - .specify/templates/plan-template.md: ✅ updated (added Constitution Check gates for 6 principles + security checklist)
  - .specify/templates/spec-template.md: ✅ updated (added Security Requirements section)
  - .specify/templates/tasks-template.md: ✅ no changes needed
Follow-up TODOs: None
-->

# Google Workspace MCP Server Constitution

## Core Principles

### I. Context-Scoped Credential & Session Management

User context and credentials MUST be managed at request scope using context variables and middleware state. This enables multi-user support while maintaining strict isolation between users.

**Requirements:**
- Per-request authentication state MUST use Python's `contextvars` to prevent cross-user leakage
- Session middleware MUST be the outermost layer in the middleware stack
- User email and authentication method MUST be extractable from context in any tool handler
- Credentials MUST NOT be stored in global variables or module-level state

**Evidence:** `core/context.py`, `auth/auth_info_middleware.py`, `core/server.py`

### II. Decorator-Based Service Injection

Authentication complexity MUST be abstracted via decorators that automatically handle credential resolution, OAuth version detection, and service instantiation based on scope requirements.

**Requirements:**
- Tools MUST use `@require_google_service` decorator rather than implementing authentication logic directly
- Decorators MUST remove the `service` parameter from user-facing tool signatures
- OAuth version detection MUST happen automatically based on configuration and context
- Required scopes MUST be attached as metadata to decorated functions for downstream filtering

**Evidence:** `auth/service_decorator.py`, `gdrive/drive_tools.py`, `gmail/gmail_tools.py`

### III. Centralized, Environment-Driven Configuration

All configuration MUST flow through centralized modules that read from environment variables with sensible defaults. Hardcoded configuration values are prohibited.

**Requirements:**
- OAuth settings MUST be centralized in `auth/oauth_config.py`
- Storage backend selection MUST be environment-driven with logged fallbacks
- Credentials directory MUST respect environment variable priority with documented fallbacks
- Configuration changes MUST NOT require code modifications

**Evidence:** `auth/oauth_config.py`, `auth/google_auth.py`, `core/server.py`

### IV. Explicit Error Handling with User-Facing Messages

API errors MUST be caught and transformed into actionable, user-friendly messages that guide remediation. Security-sensitive details MUST be sanitized from error responses.

**Requirements:**
- HTTP 401/403 errors MUST include guidance about re-authentication appropriate to the OAuth mode
- Token refresh failures MUST return contextual remediation steps without exposing token details
- Missing configuration MUST point to specific environment variables to enable self-service
- Stack traces and internal error details MUST NOT be exposed to end users

**Evidence:** `core/utils.py`, `auth/service_decorator.py`

### V. Multi-Mode Authentication with Version Detection

The system MUST support multiple authentication modes (OAuth 2.0, OAuth 2.1, external OAuth, stdio single-user) and automatically detect which mode to use based on configuration and context.

**Requirements:**
- OAuth version detection MUST check configuration, middleware state, and session in priority order
- Authentication extraction MUST try sources in order with fallback to the next method on failure
- Mode-specific code paths MUST share common abstractions where possible
- Adding a new authentication mode MUST NOT require changes to existing tool implementations

**Evidence:** `auth/service_decorator.py`, `core/server.py`, `auth/auth_info_middleware.py`

### VI. Scope-Driven Tool Filtering and Capability Declaration

Tools MUST declare required scopes upfront. The system MUST use these declarations to filter tools based on OAuth capabilities and read-only mode without repeated authorization checks in tool code.

**Requirements:**
- Required scopes MUST be attached as function metadata via decorators
- Tool filtering MUST occur post-registration based on tier, OAuth mode, and scope requirements
- Scope names MUST be centralized constants in `auth/scopes.py`
- Tools MUST NOT perform scope validation internally; this is handled by the framework

**Evidence:** `core/tool_registry.py`, `auth/service_decorator.py`, `auth/scopes.py`

## Security Requirements

### Credential Protection

- Signing keys and secrets MUST be at least 32 bytes (256 bits)
- Complete signature values MUST NOT appear in logs; truncate to first 8 characters for debugging
- Bearer tokens MUST NOT be logged or included in error messages
- Auto-generated keys MUST trigger a warning log at startup

### User Isolation

- Attachments and downloaded files MUST be scoped to the user who created them
- Cross-user access attempts MUST return HTTP 403 without revealing existence of the resource
- URL signatures MUST include owner identification to prevent unauthorized access

### Configuration Security

- Sensitive environment variables MUST be documented with minimum security requirements
- Default configurations MUST be secure; insecure options require explicit opt-in

## Development Workflow

### Code Organization

- Core infrastructure belongs in `core/`
- Authentication logic belongs in `auth/`
- Google service integrations belong in service-specific directories (`gmail/`, `gdrive/`, `gcalendar/`, etc.)
- Tests mirror the source structure under `tests/`

### Adding New Tools

1. Create tool function with appropriate decorators
2. Declare required scopes via `@require_google_service(scopes=[...])`
3. Use `@handle_http_errors()` for consistent error handling
4. Avoid authentication logic in tool body; rely on injected service

### Adding New Authentication Modes

1. Implement detection logic in `_detect_oauth_version()`
2. Add middleware extraction path in `AuthInfoMiddleware`
3. Configure provider setup in `configure_server_for_http()`
4. Existing tools should work without modification

## Governance

This constitution supersedes all other development practices for the Google Workspace MCP Server project.

**Amendment Process:**
- Amendments require documentation of the change rationale
- Breaking changes to principles require MAJOR version increment
- New principles or significant expansions require MINOR version increment
- Clarifications and wording fixes require PATCH version increment

**Compliance:**
- All PRs and code reviews MUST verify compliance with these principles
- Complexity that violates a principle MUST be explicitly justified in the PR description
- Constitution violations without justification are grounds for PR rejection

**Version**: 1.0.1 | **Ratified**: 2026-02-05 | **Last Amended**: 2026-02-05
