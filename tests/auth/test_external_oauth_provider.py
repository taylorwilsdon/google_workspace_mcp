import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import auth.external_oauth_provider as provider_module
from auth.external_oauth_provider import (
    ExternalOAuthProvider,
    get_token_validation_cache_ttl,
)


def _make_provider(*, workers: int = 1, cache_ttl: int = 0) -> ExternalOAuthProvider:
    return ExternalOAuthProvider(
        client_id="test-client",
        client_secret="test-client-secret",
        base_url="https://workspace-mcp.example.test",
        resource_server_url="https://workspace-mcp.example.test",
        required_scopes=["openid"],
        token_validation_workers=workers,
        token_validation_cache_ttl=cache_ttl,
    )


async def _wait_for_thread_event(event: threading.Event) -> None:
    for _ in range(100):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    pytest.fail("validation worker did not reach the expected state")


@pytest.mark.asyncio
async def test_external_token_validation_uses_isolated_executor(monkeypatch):
    provider = _make_provider()
    default_executor = ThreadPoolExecutor(max_workers=1)
    validation_started = threading.Event()
    release_validation = threading.Event()
    observed = {}

    def blocking_get_user_info(credentials, *, skip_valid_check=False):
        observed["token"] = credentials.token
        observed["skip_valid_check"] = skip_valid_check
        validation_started.set()
        assert release_validation.wait(timeout=2)
        return {"email": "user@example.com", "id": "user-id"}

    monkeypatch.setattr("auth.google_auth.get_user_info", blocking_get_user_info)
    asyncio.get_running_loop().set_default_executor(default_executor)

    validation_task = asyncio.create_task(provider.verify_token("ya29.test-token"))
    try:
        await _wait_for_thread_event(validation_started)

        # Both the event loop and its process-wide default executor remain usable
        # while the provider's dedicated worker is blocked in token validation.
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.2)
        canary = await asyncio.wait_for(
            asyncio.to_thread(lambda: "default-executor-ready"), timeout=1
        )
        assert canary == "default-executor-ready"

        release_validation.set()
        access_token = await asyncio.wait_for(validation_task, timeout=1)
    finally:
        release_validation.set()
        provider.close()
        default_executor.shutdown(wait=True, cancel_futures=True)

    assert access_token is not None
    assert access_token.email == "user@example.com"
    assert access_token.sub == "user-id"
    assert observed == {"token": "ya29.test-token", "skip_valid_check": True}


@pytest.mark.asyncio
async def test_external_token_validation_rejects_work_above_capacity(monkeypatch):
    provider = _make_provider()
    validation_started = threading.Event()
    release_validation = threading.Event()
    call_count = 0

    def blocking_get_user_info(credentials, *, skip_valid_check=False):
        nonlocal call_count
        call_count += 1
        validation_started.set()
        assert release_validation.wait(timeout=2)
        return {"email": "user@example.com", "id": "user-id"}

    monkeypatch.setattr("auth.google_auth.get_user_info", blocking_get_user_info)

    first_validation = asyncio.create_task(provider.verify_token("ya29.first"))
    try:
        await _wait_for_thread_event(validation_started)
        rejected = await asyncio.wait_for(
            provider.verify_token("ya29.over-capacity"), timeout=0.2
        )
        assert rejected is None
        assert call_count == 1
    finally:
        release_validation.set()
        await asyncio.wait_for(first_validation, timeout=1)
        provider.close()


@pytest.mark.asyncio
async def test_cancelled_validation_holds_capacity_until_worker_finishes(monkeypatch):
    provider = _make_provider()
    validation_started = threading.Event()
    release_validation = threading.Event()
    validation_finished = threading.Event()
    call_count = 0

    def blocking_get_user_info(credentials, *, skip_valid_check=False):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            validation_started.set()
            assert release_validation.wait(timeout=2)
            validation_finished.set()
        return {"email": "user@example.com", "id": "user-id"}

    monkeypatch.setattr("auth.google_auth.get_user_info", blocking_get_user_info)

    cancelled_validation = asyncio.create_task(provider.verify_token("ya29.cancelled"))
    try:
        await _wait_for_thread_event(validation_started)
        cancelled_validation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_validation

        rejected = await asyncio.wait_for(
            provider.verify_token("ya29.while-worker-running"), timeout=0.2
        )
        assert rejected is None
        assert call_count == 1

        release_validation.set()
        await _wait_for_thread_event(validation_finished)
        for _ in range(100):
            if not provider._token_validation_slots.locked():
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("validation capacity was not released after worker completion")

        accepted = await asyncio.wait_for(
            provider.verify_token("ya29.after-worker-finished"), timeout=1
        )
        assert accepted is not None
        assert call_count == 2
    finally:
        release_validation.set()
        provider.close()


@pytest.mark.asyncio
async def test_closed_provider_rejects_new_validation_work(monkeypatch):
    provider = _make_provider()
    called = False

    def get_user_info(credentials, *, skip_valid_check=False):
        nonlocal called
        called = True
        return {"email": "user@example.com"}

    monkeypatch.setattr("auth.google_auth.get_user_info", get_user_info)
    provider.close()

    assert await provider.verify_token("ya29.after-close") is None
    assert called is False


@pytest.mark.asyncio
async def test_validation_failure_returns_none_and_releases_capacity(monkeypatch):
    provider = _make_provider()
    results = iter([None, {"email": "user@example.com", "id": "user-id"}])

    def get_user_info(credentials, *, skip_valid_check=False):
        return next(results)

    monkeypatch.setattr("auth.google_auth.get_user_info", get_user_info)

    try:
        assert await provider.verify_token("ya29.invalid") is None
        valid = await provider.verify_token("ya29.valid")
    finally:
        provider.close()

    assert valid is not None
    assert valid.email == "user@example.com"


def _counting_user_info(monkeypatch):
    calls = []

    def get_user_info(credentials, *, skip_valid_check=False):
        calls.append(credentials.token)
        return {"email": f"{credentials.token}@example.com", "id": credentials.token}

    monkeypatch.setattr("auth.google_auth.get_user_info", get_user_info)
    return calls


@pytest.mark.asyncio
async def test_validation_cache_is_off_by_default(monkeypatch):
    provider = _make_provider()
    calls = _counting_user_info(monkeypatch)

    try:
        await provider.verify_token("ya29.same")
        await provider.verify_token("ya29.same")
    finally:
        provider.close()

    assert calls == ["ya29.same", "ya29.same"]


@pytest.mark.asyncio
async def test_cached_token_skips_userinfo_and_keeps_its_identity(monkeypatch):
    provider = _make_provider(cache_ttl=60)
    calls = _counting_user_info(monkeypatch)

    try:
        first = await provider.verify_token("ya29.alice")
        second = await provider.verify_token("ya29.alice")
        other = await provider.verify_token("ya29.bob")
    finally:
        provider.close()

    assert calls == ["ya29.alice", "ya29.bob"]
    assert second.token == "ya29.alice"
    assert (second.email, second.sub) == (first.email, first.sub)
    assert other.email == "ya29.bob@example.com"
    assert "ya29.alice" not in provider._validated_identities


@pytest.mark.asyncio
async def test_cached_token_bypasses_exhausted_validation_capacity(monkeypatch):
    provider = _make_provider(cache_ttl=60)
    validation_started = threading.Event()
    release_validation = threading.Event()

    def get_user_info(credentials, *, skip_valid_check=False):
        if credentials.token == "ya29.slow":
            validation_started.set()
            assert release_validation.wait(timeout=2)
        return {"email": "user@example.com", "id": "user-id"}

    monkeypatch.setattr("auth.google_auth.get_user_info", get_user_info)

    await provider.verify_token("ya29.cached")
    slow_validation = asyncio.create_task(provider.verify_token("ya29.slow"))
    try:
        await _wait_for_thread_event(validation_started)
        assert await provider.verify_token("ya29.uncached") is None
        cached = await asyncio.wait_for(
            provider.verify_token("ya29.cached"), timeout=0.2
        )
        assert cached is not None
    finally:
        release_validation.set()
        await asyncio.wait_for(slow_validation, timeout=1)
        provider.close()


@pytest.mark.asyncio
async def test_cached_token_is_revalidated_after_ttl(monkeypatch):
    provider = _make_provider(cache_ttl=60)
    calls = _counting_user_info(monkeypatch)
    now = [1000.0]
    monkeypatch.setattr(provider_module.time, "monotonic", lambda: now[0])

    try:
        await provider.verify_token("ya29.token")
        now[0] += 59
        await provider.verify_token("ya29.token")
        now[0] += 2
        await provider.verify_token("ya29.token")
    finally:
        provider.close()

    assert calls == ["ya29.token", "ya29.token"]


@pytest.mark.asyncio
async def test_failed_validation_is_not_cached(monkeypatch):
    provider = _make_provider(cache_ttl=60)
    results = iter([None, {"email": "user@example.com", "id": "user-id"}])

    def get_user_info(credentials, *, skip_valid_check=False):
        return next(results)

    monkeypatch.setattr("auth.google_auth.get_user_info", get_user_info)

    try:
        assert await provider.verify_token("ya29.flaky") is None
        assert await provider.verify_token("ya29.flaky") is not None
    finally:
        provider.close()


@pytest.mark.asyncio
async def test_validation_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(provider_module, "_TOKEN_VALIDATION_CACHE_MAX_ENTRIES", 3)
    provider = _make_provider(cache_ttl=60)
    _counting_user_info(monkeypatch)

    try:
        for i in range(5):
            await provider.verify_token(f"ya29.token-{i}")
    finally:
        provider.close()

    assert len(provider._validated_identities) == 3


def test_cache_ttl_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL", raising=False)

    assert get_token_validation_cache_ttl() == 0


def test_cache_ttl_reads_env(monkeypatch):
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL", " 60 ")

    assert get_token_validation_cache_ttl() == 60


@pytest.mark.parametrize("raw", ["-1", "soon", "1.5"])
def test_cache_ttl_rejects_invalid_env(monkeypatch, raw):
    monkeypatch.setenv("WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL", raw)

    with pytest.raises(ValueError, match="WORKSPACE_MCP_TOKEN_VALIDATION_CACHE_TTL"):
        get_token_validation_cache_ttl()
