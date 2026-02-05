# Feature Specification: User-Scoped Attachment Security

**Feature Branch**: `001-user-scoped-attachments`
**Created**: 2026-02-04
**Status**: Draft
**Input**: User description: "User-scoped attachment security for multi-user MCP server - prevent users from accessing other users' Gmail attachments"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Secure Attachment Download (Priority: P1)

As a user of the MCP server, I want my downloaded attachments to be accessible only to me, so that my private email attachments cannot be viewed by other users sharing the same server.

**Why this priority**: This is the core security feature. Without user isolation, the multi-user deployment is fundamentally insecure. Sensitive email attachments (contracts, personal documents, confidential information) could be exposed to unauthorized users.

**Independent Test**: Can be fully tested by downloading an attachment as User A, then attempting to access the same URL as User B. Delivers the fundamental security guarantee of user isolation.

**Acceptance Scenarios**:

1. **Given** User A downloads a Gmail attachment, **When** User A accesses the generated URL, **Then** the attachment file is successfully downloaded
2. **Given** User A downloads a Gmail attachment, **When** User B attempts to access User A's attachment URL, **Then** access is denied with a 403 Forbidden response
3. **Given** User A downloads a Gmail attachment, **When** User A accesses the URL after 1 hour, **Then** access is denied because the URL has expired

---

### User Story 2 - Signed URL Generation (Priority: P1)

As a user, I want attachment URLs to contain cryptographic proof of ownership, so that the server can verify my identity without requiring additional authentication headers.

**Why this priority**: Signed URLs are essential for universal access (browsers, CLI, embedded images). Without this, attachments wouldn't work in contexts where custom headers cannot be sent.

**Independent Test**: Can be tested by verifying that generated URLs contain signature parameters and that tampering with any parameter causes access denial.

**Acceptance Scenarios**:

1. **Given** a user downloads an attachment, **When** the URL is generated, **Then** the URL contains signature, expiry, and user identifier parameters
2. **Given** a valid signed URL, **When** any URL parameter is modified, **Then** the signature becomes invalid and access is denied
3. **Given** a valid signed URL, **When** the same user accesses it before expiry, **Then** access is granted

---

### User Story 3 - Drive File Download Security (Priority: P2)

As a user, I want Google Drive file downloads to have the same security protections as Gmail attachments, so that all file downloads are consistently protected.

**Why this priority**: While Gmail attachments were the initial concern, Drive files use the same storage mechanism and need identical protection for consistent security.

**Independent Test**: Can be tested by downloading a Drive file and verifying the same security behaviors apply as with Gmail attachments.

**Acceptance Scenarios**:

1. **Given** User A downloads a Drive file, **When** User B attempts to access the download URL, **Then** access is denied
2. **Given** a Drive file download URL, **When** it expires after 1 hour, **Then** access is denied with appropriate error message

---

### User Story 4 - Configurable Signing Key (Priority: P3)

As a server administrator, I want to optionally configure a persistent signing key, so that attachment URLs remain valid across server restarts.

**Why this priority**: In development, auto-generated keys are acceptable. In production, administrators may want URL validity to survive restarts.

**Independent Test**: Can be tested by setting the environment variable, generating a URL, restarting the server, and verifying the URL still works.

**Acceptance Scenarios**:

1. **Given** no signing key environment variable is set, **When** the server starts, **Then** a random signing key is generated and a warning is logged
2. **Given** a signing key environment variable is set, **When** the server starts, **Then** the provided key is used for signing
3. **Given** a persistent signing key, **When** the server restarts, **Then** previously generated URLs remain valid (until their natural expiry)

---

### Edge Cases

- What happens when a URL is accessed after expiry? Both file deletion and URL signature share the same 1-hour expiry, so an expired URL will receive 403 (signature expired); if file cleanup runs first, 404 is possible but rare.
- What happens when a user's email address changes? Old URLs signed with the previous email become invalid.
- What happens when clock skew exists between signing and verification? A small tolerance (e.g., 60 seconds) should be allowed for expiry checks.
- What happens when the signing key is rotated? All existing URLs become invalid; this is acceptable behavior documented for administrators.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST store the owner's email address with each saved attachment
- **FR-002**: System MUST generate cryptographically signed URLs when creating attachment download links
- **FR-003**: System MUST include expiry timestamp in signed URLs
- **FR-004**: System MUST include the owner identifier as a SHA-256 hash of the email, base64url-encoded, in signed URLs for verification
- **FR-005**: System MUST verify the signature before serving any attachment
- **FR-006**: System MUST deny access with HTTP 403 when signature verification fails
- **FR-007**: System MUST deny access with HTTP 403 when the SHA-256 hash of the requesting user's email does not match the owner hash in the URL
- **FR-008**: System MUST deny access with HTTP 401 when signature parameters are missing
- **FR-009**: System MUST use HMAC-SHA256 for signature generation and verification
- **FR-010**: System MUST support configurable signing key via environment variable `ATTACHMENT_SIGNING_KEY` (minimum 32 bytes; reject shorter keys at startup)
- **FR-011**: System MUST auto-generate a random signing key when no environment variable is provided
- **FR-012**: System MUST log a warning when using an auto-generated signing key
- **FR-013**: System MUST apply the same security to both Gmail attachments and Drive file downloads
- **FR-014**: System MUST never log complete signature values (only first 8 characters for debugging)
- **FR-015**: System MUST allow 60-second tolerance for URL expiry verification to accommodate clock skew

### Key Entities

- **Attachment Metadata**: Represents a stored attachment file. Now includes `owner_id` (user email) in addition to existing fields (file_path, filename, mime_type, size, created_at, expires_at).
- **Signed URL**: A URL containing the attachment identifier plus cryptographic parameters (signature, expiry, user identifier hash) that prove ownership.
- **Signing Key**: A secret key used for HMAC signature generation. Either provided via environment or auto-generated at startup.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of attachment URLs include valid cryptographic signatures
- **SC-002**: 0% of cross-user access attempts succeed (User B cannot access User A's attachments)
- **SC-003**: Signature verification adds less than 10ms overhead per request
- **SC-004**: All existing Gmail and Drive download functionality continues to work for legitimate users
- **SC-005**: URL expiry enforced within 1 minute of configured expiry time
- **SC-006**: Server logs never contain complete signature values (security audit requirement)

## Clarifications

### Session 2026-02-04

- Q: What is the minimum signing key length for ATTACHMENT_SIGNING_KEY? → A: 32 bytes minimum (256-bit, matches SHA-256 output)
- Q: Should the URL expose the raw email or a hashed version? → A: SHA-256 hash of email, base64url-encoded (privacy-preserving)
- Q: Should clock skew tolerance be formalized as a requirement? → A: Yes, 60 seconds tolerance (formalized as FR-015)
- Q: Are file deletion time and URL expiry time the same? → A: Yes, both are 1 hour (same expiry simplifies implementation)
- Q: How should user verification work given hashed owner in URL? → A: Hash requesting user's email, compare to URL hash parameter

## Assumptions

- The user's email address is a stable identifier for the duration of the attachment's lifetime (1 hour)
- HMAC-SHA256 provides sufficient security for URL signing
- 1 hour expiry is acceptable for all attachment use cases
- Server administrators can set environment variables in production deployments
- Clock synchronization between signing and verification is within 60 seconds
