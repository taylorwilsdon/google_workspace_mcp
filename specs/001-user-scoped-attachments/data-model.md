# Data Model: User-Scoped Attachment Security

**Feature**: 001-user-scoped-attachments
**Date**: 2026-02-04

## Entities

### AttachmentMetadata (Modified)

Represents metadata for a stored attachment file.

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| file_path | str | Absolute path to stored file | Required |
| filename | str | Original filename | Required, default: "attachment{ext}" |
| mime_type | str | MIME type | Required, default: "application/octet-stream" |
| size | int | File size in bytes | Required, >= 0 |
| created_at | datetime | When file was created | Required |
| expires_at | datetime | When file expires | Required, created_at + 1 hour |
| **owner_id** | str | **Owner's email address** | **Required (NEW)** |

**Storage**: In-memory dict keyed by file_id (UUID string)

**Example**:
```python
{
    "file_path": "/tmp/attachments/abc-123.pdf",
    "filename": "contract.pdf",
    "mime_type": "application/pdf",
    "size": 102400,
    "created_at": datetime(2026, 2, 4, 10, 30, 0),
    "expires_at": datetime(2026, 2, 4, 11, 30, 0),
    "owner_id": "user@example.com"  # NEW
}
```

---

### SignedURLParams (New)

Represents the cryptographic parameters embedded in a signed attachment URL.

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| file_id | str | UUID of the attachment | Required, UUID format |
| signature | str | HMAC-SHA256 signature | Required, base64url-encoded |
| expiry | int | Unix timestamp (seconds) | Required, > 0 |
| owner_hash | str | SHA-256 of owner email | Required, base64url-encoded |

**URL Format**: `/attachments/{file_id}?sig={signature}&exp={expiry}&uid={owner_hash}`

**Example URL**:
```
/attachments/550e8400-e29b-41d4-a716-446655440000?sig=dGhpcyBpcyBhIHNpZ25hdHVyZQ&exp=1707048600&uid=aGFzaGVkX2VtYWls
```

---

### SigningKey (New)

Represents the server's signing key for HMAC operations.

| Field | Type | Description | Constraints |
|-------|------|-------------|-------------|
| key | bytes | Raw key material | Required, >= 32 bytes |
| source | str | "environment" or "auto-generated" | Required |

**Lifecycle**:
- Created at server startup
- Persists for server lifetime
- If auto-generated, invalidates on restart

**Configuration**:
- Environment variable: `ATTACHMENT_SIGNING_KEY`
- Minimum length: 32 bytes (256 bits)

---

### VerifyResult (New)

Result of URL signature verification.

| Field | Type | Description |
|-------|------|-------------|
| valid | bool | Whether verification succeeded |
| error_code | int | HTTP status code (0 if valid) |
| error_message | str | Human-readable error (empty if valid) |

**Error Codes**:
| Code | Condition |
|------|-----------|
| 0 | Success |
| 401 | Missing required parameters (sig, exp, uid) |
| 403 | Invalid signature |
| 403 | Expired URL |
| 403 | Owner hash mismatch |

---

## Relationships

```
AttachmentMetadata 1:1 SignedURL
    - Each attachment has exactly one owner
    - URL contains hash of owner_id for verification

SigningKey 1:N SignedURL
    - One key signs all URLs
    - Key rotation invalidates all existing URLs
```

---

## State Transitions

### Attachment Lifecycle

```
[Created] --> [Valid] --> [Expired] --> [Deleted]
    |                         |
    +---- 1 hour expiry ------+
```

### URL Verification States

```
[Request Received]
    |
    v
[Check Parameters] -- missing --> [401 Unauthorized]
    |
    v (present)
[Verify Signature] -- invalid --> [403 Forbidden]
    |
    v (valid)
[Check Expiry] -- expired --> [403 Forbidden]
    |
    v (not expired)
[Check Owner] -- mismatch --> [403 Forbidden]
    |
    v (match)
[Serve File] --> [200 OK]
```

---

## Validation Rules

1. **owner_id**: Must be non-empty string, should be valid email format
2. **expiry**: Must be future timestamp at creation, allows 60s tolerance at verification
3. **signature**: Must be valid base64url, exactly 43 characters (256 bits without padding)
4. **owner_hash**: Must be valid base64url, exactly 43 characters (256 bits without padding)
5. **signing_key**: Must be at least 32 bytes when provided via environment
