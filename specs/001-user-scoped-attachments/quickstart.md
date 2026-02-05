# Quickstart: User-Scoped Attachment Security

**Feature**: 001-user-scoped-attachments

## Overview

This feature adds cryptographic signed URLs to the attachment download system, ensuring that only the user who downloaded an attachment can access it.

## Configuration

### Production (Recommended)

Set a persistent signing key to ensure URLs remain valid across server restarts:

```bash
# Generate a secure 32-byte key (base64-encoded for easy handling)
export ATTACHMENT_SIGNING_KEY=$(openssl rand -base64 32)

# Start the server
python main.py
```

### Development (Zero Config)

No configuration needed. The server auto-generates a signing key at startup:

```bash
python main.py
# WARNING: Using auto-generated signing key; URLs will not survive restart
```

## How It Works

### 1. User Downloads Attachment

When a user requests an attachment via Gmail or Drive tools:

```
User A calls: download_gmail_attachment(message_id, attachment_id)
```

### 2. Server Generates Signed URL

The server creates a URL with cryptographic proof of ownership:

```
https://server/attachments/abc-123?sig=dGhpcyBpcw&exp=1707048600&uid=aGFzaGVk
                                    ^              ^              ^
                                    |              |              |
                            HMAC signature   Expiry time    Owner hash
```

### 3. User Accesses URL

When the user opens the URL:

1. Server verifies the signature matches the parameters
2. Server checks the URL hasn't expired (1 hour + 60s tolerance)
3. Server verifies the requesting user's email hash matches `uid`
4. If all pass, file is served

### 4. Other Users Denied

If User B tries to access User A's attachment URL:

```
403 Forbidden: Access denied: user mismatch
```

## URL Parameters

| Parameter | Description | Format |
|-----------|-------------|--------|
| `sig` | HMAC-SHA256 signature | base64url, 43 chars |
| `exp` | Expiry timestamp | Unix epoch seconds |
| `uid` | Owner email hash | base64url SHA-256, 43 chars |

## Error Responses

| Code | When | Message |
|------|------|---------|
| 401 | Missing sig/exp/uid | "Missing required parameters" |
| 403 | Bad signature | "Invalid signature" |
| 403 | Past expiry | "URL has expired" |
| 403 | Wrong user | "Access denied: user mismatch" |
| 404 | File deleted | "Attachment not found or expired" |

## Testing

### Manual Testing

1. Download an attachment as User A:
   ```
   # In MCP client as user-a@example.com
   download_gmail_attachment(message_id="abc", attachment_id="123")
   # Returns: https://server/attachments/xyz?sig=...&exp=...&uid=...
   ```

2. Access URL as User A (should work):
   ```bash
   curl -H "Authorization: Bearer <user-a-token>" \
        "https://server/attachments/xyz?sig=...&exp=...&uid=..."
   # 200 OK with file content
   ```

3. Access URL as User B (should fail):
   ```bash
   curl -H "Authorization: Bearer <user-b-token>" \
        "https://server/attachments/xyz?sig=...&exp=...&uid=..."
   # 403 Forbidden
   ```

### Automated Tests

```bash
# Run unit tests
pytest tests/test_url_signer.py -v

# Run integration tests
pytest tests/test_attachment_security.py -v
```

## Security Notes

1. **Key Management**: In production, store `ATTACHMENT_SIGNING_KEY` securely (e.g., Kubernetes secrets, AWS Secrets Manager)

2. **Key Rotation**: Rotating the signing key invalidates all existing URLs. Plan for this during maintenance windows.

3. **Logging**: Signature values are truncated in logs (first 8 chars only) to prevent leakage.

4. **Expiry**: URLs expire after 1 hour. Users must re-download if they need access later.
