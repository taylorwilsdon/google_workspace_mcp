import base64
import json as _json
from types import SimpleNamespace

import pytest

import auth.oauth21_session_store as oauth21_session_store
from auth.oauth21_session_store import (
    OAuth21SessionStore,
    _build_credentials_from_provider,
    _extract_jti_from_jwt,
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
    )

    state_info = store_b.validate_and_consume_oauth_state(
        "shared-state",
        session_id="session-123",
    )

    assert state_info["session_id"] == "session-123"
    assert state_info["code_verifier"] == "verifier-123"


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
# _build_credentials_from_provider — fastmcp 3.x JTI -> upstream-token lookup
# ---------------------------------------------------------------------------


def _make_jwt(jti: str) -> str:
    """Build a minimal three-segment JWT-shaped string with the given jti claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        _json.dumps({"jti": jti, "client_id": "test"}).encode()
    ).rstrip(b"=")
    sig = b"signature"
    return b".".join([header, payload, sig]).decode()


class _AsyncStore:
    """Minimal stand-in for fastmcp's PydanticAdapter[T] used in tests."""

    def __init__(self, data):
        """Initialize the fake store with a dict of key -> value entries."""
        self._data = data

    async def get(self, *, key):
        """Return the stored value for ``key`` or ``None`` if absent."""
        return self._data.get(key)


class _FakeProxyProvider:
    """Stand-in for FastMCP GoogleProvider exposing the two stores we use."""

    def __init__(self, jti_mappings, upstream_tokens):
        """Wrap two dict-based fakes as the provider's JTI and upstream stores."""
        self._jti_mapping_store = _AsyncStore(jti_mappings)
        self._upstream_token_store = _AsyncStore(upstream_tokens)
        # Used by _resolve_client_credentials to find client_id/secret.
        self._upstream_client_id = "test-client-id"
        self._upstream_client_secret = "test-client-secret"


def test_extract_jti_from_jwt_returns_claim():
    """A well-formed JWT with a string ``jti`` returns that claim verbatim."""
    token = _make_jwt("abc123")
    assert _extract_jti_from_jwt(token) == "abc123"


def test_extract_jti_from_jwt_returns_none_for_malformed_token():
    """Tokens that don't have exactly three dot-separated segments return None."""
    assert _extract_jti_from_jwt("not.a.jwt.has-extra-part") is None
    assert _extract_jti_from_jwt("only-one-segment") is None
    assert _extract_jti_from_jwt("") is None


def test_extract_jti_from_jwt_returns_none_when_jti_claim_missing():
    """A valid JWT shape without a ``jti`` claim returns None rather than raising."""
    payload = base64.urlsafe_b64encode(_json.dumps({"sub": "x"}).encode()).rstrip(b"=")
    header = base64.urlsafe_b64encode(b"{}").rstrip(b"=")
    token = b".".join([header, payload, b"sig"]).decode()
    assert _extract_jti_from_jwt(token) is None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_when_no_provider(monkeypatch):
    """With no auth provider registered, the function short-circuits to None."""
    monkeypatch.setattr(oauth21_session_store, "_auth_provider", None)
    access_token = SimpleNamespace(token=_make_jwt("x"), claims={}, scopes=[])
    assert await _build_credentials_from_provider(access_token) is None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_when_provider_lacks_proxy_stores(
    monkeypatch,
):
    """Provider without _jti_mapping_store (e.g. external OAuth or single-user)
    must return None so callers can fall back to the manual-construction path."""
    monkeypatch.setattr(
        oauth21_session_store,
        "_auth_provider",
        SimpleNamespace(),  # no proxy stores
    )
    access_token = SimpleNamespace(token=_make_jwt("x"), claims={}, scopes=[])
    assert await _build_credentials_from_provider(access_token) is None


@pytest.mark.asyncio
async def test_build_credentials_resolves_via_jti_to_upstream_chain(monkeypatch):
    """Regression test for the OAuth 2.1 refresh bug: refresh_token must be
    populated from the upstream token set, not be silently None."""
    jti = "jti-abc"
    upstream_id = "upstream-xyz"
    upstream_set = SimpleNamespace(
        upstream_token_id=upstream_id,
        access_token="ya29.upstream-access",
        refresh_token="1//upstream-refresh",
        expires_at=2_000_000_000,  # far future
        scope="https://www.googleapis.com/auth/spreadsheets openid email",
    )
    provider = _FakeProxyProvider(
        jti_mappings={jti: SimpleNamespace(upstream_token_id=upstream_id, jti=jti)},
        upstream_tokens={upstream_id: upstream_set},
    )
    monkeypatch.setattr(oauth21_session_store, "_auth_provider", provider)

    access_token = SimpleNamespace(
        token=_make_jwt(jti), claims={"email": "u@example.com"}, scopes=[]
    )
    creds = await _build_credentials_from_provider(access_token)

    assert creds is not None
    assert creds.token == "ya29.upstream-access"
    assert creds.refresh_token == "1//upstream-refresh"
    assert creds.client_id == "test-client-id"
    assert creds.client_secret == "test-client-secret"
    assert creds.token_uri == "https://oauth2.googleapis.com/token"
    assert creds.scopes == upstream_set.scope.split()
    assert creds.expiry is not None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_when_jti_mapping_missing(monkeypatch):
    """A revoked or never-issued JTI returns None so the caller can fall back."""
    provider = _FakeProxyProvider(jti_mappings={}, upstream_tokens={})
    monkeypatch.setattr(oauth21_session_store, "_auth_provider", provider)
    access_token = SimpleNamespace(token=_make_jwt("missing"), claims={}, scopes=[])
    assert await _build_credentials_from_provider(access_token) is None


@pytest.mark.asyncio
async def test_build_credentials_returns_none_when_upstream_token_missing(
    monkeypatch,
):
    """Dangling JTI mapping pointing at a missing upstream token returns None."""
    provider = _FakeProxyProvider(
        jti_mappings={"j": SimpleNamespace(upstream_token_id="missing", jti="j")},
        upstream_tokens={},
    )
    monkeypatch.setattr(oauth21_session_store, "_auth_provider", provider)
    access_token = SimpleNamespace(token=_make_jwt("j"), claims={}, scopes=[])
    assert await _build_credentials_from_provider(access_token) is None


@pytest.mark.asyncio
async def test_ensure_session_falls_back_to_manual_construction_when_provider_missing(
    monkeypatch,
):
    """When no proxy is configured, ensure_session_from_access_token still
    returns a Credentials object (with refresh_token=None) by manual construction."""
    monkeypatch.setattr(oauth21_session_store, "_auth_provider", None)
    access_token = SimpleNamespace(
        token="raw-token",
        claims={"email": "u@example.com"},
        scopes=["https://www.googleapis.com/auth/userinfo.email"],
        expires_at=2_000_000_000,
    )
    creds = await ensure_session_from_access_token(access_token, "u@example.com")
    assert creds is not None
    assert creds.token == "raw-token"
    assert creds.refresh_token is None
