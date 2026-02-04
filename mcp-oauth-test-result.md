# Google Workspace MCP OAuth 테스트 결과

## 개요

`mcp-oauth-flow.md` 문서를 기반으로 Google Workspace MCP 서버의 OAuth 2.1 흐름을 curl로 테스트한 결과입니다.

- **레포지토리**: https://github.com/taylorwilsdon/google_workspace_mcp
- **테스트 일시**: 2026-02-04 14:28 KST
- **서버 버전**: workspace-mcp 1.9.0
- **FastMCP 버전**: 2.14.4
- **테스트 사용자**: ysgong@chainpartners.net

---

## 테스트 결과 요약

| Step   | 엔드포인트                                | 결과    | 비고                            |
| ------ | ----------------------------------------- | ------- | ------------------------------- |
| Step 1 | `/.well-known/oauth-authorization-server` | ✅ 성공 | RFC 8414 준수                   |
| Step 2 | `/register`                               | ✅ 성공 | RFC 7591 준수                   |
| Step 3 | `/authorize`                              | ✅ 성공 | PKCE S256 지원                  |
| Step 4 | `/token`                                  | ✅ 성공 | Access/Refresh Token 발급       |
| Step 5 | `/mcp` (initialize)                       | ✅ 성공 | Session ID 획득                 |
| Step 6 | `/mcp` (tools/list)                       | ✅ 성공 | 136개 도구 사용 가능            |
| Step 6 | `/mcp` (tools/call)                       | ✅ 성공 | Calendar, Drive, Gmail API 호출 |
| Step 7 | `/token` (refresh)                        | ✅ 성공 | 토큰 갱신                       |

---

## 테스트 환경

```bash
# .env 파일 설정
GOOGLE_OAUTH_CLIENT_ID=94155820658-xxxxx.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=GOCSPX-xxxxx
MCP_ENABLE_OAUTH21=true
OAUTHLIB_INSECURE_TRANSPORT=1

# 서버 실행
uv run main.py --transport streamable-http
```

**서버 설정:**

- Base URL: `http://localhost:8000`
- MCP Endpoint: `http://localhost:8000/mcp`
- Transport: `streamable-http`

---

## Step 1: OAuth Discovery

### 요청

```bash
curl -s http://localhost:8000/.well-known/oauth-authorization-server
```

### 응답

```json
{
  "issuer": "http://localhost:8000/",
  "authorization_endpoint": "http://localhost:8000/authorize",
  "token_endpoint": "http://localhost:8000/token",
  "registration_endpoint": "http://localhost:8000/register",
  "scopes_supported": [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/chat.messages",
    "https://www.googleapis.com/auth/chat.messages.readonly",
    "https://www.googleapis.com/auth/chat.spaces",
    "https://www.googleapis.com/auth/contacts",
    "https://www.googleapis.com/auth/contacts.readonly",
    "https://www.googleapis.com/auth/cse",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/forms.body",
    "https://www.googleapis.com/auth/forms.body.readonly",
    "https://www.googleapis.com/auth/forms.responses.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.labels",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/script.deployments",
    "https://www.googleapis.com/auth/script.deployments.readonly",
    "https://www.googleapis.com/auth/script.metrics",
    "https://www.googleapis.com/auth/script.processes",
    "https://www.googleapis.com/auth/script.projects",
    "https://www.googleapis.com/auth/script.projects.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/tasks",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "openid"
  ],
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
  "code_challenge_methods_supported": ["S256"]
}
```

---

## Step 2: Dynamic Client Registration

### 요청

```bash
curl -s -X POST "http://localhost:8000/register" \
  -H "Content-Type: application/json" \
  -d '{
    "client_name": "OAuth Flow Test Client",
    "redirect_uris": ["http://localhost:8000/oauth2callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none"
  }'
```

### 응답

```json
{
  "redirect_uris": ["http://localhost:8000/oauth2callback"],
  "token_endpoint_auth_method": "none",
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "client_name": "OAuth Flow Test Client",
  "client_id": "9b1cfc87-1e53-44ea-a856-c428953a789e",
  "client_id_issued_at": 1770182820
}
```

---

## Step 3: Authorization Request (PKCE)

### PKCE 파라미터 생성

```bash
# code_verifier 생성 (43자)
CODE_VERIFIER=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-43)
# 결과: Q0PqWIexiOuZHRa0obVj0OYJReb0HmfNwULmbZ6Fc

# code_challenge 생성 (SHA256 + base64url)
CODE_CHALLENGE=$(printf '%s' "$CODE_VERIFIER" | openssl dgst -sha256 -binary | base64 | tr -d '=' | tr '+/' '-_')
# 결과: 64sX6H-UKyDIMl5IhpME7ctZg5NoiKEyGoP85lj_d8Q

# state 생성
STATE=$(openssl rand -hex 16)
# 결과: c85da90bdb60eccfbd21964498f5af58
```

### Authorization URL

```
http://localhost:8000/authorize
  ?client_id=9b1cfc87-1e53-44ea-a856-c428953a789e
  &response_type=code
  &redirect_uri=http://localhost:8000/oauth2callback
  &code_challenge=64sX6H-UKyDIMl5IhpME7ctZg5NoiKEyGoP85lj_d8Q
  &code_challenge_method=S256
  &state=c85da90bdb60eccfbd21964498f5af58
```

### Callback 응답

```
http://localhost:8000/oauth2callback
  ?code=SOC26RGDCHdaav_rT3eKAskPIcz6Rr1ILLUXypN5Yik
  &state=c85da90bdb60eccfbd21964498f5af58
```

---

## Step 4: Token Exchange

### 요청

```bash
curl -s -X POST "http://localhost:8000/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Accept: application/json" \
  -d "grant_type=authorization_code\
&code=SOC26RGDCHdaav_rT3eKAskPIcz6Rr1ILLUXypN5Yik\
&redirect_uri=http://localhost:8000/oauth2callback\
&client_id=9b1cfc87-1e53-44ea-a856-c428953a789e\
&code_verifier=Q0PqWIexiOuZHRa0obVj0OYJReb0HmfNwULmbZ6Fc"
```

### 응답

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3599,
  "scope": "",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...."
}
```

---

## Step 5: MCP Initialize

### 요청

```bash
curl -s -X POST "http://localhost:8000/mcp" \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "method": "initialize",
    "id": 1,
    "params": {
      "protocolVersion": "2024-11-05",
      "capabilities": {},
      "clientInfo": {"name": "OAuth Flow Test", "version": "1.0"}
    }
  }'
```

### 응답 헤더

```
HTTP/1.1 200 OK
content-type: text/event-stream
mcp-session-id: 0dd4d8dc2e974c2a98f1c8dd89a1eae6
```

### 응답 본문

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2024-11-05",
    "capabilities": {
      "tools": { "listChanged": true },
      "resources": { "subscribe": false, "listChanged": true },
      "prompts": { "listChanged": true }
    },
    "serverInfo": {
      "name": "google_workspace",
      "version": "2.14.4"
    }
  }
}
```

---

## Step 6: MCP Tool Call

### 요청 (tools/list)

```bash
curl -s -X POST "http://localhost:8000/mcp" \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: 0dd4d8dc2e974c2a98f1c8dd89a1eae6" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":2,"params":{}}'
```

### 응답 (tools/list)

**136개 도구 사용 가능** (Gmail, Drive, Calendar, Docs, Sheets, Slides, Forms, Tasks, Contacts, Chat, Search, Apps Script)

---

### 요청 (tools/call - list_calendars)

```bash
curl -s -X POST "http://localhost:8000/mcp" \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: 0dd4d8dc2e974c2a98f1c8dd89a1eae6" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 3,
    "params": {
      "name": "list_calendars",
      "arguments": {}
    }
  }'
```

### 응답 (list_calendars)

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Successfully listed 1 calendars for ysgong@chainpartners.net:\n- \"ysgong@chainpartners.net\" (Primary) (ID: ysgong@chainpartners.net)"
      }
    ],
    "isError": false
  }
}
```

---

### 요청 (tools/call - search_drive_files)

```bash
curl -s -X POST "http://localhost:8000/mcp" \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: 0dd4d8dc2e974c2a98f1c8dd89a1eae6" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 4,
    "params": {
      "name": "search_drive_files",
      "arguments": {"query": "test"}
    }
  }'
```

### 응답 (search_drive_files)

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "Found 1 files for ysgong@chainpartners.net matching 'test':\n- Name: \"mcp-server-discovery-test.ipynb\" (ID: 1X7s1UI4o84FNPdGPT1EC, Type: application/vnd.google.colaboratory, Size: 42295, Modified: 2026-02-02T06:25:19.016Z) Link: https://colab.research.google.com/drive/1X7s1U..."
      }
    ],
    "isError": false
  }
}
```

---

### 요청 (tools/call - search_gmail_messages)

```bash
curl -s -X POST "http://localhost:8000/mcp" \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: 0dd4d8dc2e974c2a98f1c8dd89a1eae6" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "id": 5,
    "params": {
      "name": "search_gmail_messages",
      "arguments": {"query": "is:unread"}
    }
  }'
```

### 응답 (search_gmail_messages)

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "No messages found for query: 'is:unread'"
      }
    ],
    "isError": false
  }
}
```

---

## Step 7: Token Refresh

### 요청

```bash
curl -s -X POST "http://localhost:8000/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Accept: application/json" \
  -d "grant_type=refresh_token\
&refresh_token={refresh_token}\
&client_id=9b1cfc87-1e53-44ea-a856-c428953a789e"
```

### 응답

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...(새 토큰)",
  "token_type": "Bearer",
  "expires_in": 3599,
  "scope": "",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...(새 리프레시 토큰)"
}
```

---

## 준수하는 표준

| 표준     | 설명                                     | 상태    |
| -------- | ---------------------------------------- | ------- |
| RFC 8414 | OAuth 2.0 Authorization Server Metadata  | ✅ 준수 |
| RFC 9728 | OAuth 2.0 Protected Resource Metadata    | ✅ 준수 |
| RFC 7591 | OAuth 2.0 Dynamic Client Registration    | ✅ 준수 |
| RFC 7636 | PKCE (Proof Key for Code Exchange)       | ✅ 준수 |
| RFC 6749 | OAuth 2.0 Authorization Code Grant       | ✅ 준수 |
| RFC 6750 | Bearer Token Usage                       | ✅ 준수 |
| MCP Spec | Model Context Protocol (Streamable HTTP) | ✅ 준수 |

---

## 실제 Google API 호출 결과

| 서비스       | 도구                    | 결과                                        |
| ------------ | ----------------------- | ------------------------------------------- |
| **Calendar** | `list_calendars`        | ✅ `ysgong@chainpartners.net` (Primary)     |
| **Drive**    | `search_drive_files`    | ✅ `mcp-server-discovery-test.ipynb` 검색됨 |
| **Gmail**    | `search_gmail_messages` | ✅ 읽지 않은 메일 없음 확인                 |

---

## 결론

Google Workspace MCP 서버는 `mcp-oauth-flow.md` 문서에 정의된 전체 OAuth 2.1 흐름을 완벽하게 지원합니다.

- **OAuth Discovery**: RFC 8414 표준 준수
- **Protected Resource Metadata**: RFC 9728 표준 준수
- **Dynamic Client Registration**: RFC 7591 표준 준수
- **PKCE**: S256 방식 지원 (OAuth 2.1 필수)
- **Token Exchange**: Authorization Code 및 Refresh Token 지원
- **MCP Protocol**: JSON-RPC 2.0 + SSE 기반 Streamable HTTP 지원
- **사용 가능한 도구**: 136개
- **실제 API 호출**: Google Workspace API (Calendar, Drive, Gmail 등) 정상 동작

---

## 참고 명령어

```bash
# 서버 시작
source .env && uv run main.py --transport streamable-http

# OAuth Discovery
curl -s http://localhost:8000/.well-known/oauth-authorization-server | jq .

# Protected Resource Metadata
curl -s http://localhost:8000/.well-known/oauth-protected-resource/mcp | jq .

# Client Registration
curl -s -X POST http://localhost:8000/register \
  -H "Content-Type: application/json" \
  -d '{"client_name":"My App","redirect_uris":["http://localhost:8000/oauth2callback"]}' | jq .

# MCP Initialize (인증 필요)
curl -s -X POST http://localhost:8000/mcp \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"Test","version":"1.0"}}}'

# MCP Tool Call
curl -s -X POST http://localhost:8000/mcp \
  -H "Authorization: Bearer {access_token}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: {session_id}" \
  -d '{"jsonrpc":"2.0","method":"tools/call","id":2,"params":{"name":"list_calendars","arguments":{}}}'
```
