# Research: User-Scoped Attachment Security

**Feature**: 001-user-scoped-attachments
**Date**: 2026-02-04

## Research Questions Resolved

### 1. HMAC-SHA256 Implementation in Python

**Question**: What is the recommended way to implement HMAC-SHA256 in Python?

**Finding**: Python's `hmac` module (stdlib) is the standard approach. The `cryptography` library (already a dependency) can also be used but `hmac` is simpler for this use case.

**Code Pattern**:
```python
import hmac
import hashlib
import base64

def sign(key: bytes, message: str) -> str:
    signature = hmac.new(key, message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(signature).decode().rstrip("=")

def verify(key: bytes, message: str, signature: str) -> bool:
    expected = sign(key, message)
    return hmac.compare_digest(expected, signature)
```

**Decision**: Use stdlib `hmac` module for simplicity and zero additional dependencies.

---

### 2. Secure Random Key Generation

**Question**: How to generate cryptographically secure random keys in Python?

**Finding**: Python's `secrets` module (stdlib, Python 3.6+) is designed for this purpose.

**Code Pattern**:
```python
import secrets

signing_key = secrets.token_bytes(32)  # 256-bit key
```

**Decision**: Use `secrets.token_bytes(32)` for auto-generated keys.

---

### 3. URL-Safe Base64 Encoding

**Question**: What encoding to use for signatures and hashes in URLs?

**Finding**: Standard base64 uses `+` and `/` which are not URL-safe. Use `base64.urlsafe_b64encode()` which uses `-` and `_` instead.

**Code Pattern**:
```python
import base64

# Encode
encoded = base64.urlsafe_b64encode(data).decode().rstrip("=")

# Decode (add padding back)
padded = encoded + "=" * (4 - len(encoded) % 4)
decoded = base64.urlsafe_b64decode(padded)
```

**Decision**: Use base64url encoding without padding for shorter URLs.

---

### 4. Existing User Context in Codebase

**Question**: How is user identity currently propagated in the MCP server?

**Finding**: The codebase has `AuthInfoMiddleware` in `auth/auth_info_middleware.py` that extracts user info from OAuth tokens and injects it into the FastMCP context. The `@require_google_service` decorator in `auth/service_decorator.py` provides access to credentials.

**Relevant Code** (from auth patterns):
- `request.state.user_email` - set by session middleware
- Context can be accessed in tool handlers via FastMCP's context mechanism

**Decision**: Use existing auth middleware patterns. Extract user email from context in gmail_tools.py and drive_tools.py.

---

### 5. Clock Skew Tolerance Implementation

**Question**: How to implement 60-second tolerance for expiry checks?

**Finding**: Simple arithmetic comparison with tolerance:

**Code Pattern**:
```python
import time

CLOCK_SKEW_TOLERANCE = 60  # seconds

def is_expired(expiry_timestamp: int) -> bool:
    return time.time() > expiry_timestamp + CLOCK_SKEW_TOLERANCE
```

**Decision**: Add 60 seconds to expiry timestamp before comparison.

---

### 6. Signature Message Format

**Question**: What data should be included in the signed message?

**Finding**: Best practice is to sign all parameters that affect authorization:
- file_id (which file)
- owner_hash (who owns it)
- expiry (when it expires)

**Format**: `{file_id}:{owner_hash}:{expiry}`

**Rationale**: Colon-separated is simple and unambiguous since none of these fields contain colons.

**Decision**: Sign concatenated string `f"{file_id}:{owner_hash}:{expiry}"`.

---

### 7. Environment Variable Key Length Validation

**Question**: How to validate environment variable key length?

**Finding**: Check byte length, not character length (for UTF-8 safety):

**Code Pattern**:
```python
key = os.getenv("ATTACHMENT_SIGNING_KEY", "")
if key:
    key_bytes = key.encode("utf-8")
    if len(key_bytes) < 32:
        raise ValueError("ATTACHMENT_SIGNING_KEY must be at least 32 bytes")
```

**Decision**: Validate at startup, fail fast with clear error message.

---

## Best Practices Applied

1. **Constant-time comparison**: Use `hmac.compare_digest()` to prevent timing attacks
2. **No logging of secrets**: Log only first 8 characters of signatures for debugging
3. **Fail closed**: Any verification error results in access denial
4. **Defense in depth**: Check signature AND owner match AND expiry
