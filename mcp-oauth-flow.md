# MCP OAuth Flow 문서

## 개요

이 문서는 Remote MCP(Model Context Protocol) 서버 연결 시 OAuth 인증 흐름을 설명합니다.
OAuth 표준, MCP 표준, Provider별 구현(Notion, Atlassian 등)을 구분하여 정리합니다.

---

## 전체 Flow 다이어그램

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  INPUT: MCP 서버 URL만 있음                                                      │
│         예: https://mcp.notion.com/mcp                                          │
│             https://mcp.atlassian.com/v1/mcp                                    │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 1. OAuth Discovery                                                         │
│  GET {new URL(mcp_url).origin}/.well-known/oauth-authorization-server           │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 2. Dynamic Client Registration                                             │
│  POST {registration_endpoint}                                                    │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 3. Authorization Request (사용자 인증)                                     │
│  GET {authorization_endpoint}?client_id=...&code_challenge=...                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 4. Token Exchange                                                          │
│  POST {token_endpoint}                                                           │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 5. MCP Initialize                                                          │
│  POST {mcp_url} + Authorization: Bearer {access_token}                          │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 6. MCP Tool Call                                                           │
│  POST {mcp_url} + Mcp-Session-Id: {session_id}                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│  Step 7. Token Refresh (만료 시)                                                 │
│  POST {token_endpoint} + grant_type=refresh_token                               │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Step별 상세 설명

### Step 1: OAuth Discovery

MCP 서버 URL에서 OAuth 메타데이터 엔드포인트를 도출하고 조회합니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `mcp_url` | 사용자 입력 또는 DB | MCP 서버 URL |

#### Discovery URL 생성 과정

RFC 8414 표준에 따라 `.well-known`은 **항상 origin 루트**에 위치합니다.

```javascript
// 1. mcp_url에서 origin 추출 (scheme + host만, 경로는 전부 제거)
const mcpUrl = "https://mcp.atlassian.com/v1/mcp";
const origin = new URL(mcpUrl).origin;  // "https://mcp.atlassian.com"

// 2. .well-known 경로 붙이기
const discoveryUrl = `${origin}/.well-known/oauth-authorization-server`;
// → "https://mcp.atlassian.com/.well-known/oauth-authorization-server"
```

| mcp_url | discovery_url |
|---------|---------------|
| `https://mcp.notion.com/mcp` | `https://mcp.notion.com/.well-known/oauth-authorization-server` |
| `https://mcp.atlassian.com/v1/mcp` | `https://mcp.atlassian.com/.well-known/oauth-authorization-server` |

#### 요청

```http
GET {discovery_url}
```

#### 응답 예시

**Notion:**
```json
{
  "issuer": "https://mcp.notion.com",
  "authorization_endpoint": "https://mcp.notion.com/authorize",
  "token_endpoint": "https://mcp.notion.com/token",
  "registration_endpoint": "https://mcp.notion.com/register",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
  "code_challenge_methods_supported": ["plain", "S256"]
}
```

**Atlassian:**
```json
{
  "issuer": "https://cf.mcp.atlassian.com",
  "authorization_endpoint": "https://mcp.atlassian.com/v1/authorize",
  "token_endpoint": "https://cf.mcp.atlassian.com/v1/token",
  "registration_endpoint": "https://cf.mcp.atlassian.com/v1/register",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
  "code_challenge_methods_supported": ["plain", "S256"]
}
```

#### 획득하는 정보 (다음 Step에서 사용)

| 정보 | 용도 |
|------|------|
| `authorization_endpoint` | Step 3에서 사용 |
| `token_endpoint` | Step 4, 7에서 사용 |
| `registration_endpoint` | Step 2에서 사용 |
| `code_challenge_methods_supported` | Step 3 PKCE 방식 결정 |

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔷 OAuth | RFC 8414 - OAuth 2.0 Authorization Server Metadata |
| 🔶 MCP | Remote MCP 서버는 이 엔드포인트 제공 필수 |

#### Fallback: Protected Resource Metadata (RFC 9728)

일부 MCP 서버(GitHub 등)는 MCP 서버 도메인에서 직접 `.well-known/oauth-authorization-server`를 제공하지 않습니다.
이 경우 **Protected Resource Metadata**를 통해 Authorization Server 위치를 찾아야 합니다.

**Discovery 우선순위:**

1. **1차 시도**: `{origin}/.well-known/oauth-authorization-server` 직접 조회
2. **1차 실패 시**: Protected Resource Metadata에서 `authorization_servers` 추출 후 해당 URL에서 조회

**Protected Resource Metadata 조회:**

```http
GET {origin}/.well-known/oauth-protected-resource{path}
```

**GitHub 예시:**
```
GET https://api.githubcopilot.com/.well-known/oauth-protected-resource/mcp/
```

```json
{
  "resource": "https://api.githubcopilot.com/mcp/",
  "authorization_servers": ["https://github.com/login/oauth"],
  "scopes_supported": ["repo", "user", "gist", ...]
}
```

**Authorization Server Metadata 조회 (RFC 8414):**

`authorization_servers`에서 얻은 URL로 Authorization Server Metadata를 조회합니다.

```
GET https://github.com/.well-known/oauth-authorization-server/login/oauth
```

```json
{
  "issuer": "https://github.com/login/oauth",
  "authorization_endpoint": "https://github.com/login/oauth/authorize",
  "token_endpoint": "https://github.com/login/oauth/access_token",
  "code_challenge_methods_supported": ["S256"]
}
```

**서비스별 Discovery 방식:**

| 서비스 | 1차 (직접 조회) | 2차 (Protected Resource) | Authorization Server |
|--------|----------------|-------------------------|---------------------|
| Notion | ✅ 성공 | ✅ 있음 | `mcp.notion.com` (동일) |
| Atlassian | ✅ 성공 | ❌ 없음 | `mcp.atlassian.com` (동일) |
| Figma | ✅ 성공 | ✅ 있음 | `api.figma.com` (다름) |
| GitHub | ❌ 404 | ✅ 있음 | `github.com/login/oauth` (다름) |

---

### Step 2: Dynamic Client Registration

클라이언트를 동적으로 등록하여 `client_id`를 발급받습니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `registration_endpoint` | Step 1 응답 | 클라이언트 등록 URL |
| `redirect_uri` | 프론트엔드 콜백 페이지 URL | 인증 완료 후 code를 받을 페이지 |
| `client_name` | 서비스 설정 (고정값) | OAuth 인증 화면에 표시되는 앱 이름 (예: "WRKS") |

#### redirect_uri 설명

`redirect_uri`는 OAuth 인증 완료 후 **브라우저가 리다이렉트되는 URL**입니다.

인증 완료 시 이 URL로 `code`와 `state`가 쿼리 파라미터로 전달됩니다:
```
https://app.wrks.com/mcp/callback?code=abc123&state=xyz789
```

**팝업 방식 필수**: 채팅 페이지에서 OAuth 진행 시 페이지가 이동하면 안 되므로, 팝업(새 창)으로 열어야 합니다.

```javascript
// 채팅창에서 팝업으로 OAuth 시작
const popup = window.open(authUrl, 'mcp-oauth', 'width=600,height=700');

// 팝업의 콜백 페이지에서 code 수신 후 부모 창으로 전달
// (콜백 페이지)
window.opener.postMessage({ code, state }, window.location.origin);
window.close();

// (채팅창) 메시지 수신
window.addEventListener('message', (event) => {
  const { code, state } = event.data;
  // 백엔드로 code 전송하여 토큰 교환
});
```

**주의**: redirect_uri는 채팅 페이지와 **같은 도메인**이어야 `postMessage` 통신이 가능합니다.

#### client_name 설명

`client_name`은 **OAuth 인증 화면에서 사용자에게 보여지는 앱 이름**입니다.
- 예: "WRKS가 Notion에 접근하려고 합니다. 허용하시겠습니까?"
- 로직에 영향 없음 - 단순 표시용
- 백엔드에서 하드코딩 또는 환경변수로 고정 (예: `MCP_CLIENT_NAME=WRKS`)

#### 요청

```http
POST {registration_endpoint}
Content-Type: application/json

{
  "client_name": "My MCP Client",
  "redirect_uris": ["{redirect_uri}"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

**Notion 예시:**
```http
POST https://mcp.notion.com/register
Content-Type: application/json

{
  "client_name": "My MCP Client",
  "redirect_uris": ["https://my-backend.com/mcp/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

**Atlassian 예시:**
```http
POST https://cf.mcp.atlassian.com/v1/register
Content-Type: application/json

{
  "client_name": "My MCP Client",
  "redirect_uris": ["https://my-backend.com/mcp/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

#### 응답 예시

```json
{
  "client_id": "P21GEVNxkfAwYm8i",
  "redirect_uris": ["https://my-backend.com/mcp/callback"],
  "client_name": "My MCP Client",
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none",
  "client_id_issued_at": 1768804934
}
```

#### 획득하는 정보 (다음 Step에서 사용)

| 정보 | 용도 | 저장 위치 |
|------|------|----------|
| `client_id` | Step 3, 4, 7에서 사용 | DB (mcp_servers.oauth) |
| `redirect_uris` | Step 3에서 사용 | 환경변수 (MCP_OAUTH_REDIRECT_URI) |

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔷 OAuth | RFC 7591 - OAuth 2.0 Dynamic Client Registration |
| 🔶 MCP | `registration_endpoint` 있으면 사용 권장 |

**참고**: `token_endpoint_auth_method: "none"`은 Public Client로 등록됨 (client_secret 없음)

---

### Step 3: Authorization Request

사용자를 인증 페이지로 리다이렉트합니다. PKCE를 필수로 사용합니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `authorization_endpoint` | Step 1 응답 | 인증 URL |
| `client_id` | Step 2 응답 | 클라이언트 ID |
| `redirect_uri` | Step 2 요청 시 설정 | 콜백 URL |
| `code_verifier` | 서버에서 생성 | PKCE용 랜덤 문자열 (43-128자) |
| `code_challenge` | `code_verifier`에서 계산 | SHA256(code_verifier)의 base64url |
| `state` | 서버에서 생성 | CSRF 방지용 랜덤 값 |

#### PKCE 생성

```javascript
// code_verifier: 43-128자의 랜덤 문자열
const codeVerifier = generateRandomString(43);

// code_challenge: code_verifier의 SHA256 해시 (base64url)
const codeChallenge = base64url(sha256(codeVerifier));

// code_verifier는 Step 4에서 사용하므로 임시 저장 필요 (Redis 등)
await redis.set(`pkce:${state}`, codeVerifier, 'EX', 600); // 10분 TTL
```

#### 요청 URL

```
{authorization_endpoint}
  ?client_id={client_id}
  &response_type=code
  &redirect_uri={redirect_uri}
  &code_challenge={code_challenge}
  &code_challenge_method=S256
  &state={state}
```

**Notion 예시:**
```
https://mcp.notion.com/authorize
  ?client_id=P21GEVNxkfAwYm8i
  &response_type=code
  &redirect_uri=https://my-backend.com/mcp/callback
  &code_challenge=RToftljHmGH9a4ec01caq_2Lg_9uGpwgkQdHxuBuR9M
  &code_challenge_method=S256
  &state=random_state_value
```

**Atlassian 예시:**
```
https://mcp.atlassian.com/v1/authorize
  ?client_id=_4yjboLI9WRpD8Zs
  &response_type=code
  &redirect_uri=https://my-backend.com/mcp/callback
  &code_challenge=Cy-ti4x-USLtlaPB2hsFqOfhrGV4nZ-HmD9eaCGChpg
  &code_challenge_method=S256
  &state=random_state_value
```

#### 리다이렉트 응답 (인증 완료 후)

```
{redirect_uri}?code={authorization_code}&state={state}
```

예시:
```
https://my-backend.com/mcp/callback?code=xxx&state=random_state_value
```

#### 획득하는 정보 (다음 Step에서 사용)

| 정보 | 용도 |
|------|------|
| `code` (authorization_code) | Step 4에서 사용 |
| `state` | CSRF 검증 및 code_verifier 조회 키 |

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔷 OAuth | RFC 6749 - Authorization Code Grant |
| 🔷 OAuth | RFC 7636 - PKCE (Proof Key for Code Exchange) |
| 🔶 MCP | PKCE 필수 (S256 권장) |
| 🟠 Notion | `mcp.notion.com/authorize` → `notion.so/install-integration`으로 리다이렉트 |
| 🟠 Atlassian | `mcp.atlassian.com/v1/authorize` → Atlassian 로그인 페이지로 리다이렉트 |

---

### Step 4: Token Exchange

Authorization code를 access_token으로 교환합니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `token_endpoint` | Step 1 응답 | 토큰 교환 URL |
| `code` | Step 3 콜백 | Authorization code |
| `redirect_uri` | Step 2 요청 시 설정 | 콜백 URL (동일해야 함) |
| `client_id` | Step 2 응답 | 클라이언트 ID |
| `code_verifier` | Step 3에서 저장 | PKCE 원본 값 |

#### 요청

```http
POST {token_endpoint}
Content-Type: application/x-www-form-urlencoded
Accept: application/json

grant_type=authorization_code
&code={code}
&redirect_uri={redirect_uri}
&client_id={client_id}
&code_verifier={code_verifier}
```

> ⚠️ **중요**: `Accept: application/json` 헤더 필수
>
> GitHub 등 일부 서비스는 이 헤더가 없으면 `application/x-www-form-urlencoded` 형식으로 응답합니다:
> ```
> access_token=gho_xxx&token_type=bearer&scope=repo
> ```
> JSON 응답을 받으려면 반드시 `Accept: application/json` 헤더를 포함해야 합니다.

**Notion 예시:**
```http
POST https://mcp.notion.com/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=3a44b6fd-9738-4c2f-b085-19c868810177:DlxXE81AdnjfCZE5:pQBerXE676bA1J5aBLVpaGpJYTydHGeD
&redirect_uri=https://my-backend.com/mcp/callback
&client_id=P21GEVNxkfAwYm8i
&code_verifier=2txUGjWaIVB0hDfoxAoGCJoM9djUH8qDawiCydZMMg
```

**Atlassian 예시:**
```http
POST https://cf.mcp.atlassian.com/v1/token
Content-Type: application/x-www-form-urlencoded

grant_type=authorization_code
&code=712020-13d6d4be-aedc-401a-b8c0-abe65844f086:dS62yRrIapWDetQD:d9XuNWssgfmNN7Vy3wb9GbUsswL0Hxw2
&redirect_uri=https://my-backend.com/mcp/callback
&client_id=_4yjboLI9WRpD8Zs
&code_verifier=rbsEyNwFCW8A1L9oqiwu9Wn9laJN1iYIhIFIvjPBo
```

#### 응답 예시

**Notion:**
```json
{
  "access_token": "3a44b6fd-...:DlxXE81A...:FfgkOIOj...",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "3a44b6fd-...:DlxXE81A...:kRZWDDTP...",
  "scope": ""
}
```

**Atlassian:**
```json
{
  "access_token": "712020-...:dS62yRrI...:mdeT2hXa...",
  "token_type": "bearer",
  "expires_in": 3300,
  "refresh_token": "712020-...:dS62yRrI...:4o4aRp1K...",
  "scope": ""
}
```

#### 획득하는 정보 (저장 필요)

| 정보 | 용도 | 저장 위치 |
|------|------|----------|
| `access_token` | Step 5, 6에서 사용 | DB (mcp_oauth_connections), 암호화 |
| `refresh_token` | Step 7에서 사용 | DB (mcp_oauth_connections), 암호화 |
| `expires_in` | 만료 시간 계산 | DB (expires_at = now + expires_in) |

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔷 OAuth | RFC 6749 - Token Request |
| 🔷 OAuth | RFC 7636 - PKCE (code_verifier 검증) |
| 🟠 Notion | 토큰 형식: `{user_id}:{session}:{token}`, 만료: 3600초 (1시간) |
| 🟠 Atlassian | 토큰 형식: `{user_id}:{session}:{token}`, 만료: 3300초 (55분) |
| 🟠 GitHub | 기본 응답 `application/x-www-form-urlencoded`, `Accept: application/json` 필수 |

---

### Step 5: MCP Initialize

MCP 서버와 연결을 초기화합니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `mcp_url` | 사용자 입력 또는 DB | MCP 서버 URL |
| `access_token` | Step 4 응답 | Bearer 토큰 |

#### 요청

```http
POST {mcp_url}
Authorization: Bearer {access_token}
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "method": "initialize",
  "id": 1,
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {
      "name": "My Client",
      "version": "1.0"
    }
  }
}
```

**Notion 예시:**
```http
POST https://mcp.notion.com/mcp
Authorization: Bearer 3a44b6fd-...:DlxXE81A...:FfgkOIOj...
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "method": "initialize",
  "id": 1,
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "My Client", "version": "1.0"}
  }
}
```

**Atlassian 예시:**
```http
POST https://mcp.atlassian.com/v1/mcp
Authorization: Bearer 712020-...:dS62yRrI...:mdeT2hXa...
Content-Type: application/json
Accept: application/json, text/event-stream

{
  "jsonrpc": "2.0",
  "method": "initialize",
  "id": 1,
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "My Client", "version": "1.0"}
  }
}
```

#### 응답 예시 (SSE 형식)

**Notion:**
```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{"listChanged":true},"resources":{"listChanged":true}},"serverInfo":{"name":"Notion MCP","version":"1.0.1"}}}
```

**Atlassian:**
```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05","capabilities":{"logging":{},"tools":{"listChanged":true},"resources":{}},"serverInfo":{"name":"atlassian-mcp-server","version":"1.0.0"}}}
```

#### 응답 헤더에서 Session ID 추출

```
Mcp-Session-Id: dc0cfb612cc3c7ef9c4c8dbfa3ae5d8e23a22fb68026dd82041f770c3140f78b
```

#### 획득하는 정보 (다음 Step에서 사용)

| 정보 | 용도 | 저장 위치 |
|------|------|----------|
| `Mcp-Session-Id` (응답 헤더) | Step 6에서 사용 | 메모리/Redis (선택적) |
| `serverInfo` | 서버 정보 표시 | - |
| `capabilities` | 지원 기능 확인 | - |

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔷 OAuth | RFC 6750 - Bearer Token Usage |
| 🔶 MCP | JSON-RPC 2.0 프로토콜 |
| 🔶 MCP | Streamable HTTP Transport |
| 🔶 MCP | `Accept: application/json, text/event-stream` 필수 |
| 🔶 MCP | `Mcp-Session-Id` 헤더 (응답에서 받아 이후 요청에 사용) |

---

### Step 6: MCP Tool Call

MCP 도구를 호출합니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `mcp_url` | 사용자 입력 또는 DB | MCP 서버 URL |
| `access_token` | Step 4 응답 (DB 저장) | Bearer 토큰 |
| `session_id` | Step 5 응답 헤더 | MCP 세션 ID |

#### 도구 목록 조회

```http
POST {mcp_url}
Authorization: Bearer {access_token}
Content-Type: application/json
Accept: application/json, text/event-stream
Mcp-Session-Id: {session_id}

{
  "jsonrpc": "2.0",
  "method": "tools/list",
  "id": 2,
  "params": {}
}
```

#### 도구 실행

```http
POST {mcp_url}
Authorization: Bearer {access_token}
Content-Type: application/json
Accept: application/json, text/event-stream
Mcp-Session-Id: {session_id}

{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 3,
  "params": {
    "name": "{tool_name}",
    "arguments": { ... }
  }
}
```

**Notion 예시 (검색):**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 3,
  "params": {
    "name": "notion-search",
    "arguments": {
      "query": "검색어",
      "query_type": "internal"
    }
  }
}
```

**Atlassian 예시 (사용자 정보):**
```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "id": 3,
  "params": {
    "name": "atlassianUserInfo",
    "arguments": {}
  }
}
```

#### 주요 도구 목록

**Notion MCP:**
- `notion-search` - Notion 검색
- `notion-fetch` - 페이지/DB 조회
- `notion-create-page` - 페이지 생성
- `notion-update-page` - 페이지 수정
- `notion-create-database` - 데이터베이스 생성

**Atlassian MCP:**
- `atlassianUserInfo` - 사용자 정보
- `getAccessibleAtlassianResources` - 접근 가능한 리소스 목록
- `getConfluencePage` - Confluence 페이지 조회
- `searchJiraIssues` - Jira 이슈 검색
- `createJiraIssue` - Jira 이슈 생성
- `updateJiraIssue` - Jira 이슈 수정

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔶 MCP | `tools/list` - 사용 가능한 도구 목록 |
| 🔶 MCP | `tools/call` - 도구 실행 |
| 🔶 MCP | `resources/list`, `resources/read` - 리소스 관련 |
| 🟠 Notion | `notion-*` 전용 도구 |
| 🟠 Atlassian | `*Jira*`, `*Confluence*`, `atlassian*` 전용 도구 |

---

### Step 7: Token Refresh

Access token이 만료되면 refresh token으로 갱신합니다.

#### 필요한 정보

| 정보 | 출처 | 설명 |
|------|------|------|
| `token_endpoint` | Step 1 응답 (DB 캐시 가능) | 토큰 교환 URL |
| `refresh_token` | Step 4 응답 (DB 저장) | 갱신 토큰 |
| `client_id` | Step 2 응답 (DB 저장) | 클라이언트 ID |

#### 요청

```http
POST {token_endpoint}
Content-Type: application/x-www-form-urlencoded
Accept: application/json

grant_type=refresh_token
&refresh_token={refresh_token}
&client_id={client_id}
```

> ⚠️ **중요**: Step 4와 동일하게 `Accept: application/json` 헤더 필수 (GitHub 등)

**Notion 예시:**
```http
POST https://mcp.notion.com/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json

grant_type=refresh_token
&refresh_token=3a44b6fd-...:DlxXE81A...:kRZWDDTP...
&client_id=P21GEVNxkfAwYm8i
```

**Atlassian 예시:**
```http
POST https://cf.mcp.atlassian.com/v1/token
Content-Type: application/x-www-form-urlencoded
Accept: application/json

grant_type=refresh_token
&refresh_token=712020-...:dS62yRrI...:4o4aRp1K...
&client_id=_4yjboLI9WRpD8Zs
```

#### 응답 예시

```json
{
  "access_token": "새로운_access_token",
  "token_type": "bearer",
  "expires_in": 3600,
  "refresh_token": "새로운_refresh_token"
}
```

#### 갱신 후 저장

| 정보 | 저장 위치 |
|------|----------|
| 새 `access_token` | DB (mcp_oauth_connections) 업데이트 |
| 새 `refresh_token` | DB (mcp_oauth_connections) 업데이트 |
| 새 `expires_at` | DB (now + expires_in) |

#### 표준 구분

| 구분 | 표준 |
|------|------|
| 🔷 OAuth | RFC 6749 - Refresh Token Grant |
| 🟠 Notion | 1시간(3600초)마다 갱신 필요 |
| 🟠 Atlassian | 55분(3300초)마다 갱신 필요 |
| 🟠 GitHub | Step 4와 동일, `Accept: application/json` 필수 |

---

## 데이터 흐름 요약

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           데이터 흐름 및 저장 위치                                │
└─────────────────────────────────────────────────────────────────────────────────┘

Step 1: Discovery
  INPUT:  mcp_url (DB 또는 사용자 입력)
  OUTPUT: authorization_endpoint, token_endpoint, registration_endpoint
  저장:   캐시 가능 (Redis, 1일 TTL)

Step 2: Client Registration
  INPUT:  registration_endpoint (Step 1), redirect_uri (환경변수)
  OUTPUT: client_id, client_secret (선택)
  저장:   DB (mcp_servers.oauth 컬럼)

Step 3: Authorization
  INPUT:  authorization_endpoint (Step 1), client_id (Step 2), redirect_uri
  생성:   code_verifier, code_challenge, state
  저장:   code_verifier → Redis (state 키, 10분 TTL)
  OUTPUT: authorization_code (콜백 URL 파라미터)

Step 4: Token Exchange
  INPUT:  token_endpoint (Step 1), code (Step 3), client_id (Step 2),
          code_verifier (Redis), redirect_uri
  OUTPUT: access_token, refresh_token, expires_in
  저장:   DB (mcp_oauth_connections 테이블, 암호화)

Step 5: MCP Initialize
  INPUT:  mcp_url, access_token (DB)
  OUTPUT: session_id (응답 헤더)
  저장:   선택적 (메모리 또는 Redis)

Step 6: Tool Call
  INPUT:  mcp_url, access_token (DB), session_id
  OUTPUT: 도구 실행 결과

Step 7: Token Refresh
  INPUT:  token_endpoint, refresh_token (DB), client_id (DB)
  OUTPUT: 새 access_token, refresh_token
  저장:   DB 업데이트
```

---

## 표준 구분 요약

| Step | 🔷 OAuth 표준 | 🔶 MCP 표준 | 🟠 Provider 전용 |
|------|-------------|------------|-----------------|
| **1. Discovery** | RFC 8414 | 필수 제공 | - |
| **2. Client Registration** | RFC 7591 | 권장 | - |
| **3. Authorization** | RFC 6749 + RFC 7636 | PKCE 필수 | 리다이렉트 방식 |
| **4. Token Exchange** | RFC 6749 | - | 토큰 형식, 만료시간 |
| **5. MCP Initialize** | RFC 6750 (Bearer) | JSON-RPC 2.0, Session-Id | - |
| **6. Tool Call** | - | tools/list, tools/call | 도구 목록 |
| **7. Token Refresh** | RFC 6749 | - | 갱신 주기 |

### 범례

- 🔷 **OAuth 표준**: RFC 문서로 정의된 표준 (모든 OAuth 서버 동일)
- 🔶 **MCP 표준**: MCP 스펙으로 정의 (modelcontextprotocol.io)
- 🟠 **Provider 전용**: 각 MCP 서버 구현체별 차이 (Notion, Atlassian 등)

---

## Provider별 비교 (테스트 완료)

| 항목 | Notion | Atlassian (Jira/Confluence) | GitHub |
|------|--------|----------------------------|--------|
| **MCP URL** | `mcp.notion.com/mcp` | `mcp.atlassian.com/v1/mcp` | `api.githubcopilot.com/mcp/` |
| **Discovery URL** | `mcp.notion.com/.well-known/oauth-authorization-server` | `mcp.atlassian.com/.well-known/oauth-authorization-server` | Protected Resource Metadata 경유 |
| **Token Endpoint** | `mcp.notion.com/token` | `cf.mcp.atlassian.com/v1/token` | `github.com/login/oauth/access_token` |
| **Dynamic Registration** | ✅ 지원 | ✅ 지원 | ❌ 미지원 (수동 등록 필요) |
| **redirect_uri** | 자유 설정 | 자유 설정 | GitHub OAuth App에서 사전 설정 필요 |
| **Token 만료** | 3600초 (1시간) | 3300초 (55분) | 8시간 |
| **Refresh Token** | ✅ 있음 | ✅ 있음 | ✅ 있음 |
| **토큰 형식** | `{user}:{session}:{token}` | `{user}:{session}:{token}` | `gho_xxxx` |
| **Session ID 헤더** | `Mcp-Session-Id` | `Mcp-Session-Id` | `Mcp-Session-Id` |
| **특이사항** | - | - | `Accept: application/json` 필수 |

---

## DB 스키마 설계

> **중요**: 이 프로젝트는 **MySQL + PostgreSQL 이중 DB 구조**를 사용합니다.
> - **MySQL**: users, workspaces, mcp_servers 등 핵심 비즈니스 데이터
> - **PostgreSQL**: ai_chats, ai_messages, vectorstores 등 AI/벡터 데이터
>
> MCP OAuth 관련 테이블은 **MySQL**에 생성합니다 (mcp_servers와 FK 관계).

### mcp_servers (기존 테이블 수정 - MySQL)

```sql
-- 기존 테이블 (src/libs/entity/mcp.entity.ts)
-- oauth 컬럼 추가
ALTER TABLE mcp_servers
ADD COLUMN oauth JSON NULL COMMENT 'OAuth 설정';
```

**oauth 컬럼 구조:**
```typescript
interface McpServerOAuthConfig {
  required: boolean;           // OAuth 필수 여부
  provider?: string;           // OAuth provider 식별자 (notion, github 등)

  // OAuth Client 정보 (1:1 관계이므로 별도 테이블 불필요)
  clientId?: string;
  clientSecret?: string;       // 암호화 저장

  // Discovery 캐시 (선택)
  authorizationEndpoint?: string;
  tokenEndpoint?: string;
}
```

### mcp_oauth_connections (신규 - MySQL)

사용자별 OAuth 연결 상태 저장

```sql
CREATE TABLE mcp_oauth_connections (
    id INT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id INT UNSIGNED NOT NULL,
    mcp_server_id INT UNSIGNED NOT NULL,

    -- 토큰 (암호화 저장)
    access_token TEXT NOT NULL,
    refresh_token TEXT NULL,
    expires_at DATETIME(6) NULL,

    -- MCP 세션 (선택적)
    session_id VARCHAR(255) NULL,

    -- 상태
    status ENUM('active','expired','revoked') DEFAULT 'active',

    -- 스코프
    scope VARCHAR(500) NULL,

    created_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY uk_user_mcp (user_id, mcp_server_id),
    KEY idx_user_id (user_id),
    KEY idx_mcp_server_id (mcp_server_id),
    KEY idx_expires_at (expires_at),
    CONSTRAINT fk_oauth_conn_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_oauth_conn_server FOREIGN KEY (mcp_server_id) REFERENCES mcp_servers(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**엔티티 위치**: `src/libs/entity/mcp-oauth-connection.entity.ts`

### PostgreSQL 변경 없음

AI 채팅/파일/벡터 관련 테이블(ai_chats, ai_messages, ai_files, vectorstores 등)은 변경 없음.
OAuth 연결 정보는 MySQL에서만 관리합니다.

---

## 프론트엔드 인증 트리거

| 상황 | 처리 방식 |
|------|----------|
| **최초 연결** | OAuth 팝업 (Step 3 URL로 리다이렉트) |
| **토큰 만료** | 백엔드에서 자동 갱신 (Step 7) |
| **Refresh 실패** | 프론트에 401 반환 → 재인증 팝업 |
| **연결 해제** | DB에서 토큰 삭제 |
| **권한 취소** | 403 에러 → 재인증 필요 안내 |

---

## 참고 자료

### OAuth 표준
- [RFC 6749 - OAuth 2.0](https://datatracker.ietf.org/doc/html/rfc6749)
- [RFC 6750 - Bearer Token](https://datatracker.ietf.org/doc/html/rfc6750)
- [RFC 7591 - Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 7636 - PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [RFC 8414 - Authorization Server Metadata](https://datatracker.ietf.org/doc/html/rfc8414)

### MCP 표준
- [MCP Specification](https://modelcontextprotocol.io/specification)
- [MCP Authorization](https://modelcontextprotocol.io/specification/draft/basic/authorization)

### Provider 문서
- [Notion MCP Docs](https://developers.notion.com/docs/mcp)
- [Notion MCP GitHub](https://github.com/makenotion/notion-mcp-server)
- [Atlassian MCP Docs](https://support.atlassian.com/atlassian-rovo-mcp-server/docs/getting-started-with-the-atlassian-remote-mcp-server/)
- [Atlassian MCP GitHub](https://github.com/atlassian/atlassian-mcp-server)
