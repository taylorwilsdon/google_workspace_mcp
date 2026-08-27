"""Tests for signed attachment capability tokens (Design A).

The token is the per-user authorization for the /attachments/signed route, so the
security-relevant properties are: a valid token round-trips, a tampered or
wrong-key token is rejected, and an expired token is rejected.
"""

import jwt
import pytest

from core import attachment_signing as sign
from core.signed_download import get_fetcher


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv(
        "WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY",
        "test-signing-key-padding-to-32-bytes-min",
    )
    yield


class TestEnabledFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_SIGNED_ATTACHMENT_URLS", raising=False)
        assert sign.signed_attachment_urls_enabled() is False

    def test_enabled_when_true(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_MCP_SIGNED_ATTACHMENT_URLS", "true")
        assert sign.signed_attachment_urls_enabled() is True


class TestRoundTrip:
    def test_valid_token_round_trips(self):
        token = sign.mint_attachment_token(
            source="gmail",
            message_id="msg1",
            attachment_id="att1",
            user_email="user@example.com",
        )
        claims = sign.verify_attachment_token(token)
        assert claims is not None
        assert claims["src"] == "gmail"
        assert claims["mid"] == "msg1"
        assert claims["aid"] == "att1"
        assert claims["sub"] == "user@example.com"

    def test_tampered_token_is_rejected(self):
        token = sign.mint_attachment_token(
            source="gmail",
            message_id="msg1",
            attachment_id="att1",
            user_email="user@example.com",
        )
        assert sign.verify_attachment_token(token + "x") is None

    def test_wrong_key_is_rejected(self, monkeypatch):
        token = sign.mint_attachment_token(
            source="gmail",
            message_id="msg1",
            attachment_id="att1",
            user_email="user@example.com",
        )
        monkeypatch.setenv(
            "WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY",
            "a-different-signing-key-32-bytes-minimum",
        )
        assert sign.verify_attachment_token(token) is None

    def test_expired_token_is_rejected(self):
        token = sign.mint_attachment_token(
            source="gmail",
            message_id="msg1",
            attachment_id="att1",
            user_email="user@example.com",
            ttl_seconds=-1,
        )
        assert sign.verify_attachment_token(token) is None

    def test_malformed_token_is_rejected(self):
        assert sign.verify_attachment_token("not.a.jwt") is None


class TestSigningKeyFallback:
    def test_falls_back_to_client_secret(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY", raising=False)
        monkeypatch.setenv(
            "GOOGLE_OAUTH_CLIENT_SECRET", "client-secret-padded-to-32-bytes-minimum"
        )
        token = sign.mint_attachment_token(
            source="gmail",
            message_id="m",
            attachment_id="a",
            user_email="u@example.com",
        )
        # Verifiable with the same fallback key.
        assert sign.verify_attachment_token(token) is not None
        # And the payload is signed with that secret, not the dedicated key.
        assert jwt.decode(
            token, "client-secret-padded-to-32-bytes-minimum", algorithms=["HS256"]
        )["sub"] == ("u@example.com")

    def test_raises_when_no_key_available(self, monkeypatch):
        monkeypatch.delenv("WORKSPACE_MCP_ATTACHMENT_SIGNING_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
        with pytest.raises(RuntimeError):
            sign.mint_attachment_token(
                source="gmail",
                message_id="m",
                attachment_id="a",
                user_email="u@example.com",
            )


class TestUrlBuilder:
    def test_uses_external_url_when_set(self, monkeypatch):
        monkeypatch.setenv("WORKSPACE_EXTERNAL_URL", "https://gw.example.com/")
        url = sign.get_signed_attachment_url("TOK")
        assert url == "https://gw.example.com/attachments/signed/TOK"


class TestDownloadToken:
    def test_gmail_wrapper_matches_generic(self):
        # mint_attachment_token is a thin Gmail wrapper over mint_download_token.
        claims = sign.verify_attachment_token(
            sign.mint_attachment_token(
                source="gmail",
                message_id="msg1",
                attachment_id="att1",
                user_email="user@example.com",
            )
        )
        assert claims["src"] == "gmail"
        assert claims["mid"] == "msg1"
        assert claims["aid"] == "att1"
        assert "fn" not in claims  # Gmail resolves filename in the route, by size

    def test_drive_token_carries_ref_and_filename(self):
        claims = sign.verify_attachment_token(
            sign.mint_download_token(
                source="drive",
                user_email="user@example.com",
                ref={"fid": "FILE123", "emt": "application/pdf"},
                filename="Report.pdf",
                mime_type="application/pdf",
            )
        )
        assert claims["src"] == "drive"
        assert claims["fid"] == "FILE123"
        assert claims["emt"] == "application/pdf"
        # Drive ids are stable, so filename/MIME are signed in at mint.
        assert claims["fn"] == "Report.pdf"
        assert claims["mt"] == "application/pdf"

    def test_ref_with_reserved_key_is_rejected(self):
        # A locator must not be able to override reserved claims (src/sub/exp/...).
        with pytest.raises(ValueError):
            sign.mint_download_token(
                source="drive",
                user_email="user@example.com",
                ref={"fid": "F", "sub": "attacker@example.com"},
            )


class TestFetcherRegistry:
    def test_known_sources_resolve(self):
        assert get_fetcher("gmail") is not None
        assert get_fetcher("drive") is not None

    def test_unknown_source_is_none(self):
        assert get_fetcher("ftp") is None


class TestClampTtlToExpiry:
    """A signed URL must never outlive its (non-refreshable) credential snapshot."""

    def _now(self):
        from datetime import datetime, timezone

        return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_expiry_returns_default(self):
        assert sign.clamp_ttl_to_expiry(None, default_ttl=900) == 900

    def test_far_future_expiry_capped_at_default(self):
        from datetime import timedelta

        expiry = (self._now() + timedelta(hours=1)).replace(tzinfo=None)  # naive UTC
        assert sign.clamp_ttl_to_expiry(expiry, default_ttl=900, now=self._now()) == 900

    def test_near_expiry_clamps_below_token_life(self):
        from datetime import timedelta

        # 5 min of token life left -> 300 - 30 margin = 270, under the 900 default.
        expiry = (self._now() + timedelta(seconds=300)).replace(tzinfo=None)
        assert sign.clamp_ttl_to_expiry(expiry, default_ttl=900, now=self._now()) == 270

    def test_expired_token_returns_non_positive(self):
        from datetime import timedelta

        # Already expired -> non-positive TTL signals the caller to skip minting
        # (and fall back to the normal download) rather than emit a doomed URL.
        expiry = (self._now() - timedelta(seconds=120)).replace(tzinfo=None)
        assert sign.clamp_ttl_to_expiry(expiry, default_ttl=900, now=self._now()) <= 0

    def test_too_near_expiry_returns_non_positive(self):
        from datetime import timedelta

        # Less remaining life than the safety margin -> non-positive (never floored
        # up to a value that would outlive the credential).
        expiry = (self._now() + timedelta(seconds=20)).replace(tzinfo=None)
        assert sign.clamp_ttl_to_expiry(expiry, default_ttl=900, now=self._now()) <= 0

    def test_url_never_outlives_snapshot(self):
        # Property: the URL's TTL never exceeds the credential's remaining life.
        from datetime import timedelta

        for secs in (60, 120, 600, 3600):
            expiry = (self._now() + timedelta(seconds=secs)).replace(tzinfo=None)
            ttl = sign.clamp_ttl_to_expiry(expiry, default_ttl=900, now=self._now())
            assert ttl <= secs


class TestFormatTtl:
    """The tool response must state the real (clamped) link lifetime."""

    def test_short_lifetimes_reported_in_seconds(self):
        assert sign.format_ttl(45) == "45 seconds"
        assert sign.format_ttl(119) == "119 seconds"

    def test_longer_lifetimes_reported_in_minutes(self):
        assert sign.format_ttl(120) == "~2 minutes"
        assert sign.format_ttl(270) == "~4 minutes"
        assert sign.format_ttl(900) == "~15 minutes"
