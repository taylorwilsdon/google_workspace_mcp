import time
from types import SimpleNamespace

import pytest
from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.memory import MemoryStore

import auth.oauth21_session_store as session_store
from auth.oauth21_session_store import (
    OAuth21SessionStore,
    _build_credentials_from_provider,
    ensure_session_from_access_token,
)


def test_oauth_state_persists_across_store_instances(tmp_path):
    state_file = tmp_path / "oauth_states.json"
    store_a = OAuth21SessionStore(oauth_state_file=str(state_file))
    store_b = OAuth21SessionStore(oauth_state_file=str(state_file))

    store_a.store_oauth_state(
        "shared-state",
        session_id="session-123",
        code_verifier="verifier-123",
        expected_user_email="user@example.com",
        enforce_user_email_match=True,
        principal_source="gateway_assertion",
    )

    state_info = store_b.validate_and_consume_oauth_state(
        "shared-state",
        session_id="session-123",
    )

    assert state_info["session_id"] == "session-123"
    assert state_info["code_verifier"] == "verifier-123"
    assert state_info["expected_user_email"] == "user@example.com"
    assert state_info["enforce_user_email_match"] is True
    assert state_info["principal_source"] == "gateway_assertion"


def test_consume_latest_oauth_state_reads_from_shared_file(tmp_path):
    state_file = tmp_path / "oauth_states.json"
    store_a = OAuth21SessionStore(oauth_state_file=str(state_file))
    store_b = OAuth21SessionStore(oauth_state_file=str(state_file))

    store_a.store_oauth_state(
        "latest-state",
        session_id=None,
        code_verifier="latest-verifier",
    )

    state_info = store_b.consume_latest_oauth_state()

    assert state_info is not None
    assert state_info["code_verifier"] == "latest-verifier"
    assert store_a.consume_latest_oauth_state() is None


def test_consume_latest_oauth_state_without_session_does_not_read_bound_state_by_default(
    tmp_path,
):
    state_file = tmp_path / "oauth_states.json"
    store_a = OAuth21SessionStore(oauth_state_file=str(state_file))
    store_b = OAuth21SessionStore(oauth_state_file=str(state_file))

    store_a.store_oauth_state(
        "bound-state",
        session_id="session-123",
        code_verifier="bound-verifier",
    )

    state_info = store_b.consume_latest_oauth_state()

    assert state_info is None

    remaining_state_info = store_a.consume_latest_oauth_state(
        initiating_session_id="session-123"
    )
    assert remaining_state_info is not None
    assert remaining_state_info["session_id"] == "session-123"
    assert remaining_state_info["code_verifier"] == "bound-verifier"


def test_consume_latest_oauth_state_without_session_reads_bound_state_when_allowed(
    tmp_path,
):
    state_file = tmp_path / "oauth_states.json"
    store_a = OAuth21SessionStore(oauth_state_file=str(state_file))
    store_b = OAuth21SessionStore(oauth_state_file=str(state_file))

    store_a.store_oauth_state(
        "bound-state",
        session_id="session-123",
        code_verifier="bound-verifier",
    )

    state_info = store_b.consume_latest_oauth_state(allow_any_session=True)

    assert state_info is not None
    assert state_info["session_id"] == "session-123"
    assert state_info["code_verifier"] == "bound-verifier"


def test_consume_latest_oauth_state_filters_by_initiating_session_id(tmp_path):
    state_file = tmp_path / "oauth_states.json"
    store_a = OAuth21SessionStore(oauth_state_file=str(state_file))
    store_b = OAuth21SessionStore(oauth_state_file=str(state_file))

    store_a.store_oauth_state(
        "state-none",
        session_id=None,
        code_verifier="verifier-none",
    )
    store_a.store_oauth_state(
        "state-session-1",
        session_id="session-1",
        code_verifier="verifier-session-1",
    )

    state_info = store_b.consume_latest_oauth_state(initiating_session_id="session-1")

    assert state_info is not None
    assert state_info["session_id"] == "session-1"
    assert state_info["code_verifier"] == "verifier-session-1"

    remaining_state_info = store_a.consume_latest_oauth_state(
        initiating_session_id=None
    )
    assert remaining_state_info is not None
    assert remaining_state_info["session_id"] is None
    assert remaining_state_info["code_verifier"] == "verifier-none"


def test_deserialize_oauth_state_entry_normalizes_invalid_and_naive_timestamps(
    tmp_path,
):
    state_file = tmp_path / "oauth_states.json"
    store = OAuth21SessionStore(oauth_state_file=str(state_file))

    deserialized = store._deserialize_oauth_state_entry(
        {
            "created_at": "2026-04-21T12:00:00",
            "expires_at": "not-a-timestamp",
            "session_id": "session-123",
        }
    )

    assert deserialized["created_at"] is not None
    assert deserialized["created_at"].tzinfo is not None
    assert deserialized["expires_at"] is None


def test_serialize_oauth_state_preserves_missing_enforcement_marker(tmp_path):
    store = OAuth21SessionStore(oauth_state_file=str(tmp_path / "oauth_states.json"))

    serialized = store._serialize_oauth_state_entry(
        {
            "session_id": "session-123",
            "code_verifier": "verifier",
            "user_email": "old-gateway@example.com",
        }
    )

    assert "enforce_user_email_match" not in serialized


def test_store_session_rejects_mcp_session_rebind_by_default(tmp_path):
    state_file = tmp_path / "oauth_states.json"
    store = OAuth21SessionStore(oauth_state_file=str(state_file))

    store.store_session(
        user_email="account-a@example.com",
        access_token="token-a",
        mcp_session_id="session-123",
    )

    with pytest.raises(ValueError, match="already bound to a different user"):
        store.store_session(
            user_email="account-b@example.com",
            access_token="token-b",
            mcp_session_id="session-123",
        )


def test_store_session_skips_mcp_binding_in_single_user_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_SINGLE_USER_MODE", "1")

    state_file = tmp_path / "oauth_states.json"
    store = OAuth21SessionStore(oauth_state_file=str(state_file))

    store.store_session(
        user_email="account-a@example.com",
        access_token="token-a",
        mcp_session_id="session-123",
    )
    store.store_session(
        user_email="account-b@example.com",
        access_token="token-b",
        mcp_session_id="session-123",
    )

    assert store.get_user_by_mcp_session("session-123") is None
    assert store.get_credentials("account-b@example.com").token == "token-b"


# ---------------------------------------------------------------------------
# _build_credentials_from_provider — fastmcp 3.x jti -> upstream token lookup
# (regression coverage for #886: refresh_token must be populated)
# ---------------------------------------------------------------------------


class _AsyncKVStore:
    """Minimal async stand-in for fastmcp's PydanticAdapter key/value store."""

    def __init__(self, data):
        self._data = data

    async def get(self, *, key):
        return self._data.get(key)


class _FakeJwtIssuer:
    """Stand-in for OAuthProxy.jwt_issuer; only ``fastmcp-jwt`` is a valid token."""

    def __init__(self, payload):
        self._payload = payload

    def verify_token(self, token, expected_token_use="access"):  # noqa: ARG002
        if token != "fastmcp-jwt":
            raise ValueError("not a FastMCP-issued token")
        return self._payload


class _FakeOAuthProxy:
    """Stand-in for a FastMCP GoogleProvider exposing the stores and issuer that
    ``_build_credentials_from_provider`` resolves the upstream token through."""

    def __init__(self, jti_mappings, upstream_tokens, jti_payload):
        self._jti_mapping_store = _AsyncKVStore(jti_mappings)
        self._upstream_token_store = _AsyncKVStore(upstream_tokens)
        self.jwt_issuer = _FakeJwtIssuer(jti_payload)
        self._upstream_client_id = "client-id-123"
        self._upstream_client_secret = "client-secret-456"


def _headers_with_bearer(token):
    return lambda include=None: {"authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_build_credentials_resolves_upstream_refresh_token(monkeypatch):
    """The credential must carry the upstream refresh token resolved via
    jti -> _jti_mapping_store -> _upstream_token_store, not a bare token."""
    jti = "jti-abc"
    upstream_id = "upstream-xyz"
    upstream = SimpleNamespace(
        upstream_token_id=upstream_id,
        access_token="ya29.google-access",
        refresh_token="1//google-refresh",
        expires_at=2_000_000_000,
        scope="https://www.googleapis.com/auth/drive openid email",
    )
    provider = _FakeOAuthProxy(
        jti_mappings={jti: SimpleNamespace(jti=jti, upstream_token_id=upstream_id)},
        upstream_tokens={upstream_id: upstream},
        jti_payload={"jti": jti},
    )
    monkeypatch.setattr(session_store, "_auth_provider", provider)
    monkeypatch.setattr(
        session_store, "get_http_headers", _headers_with_bearer("fastmcp-jwt")
    )

    creds = await _build_credentials_from_provider()

    assert creds is not None
    assert creds.token == "ya29.google-access"
    assert creds.refresh_token == "1//google-refresh"
    assert creds.client_id == "client-id-123"
    assert creds.client_secret == "client-secret-456"
    assert creds.token_uri == "https://oauth2.googleapis.com/token"
    assert creds.scopes == upstream.scope.split()
    assert creds.expiry is not None


@pytest.mark.asyncio
async def test_build_credentials_matches_fastmcp_oauth_proxy_contract(monkeypatch):
    """Exercise the private FastMCP contract used to recover upstream tokens."""
    provider = GoogleProvider(
        client_id="google-client",
        client_secret="google-secret",
        base_url="https://example.test",
        client_storage=MemoryStore(),
        jwt_signing_key="test-signing-key",
        required_scopes=["openid"],
    )
    provider.get_routes("/mcp")

    now = time.time()
    jti = "access-jti"
    upstream_id = "upstream-id"
    upstream = UpstreamTokenSet(
        upstream_token_id=upstream_id,
        access_token="ya29.google-access",
        refresh_token="1//google-refresh",
        refresh_token_expires_at=None,
        expires_at=now + 3600,
        token_type="Bearer",
        scope="openid email",
        client_id="mcp-client",
        created_at=now,
    )
    await provider._upstream_token_store.put(key=upstream_id, value=upstream, ttl=3600)
    await provider._jti_mapping_store.put(
        key=jti,
        value=JTIMapping(
            jti=jti,
            upstream_token_id=upstream_id,
            created_at=now,
        ),
        ttl=3600,
    )
    bearer = provider.jwt_issuer.issue_access_token(
        client_id="mcp-client",
        scopes=["openid", "email"],
        jti=jti,
        expires_in=3600,
    )

    monkeypatch.setattr(session_store, "_auth_provider", provider)
    monkeypatch.setattr(
        session_store,
        "get_http_headers",
        _headers_with_bearer(bearer),
    )

    creds = await _build_credentials_from_provider()

    assert creds is not None
    assert creds.token == upstream.access_token
    assert creds.refresh_token == upstream.refresh_token
    assert creds.client_id == "google-client"
    assert creds.client_secret == "google-secret"
    assert creds.scopes == ["openid", "email"]


@pytest.mark.asyncio
async def test_build_credentials_returns_none_without_proxy_stores(monkeypatch):
    """External OAuth 2.1 / single-user providers lack the proxy stores, so the
    resolver returns None and the caller falls back to a minimal credential."""
    monkeypatch.setattr(session_store, "_auth_provider", SimpleNamespace())
    assert await _build_credentials_from_provider() is None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_without_inbound_bearer(monkeypatch):
    """No Authorization header (e.g. no active request) yields no jti to resolve."""
    provider = _FakeOAuthProxy({}, {}, {"jti": "x"})
    monkeypatch.setattr(session_store, "_auth_provider", provider)
    monkeypatch.setattr(session_store, "get_http_headers", lambda include=None: {})
    assert await _build_credentials_from_provider() is None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_for_direct_google_token(monkeypatch):
    """A raw Google token (not a FastMCP JWT) can't be mapped to a refresh token."""
    provider = _FakeOAuthProxy({}, {}, {"jti": "x"})
    monkeypatch.setattr(session_store, "_auth_provider", provider)
    monkeypatch.setattr(
        session_store, "get_http_headers", _headers_with_bearer("ya29.raw-token")
    )
    assert await _build_credentials_from_provider() is None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_when_jti_mapping_missing(monkeypatch):
    """A revoked or expired jti has no mapping, so the resolver returns None."""
    provider = _FakeOAuthProxy(
        jti_mappings={}, upstream_tokens={}, jti_payload={"jti": "gone"}
    )
    monkeypatch.setattr(session_store, "_auth_provider", provider)
    monkeypatch.setattr(
        session_store, "get_http_headers", _headers_with_bearer("fastmcp-jwt")
    )
    assert await _build_credentials_from_provider() is None


@pytest.mark.asyncio
async def test_ensure_session_falls_back_to_non_refreshable_credential(monkeypatch):
    """With no proxy provider, ensure_session still returns a usable (but
    non-refreshable) credential built directly from the access token."""
    monkeypatch.setattr(session_store, "_auth_provider", None)
    monkeypatch.setattr(
        session_store,
        "get_oauth21_session_store",
        lambda: SimpleNamespace(store_session=lambda **kwargs: None),
    )
    access_token = SimpleNamespace(
        token="ya29.direct",
        claims={"email": "user@example.com"},
        scopes=["https://www.googleapis.com/auth/userinfo.email"],
        expires_at=2_000_000_000,
    )

    creds = await ensure_session_from_access_token(access_token, "user@example.com")

    assert creds is not None
    assert creds.token == "ya29.direct"
    assert creds.refresh_token is None
