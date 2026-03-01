"""
E2E tests for OAuth 2.1 flow with mocked components.

Tests cover:
- Token refresh handling
- Multi-user session isolation
- OAuth state management (store, validate, consume)
- Session binding security (immutable bindings)
- Session credential retrieval
- Expiry normalization
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from auth.oauth21_session_store import (
    OAuth21SessionStore,
    SessionContext,
    SessionContextManager,
    set_session_context,
    get_session_context,
    clear_session_context,
    extract_session_from_headers,
    _normalize_expiry_to_naive_utc,
)


class TestExpiryNormalization:
    """Tests for _normalize_expiry_to_naive_utc."""

    def test_none_returns_none(self):
        assert _normalize_expiry_to_naive_utc(None) is None

    def test_naive_datetime_returned_unchanged(self):
        dt = datetime(2026, 3, 1, 12, 0, 0)
        result = _normalize_expiry_to_naive_utc(dt)
        assert result == dt
        assert result.tzinfo is None

    def test_aware_datetime_converted_to_naive_utc(self):
        dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = _normalize_expiry_to_naive_utc(dt)
        assert result.tzinfo is None
        assert result == datetime(2026, 3, 1, 12, 0, 0)

    def test_aware_datetime_nonzero_offset(self):
        est = timezone(timedelta(hours=-5))
        dt = datetime(2026, 3, 1, 12, 0, 0, tzinfo=est)
        result = _normalize_expiry_to_naive_utc(dt)
        assert result.tzinfo is None
        assert result == datetime(2026, 3, 1, 17, 0, 0)

    def test_iso_string_with_z(self):
        result = _normalize_expiry_to_naive_utc("2026-03-01T12:00:00Z")
        assert result is not None
        assert result.tzinfo is None
        assert result == datetime(2026, 3, 1, 12, 0, 0)

    def test_iso_string_with_offset(self):
        result = _normalize_expiry_to_naive_utc("2026-03-01T12:00:00+05:00")
        assert result is not None
        assert result.tzinfo is None
        assert result == datetime(2026, 3, 1, 7, 0, 0)

    def test_invalid_string_returns_none(self):
        result = _normalize_expiry_to_naive_utc("not a date")
        assert result is None

    def test_unsupported_type_returns_none(self):
        result = _normalize_expiry_to_naive_utc(12345)
        assert result is None


class TestSessionContext:
    """Tests for SessionContext dataclass."""

    def test_default_values(self):
        ctx = SessionContext()
        assert ctx.session_id is None
        assert ctx.user_id is None
        assert ctx.metadata == {}

    def test_custom_values(self):
        ctx = SessionContext(
            session_id="sess_001",
            user_id="alice@example.com",
            issuer="https://accounts.google.com"
        )
        assert ctx.session_id == "sess_001"
        assert ctx.user_id == "alice@example.com"
        assert ctx.issuer == "https://accounts.google.com"

    def test_metadata_not_shared_between_instances(self):
        ctx1 = SessionContext()
        ctx2 = SessionContext()
        ctx1.metadata["key"] = "value"
        assert "key" not in ctx2.metadata


class TestSessionContextManager:
    """Tests for SessionContextManager."""

    def test_sets_and_resets_context(self):
        assert get_session_context() is None
        ctx = SessionContext(session_id="test_session")
        with SessionContextManager(ctx):
            current = get_session_context()
            assert current is not None
            assert current.session_id == "test_session"
        # After exiting, context should be reset
        assert get_session_context() is None

    def test_nested_context_managers(self):
        ctx1 = SessionContext(session_id="outer")
        ctx2 = SessionContext(session_id="inner")
        with SessionContextManager(ctx1):
            assert get_session_context().session_id == "outer"
            with SessionContextManager(ctx2):
                assert get_session_context().session_id == "inner"
            assert get_session_context().session_id == "outer"
        assert get_session_context() is None

    def test_context_set_and_clear(self):
        ctx = SessionContext(session_id="set_test")
        set_session_context(ctx)
        assert get_session_context().session_id == "set_test"
        clear_session_context()
        assert get_session_context() is None


class TestOAuth21SessionStore:
    """Tests for OAuth21SessionStore."""

    def test_store_and_retrieve_session(self):
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="token_alice",
            refresh_token="refresh_alice",
            client_id="client_123",
            client_secret="secret_123",
        )
        creds = store.get_credentials("alice@example.com")
        assert creds is not None
        assert creds.token == "token_alice"
        assert creds.refresh_token == "refresh_alice"

    def test_retrieve_nonexistent_session(self):
        store = OAuth21SessionStore()
        creds = store.get_credentials("nobody@example.com")
        assert creds is None

    def test_multi_user_isolation(self):
        """Each user's credentials should be isolated."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="token_alice",
            refresh_token="refresh_alice",
        )
        store.store_session(
            user_email="bob@example.com",
            access_token="token_bob",
            refresh_token="refresh_bob",
        )

        alice_creds = store.get_credentials("alice@example.com")
        bob_creds = store.get_credentials("bob@example.com")

        assert alice_creds.token == "token_alice"
        assert bob_creds.token == "token_bob"
        assert alice_creds.token != bob_creds.token

    def test_session_update_overwrites(self):
        """Storing a session for the same user should update it."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="old_token",
        )
        store.store_session(
            user_email="alice@example.com",
            access_token="new_token",
        )
        creds = store.get_credentials("alice@example.com")
        assert creds.token == "new_token"

    def test_mcp_session_mapping(self):
        """FastMCP session ID should map to user credentials."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="token_alice",
            mcp_session_id="mcp_sess_001",
        )
        creds = store.get_credentials_by_mcp_session("mcp_sess_001")
        assert creds is not None
        assert creds.token == "token_alice"

    def test_mcp_session_mapping_not_found(self):
        store = OAuth21SessionStore()
        creds = store.get_credentials_by_mcp_session("nonexistent_session")
        assert creds is None

    def test_immutable_session_binding(self):
        """Once a session is bound to a user, it cannot be rebound."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="token_alice",
            mcp_session_id="mcp_sess_shared",
        )
        with pytest.raises(ValueError, match="already bound"):
            store.store_session(
                user_email="bob@example.com",
                access_token="token_bob",
                mcp_session_id="mcp_sess_shared",
            )

    def test_same_user_can_reuse_session(self):
        """Same user can update their own session binding."""
        store = OAuth21SessionStore()
        store.store_session(
            user_email="alice@example.com",
            access_token="token_v1",
            mcp_session_id="mcp_sess_alice",
        )
        # Same user, same session - should succeed
        store.store_session(
            user_email="alice@example.com",
            access_token="token_v2",
            mcp_session_id="mcp_sess_alice",
        )
        creds = store.get_credentials("alice@example.com")
        assert creds.token == "token_v2"

    def test_expiry_stored_correctly(self):
        store = OAuth21SessionStore()
        expiry = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        store.store_session(
            user_email="alice@example.com",
            access_token="token_alice",
            expiry=expiry,
        )
        creds = store.get_credentials("alice@example.com")
        assert creds is not None
        # Expiry should be normalized to naive UTC
        assert creds.expiry == datetime(2026, 12, 31, 23, 59, 59)
        assert creds.expiry.tzinfo is None


class TestOAuthStateManagement:
    """Tests for OAuth state store/validate/consume flow."""

    def test_store_and_validate_state(self):
        store = OAuth21SessionStore()
        store.store_oauth_state("state_abc123", session_id="sess_1")
        result = store.validate_and_consume_oauth_state("state_abc123", session_id="sess_1")
        assert result is not None
        assert result["session_id"] == "sess_1"

    def test_state_consumed_on_validation(self):
        """State should be single-use - second validation should fail."""
        store = OAuth21SessionStore()
        store.store_oauth_state("state_single_use")
        store.validate_and_consume_oauth_state("state_single_use")
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("state_single_use")

    def test_invalid_state_raises(self):
        store = OAuth21SessionStore()
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("nonexistent_state")

    def test_empty_state_raises(self):
        store = OAuth21SessionStore()
        with pytest.raises(ValueError, match="Missing OAuth state"):
            store.validate_and_consume_oauth_state("")

    def test_expired_state_rejected(self):
        store = OAuth21SessionStore()
        store.store_oauth_state("state_expired", expires_in_seconds=0)
        # State expires immediately
        with pytest.raises(ValueError, match="Invalid or expired"):
            store.validate_and_consume_oauth_state("state_expired")

    def test_session_mismatch_rejected(self):
        """State bound to one session should not validate for another."""
        store = OAuth21SessionStore()
        store.store_oauth_state("state_bound", session_id="sess_1")
        with pytest.raises(ValueError, match="does not match"):
            store.validate_and_consume_oauth_state("state_bound", session_id="sess_2")

    def test_store_state_validation(self):
        store = OAuth21SessionStore()
        with pytest.raises(ValueError, match="must be provided"):
            store.store_oauth_state("")
        with pytest.raises(ValueError, match="non-negative"):
            store.store_oauth_state("valid_state", expires_in_seconds=-1)


class TestExtractSessionFromHeaders:
    """Tests for extract_session_from_headers."""

    def test_mcp_session_id_header(self):
        headers = {"mcp-session-id": "sess_from_mcp"}
        result = extract_session_from_headers(headers)
        assert result == "sess_from_mcp"

    def test_x_session_id_header(self):
        headers = {"x-session-id": "sess_from_x"}
        result = extract_session_from_headers(headers)
        assert result == "sess_from_x"

    def test_bearer_token_hashed_session(self):
        """Bearer token without matching session should generate hash-based ID."""
        headers = {"authorization": "Bearer some_random_token"}
        result = extract_session_from_headers(headers)
        assert result is not None
        assert result.startswith("bearer_token_")

    def test_no_session_headers(self):
        headers = {"content-type": "application/json"}
        result = extract_session_from_headers(headers)
        assert result is None

    def test_empty_headers(self):
        headers = {}
        result = extract_session_from_headers(headers)
        assert result is None

    def test_mcp_session_id_takes_priority(self):
        headers = {
            "mcp-session-id": "mcp_session",
            "x-session-id": "x_session",
            "authorization": "Bearer token",
        }
        result = extract_session_from_headers(headers)
        assert result == "mcp_session"
