# Implementation Plan: User-Scoped Attachment Security

**Branch**: `001-user-scoped-attachments` | **Date**: 2026-02-04 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-user-scoped-attachments/spec.md`

## Summary

Add user-scoped security to the attachment download system. Currently, attachment URLs are accessible to anyone who knows the URL. This feature adds cryptographic signed URLs with owner verification, ensuring only the user who downloaded an attachment can access it. Uses HMAC-SHA256 for signature generation with configurable or auto-generated signing keys.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, fastmcp, cryptography (already in deps)
**Storage**: File-based (./tmp/attachments) with in-memory metadata
**Testing**: pytest, pytest-asyncio
**Target Platform**: Linux server (also macOS for dev)
**Project Type**: Single project (MCP server)
**Performance Goals**: <10ms signature verification overhead (SC-003)
**Constraints**: 1-hour URL expiry, 32-byte minimum signing key
**Scale/Scope**: Multi-user MCP server deployment

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The constitution file contains only template placeholders. No specific principles to validate against. Proceeding with standard best practices:

- [x] Security-first: HMAC-SHA256 for signatures, no sensitive data in logs
- [x] Backward compatibility: Existing API preserved, new security layer added
- [x] Testability: All security behaviors have acceptance scenarios

## Project Structure

### Documentation (this feature)

```text
specs/001-user-scoped-attachments/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
└── tasks.md             # Phase 2 output (created by /speckit.tasks)
```

### Source Code (repository root)

```text
core/
├── attachment_storage.py    # MODIFY: Add owner_id to metadata, save_attachment signature
├── url_signer.py            # NEW: HMAC signing/verification logic
└── server.py                # MODIFY: Add signature verification to serve_attachment

gmail/
└── gmail_tools.py           # MODIFY: Pass user email to save_attachment

gdrive/
└── drive_tools.py           # MODIFY: Pass user email to save_attachment

tests/
├── test_url_signer.py       # NEW: Unit tests for signing/verification
└── test_attachment_security.py  # NEW: Integration tests for access control
```

**Structure Decision**: Existing single-project structure maintained. New `url_signer.py` module added to `core/` for separation of concerns.

## Complexity Tracking

No constitution violations to justify.

---

## Phase 0: Research Summary

### Decision 1: Signing Key Management

**Decision**: Use `ATTACHMENT_SIGNING_KEY` environment variable with auto-generation fallback
**Rationale**: Matches existing pattern in codebase (e.g., `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY`). Auto-generation allows zero-config dev experience while warning about persistence implications.
**Alternatives considered**:
- Derive from OAuth client secret: Rejected because signing key should be independent of OAuth config
- Require key always: Rejected for dev ergonomics

### Decision 2: URL Parameter Format

**Decision**: Query parameters with base64url encoding
**Rationale**: Compatible with all HTTP clients, no path encoding issues, easy to parse
**Format**: `/attachments/{file_id}?sig={signature}&exp={expiry_timestamp}&uid={owner_hash}`
**Alternatives considered**:
- Path-based tokens: Harder to parse, potential routing conflicts
- JWT in query param: Overkill for simple signed URL use case

### Decision 3: User Identity Source

**Decision**: Extract user email from MCP context via existing auth middleware
**Rationale**: The codebase already has `AuthInfoMiddleware` that injects user info into context. Reuse this pattern.
**Alternatives considered**:
- Pass user email explicitly: More coupling, error-prone

### Decision 4: Error Response Codes

**Decision**: Use HTTP 401 for missing params, 403 for verification failures
**Rationale**: Per spec (FR-006, FR-007, FR-008). Consistent with RFC 7235 and 7231.
**Alternatives considered**:
- 404 for all errors: Hides security info but violates least astonishment

---

## Phase 1: Design

### Data Model Changes

See [data-model.md](./data-model.md) for complete entity definitions.

**AttachmentMetadata** (existing, modified):
- Add `owner_id: str` field (user email address)

**SignedURLParams** (new):
- `file_id: str` - UUID of the attachment
- `signature: str` - HMAC-SHA256 signature, base64url-encoded
- `expiry: int` - Unix timestamp when URL expires
- `owner_hash: str` - SHA-256 of owner email, base64url-encoded

### API Contracts

See [contracts/](./contracts/) for OpenAPI schema.

**Modified Endpoint**: `GET /attachments/{file_id}`
- **Query Parameters**: `sig`, `exp`, `uid` (all required when security enabled)
- **Success Response**: 200 with file content
- **Error Responses**:
  - 401: Missing signature parameters
  - 403: Invalid signature, wrong user, or expired URL
  - 404: Attachment not found

### Module Design

**core/url_signer.py**:
```python
class URLSigner:
    def __init__(self, signing_key: bytes)
    def sign_url(self, file_id: str, owner_email: str, expiry_seconds: int) -> str
    def verify_url(self, file_id: str, signature: str, expiry: int,
                   owner_hash: str, requesting_user_email: str) -> VerifyResult

class VerifyResult:
    valid: bool
    error_code: int  # 401, 403, or 0 for success
    error_message: str
```

**Initialization** (in main.py or server startup):
```python
# Load or generate signing key
signing_key = os.getenv("ATTACHMENT_SIGNING_KEY")
if signing_key:
    if len(signing_key.encode()) < 32:
        raise ValueError("ATTACHMENT_SIGNING_KEY must be at least 32 bytes")
else:
    signing_key = secrets.token_bytes(32)
    logger.warning("Using auto-generated signing key; URLs will not survive restart")
```

### Integration Points

1. **gmail/gmail_tools.py**: After `storage.save_attachment()`, pass user email from context
2. **gdrive/drive_tools.py**: Same as above
3. **core/server.py**: Verify signature before serving file in `serve_attachment()`
4. **core/attachment_storage.py**: Accept and store `owner_id` parameter

---

## Implementation Phases

### Phase A: Core Signing Module (Foundation)
1. Create `core/url_signer.py` with `URLSigner` class
2. Implement `sign_url()` and `verify_url()` methods
3. Add signing key initialization logic
4. Write unit tests for signing/verification

### Phase B: Storage Layer Changes
1. Modify `AttachmentStorage.save_attachment()` to accept `owner_id`
2. Store `owner_id` in metadata dict
3. Update `get_attachment_metadata()` to return owner info
4. Update existing callers (gmail_tools.py, drive_tools.py)

### Phase C: URL Generation Integration
1. Modify `get_attachment_url()` to accept owner email
2. Generate signed URLs instead of plain URLs
3. Pass user context from tool handlers

### Phase D: Verification Middleware
1. Modify `serve_attachment()` to extract signature params
2. Verify signature, expiry, and user match
3. Return appropriate error responses (401, 403)
4. Add structured logging (signature prefix only)

### Phase E: Configuration & Logging
1. Add `ATTACHMENT_SIGNING_KEY` env var support
2. Add startup validation for key length
3. Add warning log for auto-generated keys
4. Ensure signature values are truncated in logs

### Phase F: Integration Testing
1. Test cross-user access denial
2. Test URL expiry enforcement
3. Test signature tampering detection
4. Test backward compatibility with authenticated users
