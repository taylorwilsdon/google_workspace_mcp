"""Tests for the short-TTL attachment credential cache (Design A, hosted path).

Covers the credential record round-trip (the security-relevant serialization the
route depends on) and the end-to-end stash/load against the in-process fallback
store.
"""

from datetime import datetime

import pytest
from google.oauth2.credentials import Credentials

from core import attachment_cred_cache as cache


@pytest.fixture(autouse=True)
def _reset_store(monkeypatch):
    # Force the in-process MemoryStore fallback and a clean singleton each test.
    monkeypatch.delenv("WORKSPACE_MCP_OAUTH_PROXY_VALKEY_HOST", raising=False)
    monkeypatch.delenv("WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND", raising=False)
    monkeypatch.setattr(cache, "_store", None)
    monkeypatch.setattr(cache, "_store_built", False)
    yield
    monkeypatch.setattr(cache, "_store", None)
    monkeypatch.setattr(cache, "_store_built", False)


def _sample_credentials():
    return Credentials(
        token="access-123",
        refresh_token="refresh-456",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        expiry=datetime(2030, 1, 1, 12, 0, 0),
    )


class TestRecordRoundTrip:
    def test_credentials_survive_serialization(self):
        creds = _sample_credentials()
        record = cache._credentials_to_record(creds)
        restored = cache._record_to_credentials(record)

        assert restored.token == "access-123"
        assert restored.refresh_token == "refresh-456"
        assert restored.token_uri == "https://oauth2.googleapis.com/token"
        assert restored.client_id == "client-id"
        assert restored.client_secret == "client-secret"
        assert restored.scopes == ["https://www.googleapis.com/auth/gmail.readonly"]
        # google-auth keeps expiry naive UTC.
        assert restored.expiry == datetime(2030, 1, 1, 12, 0, 0)
        assert restored.expiry.tzinfo is None

    def test_record_is_json_serializable(self):
        record = cache._credentials_to_record(_sample_credentials())
        # The KV store JSON-encodes values; ensure no non-serializable types leak.
        import json

        json.loads(json.dumps(record))

    def test_handles_missing_expiry(self):
        creds = Credentials(token="t", refresh_token=None, token_uri="u")
        restored = cache._record_to_credentials(cache._credentials_to_record(creds))
        assert restored.expiry is None


class TestStashLoad:
    @pytest.mark.asyncio
    async def test_stash_then_load(self):
        creds = _sample_credentials()
        stored = await cache.stash_credentials(
            "user@example.com", creds, ttl_seconds=60
        )
        assert stored is True

        loaded = await cache.load_credentials("user@example.com")
        assert loaded is not None
        assert loaded.token == "access-123"
        assert loaded.refresh_token == "refresh-456"

    @pytest.mark.asyncio
    async def test_load_missing_returns_none(self):
        assert await cache.load_credentials("nobody@example.com") is None

    @pytest.mark.asyncio
    async def test_isolation_between_emails(self):
        await cache.stash_credentials(
            "a@example.com", _sample_credentials(), ttl_seconds=60
        )
        assert await cache.load_credentials("b@example.com") is None
