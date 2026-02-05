# Tasks: User-Scoped Attachment Security

**Input**: Design documents from `/specs/001-user-scoped-attachments/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Not explicitly requested in feature specification. Test tasks included in Phase 6 (Polish) as optional validation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

This is a single Python project with existing structure:
- `core/` - Core modules (attachment_storage.py, server.py)
- `gmail/` - Gmail tools
- `gdrive/` - Drive tools
- `tests/` - Test files

---

## Phase 1: Setup

**Purpose**: No setup needed - existing project with established structure

- [ ] T001 Verify cryptography package is available (already in pyproject.toml dependencies)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core signing infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [ ] T002 Create URLSigner class with `__init__(signing_key: bytes)` in core/url_signer.py
- [ ] T003 Implement `_hash_email(email: str) -> str` helper returning base64url SHA-256 in core/url_signer.py
- [ ] T004 Implement `sign_url(file_id: str, owner_email: str, expiry_seconds: int) -> str` in core/url_signer.py
- [ ] T005 Implement VerifyResult dataclass with valid, error_code, error_message fields in core/url_signer.py
- [ ] T006 Implement `verify_url(file_id, signature, expiry, owner_hash, requesting_user_email) -> VerifyResult` in core/url_signer.py
- [ ] T007 Add 60-second clock skew tolerance in expiry verification in core/url_signer.py
- [ ] T008 Implement signing key initialization with env var and auto-generation fallback in core/url_signer.py
- [ ] T009 Add key length validation (minimum 32 bytes) with ValueError on startup in core/url_signer.py
- [ ] T010 Add warning log for auto-generated signing key in core/url_signer.py

**Checkpoint**: Signing infrastructure ready - user story implementation can now begin

---

## Phase 3: User Story 1 & 2 - Secure Attachment Download + Signed URL Generation (Priority: P1) 🎯 MVP

**Goal**: Gmail attachments are accessible only to the user who downloaded them via cryptographically signed URLs

**Note**: User Stories 1 and 2 are tightly coupled (signed URLs enable secure downloads) so they are combined into a single phase.

**Independent Test**: Download an attachment as User A, verify URL contains signature params. Access URL as User A (succeeds). Attempt access as User B (403). Wait 1 hour and access (403 expired).

### Implementation for User Story 1 & 2

- [ ] T011 [US1] Modify `save_attachment()` to accept `owner_id: str` parameter in core/attachment_storage.py
- [ ] T012 [US1] Store `owner_id` in metadata dict alongside existing fields in core/attachment_storage.py
- [ ] T013 [US1] Update `get_attachment_metadata()` to include owner_id in returned dict in core/attachment_storage.py
- [ ] T014 [US2] Modify `get_attachment_url()` to accept `owner_email: str` parameter in core/attachment_storage.py
- [ ] T015 [US2] Import and use URLSigner to generate signed URLs in `get_attachment_url()` in core/attachment_storage.py
- [ ] T016 [US1] Extract user email via `ctx.get_state("authenticated_user_email")` from FastMCP context in `download_gmail_attachment` tool in gmail/gmail_tools.py (per Constitution Principle I)
- [ ] T017 [US1] Pass owner email to `save_attachment()` and `get_attachment_url()` in gmail/gmail_tools.py
- [ ] T018 [US1] Modify `serve_attachment()` to extract sig, exp, uid query parameters in core/server.py
- [ ] T019 [US1] Return 401 JSON response when signature parameters are missing in core/server.py
- [ ] T020 [US1] Extract requesting user email from Authorization header (JWT decode) or request.state in `serve_attachment()` in core/server.py (per Constitution Principle I; see auth/mcp_session_middleware.py for pattern)
- [ ] T021 [US1] Call `URLSigner.verify_url()` before serving attachment in core/server.py
- [ ] T022 [US1] Return 403 JSON response for invalid signature with error message in core/server.py
- [ ] T023 [US1] Return 403 JSON response for expired URL with error message in core/server.py
- [ ] T024 [US1] Return 403 JSON response for user mismatch with error message in core/server.py
- [ ] T025 [US1] Add structured logging with truncated signature (first 8 chars only) in core/server.py

**Checkpoint**: Gmail attachments are now secured with user-scoped signed URLs

---

## Phase 4: User Story 3 - Drive File Download Security (Priority: P2)

**Goal**: Google Drive file downloads have the same security protections as Gmail attachments

**Independent Test**: Download a Drive file as User A, verify URL has signature params. Access as User B (403). Wait 1 hour (403 expired).

### Implementation for User Story 3

- [ ] T026 [US3] Extract user email via `ctx.get_state("authenticated_user_email")` from FastMCP context in `download_file` tool in gdrive/drive_tools.py (per Constitution Principle I)
- [ ] T027 [US3] Pass owner email to `save_attachment()` and `get_attachment_url()` in gdrive/drive_tools.py
- [ ] T028 [US3] Verify Drive downloads use same signed URL generation path as Gmail in gdrive/drive_tools.py

**Checkpoint**: Both Gmail and Drive downloads are now consistently secured

---

## Phase 5: User Story 4 - Configurable Signing Key (Priority: P3)

**Goal**: Server administrators can configure a persistent signing key via environment variable

**Independent Test**: Set ATTACHMENT_SIGNING_KEY, generate URL, restart server, verify URL still works

### Implementation for User Story 4

- [ ] T029 [US4] Document ATTACHMENT_SIGNING_KEY environment variable in README or env.example
- [ ] T030 [US4] Add startup validation that logs warning for auto-generated key in main.py or server startup
- [ ] T031 [US4] Add startup validation that fails fast for key < 32 bytes with clear error in main.py or server startup
- [ ] T032 [US4] Ensure signing key is initialized before any attachment operations in main.py or server startup

**Checkpoint**: Signing key can be persisted across restarts in production

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validation, documentation, and optional testing

- [ ] T033 [P] Ensure no complete signature values appear in any log statements (security audit)
- [ ] T034 [P] Update quickstart.md with manual testing instructions if needed in specs/001-user-scoped-attachments/quickstart.md
- [ ] T035 Run manual test: Download Gmail attachment, verify signed URL works for owner
- [ ] T036 Run manual test: Attempt access to Gmail attachment URL as different user (expect 403)
- [ ] T037 Run manual test: Download Drive file, verify signed URL works for owner
- [ ] T038 Run manual test: Attempt access to Drive file URL as different user (expect 403)
- [ ] T039 Run manual test: Verify expired URL returns 403
- [ ] T040 Run manual test: Verify tampered signature returns 403

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - immediate start
- **Foundational (Phase 2)**: Depends on Setup - BLOCKS all user stories
- **User Story 1 & 2 (Phase 3)**: Depends on Foundational completion
- **User Story 3 (Phase 4)**: Depends on Phase 3 (reuses same signing infrastructure)
- **User Story 4 (Phase 5)**: Depends on Phase 2 (signing key infra exists)
- **Polish (Phase 6)**: Depends on all user stories being complete

### User Story Dependencies

- **User Story 1 & 2 (P1)**: Can start after Foundational (Phase 2) - MVP delivery
- **User Story 3 (P2)**: Can start after US1/US2 - uses same signing code path
- **User Story 4 (P3)**: Can start after Foundational - independent of other stories

### Within Each Phase

- Signing module tasks (T002-T010) should be completed sequentially as they build on each other
- Storage tasks (T011-T015) can mostly run in parallel
- Server verification tasks (T018-T025) must be sequential (verification flow)

### Parallel Opportunities

- T033 and T034 can run in parallel (different concerns)
- T035-T040 are manual tests that can be run in any order after implementation

---

## Parallel Example: Foundational Phase

```bash
# These tasks build on each other - run sequentially:
Task: "T002 Create URLSigner class"
Task: "T003 Implement _hash_email helper"
Task: "T004 Implement sign_url method"
# etc.
```

## Parallel Example: Phase 3 Storage vs Gmail

```bash
# Storage modifications can proceed while Gmail integration is planned:
Task: "T011 Modify save_attachment() to accept owner_id"
Task: "T012 Store owner_id in metadata"

# After storage is ready, Gmail integration follows
```

---

## Implementation Strategy

### MVP First (User Stories 1 & 2 Only)

1. Complete Phase 1: Setup (verify deps)
2. Complete Phase 2: Foundational (URLSigner module)
3. Complete Phase 3: User Story 1 & 2 (Gmail attachment security)
4. **STOP and VALIDATE**: Test Gmail attachments with two users
5. Deploy/demo if ready - Gmail attachments are secured

### Incremental Delivery

1. Setup + Foundational → Signing infrastructure ready
2. Add US1 & US2 → Gmail attachment security → Deploy (MVP!)
3. Add US3 → Drive file security → Deploy
4. Add US4 → Persistent signing key → Deploy
5. Each story adds value without breaking previous stories

### Single Developer Strategy

1. Complete Setup → Foundational → US1 & US2 in sequence
2. Test manually (T035-T036)
3. Add US3 (Drive security)
4. Test manually (T037-T038)
5. Add US4 (config) if needed for production
6. Final validation (T039-T040)

---

## Notes

- [P] tasks = different files, no dependencies
- [Story] label maps task to specific user story for traceability
- User Stories 1 & 2 are combined because signed URLs (US2) are the mechanism for secure downloads (US1)
- No explicit test tasks (TDD not requested) - manual validation in Phase 6
- Avoid: modifying same file in parallel (conflicts), cross-story dependencies that break independence
