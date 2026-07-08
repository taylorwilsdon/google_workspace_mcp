"""
Trusted-Upstream Mode
=====================

Alternative authentication path for deployments where an upstream
service (e.g. a multi-tenant SaaS gateway) has already authenticated
the end-user with Google and forwards the request to workspace-mcp.

The default modes (``EXTERNAL_OAUTH21_PROVIDER=true`` or a fully local
OAuth server) require workspace-mcp to hold its OWN Google Cloud
``client_id`` / ``client_secret`` at startup. That's fine for a
single-tenant install, but a gateway that serves multiple tenants —
each with their own Google Cloud OAuth app — has no single pair of
credentials to bake into the sidecar env vars.

Trusted-Upstream Mode solves this by moving token verification back to
the gateway itself. The gateway :

1. Validates the user's Google access-token server-side (via userinfo
   or its own cached refresh flow),
2. Extracts the user's email,
3. Signs a short, replay-resistant tuple ``(email, timestamp)`` with a
   shared HMAC secret,
4. Forwards the request to workspace-mcp with three extra headers.

workspace-mcp then :

- Verifies the HMAC signature against the shared secret,
- Verifies the timestamp is within ±60 seconds (replay window),
- Skips its own Google userinfo lookup entirely,
- Uses the header-supplied email as the authenticated user.

The Bearer forwarded on to ``googleapis.com`` is still the real user
token — Google itself is the final authority on what data comes back.
The HMAC only asserts "this identity is the one the gateway vouched
for" to prevent an attacker on the same network from forging a user
identity even if they somehow obtained a valid Bearer.

Threat model (see design doc for detail) :

- ``MCP_UPSTREAM_SECRET`` leak → attacker can forge any identity, but
  still needs a valid Bearer to actually pull data (googleapis is the
  final gate). Rotate the secret + restart both sides.
- Replay within 60 s → possible ; most tools are read-only or require
  fresh args to have side effects. Add a nonce store if a specific
  audit demands it.
- ``TRUSTED_UPSTREAM_MODE`` enabled without ``MCP_UPSTREAM_SECRET`` →
  ``is_enabled()`` returns False + log critical (config bug protection).

Env vars :

- ``TRUSTED_UPSTREAM_MODE``  ``"true"`` to enable ; anything else is off.
- ``MCP_UPSTREAM_SECRET``    HMAC secret. 32+ random bytes hex.
- ``TRUSTED_UPSTREAM_WINDOW_SECS``  optional, default 60. Replay window.

Headers :

- ``X-Abra-User-Email``      user's Google email
- ``X-Abra-Timestamp``       Unix ms as decimal string
- ``X-Abra-Signature``       hex HMAC-SHA256 of ``f"{email}\\n{timestamp}"``
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

# Header names — deliberately not "X-Forwarded-*" (RFC-reserved) and
# not upstream-agnostic like "X-User-Email" (would collide with random
# proxies). The ``X-Abra-*`` prefix makes the origin clear in traces.
HEADER_EMAIL = "x-abra-user-email"
HEADER_TIMESTAMP = "x-abra-timestamp"
HEADER_SIGNATURE = "x-abra-signature"

_DEFAULT_WINDOW_SECS = 60


def is_enabled() -> bool:
    """
    True iff the sidecar is running in trusted-upstream mode with a
    usable secret. Also returns False (with a critical log line) if
    the mode env is on but the secret is missing — refuse to run in
    insecure config rather than default open.
    """
    if os.getenv("TRUSTED_UPSTREAM_MODE", "").strip().lower() != "true":
        return False
    if not _get_secret():
        logger.critical(
            "TRUSTED_UPSTREAM_MODE=true but MCP_UPSTREAM_SECRET is empty. "
            "Refusing to enable trusted-upstream mode — every request will "
            "fall through to the normal auth path. Set MCP_UPSTREAM_SECRET "
            "to a 32+ byte hex string and restart the sidecar."
        )
        return False
    return True


def _get_secret() -> Optional[str]:
    secret = os.getenv("MCP_UPSTREAM_SECRET", "")
    return secret if secret.strip() else None


def _get_window_secs() -> int:
    try:
        return int(os.getenv("TRUSTED_UPSTREAM_WINDOW_SECS", str(_DEFAULT_WINDOW_SECS)))
    except (TypeError, ValueError):
        return _DEFAULT_WINDOW_SECS


def _canonical_message(email: str, timestamp: str) -> bytes:
    """
    The exact byte string that both sides sign. Kept in one function
    so gateway and sidecar can never accidentally disagree on the
    canonical form (delimiter, encoding, trailing newline, …).
    """
    return f"{email}\n{timestamp}".encode("utf-8")


def sign(email: str, timestamp_ms: int, secret: str) -> str:
    """
    Compute the hex HMAC-SHA256 signature. Kept exported so the fork
    tests + the Abra gateway can call the SAME function and never
    drift. Not called by the middleware directly — the middleware
    only verifies.
    """
    return hmac.new(
        secret.encode("utf-8"),
        _canonical_message(email, str(timestamp_ms)),
        hashlib.sha256,
    ).hexdigest()


def _normalize(headers: Mapping[str, str]) -> dict[str, str]:
    """
    HTTP headers are case-insensitive but Python dicts aren't. Lower
    every key once so downstream lookups are trivial and consistent.
    """
    return {k.lower(): v for k, v in headers.items()}


def extract_and_verify(headers: Mapping[str, str]) -> Optional[str]:
    """
    Central entry point. Given a request's HTTP headers, return the
    authenticated user email if and only if all three headers are
    present, the timestamp is inside the replay window, and the HMAC
    matches. Any failure returns ``None`` and logs at ``warning``.

    This function does NOT check ``is_enabled()`` — callers must
    guard with ``is_enabled()`` first. Keeping this separation makes
    the unit tests trivially reproducible without env manipulation.
    """
    secret = _get_secret()
    if not secret:
        return None

    h = _normalize(headers)
    email = h.get(HEADER_EMAIL, "").strip()
    ts_raw = h.get(HEADER_TIMESTAMP, "").strip()
    sig = h.get(HEADER_SIGNATURE, "").strip()

    if not email or not ts_raw or not sig:
        logger.warning(
            "[trusted-upstream] missing headers "
            "(email=%s, timestamp=%s, signature=%s)",
            bool(email),
            bool(ts_raw),
            bool(sig),
        )
        return None

    try:
        ts_ms = int(ts_raw)
    except ValueError:
        logger.warning("[trusted-upstream] invalid timestamp value: %r", ts_raw)
        return None

    # Replay window : reject anything older or younger than ±window
    # seconds. The bidirectional check catches an attacker replaying
    # a captured request AND a misconfigured upstream sending future
    # timestamps (clock drift). ±window also tolerates modest drift
    # between gateway and sidecar clocks.
    now_ms = int(time.time() * 1000)
    delta = abs(now_ms - ts_ms)
    max_delta = _get_window_secs() * 1000
    if delta > max_delta:
        logger.warning(
            "[trusted-upstream] timestamp outside window "
            "(delta_ms=%d, max_ms=%d)",
            delta,
            max_delta,
        )
        return None

    expected = sign(email, ts_ms, secret)
    # ``compare_digest`` — constant-time comparison. Not doing this
    # opens a timing side-channel where an attacker measures how many
    # bytes of the expected signature they matched.
    if not hmac.compare_digest(expected, sig):
        logger.warning(
            "[trusted-upstream] HMAC mismatch for email=%s", email
        )
        return None

    return email
