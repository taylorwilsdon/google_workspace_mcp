"""Tests for the per-account async credential lock (issue #1104).

A single durable HTTP process (the opt-in OpenClaw preset) can receive many
concurrent tool calls for the same Google account. These tests verify:

- get_account_lock returns the same lock instance for the same account and
  different instances for different accounts.
- get_credentials_async actually serializes concurrent refresh attempts for
  the same account: N concurrent callers produce exactly one refresh, and
  callers that waited behind the refresh observe the already-refreshed
  credentials instead of triggering a second refresh.
"""

import asyncio
import threading

import pytest

from auth.credential_store import get_account_lock
from auth.google_auth import get_credentials_async


def test_get_account_lock_is_stable_per_account():
    lock_a1 = get_account_lock("user-a@example.com")
    lock_a2 = get_account_lock("user-a@example.com")
    lock_b = get_account_lock("user-b@example.com")

    assert lock_a1 is lock_a2
    assert lock_a1 is not lock_b


class _FakeCredentials:
    """Minimal stand-in for google.oauth2.credentials.Credentials.

    Starts expired-but-refreshable; refresh() sleeps briefly (to create a
    real race window under concurrency) then flips to valid and increments a
    shared call counter.
    """

    def __init__(self, call_counter: dict):
        self.refresh_token = "refresh-token"
        self.scopes = ["openid"]
        self._valid = False
        self._call_counter = call_counter

    @property
    def valid(self):
        return self._valid

    @property
    def expired(self):
        return not self._valid

    async def refresh_async(self):
        await asyncio.sleep(0.02)
        self._call_counter["count"] += 1
        self._valid = True


@pytest.mark.asyncio
async def test_concurrent_refresh_attempts_are_serialized_per_account():
    """N concurrent callers for the same account trigger exactly one refresh."""
    call_counter = {"count": 0}
    shared_credentials = _FakeCredentials(call_counter)
    account_id = "race-test@example.com"

    async def load_check_refresh():
        async with get_account_lock(account_id):
            # Reload after acquiring the lock: a caller that waited behind the
            # refresh must observe the already-refreshed state and skip
            # refreshing again.
            if shared_credentials.expired and shared_credentials.refresh_token:
                await shared_credentials.refresh_async()
            return shared_credentials.valid

    results = await asyncio.gather(*(load_check_refresh() for _ in range(10)))

    assert call_counter["count"] == 1
    assert all(results)


@pytest.mark.asyncio
async def test_get_credentials_async_serializes_real_refresh_path(monkeypatch):
    """End-to-end: get_credentials_async serializes calls into get_credentials.

    Patches the module-level get_credentials (invoked via asyncio.to_thread)
    with a fake that sleeps briefly and counts refreshes, mirroring a real
    network round-trip. Because get_credentials_async holds the per-account
    lock across the whole to_thread call, only one thread can be executing
    the "refresh" body for this account at a time.
    """
    import time

    account_id = "integration-race@example.com"
    state = {"refreshed": False, "refresh_count": 0}
    state_lock = threading.Lock()

    def fake_get_credentials(
        *,
        user_google_email,
        required_scopes,
        client_secrets_path=None,
        credentials_base_dir=None,
        session_id=None,
    ):
        with state_lock:
            already_valid = state["refreshed"]
        if already_valid:
            return "cached-credentials"
        time.sleep(0.02)
        with state_lock:
            state["refresh_count"] += 1
            state["refreshed"] = True
        return "fresh-credentials"

    monkeypatch.setattr("auth.google_auth.get_credentials", fake_get_credentials)

    results = await asyncio.gather(
        *(
            get_credentials_async(
                user_google_email=account_id,
                required_scopes=["openid"],
            )
            for _ in range(10)
        )
    )

    assert state["refresh_count"] == 1
    assert all(r in ("cached-credentials", "fresh-credentials") for r in results)


@pytest.mark.asyncio
async def test_unlocked_access_would_race(monkeypatch):
    """Sanity check: without the lock, the same scenario races (multiple refreshes).

    This isn't testing our code path directly — it documents why the lock in
    the test above is load-bearing, by showing the naive unlocked version
    calls refresh() more than once.
    """
    call_counter = {"count": 0}
    shared_credentials = _FakeCredentials(call_counter)

    async def load_check_refresh_unlocked():
        if shared_credentials.expired and shared_credentials.refresh_token:
            await shared_credentials.refresh_async()
        return shared_credentials.valid

    await asyncio.gather(*(load_check_refresh_unlocked() for _ in range(10)))

    assert call_counter["count"] > 1
