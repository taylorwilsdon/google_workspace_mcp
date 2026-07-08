"""
Unit tests for auth.trusted_upstream — the HMAC-signed upstream
identity path.

Kept pure : no FastMCP fixtures, no HTTP mocking. Every test only
manipulates env vars + a plain dict of headers, so they run in <10 ms
each and are trivially reproducible.
"""

from __future__ import annotations

import importlib
import time

import pytest

from auth import trusted_upstream


SECRET = "0" * 64  # any hex-ish string of usable length
EMAIL = "alice@example.com"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Default state : mode OFF + secret empty. Individual tests enable
    what they need. ``autouse`` guarantees no test bleeds into the
    next.
    """
    monkeypatch.delenv("TRUSTED_UPSTREAM_MODE", raising=False)
    monkeypatch.delenv("MCP_UPSTREAM_SECRET", raising=False)
    monkeypatch.delenv("TRUSTED_UPSTREAM_WINDOW_SECS", raising=False)
    # Ensure the module reads fresh env — no module-level cache today
    # but this guards against a future accidental cache.
    importlib.reload(trusted_upstream)


def _fresh_ts_ms() -> int:
    return int(time.time() * 1000)


def _headers(email: str, ts_ms: int, secret: str) -> dict[str, str]:
    return {
        "X-Abra-User-Email": email,
        "X-Abra-Timestamp": str(ts_ms),
        "X-Abra-Signature": trusted_upstream.sign(email, ts_ms, secret),
    }


# ── is_enabled ──────────────────────────────────────────────────────────


def test_is_enabled_default_off() -> None:
    assert trusted_upstream.is_enabled() is False


def test_is_enabled_mode_only_returns_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode on without a secret is a config bug — refuse to enable."""
    monkeypatch.setenv("TRUSTED_UPSTREAM_MODE", "true")
    assert trusted_upstream.is_enabled() is False


def test_is_enabled_when_mode_and_secret_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_UPSTREAM_MODE", "true")
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    assert trusted_upstream.is_enabled() is True


def test_is_enabled_case_insensitive_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_UPSTREAM_MODE", "TRUE")
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    assert trusted_upstream.is_enabled() is True


def test_is_enabled_rejects_non_true_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    for val in ("1", "yes", "on", "", " ", "false"):
        monkeypatch.setenv("TRUSTED_UPSTREAM_MODE", val)
        assert trusted_upstream.is_enabled() is False, (
            f"unexpected enable for {val!r}"
        )


# ── extract_and_verify — happy path ─────────────────────────────────────


def test_extract_and_verify_valid_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    result = trusted_upstream.extract_and_verify(_headers(EMAIL, ts, SECRET))
    assert result == EMAIL


def test_extract_and_verify_headers_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP headers are case-insensitive — the helper must accept
    both ``X-Abra-*`` and ``x-abra-*``."""
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = {
        "x-abra-user-email": EMAIL,
        "X-ABRA-TIMESTAMP": str(ts),
        "x-Abra-signature": trusted_upstream.sign(EMAIL, ts, SECRET),
    }
    assert trusted_upstream.extract_and_verify(headers) == EMAIL


# ── extract_and_verify — replay window ──────────────────────────────────


def test_extract_and_verify_rejects_stale_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    stale = _fresh_ts_ms() - (120 * 1000)  # 2 min ago, default window 60 s
    assert (
        trusted_upstream.extract_and_verify(_headers(EMAIL, stale, SECRET))
        is None
    )


def test_extract_and_verify_rejects_future_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    future = _fresh_ts_ms() + (120 * 1000)
    assert (
        trusted_upstream.extract_and_verify(_headers(EMAIL, future, SECRET))
        is None
    )


def test_extract_and_verify_custom_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Small drift OK when the operator widens the window."""
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    monkeypatch.setenv("TRUSTED_UPSTREAM_WINDOW_SECS", "600")  # 10 min
    older = _fresh_ts_ms() - (300 * 1000)  # 5 min ago
    assert (
        trusted_upstream.extract_and_verify(_headers(EMAIL, older, SECRET))
        == EMAIL
    )


# ── extract_and_verify — signature / tampering ──────────────────────────


def test_extract_and_verify_rejects_bad_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    headers["X-Abra-Signature"] = "f" * 64  # not the right hex
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_rejects_email_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signing alice + swapping the email header to eve must fail —
    that's the core property of HMAC binding identity to timestamp."""
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    headers["X-Abra-User-Email"] = "eve@example.com"
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_rejects_ts_tamper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    # Change ts within window so window check passes, but signature
    # no longer matches.
    headers["X-Abra-Timestamp"] = str(ts + 1000)
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_rejects_wrong_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, "different-secret")
    assert trusted_upstream.extract_and_verify(headers) is None


# ── extract_and_verify — malformed / missing ────────────────────────────


def test_extract_and_verify_missing_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    del headers["X-Abra-User-Email"]
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_missing_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    del headers["X-Abra-Signature"]
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_invalid_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    headers["X-Abra-Timestamp"] = "not-a-number"
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_no_secret_returns_none() -> None:
    """Called without MCP_UPSTREAM_SECRET → None (caller should
    guard with is_enabled but the helper stays safe on its own)."""
    ts = _fresh_ts_ms()
    headers = _headers(EMAIL, ts, SECRET)
    assert trusted_upstream.extract_and_verify(headers) is None


def test_extract_and_verify_empty_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_UPSTREAM_SECRET", SECRET)
    assert trusted_upstream.extract_and_verify({}) is None


# ── sign — round-trip determinism ───────────────────────────────────────


def test_sign_deterministic() -> None:
    """Same inputs → same output. Anything else means we accidentally
    baked entropy into the signature and forgery becomes trivial."""
    sig_a = trusted_upstream.sign(EMAIL, 1234567890, SECRET)
    sig_b = trusted_upstream.sign(EMAIL, 1234567890, SECRET)
    assert sig_a == sig_b


def test_sign_changes_on_email() -> None:
    sig_a = trusted_upstream.sign("a@x.com", 1234567890, SECRET)
    sig_b = trusted_upstream.sign("b@x.com", 1234567890, SECRET)
    assert sig_a != sig_b


def test_sign_changes_on_timestamp() -> None:
    sig_a = trusted_upstream.sign(EMAIL, 1000, SECRET)
    sig_b = trusted_upstream.sign(EMAIL, 2000, SECRET)
    assert sig_a != sig_b


def test_sign_changes_on_secret() -> None:
    sig_a = trusted_upstream.sign(EMAIL, 1234567890, "secret-a")
    sig_b = trusted_upstream.sign(EMAIL, 1234567890, "secret-b")
    assert sig_a != sig_b
